"""A set seeded from a take (ADR 0019 §4): one shot of a segment's current take becomes a new set whose v1 is
proposed.

The seed is a `set_build` operation in mode `seed`, staged by its advancer:
1. **geometry and atlas** (started together): a ComfyUI job samples the shot's frames, masks every person (SAM 3.1)
   and runs Depth Anything 3 multiview (`integrations/worldbuilder/graph.py`); the engine's CPU task
   `atlas_from_frames` judges that geometry (the reconstruction gate) and, if it holds, lays it out as pinhole
   keyframes in a levelled world with its map;
2. **splat** (added once the take has a layout): the engine fits the splat to those keyframes, with a repair's
   settings;
3. **fill** (when the request fills the stage's unseen space, ADR 0020): rounds of probes around the shot
   (`set_fill.py`), ending at a stop rule;
4. **done**: the writer creates the set with v1 proposed from the last checkpoint, its keyframe stills as image
   assets, and builds the place as the set when the user asked for that.
The layout (`frames.layout`): a take whose camera moves is laid out as it was seen; one that stands still or only
turns is laid out from about one point when the build fills its unseen space, and refused otherwise. A refused take
ends the operation at stage 1 with the measured reason, and nothing is created. So no set exists until its first
version does; until then the seed is the operation, which the segment's References row reads.

What the request freezes: the candidate and its asset sha256, the shot's frame interval (never across a cut), the
sampled frames, the settings themselves (the take's, the splat fit's and the filling's, or none when the build does
not fill), the set's name, and the place to build as it when asked. The operation is keyed on the candidate, the interval, the settings and the attempt.
- A refusal measures what the take shows, so it holds: the same shot of the same take under the same settings is
  refused again without running, until the take, the shot or the settings change.
- Only a seed that stopped (a job failed or was cancelled) is tried again: Retry is the next attempt of its frozen
  request, whatever the settings are now.
"""

from __future__ import annotations

import json
from hashlib import sha256

from gaiavfx.application import set_fill
from gaiavfx.application.engine_jobs import engine_job
from gaiavfx.application.selected_takes import current_take
from gaiavfx.application.set_bundles import register_new_set
from gaiavfx.application.sets import STORE, clip_words
from gaiavfx.integrations import worldbuilder
from gaiavfx.integrations.worldbuilder import rules
from gaiavfx.integrations.worldbuilder.fill import settings as fill_settings
from gaiavfx.integrations.worldbuilder.graph import sampling, settings, take_geometry_graph
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.generation.operations.lifecycle import OPERATION_TERMINAL, JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError

MODE = "seed"
BUILD_VERSION = 1
ATLAS = "worldbuilder.atlas_from_frames"
SPLAT = "worldbuilder.splat_init_atlas"
GEOMETRY_OUTPUT = "save:geometry:0"
STOPPED = frozenset({"failed", "cancelled", "needs_attention"})
direction = DirectionService()  # plain reads, and building a place as the new set
operations = OperationService()

# The gate's reasons for the person who chose the take, and the few words the References row shows.
SHORT_REFUSALS = {
    "little_surface": "Refused: too little still surface",
    "inconsistent": "Refused: its frames do not join into one place",
    "no_parallax_turns": "Refused: the camera only turns",
    "no_parallax_still": "Refused: the camera stands still",
}
REFUSALS = {
    "little_surface": (
        "Too little of this take is still surface outside people to build a stage from."
    ),
    "inconsistent": (
        "The frames of this shot do not join into one place: it may hold a cut, a jump, or geometry that "
        "contradicts itself."
    ),
    "no_parallax_turns": (
        "The camera only turns in this take: without filling its unseen space, building a stage needs the camera "
        "to move through the place."
    ),
    "no_parallax_still": (
        "The camera stands still in this take: without filling its unseen space, building a stage needs the camera "
        "to move through the place."
    ),
}


def _frozen(fill_unseen: bool) -> dict:
    """The settings a new seed freezes in its request: the take's (`knowledge/take.toml`), the splat fit's (a
    repair's) and, when it fills the stage's unseen space, the filling's with its probe's compile (`set_fill.frozen`)."""
    return {
        "take": settings(),
        "splat": rules.rebuild(),
        "fill": set_fill.frozen() if fill_unseen else None,
    }


def _fingerprint(frozen: dict) -> str:
    return sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest()[:16]


def shots(segment: dict) -> list[dict]:
    """The segment's shots in its take's frames: the first shot, then one per cut, each up to the next cut."""
    cuts = sorted(segment["cuts"], key=lambda c: c["frame"])
    starts = [0, *(c["frame"] for c in cuts)]
    ends = [*starts[1:], segment["delivered_frames"]]
    return [
        {
            "index": index,
            "label": "First shot" if index == 0 else f"Cut {index} (f{start})",
            "start": start,
            "end": end,
            "angle_id": segment["opening_angle_id"]
            if index == 0
            else cuts[index - 1]["to_angle_id"],
        }
        for index, (start, end) in enumerate(zip(starts, ends, strict=True))
    ]


def _key(candidate_id: str, start: int, end: int, fingerprint: str, attempt: int) -> str:
    return sha256(
        json.dumps([MODE, candidate_id, start, end, BUILD_VERSION, fingerprint, attempt]).encode()
    ).hexdigest()[:12]


def _attempt(
    session, candidate_id, start, end, fingerprint
) -> tuple[dict | None, int, dict | None]:
    """The seed of this interval and settings still under way, the first unused attempt number, and the newest
    finished seed."""
    attempt, last = 1, None
    while (
        found := operations.for_request(
            session, _key(candidate_id, start, end, fingerprint, attempt)
        )
    ) is not None:
        if found["state"] not in OPERATION_TERMINAL:
            return found, attempt, last
        last = found
        attempt += 1
    return None, attempt, last


def take_refusal(session, take: dict | None) -> str | None:
    """Why no set can be built from a segment's selected take (`current_take`), or None; a shot can still refuse."""
    if take is None:
        return "Select a take of this scene first."
    if operations.candidate(session, take["candidate_id"])["kind"] != "video":
        return "The selected take is not a video."
    if take.get("stale"):
        # The shots are read from the scene as it is now; a take made before it changed may not hold them.
        return (
            "The selected take was made before this scene changed, so its frames may not match these shots. "
            "Select a current take first."
        )
    return None


def _seeds(session, segment_id: str, candidate_id: str) -> list[dict]:
    """The seeds of one take of a segment, newest first."""
    return [
        o
        for o in operations.recent(
            session, kind="set_build", scope={"segment_id": [segment_id]}, limit=20
        )
        if o["request"].get("mode") == MODE and o["request"]["candidate_id"] == candidate_id
    ]


def _context(session, library, segment_id: str, shot: int, fill_unseen: bool) -> dict:
    """What a seed of `shot` would build from, the settings it would freeze, and `refusal`, the reason it cannot, if
    any."""
    segment = direction.read_segment(session, segment_id)
    movie_id = segment["movie_id"]
    found = shots(segment)
    if not 0 <= shot < len(found):
        raise ApplicationError("This segment has no such shot.", 422)
    chosen = found[shot]
    authored = ((segment.get("content") or {}).get("place") or {}).get("description") or ""
    context = {
        "movie_id": movie_id,
        # What the place is, in words: the place's own description, else what the scene says of it.
        "place_text": authored,
        "shots": found,
        "shot": chosen,
        "candidate": None,
        "asset": None,
        "place": None,
        "frozen": _frozen(fill_unseen),
    }
    if chosen["angle_id"]:
        angle = direction.read_angle(session, chosen["angle_id"])
        context["place"] = direction.read_place(session, angle["place_id"])
        context["place_text"] = context["place"].get("description") or authored
    take = current_take(session, movie_id, segment_id)
    refusal = take_refusal(session, take)
    if take is not None:
        candidate = operations.candidate(session, take["candidate_id"])
        context |= {"candidate": candidate, "asset": library.get(session, candidate["asset_id"])}
    if refusal is None and chosen["end"] - chosen["start"] < 2:
        refusal = "This shot is too short to build a stage from."
    if refusal is None:
        running, _, last = _attempt(
            session,
            context["candidate"]["id"],
            chosen["start"],
            chosen["end"],
            _fingerprint(context["frozen"]),
        )
        if running is None and last is not None and seed_state(session, last)["state"] == "refused":
            still = not fill_unseen and str(last["review"][-1].get("reason")).startswith(
                "no_parallax"
            )
            refusal = "Building this shot again would give the same answer. " + (
                "Fill in its unseen space, choose a shot whose camera moves through the place, or choose "
                "another take."
                if still
                else "Choose another shot or another take."
            )
    return context | {"refusal": refusal}


def times(start: int, end: int, fps: int = 24) -> str:
    """A shot's span as clock time, as the ruler reads it: 0:00–0:04."""

    def clock(frame):
        seconds = round(frame / fps)
        return f"{seconds // 60}:{seconds % 60:02d}"

    return f"{clock(start)}–{clock(end)}"


def _unique(session, name: str) -> str:
    """`name`, or `name 2`, `name 3`… when a set already has it: two sets must never read alike."""
    taken = {STORE.get(session, set_id)["name"] for set_id in STORE.ids(session)}
    if name not in taken:
        return name
    n = 2
    while f"{name} {n}" in taken:
        n += 1
    return f"{name} {n}"


def estimate(frames: int, fill_unseen: bool) -> str:
    """What starting a seed means for the user's machine, measured on the 4090: about 3 minutes without filling (27
    October); with filling, 14 minutes for a still take and 23 for a moving one, whose 12 keyframes take longer to
    plan and fit (the qualification, 28 October)."""
    steps = rules.rebuild()["steps"]
    fit = f"{frames} frames are reconstructed, then a {steps}-step splat is fitted"
    after = "It runs in Jobs while you keep working, and you can cancel it there."
    if not fill_unseen:
        return f"About 3 minutes of GPU work: {fit}. {after}"
    # The renders and the time lead: they are what filling costs (Astra's usability pass, 28 October).
    rounds = fill_settings()["rounds"]
    return (
        f"Up to {rounds['renders']} renders, about 15 to 25 minutes of GPU work: {fit}, then up to "
        f"{rounds['rounds']} rounds of probes fill in what the take never saw around this shot. {after}"
    )


def what(fill_unseen: bool) -> str:
    """What the build makes, with filling on or off, for the dialog."""
    if fill_unseen:
        return (
            "Fills in what the take never saw around this shot, so angles near it hold together; Fill more unseen "
            "space in the stage workbench reaches further. A shot whose camera stands still or only turns builds "
            "this way too."
        )
    return (
        "Builds only what the take saw. The camera has to move through the place: a shot whose camera stands "
        "still or only turns is refused."
    )


def seed_preview(
    session, library, *, segment_id: str, shot: int = 0, fill_unseen: bool = True
) -> dict:
    """What Build a set from this take… will do, before it does: the shots, the frames it samples, whether and how
    far it fills the stage's unseen space, the place it can build as the set, and the newest seed of this shot."""
    found = _context(session, library, segment_id, shot, fill_unseen)
    chosen, place, candidate = found["shot"], found["place"], found["candidate"]
    frames = min(settings()["frames"], max(chosen["end"] - chosen["start"], 0))
    preview = {
        "shots": [
            {
                **{k: s[k] for k in ("index", "label", "start", "end")},
                "times": times(s["start"], s["end"]),
            }
            for s in found["shots"]
        ],
        "shot": chosen["index"],
        "frames": frames,
        "fill_unseen": fill_unseen,
        "renders": fill_renders() if fill_unseen else 0,
        "estimate": estimate(frames, fill_unseen),
        "what": what(fill_unseen),
        "name": _unique(
            session,
            clip_words(f"{place['name']} from a take" if place else "Stage from a take", 120),
        ),
        "candidate_id": candidate["id"] if candidate else None,
        "place": None
        if place is None
        else {"id": place["id"], "name": place["name"], "set_id": place.get("set_id")},
        # A place already built as a set keeps it: a seed never takes a set away from other scenes.
        "can_use_for_place": place is not None and not place.get("set_id"),
        "refusal": found["refusal"],
        "seed": None,
    }
    if candidate:
        latest = next(
            (
                o
                for o in _seeds(session, segment_id, candidate["id"])
                if o["request"]["shot"] == chosen["index"]
            ),
            None,
        )
        seed = preview["seed"] = None if latest is None else seed_state(session, latest)
        if (
            seed
            and seed["state"] == "refused"
            and fill_unseen
            and not preview["refusal"]
            and not latest["request"].get("fill")
        ):
            # The refusal in view is the last build's, which did not fill; filling, chosen now, builds the shot.
            seed["message"] += " With filling on, this shot can be built now."
    return preview


def start_seed(
    session,
    library,
    *,
    segment_id,
    shot: int,
    name: str,
    use_for_place: bool,
    fill_unseen: bool = True,
) -> dict:
    """Human-only (ADR 0018 §5), or the agent inside an approved plan: seed a set from one shot of the segment's
    current take, filling its unseen space unless asked not to. Returns the operation; a seed of the same interval
    and settings under way is returned as it is."""
    found = _context(session, library, segment_id, shot, fill_unseen)
    if found["refusal"]:
        raise ApplicationError(found["refusal"], 409)
    place = found["place"]
    if use_for_place and (place is None or place.get("set_id")):
        raise ApplicationError(
            "This shot's place is already built as a stage; use Use a stage for this scene… to change it."
            if place
            else "This shot has no place to build as the stage.",
            409,
        )
    chosen = found["shot"]
    return _begin(
        session,
        movie_id=found["movie_id"],
        segment_id=segment_id,
        shot=chosen["index"],
        start=chosen["start"],
        end=chosen["end"],
        candidate=found["candidate"],
        asset=found["asset"],
        name=name,
        place=place if use_for_place else None,
        place_text=found["place_text"],
        frozen=found["frozen"],
    )


def retry_seed(session, library, operation_id: str) -> dict:
    """Retry a seed that stopped: its frozen request (candidate, interval, settings, name, place) as the next
    attempt. A refused seed is not tried again (the module docstring), and a done one is done."""
    previous = operations.operation(session, operation_id)
    request = previous["request"]
    if previous["kind"] != "set_build" or request.get("mode") != MODE:
        raise ApplicationError("This operation is not a stage seed.", 404)
    if previous["state"] not in OPERATION_TERMINAL:
        raise ApplicationError("This seed is still running.", 409)
    if not seed_state(session, previous)["can_retry"]:
        raise ApplicationError(
            "Only a build that stopped is tried again: a refused shot gives the same answer, and a built stage "
            "is done.",
            409,
        )
    if "take" not in request:
        raise ApplicationError(
            "This build was started before builds kept their settings; build the stage again from the take.",
            409,
        )
    asset = library.get(session, request["asset_id"])
    if asset["sha256"] != request["asset_sha256"]:
        raise ApplicationError(
            "The take changed since this seed; build a new one from the current take.", 409
        )
    place = direction.read_place(session, request["place_id"]) if request.get("place_id") else None
    return _begin(
        session,
        movie_id=request["movie_id"],
        segment_id=request["segment_id"],
        shot=request["shot"],
        start=request["start"],
        end=request["end"],
        candidate=operations.candidate(session, request["candidate_id"]),
        asset=asset,
        name=request["name"],
        place=place if place and not place.get("set_id") else None,
        place_text=request["place_text"],
        # A seed recorded before filling existed did not fill.
        frozen={"take": request["take"], "splat": request["splat"], "fill": request.get("fill")},
    )


def _begin(
    session,
    *,
    movie_id,
    segment_id,
    shot,
    start,
    end,
    candidate,
    asset,
    name,
    place,
    place_text,
    frozen,
):
    from gaiavfx.application.operations import PRODUCERS, create_operation, knowledge_snapshot

    name = clip_words(name, 120)
    if not name:
        raise ApplicationError("Name the stage.", 422)
    fingerprint = _fingerprint(frozen)
    existing, attempt, _ = _attempt(session, candidate["id"], start, end, fingerprint)
    if existing is not None:
        return existing | {"_created": False}
    take, fill = frozen["take"], frozen.get("fill")
    key = _key(candidate["id"], start, end, fingerprint, attempt)
    prefix = f"gaiavfx/sets/seed-{key}"
    remote = f"{prefix}/take.mp4"
    first, length, stride = sampling(start, end, take["frames"])
    return create_operation(
        session,
        operations,
        PRODUCERS,
        kind="set_build",
        request={
            "mode": MODE,
            "movie_id": movie_id,
            "segment_id": segment_id,
            "candidate_id": candidate["id"],
            "asset_id": asset["id"],
            "asset_sha256": asset["sha256"],
            "shot": shot,
            "start": start,
            "end": end,
            "sampling": {"first": first, "length": length, "stride": stride},
            "take": take,
            "splat": frozen["splat"],
            "fill": fill,
            "settings": fingerprint,
            "name": name,
            "place_id": place["id"] if place else None,
            "place_text": place_text,
            "attempt": attempt,
        },
        request_key=key,
        budget_limit=None,
        # The probes' renders, reserved when the build starts (a later stage cannot add to its reservation).
        budget=fill["rounds"]["renders"] if fill else 0,
        awaits_review=True,
        jobs=[
            JobSpec(
                "analysis",
                "comfy",
                worldbuilder.NAME,
                inputs={asset["id"]: {"sha256": asset["sha256"], "remote_name": remote}},
                graph=take_geometry_graph(remote, start=start, end=end, take=take, prefix=prefix),
                snapshot={
                    "knowledge": knowledge_snapshot(worldbuilder),
                    "stage": {"name": "geometry"},
                },
            ),
            engine_job(
                ATLAS,
                params={"name": "seed", "place": name, "gate": take["gate"], "fill": bool(fill)},
                inputs={"geometry": {"source": "job_artifact", "output_key": GEOMETRY_OUTPUT}},
                kind="analysis",
                depends_on=0,
                stage={"name": "atlas"},
            ),
        ],
    )


def _output(job, key) -> dict:
    matches = [o for o in job["outputs"] if o.get("output_key") == key and o.get("artifact_id")]
    if len(matches) != 1:
        raise ApplicationError(f"The seed lacks its {key} artifact.", 409)
    return matches[0]


def _summary(session, library, job) -> dict:
    return json.loads(library.read_blob(session, _output(job, "summary")["artifact_id"]))


def _decision(stage: str, **fields) -> dict:
    return {"decided_by": "set_seed", "stage": stage, **fields}


def _reason(gate: dict) -> str:
    reason = gate.get("reason")
    if reason == "no_parallax":
        return "no_parallax_turns" if (gate.get("turn") or 0) >= 10 else "no_parallax_still"
    return reason


def fill_renders() -> int:
    """The probe renders a build that fills its stage's unseen space reserves, at most (ADR 0020 §7)."""
    return int(fill_settings()["rounds"]["renders"])


def refusal(gate: dict) -> str:
    """The gate's verdict in words, naming what was measured."""
    return REFUSALS.get(_reason(gate), "Could not reconstruct reliable geometry from this take.")


def _stage(job) -> str | None:
    return ((job.get("snapshot") or {}).get("stage") or {}).get("name")


def advance_seed(session, operation, *, library):
    """The seed's stages (the module docstring): after geometry and atlas, refuse or add the splat; after the
    splat, write the set or begin filling; while filling, the next stage (`set_fill.advance`) until a stop rule, then
    write the set from the checkpoint. A seed stage that stopped closes the seed with no set; a fill stage that
    stopped is `set_fill`'s to judge."""
    jobs = operation["jobs"]
    name, _, group = set_fill.latest(operation)
    if name in ("atlas", "splat"):
        current = jobs[:2] if name == "atlas" else [job for _, job in group]
        if any(job["state"] != "succeeded" for job in current):
            if any(job["state"] not in STOPPED for job in current if job["state"] != "succeeded"):
                raise ApplicationError("The seed cannot advance an incomplete stage.", 409)
            return operations.resume(session, operation["id"], _decision("stopped"), jobs=[])
    if name == "atlas":
        summary = _summary(session, library, jobs[1])
        gate = summary["gate"]
        # An atlas laid out before filling existed has no layout: it passed or it was refused.
        layout = summary.get("layout", "moving" if gate["passed"] else None)
        if not layout:
            decision = _decision(
                "refused",
                reason=gate["reason"],
                message=refusal(gate),
                label=SHORT_REFUSALS.get(_reason(gate), "Refused"),
                gate=gate,
            )
            return operations.resume(session, operation["id"], decision, jobs=[])
        splat = engine_job(
            SPLAT,
            params={"name": "seed-splat", "atlas_state": "seed", **operation["request"]["splat"]},
            kind="analysis",
            depends_on=1,
            stage={"name": "splat"},
        )
        return operations.resume(
            session,
            operation["id"],
            _decision("splat", gate=gate, layout=layout),
            jobs=[splat],
            awaits_review=True,
        )
    filled = None
    if operation["request"].get("fill"):
        try:
            step = set_fill.advance(session, library, operation)
        except ValueError as exc:
            raise ApplicationError(str(exc), 409) from exc
        if isinstance(step, set_fill.Next):
            return operations.resume(
                session, operation["id"], step.decision, jobs=step.jobs, awaits_review=True
            )
        filled = step
    elif name != "splat":
        raise ApplicationError("Unexpected seed stage.", 409)
    set_id, version_id = _write_set(session, library, operation, filled)
    decision = _decision(
        "done",
        set_id=set_id,
        version_id=version_id,
        **({"filled": filled.words, "ended": filled.reason} if filled else {}),
    )
    return operations.resume(session, operation["id"], decision, jobs=[])


def _write_set(session, library, operation, filled: set_fill.Done | None = None) -> tuple[str, str]:
    """The new set with v1 proposed, from the checkpoint's atlas and splat (the seed's own, or the last round's whose
    extension finished); the place built as it when the request asked and the place is still not built as a set."""
    request = operation["request"]
    rounds, atlas_job, splat_job = set_fill.checkpoint(operation)
    seeded = _summary(session, library, operation["jobs"][1])
    summary = _summary(session, library, atlas_job)
    fit = _summary(session, library, splat_job)
    stills = [
        library.prepare(
            library.artifact_path(
                session, _output(atlas_job, f"still:{entry['key']}")["artifact_id"]
            ),
            f"{request['name']} {entry['label']}.png",
        )
        for entry in summary["keyframes"]
    ]
    set_id, version_id = register_new_set(
        session,
        library,
        name=request["name"],
        place_text=request["place_text"],
        provenance={
            "source": "take",
            "operation_id": operation["id"],
            "candidate_id": request["candidate_id"],
            "asset_sha256": request["asset_sha256"],
            "segment_id": request["segment_id"],
            "frames": [request["start"], request["end"]],
            "sampling": request["sampling"],
            "settings": request["settings"],
            "gate": seeded["gate"],
            "layout": seeded.get("layout", "moving"),
            "levelling": seeded.get("levelling"),
            "fill": None
            if filled is None
            else {
                "rounds": rounds,
                "renders": set_fill.renders_used(operation),
                "ended": filled.reason,
            },
        },
        origin="seed",
        still_note={"type": "set_seed", "operation_id": operation["id"]},
        keyframes=summary["keyframes"],
        stills=stills,
        depth_ids=[
            _output(atlas_job, f"data:{entry['key']}")["artifact_id"]
            for entry in summary["keyframes"]
        ],
        atlas_id=_output(atlas_job, "atlas")["artifact_id"],
        map_id=_output(atlas_job, "map")["artifact_id"],
        splat_id=_output(splat_job, "splat")["artifact_id"],
        map_meta=summary["map_meta"],
        metrics={
            "uncalibrated": True,
            "heldout_psnr": fit.get("heldout_psnr_refined") or fit.get("heldout_psnr"),
            "anchor_psnr": fit.get("anchor_psnr"),
            "fill_psnr": fit.get("fill_psnr"),
            "gaussians": fit.get("gaussians"),
        },
        note=f"Built from a take: f{request['start']}–f{request['end']}"
        + (f"; it {filled.words}." if filled else "."),
    )
    if request.get("place_id"):
        place = direction.read_place(session, request["place_id"])
        if not place.get("set_id"):
            direction.set_place_set(session, place["id"], place["revision"], set_id=set_id)
    return set_id, version_id


def segment_seed(session, segment: dict) -> tuple[dict | None, str | None]:
    """For the segment's References row and its Build command: the newest seed of its current take, in words (while
    it builds, when it was refused or stopped, and once done while its v1 still waits for the human), and why no set
    can be built from that take, if so."""
    take = current_take(session, segment["movie_id"], segment["id"])
    refusal = take_refusal(session, take)
    found = next(iter(_seeds(session, segment["id"], take["candidate_id"])), None) if take else None
    if found is None:
        return None, refusal
    state = seed_state(session, found)
    if state["state"] == "done" and not state["waiting"]:
        return None, refusal  # decided: the set and the place say the rest
    return state, refusal


DECIDED = {"accepted": "v1 accepted", "discarded": "v1 discarded"}
# Why a stage build stopped, for the user, when the cause is one they can act on (as the voice build's sentences).
STOP_WORDS = {
    "comfyui": "ComfyUI did not answer. Build again once it runs.",
    "engines": "the stage engines are not set up. Build again once they are.",
    "memory": "the GPU ran out of memory. Close other GPU work, then build again.",
}


def stop_cause(operation) -> str | None:
    """Why a stage build stopped, when it is a cause someone can act on: the user stopped it, ComfyUI did not answer,
    the stage engines are not set up, the GPU ran out of memory. None for anything else, which Jobs details. The live
    trial's agent, told only that its build stopped, offered to build from the other scene against the same dead
    ComfyUI."""
    if operation["state"] == "cancelled" or operation.get("cancel_requested"):
        return "stopped"
    errors = " ".join(
        str(j.get("error") or "") for j in operation.get("jobs") or [] if j["state"] in STOPPED
    )
    errors = f"{errors} {operation.get('error') or ''}".lower()
    if any(
        text in errors
        for text in (
            "[winerror 10061]",
            "[winerror 10054]",
            "connection refused",
            "connection reset",
        )
    ):
        return "comfyui"
    if any(
        text in errors
        for text in ("not installed", "engines root", "engine worker", "no command for engine")
    ):
        return "engines"
    return "memory" if "out of memory" in errors else None


def seed_state(session, operation) -> dict:
    """A seed in words: `message`, a sentence for the dialog, and `label`, a few words for the References row,
    naming the set it builds and the shot it builds from. Building (and its stage), refused (the gate's reason),
    failed (`can_retry`), or done (the new set, and what the human made of its v1; `waiting` while they have not
    decided)."""
    review = [d for d in (operation.get("review") or []) if d.get("decided_by") == "set_seed"]
    last = review[-1] if review else {}
    stage = last.get("stage")
    request = operation["request"]
    name = request["name"]
    base = {
        "operation_id": operation["id"],
        "set_id": None,
        "name": name,
        "shot": request["shot"],
        "waiting": False,
        "can_retry": False,
    }
    if stage == "done":
        first = STORE.get(session, last["set_id"])["versions"][0]
        waiting = first["state"] == "proposed"
        label = "v1 to review" if waiting else DECIDED[first["state"]]
        then = "its v1 waits for you in the stage workbench." if waiting else f"{label}."
        return base | {
            "state": "done",
            "label": label,
            "message": f"{name} is built and {last['filled']}; {then}"
            if last.get("filled")
            else f"{name} is built: {then}",
            "set_id": last["set_id"],
            "waiting": waiting,
        }
    if stage == "refused":
        return base | {
            "state": "refused",
            "label": last.get("label", "Refused"),
            "message": last["message"],
        }
    if stage == "stopped" or operation["state"] in STOPPED:
        cause = stop_cause(operation)
        return base | {
            "state": "failed",
            "label": "Stopped before it finished",
            "message": f"Building {name} was stopped before it finished."
            if cause == "stopped"
            else f"Building {name} stopped before it finished: {STOP_WORDS[cause]}"
            if cause
            else f"Building {name} stopped before it finished; Jobs says why.",
            "can_retry": True,
        }
    label = (
        set_fill.progress(operation)
        if stage == "fill"
        else "Fitting the splat…"
        if stage == "splat"
        else "Building from this take…"
    )
    return base | {
        "state": "building",
        "label": label,
        "message": f"{name}: {label[0].lower()}{label[1:]}",
    }
