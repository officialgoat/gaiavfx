"""A set build (ADR 0018 §4): one bounded operation that turns a set's queued fixes into a proposed version.

Mode `repair` runs exclusions and extensions (reshoots wait for a later step). Two worldbuilder engine jobs first:
1. `atlas_prepare` (CPU) lays out the base version's atlas from Library files, retracts each excluded keyframe whole
   (it stays as a colour-only record: nothing is deleted, and it no longer carves, occludes or trains the splat) and
   carves the map again, cut at the base map's eye height and named on its grid, so B3 is one place in both;
2. `splat_init_atlas` (GPU) fits the splat again from the current keyframes, with the frozen rebuild settings.
With extensions queued (Fill more unseen space, ADR 0020 §4), the build then fills in rounds from that atlas and
splat (`set_fill.py`), steered toward the extensions' heading when they name one, and reserves the rounds' renders.

The advancer writes the result as a proposed version (origin `repair`, the base as its parent; from the last round's
checkpoint when it filled) and the build's fixes as done; the human compares it and accepts or discards it, which settles the issues those fixes addressed. A stopped
build marks its fixes failed: through the advancer, or through the sweep when the build stopped before any job
succeeded (it then never reaches the advancer). Nothing here starts on its own: only the human's Run queued fixes
calls `start_set_build`.
"""

import json
from hashlib import sha256

from gaiavfx.application import set_fill
from gaiavfx.application.engine_jobs import engine_job
from gaiavfx.application.sets import STORE, build_view, set_state
from gaiavfx.integrations.worldbuilder import rules
from gaiavfx.modules.generation.operations.attempts import attempts
from gaiavfx.modules.generation.operations.lifecycle import OPERATION_TERMINAL, STOPPED
from gaiavfx.modules.generation.operations.outcome import failure
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError

NAME = "set_build"
BUILD_VERSION = 1
STEPS = ("worldbuilder.atlas_prepare", "worldbuilder.splat_init_atlas")
operations = OperationService()


def _key(set_id, base_id, fix_ids, attempt) -> str:
    return sha256(
        json.dumps([NAME, set_id, base_id, sorted(fix_ids), BUILD_VERSION, attempt]).encode()
    ).hexdigest()[:12]


def _attempt(session, set_id, base_id, fix_ids) -> tuple[dict | None, int]:
    """The build for these fixes that is still under way, or the first attempt number not yet used."""
    return attempts(operations, session, lambda n: _key(set_id, base_id, fix_ids, n)).in_flight()


def _frozen(artifact: dict) -> dict:
    return {
        "source": "artifact",
        "artifact_id": artifact["id"],
        "sha256": artifact["sha256"],
        "directory": "input",
    }


def start_set_build(session, library, set_id: str, *, base_version_id: str) -> dict:
    """Run the set's queued exclusions, kept stills and extensions against `base_version_id`, the version the user is
    looking at: the user's Run queued fixes (ADR 0018 §5), or the agent's Fill more from the accepted version when
    nothing else is queued (ADR 0020 §8). Refused while a build of the set runs, with nothing to run, or when a fix
    names a keyframe the base version does not hold."""
    from gaiavfx.application.operations import PRODUCERS, create_operation
    from gaiavfx.application.sets import _check_keep

    row = STORE.get(session, set_id)
    base = next((v for v in row["versions"] if v["id"] == base_version_id), None)
    if base is None:
        raise ApplicationError("This version is not part of the stage.", 404)
    if base["state"] == "discarded":
        raise ApplicationError("A discarded version cannot be built on.", 409)
    fixes = STORE.fixes(session, set_id)
    if any(f["state"] == "running" for f in fixes):
        raise ApplicationError("A build of this stage is already running.", 409)
    exclusions = [f for f in fixes if f["state"] == "queued" and f["action"] == "retract"]
    keeps = [f for f in fixes if f["state"] == "queued" and f["action"] == "keep"]
    extensions = [f for f in fixes if f["state"] == "queued" and f["action"] == "extend"]
    if not exclusions and not keeps and not extensions:
        raise ApplicationError(
            "Nothing to run yet: exclude a keyframe, keep a still or fill more unseen space first. Reshoots wait "
            "for a later step.",
            409,
        )
    # A kept still is checked again against this version, and its geometry pinned, before anything starts.
    kept = [
        (fix, _check_keep(session, library, set_id, base["id"], fix["target"], fix_id=fix["id"]))
        for fix in keeps
    ]
    members = {k["id"]: k for k in STORE.keyframes(session, base["id"])}
    retract, seen = [], set()
    for fix in exclusions:
        keyframe = members.get((fix["target"] or {}).get("keyframe"))
        if keyframe is None:
            label = (fix["target"] or {}).get("label", "A keyframe")
            raise ApplicationError(
                f"{label} is not part of v{base['ordinal']}; cancel its exclusion or open the version it was "
                "queued in.",
                409,
            )
        if keyframe["kind"] == "erp":
            raise ApplicationError(
                "The panorama is the stage's ground truth; it cannot be excluded.", 409
            )
        if keyframe["id"] not in seen:
            seen.add(keyframe["id"])
            retract.append(
                {"record": keyframe["key"], "reason": f"Excluded in the workbench ({fix['id']})"}
            )
    keep = []
    inputs = {}
    for fix, geometry in kept:
        keyframe = members[fix["target"]["keyframe"]]
        inputs[f"glass:{keyframe['id']}"] = geometry
        keep.append(
            {
                "record": keyframe["key"],
                "glass": f"glass:{keyframe['id']}",
                "reason": f"Kept in the workbench ({fix['id']})",
            }
        )
    fix_ids = [f["id"] for f in exclusions + keeps + extensions]
    existing, attempt = _attempt(session, set_id, base["id"], fix_ids)
    if existing is not None:
        return existing | {"_created": False}
    atlas = library.artifact(session, base["atlas_artifact_id"])
    inputs, files = {"atlas": _frozen(atlas), **inputs}, {}
    for keyframe in members.values():
        still = library.get(session, keyframe["image_asset_id"])
        depth = library.artifact(session, keyframe["depth_artifact_id"])
        inputs[still["id"]] = {"sha256": still["sha256"]}
        inputs[f"depth:{keyframe['id']}"] = _frozen(depth)
        files[keyframe["key"]] = {"image": still["id"], "data": f"depth:{keyframe['id']}"}
    meta = base["map_meta"] or {}
    prepared = f"v{base['ordinal']}-repair-{attempt}"
    fill = set_fill.frozen() if extensions else None
    operation = create_operation(
        session,
        operations,
        PRODUCERS,
        kind=NAME,
        request={
            "set_id": set_id,
            "base_version_id": base["id"],
            "mode": "repair",
            "fix_ids": fix_ids,
            "attempt": attempt,
            # Extensions fill from the prepared atlas and its splat, after the rounds the base already holds; the
            # rounds' frozen settings and their renders.
            "fill": fill,
            "fill_base": {
                "atlas": prepared,
                "splat": f"{prepared}-splat",
                "rounds": set_fill.rounds_held(
                    json.loads(library.read_blob(session, base["atlas_artifact_id"]))
                ),
            }
            if fill
            else None,
            "toward": _toward(extensions),
            "name": row["name"],
            "place_text": row["place_text"],
        },
        request_key=_key(set_id, base["id"], fix_ids, attempt),
        budget_limit=None,
        budget=fill["rounds"]["renders"] if fill else 0,
        awaits_review=True,
        jobs=[
            engine_job(
                STEPS[0],
                params={
                    "name": prepared,
                    "files": files,
                    "retract": retract,
                    "keep": keep,
                    **{k: meta.get(k) for k in ("voxel", "room", "height", "grid")},
                },
                inputs=inputs,
                kind="analysis",
                stage={"name": "prepare"},
            ),
            engine_job(
                STEPS[1],
                params={"name": f"{prepared}-splat", "atlas_state": prepared, **rules.rebuild()},
                kind="analysis",
                depends_on=0,
                stage={"name": "splat"},
            ),
        ],
    )
    STORE.start_fixes(session, set_id, fix_ids, operation["id"])
    return operation


def _toward(extensions) -> float | None:
    """The heading the extensions steer the filling toward: the mean of the headings they name, when every one names
    one (an extension without a heading fills all round)."""
    import math

    headings = [(f["target"] or {}).get("heading") for f in extensions]
    if not headings or any(h is None for h in headings):
        return None
    x = sum(math.sin(math.radians(h)) for h in headings)
    z = sum(math.cos(math.radians(h)) for h in headings)
    return round(math.degrees(math.atan2(x, z)) % 360, 2)


def _json(session, library, job, key) -> dict:
    return json.loads(library.read_blob(session, set_fill.output(job, key)["artifact_id"]))


def _keyframes(
    session, library, set_id, base_id, entries, atlas_job, operation_id, prepare_job=None
) -> tuple[list[str], list[str], list[str], list[str]]:
    """The new version's members in the atlas's order: the base's own keyframes where the record is unchanged, a
    superseding record for each excluded one (the same still and depth, colour only) and each kept one (the same
    still, its trusted pixels restored), and each keyframe the filling added, with its still and depth from the atlas
    job's outputs. Also returns the base labels of the excluded and the kept keyframes."""
    members = {k["key"]: k for k in STORE.keyframes(session, base_id)}
    ids, labels, excluded, kept = [], [], [], []
    for entry in entries:
        keyframe = members.get(entry["record"])
        if keyframe is None and entry.get("supersedes") not in members:
            # A keyframe the filling added: its still becomes an image asset, its depth stays the job's artifact.
            still = library.prepare(
                library.artifact_path(
                    session, set_fill.output(atlas_job, f"still:{entry['key']}")["artifact_id"]
                ),
                f"{entry['label']}.png",
            )
            asset = library.register(
                session,
                still,
                {
                    "type": "set_build",
                    "operation_id": operation_id,
                    "role": "set_keyframe",
                    "label": entry["label"],
                },
            )
            ids.append(
                STORE.add_keyframe(
                    session,
                    set_id,
                    key=str(entry["record"])[:80],
                    kind=entry["kind"],
                    role=entry["role"],
                    viewmat=entry["viewmat"],
                    intrinsics=entry.get("intrinsics"),
                    width=int(entry["width"]),
                    height=int(entry["height"]),
                    heading=entry.get("heading"),
                    image_asset_id=asset["id"],
                    depth_artifact_id=set_fill.output(atlas_job, f"data:{entry['key']}")[
                        "artifact_id"
                    ],
                    supersedes_id=None,
                    info=entry.get("info") or {},
                )
            )
            labels.append(entry["label"])
            continue
        if (
            keyframe is None
            and (entry.get("info") or {}).get("kept")
            and entry["role"] == "generated"
        ):
            # A kept still (ADR 0020 §8): the same picture, with the trusted pixels the atlas job restored. Its record
            # stays marked kept when it is excluded later (Astra's test pass); that exclusion is appearance only.
            previous = members[entry["supersedes"]]
            still = library.get(session, previous["image_asset_id"])
            if still["sha256"] != entry["image_sha256"]:
                raise ApplicationError("A kept still keeps its picture.", 409)
            keyframe_id = STORE.add_keyframe(
                session,
                set_id,
                key=entry["record"][:80],
                kind=entry["kind"],
                role=entry["role"],
                viewmat=entry["viewmat"],
                intrinsics=entry.get("intrinsics"),
                width=previous["width"],
                height=previous["height"],
                heading=entry.get("heading"),
                image_asset_id=previous["image_asset_id"],
                depth_artifact_id=set_fill.output(prepare_job or atlas_job, f"data:{entry['key']}")[
                    "artifact_id"
                ],
                supersedes_id=previous["id"],
                info=entry.get("info") or {},
            )
            kept.append(previous["label"])
        elif keyframe is None:
            previous = members[entry["supersedes"]]
            still = library.get(session, previous["image_asset_id"])
            depth = library.artifact(session, previous["depth_artifact_id"])
            if (still["sha256"], depth["sha256"]) != (entry["image_sha256"], entry["data_sha256"]):
                raise ApplicationError("Only whole exclusions are written for now.", 409)
            keyframe_id = STORE.add_keyframe(
                session,
                set_id,
                key=entry["record"][:80],
                kind=entry["kind"],
                role=entry["role"],
                viewmat=entry["viewmat"],
                intrinsics=entry.get("intrinsics"),
                width=previous["width"],
                height=previous["height"],
                heading=entry.get("heading"),
                image_asset_id=previous["image_asset_id"],
                depth_artifact_id=previous["depth_artifact_id"],
                supersedes_id=previous["id"],
                info={**(entry.get("info") or {}), "was": previous["label"]},
            )
            excluded.append(previous["label"])
        else:
            keyframe_id = keyframe["id"]
        ids.append(keyframe_id)
        labels.append(entry["label"])
    return ids, labels, excluded, kept


def _write_version(session, library, operation, filled: set_fill.Done | None = None) -> str:
    """The proposed version, from the checkpoint: the prepared atlas and its splat, or, when the build filled, the
    last round whose extension finished."""
    request = operation["request"]
    set_id, base_id = request["set_id"], request["base_version_id"]
    _, prepare, splat = set_fill.checkpoint(operation)
    summary = _json(session, library, prepare, "summary")
    fit = _json(session, library, splat, "summary")
    # A kept still's restored pixels are the prepare job's, whichever round the version is written from.
    prepared = next(j for j in operation["jobs"] if set_fill.stage(j).get("name") == "prepare")
    ids, labels, excluded, kept = _keyframes(
        session, library, set_id, base_id, summary["keyframes"], prepare, operation["id"], prepared
    )
    base = next(v for v in STORE.get(session, set_id)["versions"] if v["id"] == base_id)
    note = f"v{base['ordinal']} rebuilt" + (f" without {', '.join(excluded)}" if excluded else "")
    note += f" keeping {', '.join(kept)}" if kept else ""
    note += f"; {filled.words}." if filled else "."
    version_id = STORE.add_version(
        session,
        set_id,
        origin="repair",
        atlas_artifact_id=set_fill.output(prepare, "atlas")["artifact_id"],
        keyframe_ids=ids,
        labels=labels,
        parent_id=base_id,
        operation_id=operation["id"],
        map_artifact_id=set_fill.output(prepare, "map")["artifact_id"],
        splat_artifact_id=set_fill.output(splat, "splat")["artifact_id"],
        map_meta=summary["map_meta"],
        metrics=set_fill.metrics(fit),
        note=note,
    )
    STORE.end_fixes(session, set_id, operation["id"], "done")
    return version_id


def advance_set_build(session, operation, *, library):
    """The one advancer of every set build: its mode's own stages (`MODES`)."""
    mode = operation["request"].get("mode", "repair")
    if mode not in MODES:
        raise ApplicationError(f"Unknown set build mode {mode!r}.", 409)
    return MODES[mode](session, operation, library=library)


def _advance_repair(session, operation, *, library):
    """After both jobs, write the proposed version and close the gate; a stopped job fails the build's fixes and
    closes it too, so the operation settles failed or partial and the workbench offers Retry."""
    jobs = operation["jobs"]
    request = operation["request"]
    if not jobs:
        raise ApplicationError("The set build has no stage to advance.", 409)
    name, _, _ = set_fill.latest(operation)
    base = [job for job in jobs if set_fill.stage(job).get("name") in ("prepare", "splat")]
    if name in ("prepare", "splat") and set_fill.stopped(base, "set build"):
        return _stopped(session, operation)
    filled = None
    if request.get("fill"):
        try:
            step = set_fill.advance(session, library, operation)
        except ValueError as exc:
            raise ApplicationError(str(exc), 409) from exc
        if isinstance(step, set_fill.Next):
            return operations.resume(
                session, operation["id"], step.decision, jobs=step.jobs, awaits_review=True
            )
        filled = step
        excluded = any(
            f["id"] in request["fix_ids"] and f["action"] in ("retract", "keep")
            for f in STORE.fixes(session, request["set_id"])
        )
        if filled.rounds == 0 and not excluded:
            # Nothing was filled and nothing excluded: the stage is as it was, so no version is written.
            return _stopped(
                session, operation, f"The build {filled.words}; the stage is as it was."
            )
    elif tuple(job["step"] for job in jobs) != STEPS:
        raise ApplicationError("Unexpected set build stage.", 409)
    version_id = _write_version(session, library, operation, filled)
    return operations.resume(
        session,
        operation["id"],
        {"decided_by": NAME, "stage": "done", "version_id": version_id},
        jobs=[],
    )


def _stopped(session, operation, reason: str | None = None):
    """Close a build that made no version: its fixes failed (Retry build queues them again)."""
    STORE.end_fixes(session, operation["request"]["set_id"], operation["id"], "failed")
    decision = {"decided_by": NAME, "stage": "stopped"} | ({"reason": reason} if reason else {})
    return operations.resume(session, operation["id"], decision, jobs=[])


def _advance_seed(session, operation, *, library):
    from gaiavfx.application.set_seed import advance_seed

    return advance_seed(session, operation, library=library)


# Each mode composes its own stages (ADR 0019 §4): `repair` here, `seed` in `application/set_seed.py`.
MODES = {"repair": _advance_repair, "seed": _advance_seed}


def sweep_set_builds(session, *, library):
    """End the fixes of builds that stopped before the advancer saw them: failed or cancelled at once, a restart
    that found them stopped. A build awaiting its advancer is left to it."""
    for fix in STORE.running_fixes(session):
        try:
            state = operations.operation(session, fix["operation_id"])["state"]
        except ApplicationError:  # an operation that is gone cannot finish the fix
            state = "failed"
        if state in OPERATION_TERMINAL:
            STORE.end_fixes(session, fix["set_id"], fix["operation_id"], "failed")
    return False


def run_queued_fixes(session, library, set_id, *, version_id, retry=False):
    """The workbench's Run queued fixes: start the build on the version the user is looking at. With `retry`
    (Retry build), the newest build's failed fixes are queued again first."""
    if retry:
        build = build_view(session, set_id)
        for fix in STORE.fixes(session, set_id):
            if build and fix["id"] in build["fix_ids"] and fix["state"] == "failed":
                STORE.retry_fix(session, set_id, fix["id"])
    start_set_build(session, library, set_id, base_version_id=version_id)
    return set_state(session, library, set_id, version_id)


# --- a build in words ----------------------------------------------------------------------------------------------
# One stage build read for the user (its References row and dialog, the workbench) and for Gaia (its turns): from a
# take (`set_seed.seed_state`) or filling more of a stage, why it stopped, and what it spent.


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
    return {
        "cancelled": "stopped",
        "unreachable": "comfyui",
        "engine_missing": "engines",
        "out_of_memory": "memory",
    }.get(failure(operation))


# Why a build stopped, in the agent's words; a stop the next build would meet the same way is not retried blindly.
STOP_CAUSES = {
    "stopped": "the user stopped it",
    "comfyui": "ComfyUI did not answer",
    "engines": "the stage engines are not set up",
    "memory": "the GPU ran out of memory",
}


def after_stop(operation, verb: str, reserved: bool, reason: str = "") -> str:
    """The end of a stopped build's sentence and what the agent may do next: build once more only when nothing it
    knows would stop the next build the same way. `reason` is the build's own, for a cause not named here."""

    cause = stop_cause(operation)
    # The build's own reason is a sentence of its own (the live trial read "…: The build stopped … as it was..").
    said = f": {STOP_CAUSES[cause]}." if cause else f". {reason.rstrip('.')}." if reason else "."
    if cause == "stopped":
        return f"{said} The build step stays open: ask the user before you {verb} again."
    if cause:
        return (
            f"{said} Another build stops the same way until that changes: tell the user, and {verb} once more in "
            "this step only when they say it is fixed."
        )
    if not reserved:
        return (
            f"{said} The build step stays open: {verb} once more in it, or tell the user and ask."
        )
    return (
        f"{said} The build step stays open with the rest: {verb} once more in it if they are enough, propose a "
        "revised plan if not, or tell the user and ask."
    )


def seed_outcome(session, operation) -> dict:
    """A system turn for a build from a take that ended or was decided: the agent's words as its message, the user's
    as its notice, and a receipt that keeps the stage in the agent's context. `decided` is the user's decision on v1
    (accepted or discarded), None before it."""
    from gaiavfx.application import set_agent as agent
    from gaiavfx.application.set_seed import seed_state

    seed = seed_state(session, operation)
    receipt = {"kind": "set_build", "set_id": seed["set_id"], "operation_id": operation["id"]}
    decided = None
    if seed["state"] == "done" and not seed["waiting"]:
        decided = STORE.get(session, seed["set_id"])["versions"][0]["state"]
    return {
        "action": {"message": agent.seed_words(session, operation), "notice": seed["message"]},
        "receipts": ([receipt] if seed["set_id"] else [])
        + ([settled(operation)] if seed["state"] == "failed" else []),
        "decided": decided,
    }


def repair_state(session, operation) -> dict:
    """A build that fills more of a stage (a repair, ADR 0020 §8) in the user's words, in `seed_state`'s shape:
    building, failed (stopped) or done, with the version it proposed and whether it still waits for the user."""
    from gaiavfx.application import set_fill

    row = STORE.get(session, operation["request"]["set_id"])
    name = row["name"]
    decided = [d for d in operation.get("review") or [] if d.get("decided_by") == "set_build"]
    last = decided[-1] if decided else {}
    base = {
        "operation_id": operation["id"],
        "set_id": row["id"],
        "name": name,
        "shot": None,
        "waiting": False,
        "can_retry": False,
    }
    if last.get("stage") == "done":
        version = next(v for v in row["versions"] if v["id"] == last["version_id"])
        n, waiting = version["ordinal"], version["state"] == "proposed"
        return base | {
            "state": "done",
            "label": f"v{n} to review" if waiting else f"v{n} {version['state']}",
            "message": f"{name} v{n} is built: {version['note']} "
            + (
                "It waits for you in the stage workbench."
                if waiting
                else f"v{n} {version['state']}."
            ),
            "waiting": waiting,
        }
    if last.get("stage") == "stopped" or operation["state"] in STOPPED:
        cause = stop_cause(operation)
        reason = (
            " was stopped before it finished."
            if cause == "stopped"
            else f" stopped before it finished: {STOP_WORDS[cause]}"
            if cause
            else f" stopped before it finished. {last['reason'].rstrip('.')}."
            if last.get("reason")
            else " stopped before it finished."
        )
        return base | {
            "state": "failed",
            "label": "Stopped before it finished",
            "message": f"Filling more of {name}{reason}",
        }
    filling = set_fill.latest(operation)[0] not in ("prepare", "splat")
    label = set_fill.progress(operation) if filling else "Rebuilding the stage…"
    return base | {
        "state": "building",
        "label": label,
        "message": f"{name}: {label[0].lower()}{label[1:]}",
    }


def repair_outcome(session, operation) -> dict:
    """`seed_outcome` for a build that fills more of a stage: the agent's words, the user's, its receipts and the
    user's decision on the version it proposed. A discarded version leaves the stage's accepted one in place."""
    state = repair_state(session, operation)
    name = state["name"]
    receipts = [{"kind": "set_build", "set_id": state["set_id"], "operation_id": operation["id"]}]
    decided = None
    if state["state"] == "building":
        words = (
            f"{name}: {state['label'].rstrip('…')[0].lower()}{state['label'].rstrip('…')[1:]}; the "
        )
        words += "conversation waits for the build."
    elif state["state"] == "failed":
        receipts.append(settled(operation))
        spent = receipts[-1]["budget_reserved"]
        stops = [d for d in operation.get("review") or [] if d.get("stage") == "stopped"]
        words = (
            f"Filling more of {name} stopped before it finished, after {spent} of its "
            f"{operation['budget_reserved']} renders"
            + after_stop(operation, "fill", True, stops[-1].get("reason", "") if stops else "")
        )
    else:
        row = STORE.get(session, state["set_id"])
        version_id = operation["review"][-1]["version_id"]
        version = next(v for v in row["versions"] if v["id"] == version_id)
        n = version["ordinal"]
        if state["waiting"]:
            words = (
                f"{name} v{n} is built ({version['note'].rstrip('.')}). It waits for the user's decision in the "
                "stage workbench; the build step completes when they accept or discard it. Tell the user where it "
                "waits: accepting is theirs."
            )
        elif version["state"] == "accepted":
            decided = "accepted"
            words = f"The user accepted v{n} of {name}; the scenes that use it read it now. Continue the plan."
        else:
            decided = "discarded"
            base = next(v for v in row["versions"] if v["id"] == version["parent_id"])
            words = (
                f"The user discarded v{n} of {name}; v{base['ordinal']} stays the stage's. The build step is "
                "skipped: tell the user and ask what they want."
            )
    return {
        "action": {"message": words, "notice": state["message"]},
        "receipts": receipts,
        "decided": decided,
    }


def build_state(session, operation) -> dict:
    """A stage build in the user's words: from a take (`seed_state`) or filling more of a stage."""
    from gaiavfx.application.set_seed import seed_state

    if operation["request"].get("mode") == "seed":
        return seed_state(session, operation)
    return repair_state(session, operation)


def build_outcome(session, operation) -> dict:
    """A stage build's system turn and the user's decision: from a take or filling more of a stage."""
    if operation["request"].get("mode") == "seed":
        return seed_outcome(session, operation)
    return repair_outcome(session, operation)


def settled(operation) -> dict:
    """What a stopped build spent, as an operation receipt that settles its reservation (ADR 0020 §8): the probes it
    allocated, a failed one included. The renders it never allocated return to its step and the conversation."""
    from gaiavfx.application import set_fill

    return {
        "kind": "operation",
        "id": operation["id"],
        "operation_kind": operation["kind"],
        "budget_reserved": min(operation["budget_reserved"], set_fill.renders_used(operation)),
        "settled": True,
    }
