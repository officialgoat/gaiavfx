"""Panoramas: two 360-degree candidates a stage can be built from, drawn from words or grown from a picture, with
their seams closed. One `panorama` operation holds both candidates, advanced in lock-step so ComfyUI loads each model
once; a person reviews them and chooses one (`review_approvals.choose_panorama`: Gaia never chooses).

From words (the research's pano-t2i): Krea 2 draws each panorama, a local step paints its seam band green, Fibo 2.0
fills the band, and the advancer's last stage closes the seam, proving nothing outside the band moved.
From a picture (f-out): the picture is cut free of letterbox bars and MoGe-3 measures its field of view; then the
advancer places it on a green canvas, Fibo grows it into two panoramas, the picture's own pixels go back inside its
footprint, and the seam is closed as above.

Intermediate images are hidden Library assets; each candidate's job keeps its seam check (and, from a picture, the
plate check) as report artifacts."""

from __future__ import annotations

import json
from dataclasses import asdict
from hashlib import sha256

from gaiavfx.application import operation_start
from gaiavfx.application.candidate_review import existing_review
from gaiavfx.application.direction_reading import DIRECTION as direction
from gaiavfx.integrations import panorama
from gaiavfx.integrations.panorama.graph import (
    fibo_edit_graph,
    parse_render,
    plate_geometry_graph,
    text_to_panorama_graph,
)
from gaiavfx.integrations.panorama.prompt import compile_description, outpaint_prompt, seam_prompt
from gaiavfx.modules.generation.operations import staged
from gaiavfx.modules.generation.operations.lifecycle import STOPPED, JobSpec
from gaiavfx.modules.generation.operations.review import ReviewService
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.panoramas.ask import DescribedPlace, Landmark, PicturedPlace
from gaiavfx.shared.errors import ApplicationError

NAME = "panorama"
VERSION = 1
CANDIDATES = 2  # each attempt draws two
RENDERS = 4  # ComfyUI image jobs an attempt runs: two panoramas, two seam fills
STAGES = ("plate", "measure", "canvas", "draw", "outpaint", "band", "fill", "seam")
WORDS = {
    "plate": "Reading the picture",
    "measure": "Measuring the picture's field of view",
    "canvas": "Placing the picture in the panorama",
    "draw": "Drawing two panoramas",
    "outpaint": "Growing the picture into two panoramas",
    "band": "Preparing the seams",
    "fill": "Filling the seams",
    "seam": "Closing the seams",
}
LETTERS = "AB"
operations = OperationService()
reviews = ReviewService()


def _frozen() -> dict:
    return operation_start.knowledge_snapshot(panorama)


def _render(request) -> dict:
    return parse_render(request["knowledge"]["render.toml"])


def _key(request: dict) -> str:
    material = {k: request[k] for k in ("mode", "ask", "picture_sha256", "seeds", "attempt")}
    material["knowledge"] = sha256(
        json.dumps(request["knowledge"], sort_keys=True).encode()
    ).hexdigest()
    return sha256(json.dumps([NAME, VERSION, material], sort_keys=True).encode()).hexdigest()[:12]


def _seeds(render: dict, attempt: int) -> list[int]:
    first = [int(seed) for seed in render["candidates"]["seeds"]]
    return [seed + CANDIDATES * attempt for seed in first]


def described(place: str, landmarks=(), light: str = "") -> DescribedPlace:
    return DescribedPlace(
        place, tuple(Landmark(item["what"], int(item["heading"])) for item in landmarks), light
    )


def preview(
    session,
    library,
    *,
    described_place: DescribedPlace | None = None,
    pictured: PicturedPlace | None = None,
) -> dict:
    """What starting would do, in words: the compiled description (from words), the estimate and the licences."""
    if (described_place is None) == (pictured is None):
        raise ApplicationError("Describe the place or choose a picture.", 422)
    render = parse_render(_frozen()["render.toml"])
    estimate = render["estimate"]
    if described_place is not None:
        minutes = CANDIDATES * (estimate["draw"] + estimate["fill"])
        prompt = compile_description(described_place)
        what = "Krea 2 draws two panoramas from your words; Fibo 2.0 closes each one's seam."
    else:
        _picture(session, library, pictured.picture_id)
        minutes = estimate["measure"] + CANDIDATES * (estimate["draw"] + estimate["fill"])
        prompt = None
        what = "Keeps your picture; everything around it is invented by Fibo 2.0 — check it before building."
    return {
        "what": what,
        "prompt": prompt,
        "estimate": f"About {max(1, round(minutes))} minutes on the tested GPU.",
        "renders": RENDERS,
        "licence": "Krea 2 and Fibo 2.0 are licensed for non-commercial use only.",
    }


def _picture(session, library, asset_id: str) -> dict:
    asset = library.get(session, asset_id)
    if asset["kind"] != "image":
        raise ApplicationError("A panorama grows from a still picture.", 422)
    return asset


def start_panorama(
    session,
    library,
    *,
    place_id: str | None = None,
    described_place: DescribedPlace | None = None,
    pictured: PicturedPlace | None = None,
    attempt: int = 0,
) -> dict:
    """Start (or return the running) attempt: two candidates from words or from one picture."""
    if (described_place is None) == (pictured is None):
        raise ApplicationError("Describe the place or choose a picture.", 422)
    if place_id:
        direction.read_place(session, place_id)
    knowledge = _frozen()
    render = parse_render(knowledge["render.toml"])
    seeds = _seeds(render, attempt)
    if described_place is not None:
        ask, words, picture = asdict(described_place), described_place.place, None
    else:
        picture = _picture(session, library, pictured.picture_id)
        ask, words = asdict(pictured), pictured.place
    request = {
        "mode": "description" if described_place is not None else "picture",
        "place_id": place_id,
        "place": words,
        "ask": ask,
        "picture_sha256": picture["sha256"] if picture else None,
        "prompt": compile_description(described_place) if described_place is not None else None,
        "seeds": seeds,
        "attempt": attempt,
        "knowledge": knowledge,
    }
    key = _key(request)
    prefix = f"gaiavfx/panoramas/{key}"
    jobs = _first_jobs(request, render, prefix, picture)
    return operation_start.create_operation(
        session,
        kind=NAME,
        request=request | {"prefix": prefix},
        jobs=jobs,
        budget_limit=None,
        awaits_review=True,
        budget=RENDERS,
        request_key=key,
    )


def make_more(session, library, operation_id: str) -> dict:
    """Two more candidates for the same request: the next pair of seeds."""
    operation = _operation(session, operation_id)
    request = operation["request"]
    newest = max(
        (o["request"]["attempt"] for o in _siblings(session, operation)), default=request["attempt"]
    )
    ask = request["ask"]
    if request["mode"] == "description":
        place = described(ask["place"], ask["landmarks"], ask["light"])
        return start_panorama(
            session,
            library,
            place_id=request["place_id"],
            described_place=place,
            attempt=newest + 1,
        )
    pictured = PicturedPlace(ask["picture_id"], ask["hfov"], ask["place"])
    return start_panorama(
        session, library, place_id=request["place_id"], pictured=pictured, attempt=newest + 1
    )


def _siblings(session, operation) -> list[dict]:
    same = {k: operation["request"][k] for k in ("mode", "ask", "place_id")}
    return [
        o
        for o in operations.recent(session, kind=NAME, limit=100)
        if {k: o["request"][k] for k in same} == same
    ]


def _comfy(kind, graph, request, stage, unit, seed=None, inputs=None, depends_on=None) -> JobSpec:
    return JobSpec(
        kind,
        "comfy",
        panorama.NAME,
        inputs=inputs or {},
        snapshot={
            "knowledge": request["knowledge"],
            "stage": {"name": stage, "unit": unit},
            "intermediate": True,
        },
        graph=graph,
        seed=seed,
        depends_on=depends_on,
    )


def _local(
    step, request, stage, unit, spec, inputs, depends_on=None, intermediate=True, expect=None
) -> JobSpec:
    snapshot = {
        "stage": {"name": stage, "unit": unit},
        "step_spec": spec,
        "intermediate": intermediate,
    }
    if expect:
        snapshot["expect"] = expect
    return JobSpec(
        "image",
        "local",
        panorama.NAME,
        inputs=inputs,
        snapshot=snapshot,
        step=step,
        depends_on=depends_on,
    )


def _band_spec(render, mode, **extra) -> dict:
    return {"mode": mode, "seam": render["seam"], **extra}


def _seam_fill(request, render, prefix, unit, seed, depends_on) -> JobSpec:
    remote = f"{prefix}/band-{unit}.png"
    return _comfy(
        "image",
        fibo_edit_graph(
            seam_prompt(), image_file=remote, seed=seed, prefix=f"{prefix}/f{unit}", render=render
        ),
        request,
        "fill",
        unit,
        seed=seed,
        inputs={"band": {"source": "job_output", "output_key": "band", "remote_name": remote}},
        depends_on=depends_on,
    )


def _first_jobs(request, render, prefix, picture) -> list[JobSpec]:
    if request["mode"] == "picture":
        remote = f"{prefix}/plate.png"
        return [
            _local(
                "panorama_plate",
                request,
                "plate",
                0,
                {"picture": picture["id"], "threshold": render["plate"]["letterbox_threshold"]},
                {picture["id"]: {"sha256": picture["sha256"]}},
            ),
            _comfy(
                "analysis",
                plate_geometry_graph(remote, prefix=prefix, render=render),
                request,
                "measure",
                0,
                inputs={
                    "plate": {"source": "job_output", "output_key": "plate", "remote_name": remote}
                },
                depends_on=0,
            ),
        ]
    jobs = []
    for unit, seed in enumerate(request["seeds"]):
        jobs.append(
            _comfy(
                "image",
                text_to_panorama_graph(
                    request["prompt"], seed=seed, prefix=f"{prefix}/d{unit}", render=render
                ),
                request,
                "draw",
                unit,
                seed=seed,
            )
        )
    for unit in range(CANDIDATES):
        jobs.append(
            _local(
                "panorama_band",
                request,
                "band",
                unit,
                _band_spec(render, "description"),
                {"raw": {"source": "job_output"}},
                depends_on=unit,
            )
        )
    for unit, seed in enumerate(request["seeds"]):
        jobs.append(_seam_fill(request, render, prefix, unit, seed, depends_on=CANDIDATES + unit))
    return jobs


def _asset(job: dict, key: str) -> dict:
    found = [o for o in job["outputs"] if o.get("output_key") == key and o.get("asset_id")]
    if len(found) != 1:
        raise ApplicationError(f"Missing unique panorama image: {key}", 409)
    return found[0]


def _grow(operation, render) -> list[JobSpec]:
    """After MoGe measured the picture: the canvas, two outpaints, the picture put back with the band, two fills."""
    request, prefix = operation["request"], operation["request"]["prefix"]
    current = staged.current_jobs(operation["jobs"], STAGES)
    plate_job, measure = current[("plate", 0)], current[("measure", 0)]
    picture = _asset(plate_job, "plate")
    geometry = staged.frozen(staged.output(measure, "save:geometry:0", "panorama"))
    base = len(operation["jobs"])
    canvas_remote = f"{prefix}/canvas.png"
    jobs = [
        _local(
            "panorama_canvas",
            request,
            "canvas",
            0,
            {
                "plate": picture["asset_id"],
                "hfov": request["ask"]["hfov"],
                "width": render["krea"]["width"],
                "height": render["krea"]["height"],
            },
            {
                picture["asset_id"]: {"sha256": picture["sha256"]},
                "geometry": geometry,
            },
        )
    ]
    for unit, seed in enumerate(request["seeds"]):
        jobs.append(
            _comfy(
                "image",
                fibo_edit_graph(
                    outpaint_prompt(),
                    image_file=canvas_remote,
                    seed=seed,
                    prefix=f"{prefix}/o{unit}",
                    render=render,
                ),
                request,
                "outpaint",
                unit,
                seed=seed,
                inputs={
                    "canvas": {
                        "source": "job_output",
                        "output_key": "canvas",
                        "remote_name": canvas_remote,
                    }
                },
                depends_on=base,
            )
        )
    for unit in range(CANDIDATES):
        jobs.append(
            _local(
                "panorama_band",
                request,
                "band",
                unit,
                _band_spec(
                    render,
                    "picture",
                    plate=picture["asset_id"],
                    hfov=request["ask"]["hfov"],
                    width=render["krea"]["width"],
                    height=render["krea"]["height"],
                    erode=render["plate"]["erode"],
                    feather=render["plate"]["feather"],
                ),
                {
                    picture["asset_id"]: {"sha256": picture["sha256"]},
                    "geometry": geometry,
                    "fill": {"source": "job_output"},
                },
                depends_on=base + 1 + unit,
            )
        )
    for unit, seed in enumerate(request["seeds"]):
        jobs.append(
            _seam_fill(request, render, prefix, unit, seed, depends_on=base + 1 + CANDIDATES + unit)
        )
    return jobs


def _seams(operation, render) -> list[JobSpec]:
    """The last stage: each unit whose band was filled gets its seam closed."""
    request = operation["request"]
    current = staged.current_jobs(operation["jobs"], STAGES)
    jobs = []
    for unit in range(CANDIDATES):
        fill = current.get(("fill", unit))
        if fill is None or fill["state"] != "succeeded":
            continue
        source = (
            current[("draw", unit)] if request["mode"] == "description" else current[("band", unit)]
        )
        raw = _asset(source, "save:images:0" if request["mode"] == "description" else "raw")
        filled = _asset(fill, "save:images:0")
        jobs.append(
            _local(
                "panorama_seam",
                request,
                "seam",
                unit,
                {"raw": raw["asset_id"], "fill": filled["asset_id"], "seam": render["seam"]},
                {
                    raw["asset_id"]: {"sha256": raw["sha256"]},
                    filled["asset_id"]: {"sha256": filled["sha256"]},
                },
                intermediate=False,
                expect={
                    "kind": "image",
                    "width": render["krea"]["width"],
                    "height": render["krea"]["height"],
                },
            )
        )
    return jobs


def advance_panorama(session, operation, *, library):
    current = staged.current_jobs(operation["jobs"], STAGES)
    names = {name for name, _ in current}
    render = _render(operation["request"])

    def close(stage, **fields):
        return operations.resume(
            session, operation["id"], {"decided_by": NAME, "stage": stage, **fields}, jobs=[]
        )

    if "seam" in names:
        return close("done")
    if operation["request"]["mode"] == "picture" and "canvas" not in names:
        if any(job["state"] in STOPPED for job in current.values()):
            return close("stopped")
        jobs = _grow(operation, render)
        return operations.resume(
            session,
            operation["id"],
            {"decided_by": NAME, "stage": "grow"},
            jobs=jobs,
            awaits_review=True,
        )
    jobs = _seams(operation, render)
    if not jobs:
        return close("stopped")
    return operations.resume(
        session,
        operation["id"],
        {"decided_by": NAME, "stage": "seam"},
        jobs=jobs,
        awaits_review=True,
    )


# ---------------------------------------------------------------- reading and the review


def _operation(session, operation_id) -> dict:
    operation = operations.operation(session, operation_id)
    if operation["kind"] != NAME:
        raise ApplicationError("This operation is not a panorama.", 404)
    return operation


def _report(session, library, job, key) -> dict | None:
    found = [o for o in job["outputs"] if o.get("output_key") == key and o.get("artifact_id")]
    return json.loads(library.read_blob(session, found[0]["artifact_id"])) if found else None


def _members(session, library, operation) -> list[dict]:
    """The candidates in seed order, lettered, with their seam and plate checks."""
    by_job = {job["id"]: job for job in operation["jobs"]}
    current = staged.current_jobs(operation["jobs"], STAGES)
    members = []
    for candidate in operation["candidates"]:
        if candidate["output_key"] != "panorama":
            continue
        job = by_job[candidate["job_id"]]
        unit = staged.stage(job)["unit"]
        band = current.get(("band", unit))
        members.append(
            {
                "letter": LETTERS[unit],
                "candidate_id": candidate["id"],
                "asset_id": candidate["asset_id"],
                "seam": _report(session, library, job, "seam"),
                "plate": _report(session, library, band, "plate_check") if band else None,
            }
        )
    return sorted(members, key=lambda m: m["letter"])


def chosen(session, operation) -> str | None:
    review = existing_review(session, operation["id"])
    choice = (review or {}).get("context", {}).get("chosen")
    return choice["candidate_id"] if choice else None


def open_panorama_review(session, operation_id) -> dict:
    operation = _operation(session, operation_id)
    if operation["state"] not in {"succeeded", "partial"}:
        raise ApplicationError("The panoramas must be ready before they can be reviewed.", 409)
    existing = existing_review(session, operation_id)
    if existing:
        return existing
    return reviews.create(
        session,
        purpose="panorama",
        candidate_ids=[c["id"] for c in operation["candidates"] if c["output_key"] == "panorama"],
        context={"place_id": operation["request"]["place_id"], "operation_id": operation_id},
        set_id=operation_id,
    )


def _superseded(session, operation) -> bool:
    """A newer finished attempt of the same request supersedes this one's review."""
    newer = [
        o
        for o in _siblings(session, operation)
        if o["state"] in {"succeeded", "partial"}
        and o["candidates"]
        and o["request"]["attempt"] > operation["request"]["attempt"]
    ]
    return bool(newer) or any(
        chosen(session, o) for o in _siblings(session, operation) if o["id"] != operation["id"]
    )


def panorama_review_pending(session, operation) -> bool:
    return not chosen(session, operation) and not _superseded(session, operation)


def builds(session, operation) -> list[dict]:
    """The stage builds started from this attempt's chosen panorama, newest first."""
    return [
        o
        for o in operations.recent(session, kind="set_build", limit=50)
        if o["request"].get("source") == "panorama"
        and o["request"].get("panorama_operation_id") == operation["id"]
    ]


def build_of(session, operation) -> dict | None:
    """The newest stage build from this attempt, in `set_seed.seed_state`'s words."""
    from gaiavfx.application.set_seed import seed_state

    found = builds(session, operation)
    return seed_state(session, found[0]) if found else None


def panorama_state(session, library, operation) -> dict:
    """An attempt in words: building (its stage), ready (two candidates to choose from), chosen, or stopped."""
    request = operation["request"]
    current = staged.current_jobs(operation["jobs"], STAGES)
    newest = max(current.values(), key=lambda job: job["ordinal"]) if current else None
    stage = staged.stage(newest)["name"] if newest else None
    members = _members(session, library, operation)
    picked = chosen(session, operation)
    for member in members:
        member["chosen"] = member["candidate_id"] == picked
    settled = operation["state"] in {
        "succeeded",
        "partial",
        "failed",
        "cancelled",
        "needs_attention",
    }
    if not settled:
        state, message = "building", WORDS.get(stage, "Starting") + "…"
    elif not members:
        state, message = (
            "stopped",
            "No panorama came out of this attempt. Make two more to try again.",
        )
    elif picked:
        state, message = (
            "chosen",
            f"Panorama {next(m['letter'] for m in members if m['chosen'])} is chosen.",
        )
    else:
        message = "Choose the panorama to build a stage from."
        if len(members) < CANDIDATES:
            message = "One panorama stopped; " + message[0].lower() + message[1:]
        state = "ready"
    return {
        "id": operation["id"],
        "mode": request["mode"],
        "place_id": request["place_id"],
        "place": request["place"],
        "prompt": request["prompt"],
        "attempt": request["attempt"],
        "state": state,
        "stage": stage,
        "message": message,
        "candidates": members,
        "chosen": picked,
        "build": build_of(session, operation) if picked else None,
        "created_at": operation["created_at"],
    }


def panoramas_for(session, library, place_id: str | None = None, limit: int = 12) -> list[dict]:
    scope = {"place_id": [place_id]} if place_id else None
    found = operations.recent(session, kind=NAME, limit=limit, scope=scope)
    return [panorama_state(session, library, operation) for operation in found]


def read_panorama(session, library, operation_id: str) -> dict:
    return panorama_state(session, library, _operation(session, operation_id))


def choose(session, library, candidate_id: str) -> dict:
    """The person's choice (called only by `review_approvals.choose_panorama`)."""
    candidate = operations.candidate(session, candidate_id)
    operation = _operation(session, candidate["operation_id"])
    if candidate["output_key"] != "panorama":
        raise ApplicationError("Only a finished panorama can be chosen.", 422)
    review = open_panorama_review(session, operation["id"])
    reviews.choose(session, review["id"], candidate_id, chosen_by="user")
    return panorama_state(session, library, _operation(session, operation["id"]))
