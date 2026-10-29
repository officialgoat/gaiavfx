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
from gaiavfx.modules.generation.operations.lifecycle import OPERATION_TERMINAL
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError

NAME = "set_build"
BUILD_VERSION = 1
STEPS = ("worldbuilder.atlas_prepare", "worldbuilder.splat_init_atlas")
STOPPED = frozenset({"failed", "cancelled", "needs_attention"})
operations = OperationService()


def _key(set_id, base_id, fix_ids, attempt) -> str:
    return sha256(
        json.dumps([NAME, set_id, base_id, sorted(fix_ids), BUILD_VERSION, attempt]).encode()
    ).hexdigest()[:12]


def _attempt(session, set_id, base_id, fix_ids) -> tuple[dict | None, int]:
    """The build for these fixes that is still under way, or the first attempt number not yet used."""
    attempt = 1
    while (
        found := operations.for_request(session, _key(set_id, base_id, fix_ids, attempt))
    ) is not None:
        if found["state"] not in OPERATION_TERMINAL:
            return found, attempt
        attempt += 1
    return None, attempt


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


def _output(job, key) -> dict:
    matches = [o for o in job["outputs"] if o.get("output_key") == key and o.get("artifact_id")]
    if len(matches) != 1:
        raise ApplicationError(f"The set build lacks its {key} artifact.", 409)
    return matches[0]


def _json(session, library, job, key) -> dict:
    return json.loads(library.read_blob(session, _output(job, key)["artifact_id"]))


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
                    session, _output(atlas_job, f"still:{entry['key']}")["artifact_id"]
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
                    depth_artifact_id=_output(atlas_job, f"data:{entry['key']}")["artifact_id"],
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
                depth_artifact_id=_output(prepare_job or atlas_job, f"data:{entry['key']}")[
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
        atlas_artifact_id=_output(prepare, "atlas")["artifact_id"],
        keyframe_ids=ids,
        labels=labels,
        parent_id=base_id,
        operation_id=operation["id"],
        map_artifact_id=_output(prepare, "map")["artifact_id"],
        splat_artifact_id=_output(splat, "splat")["artifact_id"],
        map_meta=summary["map_meta"],
        metrics={
            "uncalibrated": True,
            "heldout_psnr": fit.get("heldout_psnr_refined") or fit.get("heldout_psnr"),
            "anchor_psnr": fit.get("anchor_psnr"),
            "fill_psnr": fit.get("fill_psnr"),
            "gaussians": fit.get("gaussians"),
        },
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
    if name in ("prepare", "splat") and any(job["state"] != "succeeded" for job in base):
        if any(job["state"] not in STOPPED for job in base if job["state"] != "succeeded"):
            raise ApplicationError("The set build cannot advance an incomplete stage.", 409)
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
