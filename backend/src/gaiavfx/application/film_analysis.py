"""Film analysis as immutable Library evidence, orchestrated by Generation jobs."""

import io
import json
from importlib import resources

import numpy as np

from gaiavfx.application.recast_steps import scan_frames, validate_faces
from gaiavfx.integrations import person_analysis
from gaiavfx.integrations.person_analysis.face_scan import face_scan_graph
from gaiavfx.modules.direction.film import parse_film_policy
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError

operations = OperationService()
NAME = "film_analysis"


def start_film_analysis(session, library, asset_id: str, *, request_key: str) -> dict:
    from gaiavfx.application.operations import PRODUCERS, create_operation

    request = {"source_asset": asset_id}
    existing = operations.for_request(session, request_key)
    if existing:
        if (
            existing["kind"] != NAME
            or operations.request_snapshot(session, existing["id"]) != request
        ):
            raise ApplicationError(
                "Request key already belongs to a different operation request.", 409
            )
        return existing | {"_created": False}
    asset = library.get(session, asset_id)
    if asset["kind"] != "video":
        raise ApplicationError("Film analysis requires a Library video.", 422)
    policy = (
        resources.files("gaiavfx.modules.direction").joinpath("policy/film.toml").read_text("utf-8")
    )
    params = parse_film_policy(policy)
    params["versions"] = {
        "detector": "adaptive-numpy-2",
        "cast": "average-linkage-2",
        "embedding": "buffalo_l/w600k_r50",
        "numpy": np.__version__,
    }
    spec = {"source_asset": asset_id, "params": params}
    return create_operation(
        session,
        operations,
        PRODUCERS,
        kind=NAME,
        request=request,
        budget_limit=None,
        budget=0,
        request_key=request_key,
        awaits_review=True,
        jobs=[
            JobSpec(
                "analysis",
                "local",
                person_analysis.NAME,
                step="film_shots",
                inputs={asset_id: {"sha256": asset["sha256"]}},
                snapshot={"step_spec": spec, "knowledge": {"film.toml": policy}},
            )
        ],
    )


def _output(job, key):
    matches = [d for d in job["outputs"] if d.get("output_key") == key and d.get("artifact_id")]
    if len(matches) != 1:
        raise ApplicationError(f"Missing unique film analysis artifact: {key}")
    return matches[0]


def advance_film_analysis(session, operation, *, library):
    """Freeze chunk jobs after shots, then merge only successfully collected chunks."""
    jobs = operation["jobs"]
    if not jobs:
        raise ApplicationError("Film analysis has no stage to advance.", 409)
    if any(job["state"] != "succeeded" for job in jobs):
        if any(
            job["state"] not in {"failed", "cancelled", "needs_attention"}
            for job in jobs
            if job["state"] != "succeeded"
        ):
            raise ApplicationError("Film analysis cannot advance an incomplete stage.", 409)
        # A stopped stage closes the gate; the operation settles from its jobs (failed or
        # partial, never succeeded), so the recast shows the failure and offers a retry.
        return operations.resume(
            session, operation["id"], {"decided_by": NAME, "stage": "stopped"}, jobs=[]
        )
    shots_output = _output(jobs[0], "shots")
    shots_id = shots_output["artifact_id"]
    shots = json.loads(library.read_blob(session, shots_id))
    spec = {
        **jobs[0]["snapshot"]["step_spec"],
        "film": {key: shots[key] for key in ("width", "height", "fps")},
    }
    source = spec["source_asset"]
    source_input = jobs[0]["inputs"][source]
    knowledge = jobs[0]["snapshot"]["knowledge"]
    appended = []
    if len(jobs) == 1:
        frames = scan_frames(shots["shots"], spec["params"]["stride"])
        chunk_size = spec["params"]["scan_chunk_frames"]
        # Alternate preparation and inference, so cancellation/other jobs get a lease boundary.
        for chunk, start in enumerate(range(0, len(frames), chunk_size)):
            selected = frames[start : start + chunk_size]
            ordinal = 1 + len(appended)
            remote = f"gaiavfx/{operation['id']}/{chunk}/proxy.mp4"
            appended.extend(
                [
                    JobSpec(
                        "analysis",
                        "local",
                        person_analysis.NAME,
                        step="film_proxy",
                        depends_on=ordinal - 1,
                        inputs={source: source_input},
                        snapshot={
                            "step_spec": {**spec, "frames": selected},
                            "resumable_local": True,
                        },
                    ),
                    JobSpec(
                        "analysis",
                        "comfy",
                        person_analysis.NAME,
                        depends_on=ordinal,
                        inputs={
                            "proxy": {
                                "source": "job_artifact",
                                "output_key": "proxy",
                                "remote_name": remote,
                            }
                        },
                        graph=face_scan_graph(remote, range(len(selected))),
                        snapshot={"knowledge": knowledge, "stage": "faces"},
                    ),
                ]
            )
        return operations.resume(
            session,
            operation["id"],
            {"decided_by": NAME, "stage": "faces"},
            jobs=appended,
            awaits_review=True,
        )
    if len(jobs) % 2 != 1 or any(jobs[i]["step"] != "film_proxy" for i in range(1, len(jobs), 2)):
        raise ApplicationError("Unexpected film analysis stage.", 409)
    inputs = {source: source_input}

    def artifact_input(output):
        key = output["artifact_id"]
        inputs[key] = {"source": "artifact", "sha256": output["sha256"], "directory": "input"}
        return key

    chunks = [
        {
            "map": artifact_input(_output(jobs[i], "map")),
            "faces": artifact_input(_output(jobs[i + 1], "faces:reports:0")),
        }
        for i in range(1, len(jobs), 2)
    ]
    artifact_input(shots_output)
    return operations.resume(
        session,
        operation["id"],
        {"decided_by": NAME, "stage": "cast"},
        jobs=[
            JobSpec(
                "analysis",
                "local",
                person_analysis.NAME,
                step="film_cast",
                depends_on=len(jobs) - 1,
                inputs=inputs,
                snapshot={
                    "step_spec": {**spec, "shots_artifact": shots_id, "chunks": chunks},
                    "resumable_local": True,
                },
            ),
        ],
    )


def _operation(session, operation_id):
    operation = operations.operation(session, operation_id)
    if operation["kind"] != NAME:
        raise ApplicationError("Operation is not a film analysis.", 422)
    return operation


def film_analysis_report(session, library, operation_id: str) -> dict | None:
    operation = _operation(session, operation_id)
    if operation["state"] != "succeeded":
        return None
    shots = json.loads(
        library.read_blob(session, _output(operation["jobs"][0], "shots")["artifact_id"])
    )
    cast = json.loads(
        library.read_blob(session, _output(operation["jobs"][-1], "cast")["artifact_id"])
    )
    return {**shots, **cast}


def film_faces(session, library, operation_id: str) -> dict[str, np.ndarray]:
    operation = _operation(session, operation_id)
    if operation["state"] != "succeeded":
        raise ApplicationError("Film face scan is not complete.", 409)
    artifact = _output(operation["jobs"][-1], "faces")["artifact_id"]
    with np.load(io.BytesIO(library.read_blob(session, artifact)), allow_pickle=False) as data:
        faces = {key: data[key] for key in data.files}
    shots = json.loads(
        library.read_blob(session, _output(operation["jobs"][0], "shots")["artifact_id"])
    )
    validate_faces(faces, shots["frame_count"])
    return faces
