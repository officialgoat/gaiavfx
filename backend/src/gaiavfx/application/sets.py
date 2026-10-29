"""Sets (ADR 0018): the projections the workbench reads and the human commands on a set."""

from __future__ import annotations

import base64
import io
from pathlib import Path

import numpy as np
from PIL import Image

from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.generation.operations.lifecycle import OPERATION_TERMINAL
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.sets.cameras import rigid
from gaiavfx.modules.sets.service import SetStore
from gaiavfx.shared.errors import ApplicationError

STORE = SetStore()
DIRECTION = DirectionService()
OPERATIONS = OperationService()
BUILD_STOPPED = frozenset({"failed", "cancelled", "needs_attention"})


def clip_words(text: str, limit: int) -> str:
    """Text on one line, cut at `limit` characters with an ellipsis."""
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _version_summary(version: dict) -> dict:
    return {
        k: version[k]
        for k in (
            "id",
            "ordinal",
            "state",
            "origin",
            "parent_id",
            "operation_id",
            "note",
            "created_at",
            "decided_at",
        )
    }


def _shown_version(row: dict, version_id: str | None) -> dict:
    versions = row["versions"]
    if version_id:
        match = next((v for v in versions if v["id"] == version_id), None)
        if match is None:
            raise ApplicationError("This version is not part of the stage.", 404)
        return match
    current = next((v for v in versions if v["id"] == row["current_version_id"]), None)
    live = [v for v in versions if v["state"] != "discarded"]
    return current or (live[-1] if live else versions[-1])


def _poster(keyframes: list[dict]) -> str | None:
    """The keyframe a set's card shows: the one a person covers least (a set seeded from a take records each
    frame's share), else the first."""
    if not keyframes:
        return None
    return min(keyframes, key=lambda k: (k["info"] or {}).get("people", 0.0))["image_asset_id"]


def list_sets(session, library) -> list[dict]:
    out = []
    for set_id in STORE.ids(session):
        row = STORE.get(session, set_id)
        if not row["versions"]:
            continue
        shown = _shown_version(row, None)
        keyframes = STORE.keyframes(session, shown["id"])
        issues = STORE.issues(session, set_id)
        out.append(
            {
                "id": row["id"],
                "name": row["name"],
                "place_text": row["place_text"],
                "revision": row["revision"],
                "updated_at": row["updated_at"],
                "current_version_id": row["current_version_id"],
                "shown_version": _version_summary(shown),
                "versions": len(row["versions"]),
                "keyframes": len(keyframes),
                "open_issues": sum(i["status"] == "open" for i in issues),
                "poster_asset_id": _poster(keyframes),
                "has_splat": bool(shown["splat_artifact_id"]),
            }
        )
    return out


def set_state(session, library, set_id: str, version_id: str | None = None) -> dict:
    row = STORE.get(session, set_id)
    if not row["versions"]:
        raise ApplicationError("This stage has no versions yet.", 409)
    shown = _shown_version(row, version_id)
    splat = None
    if shown["splat_artifact_id"]:
        artifact = library.artifact(session, shown["splat_artifact_id"])
        splat = {
            "artifact_id": artifact["id"],
            "size": artifact["info"].get("size"),
            "gaussians": artifact["info"].get("gaussians"),
            "sh_degree": artifact["info"].get("sh_degree"),
        }
    return {
        "id": row["id"],
        "name": row["name"],
        "place_text": row["place_text"],
        "seed": row["seed"],
        "revision": row["revision"],
        "provenance": row["provenance"],
        "current_version_id": row["current_version_id"],
        "versions": [_version_summary(v) for v in row["versions"]],
        "version": {
            **_version_summary(shown),
            "map_meta": shown["map_meta"],
            "metrics": shown["metrics"],
            "has_map": bool(shown["map_artifact_id"]),
            "splat": splat,
            "keyframes": [
                {
                    k: key[k]
                    for k in (
                        "id",
                        "label",
                        "key",
                        "kind",
                        "role",
                        "viewmat",
                        "intrinsics",
                        "width",
                        "height",
                        "heading",
                        "image_asset_id",
                    )
                }
                # an excluded keyframe's record says which label it had before (ADR 0018 §4)
                | {"was": (key["info"] or {}).get("was")}
                # a probe still filling set aside says why, and whether it can be kept (ADR 0020 §8)
                | _set_aside(session, library, set_id, key)
                for key in STORE.keyframes(session, shown["id"])
            ],
        },
        "issues": STORE.issues(session, set_id),
        "fixes": STORE.fixes(session, set_id),
        "hide_regions": STORE.hide_regions(session, set_id, shown["id"]),
        "views": _views_with_angles(session, STORE.views(session, set_id)),
        "build": build_view(session, set_id),
        "run": queued_run(session, set_id, shown),
    }


def _set_aside(session, library, set_id: str, keyframe: dict) -> dict:
    """For a probe still that filling's consolidation set aside (ADR 0020 §8): why, in words, and whether Keep this
    still can bring it back; `dropped` is None for every other keyframe."""
    from gaiavfx.integrations.worldbuilder.fill import settings as fill_settings

    info = keyframe["info"] or {}
    report = info.get("consolidation")
    if (
        keyframe["role"] != "appearance-only"
        or not report
        or report.get("accepted")
        or info.get("was")
    ):
        return {"dropped": None, "can_keep": False}
    rules = fill_settings()["accept"]
    reasons = []
    if (report.get("residual") or 0) > rules["residual_max"]:
        reasons.append(
            f"its depth disagreed with the stage by {report['residual']:.0%} "
            f"({rules['residual_max']:.0%} allowed)"
        )
    if (report.get("contradiction") or 0) > rules["contradiction_max"]:
        reasons.append(
            f"it drew {report['contradiction']:.0%} of its new surfaces where the stage saw open space "
            f"({rules['contradiction_max']:.0%} allowed)"
        )
    psnr = report.get("anchor_psnr")
    if (
        psnr is not None
        and (report.get("anchor_supplied") or 0) >= 0.005
        and psnr < rules["anchor_psnr_min"]
    ):
        reasons.append(
            f"it changed what the take shows ({psnr:.1f} dB, {rules['anchor_psnr_min']:g} dB needed)"
        )
    words = (
        "Filling set this still aside: "
        + ("; ".join(reasons) if reasons else "it did not fit the stage")
        + ". It colours the stage but does not shape it."
    )
    return {
        "dropped": words,
        "can_keep": judged_geometry(session, library, set_id, keyframe) is not None,
    }


def judged_geometry(session, library, set_id: str, keyframe: dict) -> dict | None:
    """The still geometry of a probe still, pinned from the build that consolidated it: the build of the first version
    that holds it. None when that build or its artifact is no longer in the Library."""
    from gaiavfx.application import set_fill

    row = STORE.get(session, set_id)
    for version in sorted(row["versions"], key=lambda v: v["ordinal"]):
        if keyframe["id"] not in {k["id"] for k in STORE.keyframes(session, version["id"])}:
            continue
        # A seed's v1 names its build in the set's provenance, a repair's version in its own record.
        operation_id = version.get("operation_id") or (
            (row["provenance"] or {}).get("operation_id") if version["origin"] == "seed" else None
        )
        if not operation_id:
            return None
        try:
            pinned = set_fill.judged_still(
                OPERATIONS.operation(session, operation_id), keyframe["key"]
            )
            if pinned:
                library.artifact_path(
                    session, pinned["artifact_id"]
                )  # the file, not only its record
            return pinned
        except ApplicationError:
            return None
    return None


def _check_keep(session, library, set_id, version_id, target, fix_id=None) -> dict:
    """Keep this still (ADR 0020 §8, human only): a probe still of this version that filling set aside, kept once,
    whose geometry is still in the Library. Returns its pinned geometry."""
    members = {k["id"]: k for k in STORE.keyframes(session, version_id)}
    keyframe = members.get((target or {}).get("keyframe"))
    if keyframe is None:
        raise ApplicationError("Keep a still of the version you are looking at.", 422)
    if _set_aside(session, library, set_id, keyframe)["dropped"] is None:
        raise ApplicationError(f"{keyframe['label']} is not a still that filling set aside.", 409)
    if any(
        f["action"] == "keep"
        and f["id"] != fix_id
        and f["state"] in ("queued", "running")
        and (f["target"] or {}).get("keyframe") == keyframe["id"]
        for f in STORE.fixes(session, set_id)
    ):
        raise ApplicationError(f"Keeping {keyframe['label']} is queued already.", 409)
    geometry = judged_geometry(session, library, set_id, keyframe)
    if geometry is None:
        raise ApplicationError(
            f"The geometry filling measured for {keyframe['label']} is no longer in the Library, so it cannot be "
            "kept.",
            409,
        )
    return geometry


def queued_run(session, set_id: str, version: dict) -> dict:
    """What Run queued fixes would do on `version`, in Python's words: the fixes it runs (exclusions of its keyframes,
    kept stills and extensions, ADR 0020 §4 and §8; reshoots wait for a later step) and what that costs."""
    from gaiavfx.integrations.worldbuilder.fill import settings as fill_settings

    members = {k["id"]: k["label"] for k in STORE.keyframes(session, version["id"])}
    queued = [f for f in STORE.fixes(session, set_id) if f["state"] == "queued"]

    def of_version(action):
        return [
            f
            for f in queued
            if f["action"] == action and (f["target"] or {}).get("keyframe") in members
        ]

    exclusions, keeps = of_version("retract"), of_version("keep")
    extensions = [f for f in queued if f["action"] == "extend"]
    reshoots = [f for f in queued if f["action"] == "reshoot"]
    later = " Queued reshoots wait for a later step." if reshoots else ""
    if not exclusions and not keeps and not extensions:
        return {
            "fix_ids": [],
            "words": "Exclude a keyframe, keep a still or fill more unseen space first." + later,
        }
    without = ", ".join(members[f["target"]["keyframe"]] for f in exclusions)
    kept = ", ".join(members[f["target"]["keyframe"]] for f in keeps)
    rounds = fill_settings()["rounds"]
    words = f"Rebuilds v{version['ordinal']}" + (f" without {without}" if exclusions else "")
    words += f" keeping {kept}" if keeps else ""
    words += (
        f" and fills in what no view has seen around it: up to {rounds['rounds']} rounds and "
        f"{rounds['renders']} renders, about 15 to 25 minutes on the GPU"
        if extensions
        else ", about 4 minutes on the GPU"
    )
    next_ordinal = max(v["ordinal"] for v in STORE.get(session, set_id)["versions"]) + 1
    words += (
        f", plus any wait for it. You get a proposed v{next_ordinal} to compare, then accept or discard. Nothing "
        "is deleted." + later
    )
    return {"fix_ids": [f["id"] for f in exclusions + keeps + extensions], "words": words}


def build_view(session, set_id: str) -> dict | None:
    """The set's newest build (ADR 0018 §4) as the workbench shows it: its state and stage, its progress over both
    jobs, whether its GPU stage still waits for the card, the version it wrote and why it stopped. None before the
    first build, and again once its fixes are queued anew."""
    fixes = [f for f in STORE.fixes(session, set_id) if f["operation_id"]]
    if not fixes:
        return None
    operation = OPERATIONS.operation(
        session, max(fixes, key=lambda f: f["updated_at"])["operation_id"]
    )
    jobs = operation["jobs"]
    current = next((j for j in jobs if j["state"] != "succeeded"), jobs[-1] if jobs else None)

    def done(job) -> float:
        if job["state"] == "succeeded":
            return 1.0
        return float(next((o.get("progress", 0) for o in job["outputs"] if "progress" in o), 0))

    decision = next(
        (d for d in reversed(operation.get("review") or []) if d.get("stage") == "done"), {}
    )
    error = next((j["error"] for j in jobs if j["state"] in BUILD_STOPPED and j["error"]), None)
    # an engine's error ends with the worker's own last line (its exception), after the log's header
    error = (
        next((line for line in reversed(error.splitlines()) if line.strip()), error)
        if error
        else None
    )
    snapshot = (current or {}).get("snapshot") or {}
    settled = operation["state"] in OPERATION_TERMINAL
    stopped = next(
        (d for d in reversed(operation.get("review") or []) if d.get("stage") == "stopped"), {}
    )
    stage = snapshot.get("stage", {}).get("name")
    filling = None
    if operation["request"].get("fill") and stage not in (None, "prepare", "splat") and not settled:
        from gaiavfx.application import set_fill

        filling = set_fill.progress(operation)
    return {
        "operation_id": operation["id"],
        "state": operation["state"],
        "base_version_id": operation["request"].get("base_version_id"),
        "stage": stage,
        # Python's words while the build fills its unseen space (the jobs grow with every round, so no percentage)
        "filling": filling,
        # a GPU job reports no progress until it holds the card (the lease, ComfyUI freed)
        "waiting": bool(
            current and not settled and snapshot.get("engine", {}).get("gpu") and not done(current)
        ),
        "cancelling": bool(operation["cancel_requested"]) and not settled,
        "progress": round(sum(done(j) for j in jobs) / max(len(jobs), 1), 3),
        "version_id": decision.get("version_id"),
        # a probe that failed is a rejected probe, not the build's error
        "error": clip_words(error, 400) if error and not decision else None,
        "reason": stopped.get("reason"),
        "fix_ids": operation["request"].get("fix_ids", []),
    }


def _views_with_angles(session, views: list[dict]) -> list[dict]:
    """Each saved view with the Direction angle it is the camera of (ADR 0019 §2), named as Direct names it."""
    for view in views:
        angle = DIRECTION.read_angle(session, view["angle_id"]) if view.get("angle_id") else None
        view["angle"] = (
            {
                "id": angle["id"],
                "name": angle["name"],
                "place_name": DIRECTION.read_place(session, angle["place_id"])["name"],
            }
            if angle
            else None
        )
    return views


def version_map(session, library, set_id: str, version_id: str) -> dict:
    """The eye-level map for drawing: the plane (int8: 0 unknown, 1 free, 2 occupied, indexed [x, z]) and the
    frontier (bit-packed, row-major), base64, with the version's map metadata."""
    row = STORE.get(session, set_id)
    version = _shown_version(row, version_id)
    if not version["map_artifact_id"]:
        raise ApplicationError("This version has no map.", 404)
    arrays = np.load(
        io.BytesIO(library.read_blob(session, version["map_artifact_id"])), allow_pickle=False
    )
    plane = np.ascontiguousarray(arrays["plane"].astype(np.int8))
    frontier = np.packbits(np.ascontiguousarray(arrays["frontier"].astype(bool)))
    return {
        "version_id": version["id"],
        "shape": list(plane.shape),
        "plane": base64.b64encode(plane.tobytes()).decode("ascii"),
        "frontier": base64.b64encode(frontier.tobytes()).decode("ascii"),
        "meta": version["map_meta"],
    }


def splat_path(session, library, set_id: str, artifact_id: str) -> Path:
    row = STORE.get(session, set_id)
    if artifact_id not in {v["splat_artifact_id"] for v in row["versions"]}:
        raise ApplicationError("This splat is not part of the stage.", 404)
    return library.artifact_path(session, artifact_id)


def mark_issue(session, library, set_id, *, version_id, kind, why, cells, cameras, region, note):
    STORE.add_issue(
        session,
        set_id,
        version_id=version_id,
        kind=kind,
        author="human",
        why=why,
        cells=cells,
        cameras=cameras,
        region=region,
        note=note,
    )
    return set_state(session, library, set_id, version_id)


def set_issue_status(session, library, set_id, issue_id, *, status, expected_revision, version_id):
    STORE.set_issue_status(session, set_id, issue_id, status, expected_revision=expected_revision)
    return set_state(session, library, set_id, version_id)


def _check_exclusion(session, set_id, version_id, target) -> None:
    """An exclusion names one keyframe of the version it was queued in, never the panorama, and only once."""
    members = {k["id"]: k for k in STORE.keyframes(session, version_id)}
    keyframe = members.get((target or {}).get("keyframe"))
    if keyframe is None:
        raise ApplicationError("Exclude a keyframe of the version you are looking at.", 422)
    if keyframe["kind"] == "erp":
        raise ApplicationError(
            "The panorama is the stage's ground truth; it cannot be excluded.", 422
        )
    if keyframe["role"] == "appearance-only":
        raise ApplicationError(f"{keyframe['label']} is colour only already.", 409)
    if any(
        f["action"] == "retract"
        and f["state"] in ("queued", "running")
        and (f["target"] or {}).get("keyframe") == keyframe["id"]
        for f in STORE.fixes(session, set_id)
    ):
        raise ApplicationError(f"The exclusion of {keyframe['label']} is queued already.", 409)


FILL_MORE = "What no view of the stage has seen yet, around it."


def fill_more(session, library, set_id, *, version_id):
    """Fill more unseen space (ADR 0020 §4): queue an extension that fills all round, which Run queued fixes runs
    with any exclusions into one proposed version. One at a time: another is refused while one waits."""
    if any(
        f["state"] in ("queued", "running") and f["action"] == "extend"
        for f in STORE.fixes(session, set_id)
    ):
        raise ApplicationError("Filling more is queued already; run it with Run queued fixes.", 409)
    return queue_fix(
        session,
        library,
        set_id,
        version_id=version_id,
        action="extend",
        target={},
        shows=FILL_MORE,
        issue_id=None,
    )


def queue_fix(session, library, set_id, *, version_id, action, target, shows, issue_id):
    if action == "retract":
        _check_exclusion(session, set_id, version_id, target)
    if action == "keep":
        _check_keep(session, library, set_id, version_id, target)
    STORE.queue_fix(
        session,
        set_id,
        action=action,
        author="human",
        target=target,
        shows=shows,
        issue_id=issue_id,
    )
    return set_state(session, library, set_id, version_id)


def cancel_fix(session, library, set_id, fix_id, *, version_id):
    STORE.cancel_fix(session, set_id, fix_id)
    return set_state(session, library, set_id, version_id)


def retry_fix(session, library, set_id, fix_id, *, version_id):
    STORE.retry_fix(session, set_id, fix_id)
    return set_state(session, library, set_id, version_id)


def hide_region(session, library, set_id, *, version_id, shape, params, label):
    STORE.add_hide_region(
        session, set_id, version_id=version_id, shape=shape, params=params, label=label
    )
    return set_state(session, library, set_id, version_id)


def set_hide_region_removed(session, library, set_id, region_id, *, removed, version_id):
    STORE.set_hide_region_removed(session, set_id, region_id, removed)
    return set_state(session, library, set_id, version_id)


def save_view(session, library, set_id, *, version_id, name, viewmat, fov, width, height):
    if rigid(viewmat) is None:
        raise ApplicationError("The view's camera is not a rigid camera pose.", 422)
    STORE.save_view(
        session,
        set_id,
        version_id=version_id,
        name=name,
        viewmat=viewmat,
        fov=fov,
        width=width,
        height=height,
        author="human",
    )
    return set_state(session, library, set_id, version_id)


def prepare_view_still(library, source: Path):
    """Check a saved view's picture (a PNG the workbench drew) and copy it into Library, outside a transaction."""
    try:
        with Image.open(source) as image:
            if image.format != "PNG" or not (
                64 <= image.width <= 4096 and 64 <= image.height <= 4096
            ):
                raise ApplicationError(
                    "A view's picture is a PNG between 64 and 4096 pixels a side.", 422
                )
    except OSError as error:
        raise ApplicationError(
            "A view's picture is a PNG between 64 and 4096 pixels a side.", 422
        ) from error
    return library.prepare(source, "set view.png")


def attach_view_still(session, library, set_id, view_id, prepared):
    state = set_state(session, library, set_id)
    view = next((v for v in state["views"] if v["id"] == view_id), None)
    if view is None:
        raise ApplicationError("This view is not part of the stage.", 404)
    asset = library.register(
        session,
        prepared,
        {
            "type": "set_view",
            "set_id": set_id,
            "view_id": view_id,
            "version_id": view["version_id"],
            "viewmat": view["viewmat"],
            "fov": view["fov"],
            "hide_region_ids": view["hide_region_ids"],
            "renderer": "workbench",
        },
    )
    STORE.attach_view_still(session, set_id, view_id, asset["id"])
    return set_state(session, library, set_id, view["version_id"])


def aim_view(session, library, set_id, view_id, *, angle_id, version_id):
    """This saved view is the camera of a place's angle (ADR 0019 §2), or of none. The place must be built as this
    set. The picture a shot uses is rendered from it later and reviewed in Prepare; aiming approves nothing."""
    if angle_id is not None:
        angle = DIRECTION.read_angle(session, angle_id)
        place = DIRECTION.read_place(session, angle["place_id"])
        if place["set_id"] != set_id:
            raise ApplicationError(f"{place['name']} is not built as this stage.", 409)
    STORE.aim_view(session, set_id, view_id, angle_id)
    return set_state(session, library, set_id, version_id)


def places_built_as(session, set_id) -> list[dict]:
    """The Direction places built as this set, with their angles: where its views can be aimed."""
    return [
        {**place, "angles": DIRECTION.angles(session, place["id"])}
        for place in DIRECTION.places(session)
        if place["set_id"] == set_id
    ]


def set_view_removed(session, library, set_id, view_id, *, removed, version_id):
    STORE.set_view_removed(session, set_id, view_id, removed)
    return set_state(session, library, set_id, version_id)


def link_place(session, place_id, *, expected_revision, set_id):
    """A Direction place built as this set (ADR 0019 §1), or no longer. The set must exist; its views reach the
    place only through preparation and its review."""
    if set_id is not None:
        STORE.get(session, set_id)
    return DIRECTION.set_place_set(session, place_id, expected_revision, set_id=set_id)


def accept_version(session, library, set_id, version_id, *, expected_current_id, looks_fixed=()):
    """The human accepts a version after reviewing it; `looks_fixed` names the issues its fixes addressed that the
    human saw fixed (the rest are open again)."""
    STORE.settle_issues(session, set_id, version_id, accepted=True, looks_fixed=looks_fixed)
    STORE.accept(session, set_id, version_id, expected_current_id=expected_current_id)
    return set_state(session, library, set_id, version_id)


def discard_version(session, library, set_id, version_id):
    STORE.discard(session, set_id, version_id)
    STORE.settle_issues(session, set_id, version_id, accepted=False)
    return set_state(session, library, set_id, None)
