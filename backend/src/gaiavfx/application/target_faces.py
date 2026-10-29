"""Durable CPU face reports per Library picture, with explicit retry attempts."""

import io
import json
from collections import OrderedDict
from copy import deepcopy
from dataclasses import asdict
from functools import lru_cache
from hashlib import sha256
from weakref import WeakKeyDictionary
from zipfile import BadZipFile

import numpy as np
from PIL import Image, ImageOps

from gaiavfx.infrastructure.database import new_id
from gaiavfx.integrations import person_analysis, qwen_image_edit
from gaiavfx.integrations.person_analysis.face_scan import face_scan_graph
from gaiavfx.integrations.qwen_image_edit.graph import build_edit_graph
from gaiavfx.integrations.qwen_image_edit.prompt import compile_portrait
from gaiavfx.modules.direction.application import replacement_policy_text
from gaiavfx.modules.direction.portrait import VIEWS, Portrait
from gaiavfx.modules.direction.replacement import parse_replacement_policy
from gaiavfx.modules.direction.target_face import classify_target_faces
from gaiavfx.modules.generation.operations.lifecycle import OPERATION_TERMINAL, JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.errors import ApplicationError

SCAN_VERSION = 1
operations = OperationService()
_CACHES = WeakKeyDictionary()


@lru_cache(maxsize=8)
def _policy(text):
    policy = parse_replacement_policy(text).target_face
    if policy is None:
        raise ApplicationError("Target face checking requires a target_face policy.", 422)
    return policy


class TargetReportRefused(ApplicationError):
    """Retain queued target evidence even when another target prevents the pass."""


class TargetReportPending(TargetReportRefused):
    """The caller must retain a queued report before returning this refusal."""

    def __init__(self):
        super().__init__("Checking the target picture; try again in a moment", 409)


def _key(asset, attempt):
    return sha256(
        json.dumps(["target_face", asset["sha256"], SCAN_VERSION, attempt]).encode()
    ).hexdigest()[:12]


def _latest(session, library, asset, policy):
    attempt = 1
    operation = operations.for_request(session, _key(asset, attempt))
    while operation is not None:
        report = _report(session, library, operation, policy)
        if operation["state"] not in OPERATION_TERMINAL or report["state"] != "failed":
            return operation, attempt, report
        following = operations.for_request(session, _key(asset, attempt + 1))
        if following is None:
            return operation, attempt, report
        operation, attempt = following, attempt + 1
    return None, attempt, None


def start_target_report(session, library, asset_id, *, retry_failed=False):
    from gaiavfx.application.operations import PRODUCERS, create_operation

    policy = _policy(replacement_policy_text())
    asset = MediaLibrary.asset_info(session, asset_id)
    if asset["kind"] != "image":
        raise ApplicationError("Target face reports require a Library image.", 422)
    existing, attempt, report = _latest(session, library, asset, policy)
    if existing:
        if not (
            retry_failed and existing["state"] in OPERATION_TERMINAL and report["state"] == "failed"
        ):
            return existing | {"_created": False}
        attempt += 1
    remote = f"gaiavfx/{new_id()}/target.png"
    return create_operation(
        session,
        operations,
        PRODUCERS,
        kind="target_face",
        request={"target_asset": asset_id, "scan_version": SCAN_VERSION, "attempt": attempt},
        request_key=_key(asset, attempt),
        budget_limit=None,
        budget=0,
        awaits_review=False,
        jobs=[
            JobSpec(
                "analysis",
                "local",
                person_analysis.NAME,
                step="target_normalize",
                inputs={asset_id: {"sha256": asset["sha256"]}},
            ),
            JobSpec(
                "analysis",
                "comfy",
                person_analysis.NAME,
                depends_on=0,
                inputs={
                    "target": {
                        "source": "job_artifact",
                        "output_key": "normalized",
                        "remote_name": remote,
                    }
                },
                graph=face_scan_graph(remote, [0]),
                snapshot={"stage": "faces", "knowledge": {"scan_version": str(SCAN_VERSION)}},
            ),
        ],
    )


def target_normalize(context):
    context.staging.mkdir(parents=True, exist_ok=True)
    path = context.staging / "target.png"
    with Image.open(next(iter(context.paths.values()))) as original:
        ImageOps.exif_transpose(original).convert("RGB").save(path, format="PNG")
    return [("normalized", path, "visual_evidence")]


def _artifact(job, key):
    matches = [
        o["artifact_id"]
        for o in job["outputs"]
        if o.get("output_key") == key and o.get("artifact_id")
    ]
    if len(matches) != 1:
        raise ValueError(f"Missing unique target face artifact: {key}")
    return matches[0]


def target_report(session, library, asset_id):
    try:
        policy = _policy(replacement_policy_text())
        asset = MediaLibrary.asset_info(session, asset_id)
        return _latest(session, library, asset, policy)[2]
    except (OSError, ApplicationError) as exc:
        return {"state": "failed", "error": str(exc)}


def _report(session, library, operation, policy):
    if operation["state"] not in OPERATION_TERMINAL:
        return {"state": "checking"}
    if operation["state"] != "succeeded":
        return {"state": "failed", "error": "The target picture could not be checked."}
    token = sha256(
        json.dumps(
            [
                operation["id"],
                operation["state"],
                operation["updated_at"],
                [(j["id"], j["state"], j["outputs"]) for j in operation["jobs"]],
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    reports = _CACHES.setdefault(library, OrderedDict())
    if token not in reports:
        try:
            jobs = operation["jobs"]
            # Filesystem/Library failures can recover without a new operation token.
            # Keep these reads outside the deterministic parser's exception boundary.
            try:
                normalized = library.read_blob(session, _artifact(jobs[0], "normalized"))
                scan = library.read_blob(session, _artifact(jobs[1], "faces:reports:0"))
            except (OSError, ApplicationError) as exc:
                return {"state": "failed", "error": str(exc)}
            with Image.open(io.BytesIO(normalized)) as image:
                size = list(image.size)
            with np.load(
                io.BytesIO(scan),
                allow_pickle=False,
            ) as data:
                scores, boxes, frames = data["score"], data["box"], data["frame"]
                count = len(scores)
                if (
                    scores.shape != (count,)
                    or boxes.shape != (count, 4)
                    or frames.shape != (count,)
                    or np.any(frames != 0)
                    or not np.isfinite(scores).all()
                    or not np.isfinite(boxes).all()
                    or np.any((scores < 0) | (scores > 1))
                    or np.any(boxes[:, 2:] <= boxes[:, :2])
                ):
                    raise ValueError("Invalid target face rows")
                faces = [
                    (float(score), tuple(float(v) for v in box))
                    for score, box in zip(scores, boxes)
                ]
            result = classify_target_faces(faces, policy) | {"size": size}
        except (
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            OSError,
            EOFError,
            BadZipFile,
        ) as exc:
            result = {"state": "failed", "error": str(exc)}
        reports[token] = deepcopy(result)
        while len(reports) > 256:
            reports.popitem(last=False)
    return deepcopy(reports[token])


def start_face_portraits(session, library, asset_id, box, *, seed, request_key, attempt=1):
    from gaiavfx.application.operations import PRODUCERS, create_operation, knowledge_snapshot

    if existing := operations.for_request(session, request_key):
        return existing | {"_created": False}
    asset = MediaLibrary.asset_info(session, asset_id)
    if asset["kind"] != "image":
        raise ApplicationError("Face portraits require a Library image.", 422)
    jobs = [
        JobSpec(
            "image",
            "local",
            qwen_image_edit.NAME,
            step="portrait_crop",
            inputs={asset_id: {"sha256": asset["sha256"]}},
            snapshot={"step_spec": {"target": asset_id, "box": list(box)}},
        )
    ]
    for i, view in enumerate(VIEWS):
        prefix = f"gaiavfx/{new_id()}"
        face, target = f"{prefix}/face.png", f"{prefix}/target.png"
        request = Portrait(asset_id, "crop", view)
        jobs.append(
            JobSpec(
                "image",
                "comfy",
                qwen_image_edit.NAME,
                depends_on=0,
                seed=seed + i,
                inputs={
                    "crop": {"source": "job_artifact", "output_key": "crop", "remote_name": face},
                    asset_id: {
                        "source": "job_artifact",
                        "output_key": "normalized",
                        "remote_name": target,
                    },
                },
                graph=build_edit_graph(
                    compile_portrait(request),
                    image_files=[face, target],
                    seed=seed + i,
                    width=1024,
                    height=1024,
                    output_prefix=f"{prefix}/portrait",
                ),
                snapshot={
                    "knowledge": knowledge_snapshot(qwen_image_edit),
                    "expect": {"kind": "image", "width": 1024, "height": 1024},
                    "request": asdict(request),
                },
            )
        )
    return create_operation(
        session,
        operations,
        PRODUCERS,
        kind="face_portraits",
        request_key=request_key,
        request={"target_asset": asset_id, "box": list(box), "seed": seed, "attempt": attempt},
        jobs=jobs,
        budget=3,
        budget_limit=None,
        awaits_review=False,
    )


def start_face_sheet(session, library, asset_id, portraits_operation, *, request_key):
    from gaiavfx.application.operations import PRODUCERS, create_operation

    if existing := operations.for_request(session, request_key):
        return existing | {"_created": False}
    portraits = operations.operation(session, portraits_operation)
    if (
        portraits["kind"] != "face_portraits"
        or portraits["state"] != "succeeded"
        or portraits["request"]["target_asset"] != asset_id
        or len(portraits["candidates"]) != 3
    ):
        raise ApplicationError("Face sheet requires three settled portraits of this target.", 409)
    candidates = {c["job_id"]: c["asset_id"] for c in portraits["candidates"]}
    tiles = {
        job["snapshot"]["request"]["view"]: candidates[job["id"]]
        for job in portraits["jobs"]
        if job["id"] in candidates
    }
    if set(tiles) != set(VIEWS):
        raise ApplicationError("Face sheet requires one portrait per view.", 409)
    tiles["target"] = asset_id
    inputs = {
        aid: {"sha256": MediaLibrary.asset_info(session, aid)["sha256"]} for aid in tiles.values()
    }
    return create_operation(
        session,
        operations,
        PRODUCERS,
        kind="face_sheet",
        request_key=request_key,
        request={
            "target_asset": asset_id,
            "portraits_operation": portraits_operation,
            "attempt": portraits["request"].get("attempt", 1),
        },
        jobs=[
            JobSpec(
                "image",
                "local",
                "minimax_h3",
                step="portrait_sheet",
                inputs=inputs,
                snapshot={
                    "step_spec": tiles
                    | {"width": 768, "height": 1024, "gap": 6, "gap_rgb": [196, 196, 196]},
                    "expect": {"kind": "image", "width": 768, "height": 1024},
                },
            )
        ],
        budget=0,
        budget_limit=None,
        awaits_review=False,
    )
