"""Filling a stage's unseen space (ADR 0020 §4–5): the rounds a seed runs after its first splat, as one stage chain.

Two builds fill: a seed (`set_seed`), after its first splat, and a repair that runs queued extensions
(`set_build`), after it laid out and fitted the version it builds on. Each names its first stages' scratch states in
`request["fill_base"]` (a seed's are `seed` and `seed-splat`).

A round is seven stages, each added by the advancer when the one before it finished:
1. **explore** (engine, CPU): plan the round's probes on the current atlas and write their memory videos;
2. **paths** (engine, GPU): the current splat's depth along each probe's move;
3. **probe** (ComfyUI, one job a probe): the H3 memory probe (`minimax_h3.probe`), hidden material, never a take;
4. **stills** (engine, CPU): each rendered probe's last frame, placed and its memory restored;
5. **still_geometry** (ComfyUI, one job a still): MoGe-2 and SAM 3's glass;
6. **consolidate** (engine, CPU): the stills into the next atlas, R5's acceptance;
7. **extend** (engine, GPU): the splat grows from the accepted stills (only when a still was accepted).

Every stage reads what the stages before it wrote by identity: artifacts and assets by id and sha256, the engine's
scratch states by name (each identified by what it was made from). What the rounds follow is frozen in the request
when the build is authorized (`frozen`): the filling's settings and the H3 probe's compile, so a build in flight
never picks up a change to either.

The **checkpoint** is the atlas and splat of the last round whose extension finished, or the seed's own: the version
is written from it, never from a newer atlas with an older splat. The **stop rules** (`knowledge/fill.toml`) end the
rounds: the round cap, the render cap, a gain below the floor, a closed place, rounds without an accepted still, or
no probe left to plan. A probe whose render or geometry failed is a rejected probe; any other stage that fails ends
the filling, and the version is written from the checkpoint with words saying how far the filling got.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from hashlib import sha256

from gaiavfx.application.engine_jobs import engine_job
from gaiavfx.integrations import minimax_h3, worldbuilder
from gaiavfx.integrations.minimax_h3 import probe as h3_probe
from gaiavfx.integrations.minimax_h3.probe import build_probe_graph, compile_probe
from gaiavfx.integrations.worldbuilder.fill import settings, still_geometry_graph
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.sets.probe import ProbeRequest

EXPLORE = "worldbuilder.fill_explore"
PATHS = "worldbuilder.fill_paths"
STILLS = "worldbuilder.fill_stills"
CONSOLIDATE = "worldbuilder.fill_consolidate"
EXTEND = "worldbuilder.splat_extend"
GEOMETRY_OUTPUT = "save:geometry:0"
FPS = 24

ENDED = {
    "rounds": "filled its unseen space in {rounds}",
    "renders": "filled until its {renders} renders were spent",
    "gain": "stopped filling after round {round}: the stage grew too little to go on",
    "closure": "stopped filling after round {round}: the place is closed",
    "acceptance": "stopped filling after round {round}: no probe's still fitted the stage for {without} rounds",
    "no_probe": (
        "stopped filling in round {round}: no probe was left that could see something new and keep enough of the "
        "stage in view"
    ),
    "failed": "stopped filling in round {round}: its {stage} failed, so it holds what the rounds before found",
}
STAGE_WORDS = {
    "explore": "planning",
    "paths": "depth render",
    "stills": "placing of the stills",
    "consolidate": "consolidation",
    "extend": "splat extension",
}


@dataclass
class Next:
    """Add `jobs` with the decision `decision`."""

    decision: dict
    jobs: list = field(default_factory=list)


@dataclass
class Done:
    """The filling ended: `reason` (a key of ENDED) and the words for it."""

    reason: str
    words: str
    rounds: int  # rounds whose extension finished


def frozen() -> dict:
    """What a build that fills freezes in `request["fill"]`: the filling's settings (`knowledge/fill.toml`) and the
    probe's compile (`minimax_h3/knowledge/probe.toml`, its sampling and wording)."""
    return settings() | {"probe": asdict(h3_probe.probe_knowledge())}


def _probe_knowledge(fill: dict) -> h3_probe.ProbeKnowledge | None:
    # A build frozen before the probe's compile was frozen with it compiles with the current one.
    return h3_probe.ProbeKnowledge(**fill["probe"]) if fill.get("probe") else None


def stage(job) -> dict:
    return (job.get("snapshot") or {}).get("stage") or {}


def rounds_held(atlas: dict) -> int:
    """The highest fill round whose stills an atlas holds (keyframes `r<n>p<k>`, kept or not), 0 for none."""
    found = (re.match(r"r(\d+)p\d", k["id"]) for k in atlas["keyframes"])
    return max((int(m[1]) for m in found if m), default=0)


def judged_still(operation, key: str) -> dict | None:
    """The still geometry (MoGe-2 and SAM 3's glass) `operation` measured for its probe still `key` (`r<n>p<k>`),
    pinned as an input, or None when it measured none: a kept still restores its trusted pixels from it (ADR 0020
    §8)."""
    found = re.fullmatch(r"r(\d+)p(\d+)", key)
    if not found:
        return None
    rnd = int(found[1]) - ((operation["request"].get("fill_base") or {}).get("rounds") or 0)
    for job in operation["jobs"]:
        named = stage(job)
        if (named.get("name"), named.get("round"), named.get("probe")) == (
            "still_geometry",
            rnd,
            int(found[2]),
        ):
            output = _artifact(job, GEOMETRY_OUTPUT)
            return _pinned(output) if output else None
    return None


def _name(operation, rnd: int) -> str:
    """Round `rnd`'s name, which its scratch states and its stills' keyframes (`r<n>p<k>`) carry. A repair's rounds
    go on from the rounds its base version holds, so a still never takes a name the stage has used (the GPU smoke,
    28 October: Fill more on a filled stage named its first still r1p0 again)."""
    return f"r{(operation['request'].get('fill_base') or {}).get('rounds', 0) + rnd}"


def _decision(name: str, **fields) -> dict:
    return {"decided_by": "set_seed", "stage": name, **fields}


def latest(operation) -> tuple[str | None, int | None, list[tuple[int, dict]]]:
    """The newest stage: its name, its round and its jobs with their indexes."""
    jobs = operation["jobs"]
    last = stage(jobs[-1])
    group = []
    for index in range(len(jobs) - 1, -1, -1):
        here = stage(jobs[index])
        if (here.get("name"), here.get("round")) != (last.get("name"), last.get("round")):
            break
        group.append((index, jobs[index]))
    return last.get("name"), last.get("round"), group[::-1]


def _find(operation, name: str, rnd: int | None = None) -> list[tuple[int, dict]]:
    return [
        (index, job)
        for index, job in enumerate(operation["jobs"])
        if stage(job).get("name") == name and (rnd is None or stage(job).get("round") == rnd)
    ]


def _artifact(job, key) -> dict | None:
    found = [o for o in job["outputs"] if o.get("output_key") == key and o.get("artifact_id")]
    return found[0] if len(found) == 1 else None


def _asset(job) -> dict | None:
    found = [
        o for o in job["outputs"] if o.get("asset_id") and o.get("kind", job["kind"]) == "video"
    ]
    return found[0] if len(found) == 1 else None


def _summary(session, library, job) -> dict:
    return json.loads(library.read_blob(session, _artifact(job, "summary")["artifact_id"]))


def _pinned(output: dict, remote: str | None = None) -> dict:
    """An earlier stage's artifact as an input, by id and sha256; uploaded to ComfyUI's input folder as `remote`."""
    pinned = {
        "source": "artifact",
        "artifact_id": output["artifact_id"],
        "sha256": output["sha256"],
        "directory": "input",
    }
    return pinned | ({"remote_name": remote} if remote else {})


def checkpoint(operation) -> tuple[int, dict, dict]:
    """The rounds the stage holds, and the atlas and splat jobs to write the version from: the last round whose
    extension succeeded, or the seed's atlas and splat."""
    extended = [
        stage(job)["round"] for _, job in _find(operation, "extend") if job["state"] == "succeeded"
    ]
    if extended:
        rnd = max(extended)
        atlas = next(job for _, job in _find(operation, "consolidate", rnd))
        splat = next(job for _, job in _find(operation, "extend", rnd))
        return rnd, atlas, splat
    return (
        0,
        _base(operation),
        next(job for job in operation["jobs"] if stage(job).get("name") == "splat"),
    )


def _base(operation) -> dict:
    """The job that laid out the atlas the rounds start from: a seed's atlas, a repair's prepared one."""
    return next(job for job in operation["jobs"] if stage(job).get("name") in ("atlas", "prepare"))


def renders_used(operation) -> int:
    return len(_find(operation, "probe"))


def _states(operation) -> tuple[str, str]:
    """The checkpoint's atlas and splat state names in the operation's scratch."""
    rnd, _, _ = checkpoint(operation)
    if rnd:
        return _name(operation, rnd), f"{_name(operation, rnd)}-splat"
    base = operation["request"].get("fill_base") or {"atlas": "seed", "splat": "seed-splat"}
    return base["atlas"], base["splat"]


def _map(session, library, operation) -> dict:
    """The base atlas's map: one voxel, room, eye height and grid for every round, as a repair keeps the base map's."""
    meta = _summary(session, library, _base(operation))["map_meta"]
    return {k: meta.get(k) for k in ("voxel", "room", "height", "grid")}


def explore(session, library, operation, rnd: int) -> Next:
    """The round's planning, for no more probes than the renders left (a failed render spent its reservation)."""
    request = operation["request"]
    fill = request["fill"]
    atlas, _ = _states(operation)
    seen = _map(session, library, operation)
    left = fill["rounds"]["renders"] - renders_used(operation)
    job = engine_job(
        EXPLORE,
        params={
            "name": _name(operation, rnd),
            "atlas_state": atlas,
            "rules": fill["plan"] | {"probes": min(fill["plan"]["probes"], left)},
            "seed": rnd,
            "voxel": seen["voxel"],
            "room": seen["room"],
            "frames": fill["rounds"]["frames"],
            "toward": request.get("toward"),
        },
        kind="analysis",
        stage={"name": "explore", "round": rnd},
    )
    return Next(_decision("fill", round=rnd, step="explore"), [job])


def _seed(operation, rnd: int, probe: int) -> int:
    digest = sha256(f"{operation['id']}:{rnd}:{probe}".encode()).hexdigest()
    return int(digest[:8], 16)


def advance(session, library, operation) -> Next | Done:
    """The next stage of the filling, or how it ended. Called once the newest stage's jobs are all finished."""
    request = operation["request"]
    fill = request["fill"]
    rounds = fill["rounds"]
    name, rnd, group = latest(operation)
    if name == "splat":
        return explore(session, library, operation, 1)
    failed = [job for _, job in group if job["state"] != "succeeded"]
    if failed and name not in ("probe", "still_geometry"):
        held, _, _ = checkpoint(operation)
        return _done("failed", operation, round=rnd, stage=STAGE_WORDS[name], held=held)
    explored = next(job for _, job in _find(operation, "explore", rnd))
    plan = _summary(session, library, explored)
    prefix = f"gaiavfx/sets/fill-{operation['id']}/{_name(operation, rnd)}"
    if name == "explore":
        if rounds["renders"] - renders_used(operation) < 1:
            return _done("renders", operation, round=rnd)
        if not plan["probes"]:
            return _done("no_probe", operation, round=rnd)
        _, splat = _states(operation)
        job = engine_job(
            PATHS,
            params={"name": _name(operation, rnd), "from": splat},
            kind="analysis",
            stage={"name": "paths", "round": rnd},
        )
        return Next(_decision("fill", round=rnd, step="paths"), [job])
    if name == "paths":
        (_, paths), left = group[0], rounds["renders"] - renders_used(operation)
        knowledge = _probe_knowledge(fill)
        jobs = []
        for n, probe in enumerate(plan["probes"][:left]):
            files = {
                "first": _artifact(explored, f"first:{n}"),
                "memory": _artifact(explored, f"memory:{n}"),
                "regen": _artifact(explored, f"regen:{n}"),
                "depth": _artifact(paths, f"depth:{n}"),
            }
            remote = {
                "first": f"{prefix}/p{n}-first.png",
                "memory": f"{prefix}/p{n}-memory.mp4",
                "regen": f"{prefix}/p{n}-regen.mp4",
                "depth": f"{prefix}/p{n}-depth.mp4",
            }
            ask = ProbeRequest(
                place=request.get("place_text") or request["name"],
                move=probe["kind"],
                turn=float(probe["turn"]),
                width=probe["width"],
                height=probe["height"],
                length=fill["rounds"]["frames"],
                seed=_seed(operation, rnd, n),
            )
            jobs.append(
                JobSpec(
                    "video",
                    "comfy",
                    minimax_h3.NAME,
                    inputs={f"{k}:{n}": _pinned(files[k], remote[k]) for k in files},
                    graph=build_probe_graph(
                        ask,
                        compile_probe(ask, knowledge),
                        first_frame_file=remote["first"],
                        memory_file=remote["memory"],
                        regen_file=remote["regen"],
                        depth_file=remote["depth"],
                        fps=FPS,
                        output_prefix=f"{prefix}/p{n}",
                        knowledge=knowledge,
                    ),
                    seed=ask.seed,
                    snapshot={
                        "knowledge": _knowledge(minimax_h3),
                        "intermediate": True,
                        "probe": {
                            "round": rnd,
                            "index": n,
                            "move": probe["kind"],
                            "turn": probe["turn"],
                        },
                        "stage": {"name": "probe", "round": rnd, "probe": n},
                    },
                )
            )
        return Next(_decision("fill", round=rnd, step="probe", probes=len(jobs)), jobs)
    if name == "probe":
        videos = {
            str(stage(job)["probe"]): video
            for _, job in group
            if job["state"] == "succeeded" and (video := _asset(job))
        }
        atlas, _ = _states(operation)
        job = engine_job(
            STILLS,
            params={
                "name": _name(operation, rnd),
                "atlas_state": atlas,
                "videos": {n: video["asset_id"] for n, video in videos.items()},
            },
            inputs={video["asset_id"]: {"sha256": video["sha256"]} for video in videos.values()},
            kind="analysis",
            stage={"name": "stills", "round": rnd},
        )
        return Next(_decision("fill", round=rnd, step="stills", rendered=len(videos)), [job])
    if name == "stills":
        (_, stills) = group[0]
        placed = _summary(session, library, stills)["stills"]
        jobs = []
        for row in placed:
            if not row["still"]:
                continue
            n = row["probe"]
            remote = f"{prefix}/p{n}-still.png"
            jobs.append(
                JobSpec(
                    "analysis",
                    "comfy",
                    worldbuilder.NAME,
                    inputs={f"still:{n}": _pinned(_artifact(stills, f"still:{n}"), remote)},
                    graph=still_geometry_graph(
                        remote,
                        hfov=plan["probes"][n]["hfov"],
                        still=fill["still"],
                        prefix=f"{prefix}/p{n}",
                    ),
                    snapshot={
                        "knowledge": _knowledge(worldbuilder),
                        "stage": {"name": "still_geometry", "round": rnd, "probe": n},
                    },
                )
            )
        if not jobs:  # nothing rendered: the round consolidates nothing
            return _consolidate(session, library, operation, rnd, [])
        return Next(_decision("fill", round=rnd, step="still_geometry", stills=len(jobs)), jobs)
    if name == "still_geometry":
        return _consolidate(session, library, operation, rnd, group)
    if name == "consolidate":
        (_, consolidated) = group[0]
        result = _summary(session, library, consolidated)["round"]
        if result["accepted"]:
            _, splat = _states(operation)
            job = engine_job(
                EXTEND,
                params={
                    "from": splat,
                    "name": f"{_name(operation, rnd)}-splat",
                    "atlas_state": _name(operation, rnd),
                    "steps": rounds["extend_steps"],
                },
                kind="analysis",
                stage={"name": "extend", "round": rnd},
            )
            return Next(
                _decision("fill", round=rnd, step="extend", accepted=len(result["accepted"])), [job]
            )
        without = _rounds_without(session, library, operation)
        if without >= rounds["rounds_without_acceptance"]:
            return _done("acceptance", operation, round=rnd, without=without)
        return _after(session, library, operation, rnd, result)
    if name == "extend":
        (_, consolidated) = _find(operation, "consolidate", rnd)[0]
        result = _summary(session, library, consolidated)["round"]
        return _after(session, library, operation, rnd, result)
    raise ValueError(f"Unexpected fill stage {name!r}.")


def _knowledge(integration) -> dict:
    from gaiavfx.application.operations import knowledge_snapshot

    return knowledge_snapshot(integration)


def _consolidate(session, library, operation, rnd, group) -> Next:
    fill = operation["request"]["fill"]
    geometry = {
        str(stage(job)["probe"]): found
        for _, job in group
        if job["state"] == "succeeded" and (found := _artifact(job, GEOMETRY_OUTPUT))
    }
    probes = [job for _, job in _find(operation, "probe", rnd) if job["state"] == "succeeded"]
    videos = {str(stage(job)["probe"]): _asset(job) for job in probes if _asset(job)}
    videos = {n: v for n, v in videos.items() if n in geometry}
    atlas, _ = _states(operation)
    job = engine_job(
        CONSOLIDATE,
        params={
            "name": _name(operation, rnd),
            "atlas_state": atlas,
            "atlas": _name(operation, rnd),
            "rules": fill["accept"] | {"margin": fill["plan"]["margin"]},
            "videos": {n: video["asset_id"] for n, video in videos.items()},
            **_map(session, library, operation),
        },
        inputs={
            **{f"geometry:{n}": _pinned(found) for n, found in geometry.items() if n in videos},
            **{video["asset_id"]: {"sha256": video["sha256"]} for video in videos.values()},
        },
        kind="analysis",
        stage={"name": "consolidate", "round": rnd},
    )
    return Next(_decision("fill", round=rnd, step="consolidate", stills=len(videos)), [job])


def _rounds_without(session, library, operation) -> int:
    """Consecutive rounds, newest first, whose consolidation accepted no still."""
    count = 0
    for _, job in reversed(_find(operation, "consolidate")):
        if job["state"] != "succeeded" or _summary(session, library, job)["round"]["accepted"]:
            break
        count += 1
    return count


def _after(session, library, operation, rnd, result) -> Next | Done:
    rounds = operation["request"]["fill"]["rounds"]
    if rnd >= rounds["rounds"]:
        return _done("rounds", operation, round=rnd)
    if renders_used(operation) >= rounds["renders"]:
        return _done("renders", operation, round=rnd)
    if result["accepted"] and result["gain"] < rounds["area_gain_min"]:
        return _done("gain", operation, round=rnd)
    if result["coverage"]["closure"] >= rounds["closure_max"]:
        return _done("closure", operation, round=rnd)
    return explore(session, library, operation, rnd + 1)


def _done(reason: str, operation, **fields) -> Done:
    rounds, _, _ = checkpoint(operation)
    cap = operation["request"]["fill"]["rounds"]
    words = ENDED[reason].format(
        rounds="one round" if rounds == 1 else f"{rounds} rounds",
        renders=cap["renders"],
        **{k: v for k, v in fields.items() if k != "held"},
    )
    return Done(reason, words, rounds)


def progress(operation) -> str:
    """The words while the filling runs, for the References row and the workbench's build."""
    _, rnd, _ = latest(operation)
    total = operation["request"]["fill"]["rounds"]["rounds"]
    return f"Filling unseen space · round {rnd} of {total}…" if rnd else "Filling unseen space…"
