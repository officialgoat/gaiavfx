"""Freeze user selections and assemble a movie as a durable, local operation."""

import shutil

from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.editing.selections.service import SelectionService
from gaiavfx.modules.editing.selections.staleness import changed_since_take
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError


def movie_export_state(session, library, movie_id):
    movie = DirectionService().read_movie(session, movie_id)
    subjects = {s["id"]: s["revision"] for s in movie["subjects"]}
    segments = []
    for segment in movie["segments"]:
        selected = SelectionService().current(session, movie_id, segment["id"])
        # A provisional agent pick is never an editorial selection for export.
        selected = selected if selected and selected["authority"] == "user" else None
        reasons = []
        if selected:
            # Inspect only this user-selected take, never selection history or
            # unselected candidates. Editing owns the staleness rule.
            operations = OperationService()
            candidate = operations.candidate(session, selected["candidate_id"])
            request = operations.request_snapshot(session, candidate["operation_id"])
            reasons = changed_since_take(
                request,
                {
                    "segment_revision": segment["revision"],
                    "phase_revisions": {p["id"]: p["revision"] for p in segment["phases"]},
                    "subject_revisions": {
                        s: subjects.get(s) for s in segment["content"]["subjects"]
                    },
                },
            )
        asset = library.get(session, selected["asset_id"]) if selected else None
        segments.append(
            {
                "segment_id": segment["id"],
                "ordinal": segment["ordinal"],
                "frames": segment["delivered_frames"],
                "selection_id": selected["id"] if selected else None,
                "asset_id": asset["id"] if asset else None,
                "name": asset["name"] if asset else None,
                "stale_reasons": reasons,
            }
        )
    operations = OperationService().recent(
        session, kind="export", scope={"movie_id": [movie_id]}, limit=1
    )
    latest = export_status(operations[0]) if operations else None
    return {
        "movie_id": movie_id,
        "movie_revision": movie["revision"],
        "title": movie["title"],
        "segments": segments,
        "latest": latest,
    }


def export_status(operation):
    outputs = operation["jobs"][0]["outputs"]
    output = outputs[0] if outputs else {}
    return {
        "id": operation["id"],
        "state": operation["state"],
        "progress": output.get("progress", 0),
        "asset_id": output.get("asset_id"),
        "cancel_requested": operation["cancel_requested"],
        "error": operation["error"],
    }


def start_export(session, library, movie_id, *, allow_stale=False, request_key=None):
    service = OperationService()
    request = {"movie_id": movie_id, "allow_stale": allow_stale}
    if request_key:
        existing = service.for_request(session, request_key)
        if existing:
            if existing["kind"] != "export" or existing["request"] != request:
                raise ApplicationError(
                    "Request key already belongs to a different operation request.", 409
                )
            return existing | {"_created": False}
    state = movie_export_state(session, library, movie_id)
    segments = state["segments"]
    if not segments:
        raise ApplicationError("Add a segment before exporting this movie.", 422)
    missing = [s for s in segments if s["selection_id"] is None]
    if missing:
        raise ApplicationError("Select a take for these segments: " + _segment_list(missing), 422)
    stale = [s for s in segments if s["stale_reasons"]]
    if stale and not allow_stale:
        raise ApplicationError(
            "Changed takes need confirmation: "
            + "; ".join(
                f"Segment {s['ordinal'] + 1} ({s['segment_id']}): {', '.join(s['stale_reasons'])}"
                for s in stale
            ),
            422,
        )
    inputs, items, resolutions = {}, [], set()
    for segment in segments:
        asset = library.get(session, segment["asset_id"])
        info = asset["info"]
        if asset["kind"] != "video":
            raise ApplicationError(
                "Selected takes must be videos: " + _segment_list([segment]), 422
            )
        if info.get("duration", 0) * 24 + 0.5 < segment["frames"]:
            raise ApplicationError(
                "Selected take is shorter than its segment: " + _segment_list([segment]), 422
            )
        resolution = (info.get("width"), info.get("height"))
        if any(type(size) is not int or size <= 0 for size in resolution):
            raise ApplicationError(
                "Selected take has no usable resolution: " + _segment_list([segment]), 422
            )
        resolutions.add(resolution)
        inputs[asset["id"]] = {"sha256": asset["sha256"]}
        items.append({**segment, "has_audio": info.get("has_audio", False)})
    if len(resolutions) != 1:
        raise ApplicationError(
            "Selected takes have mixed resolutions. Use takes with a common resolution.", 422
        )
    width, height = resolutions.pop()
    snapshot = {
        "movie_id": movie_id,
        "movie_revision": state["movie_revision"],
        "title": state["title"],
        "items": items,
        "width": width,
        "height": height,
        "stale_segments": [
            {"segment_id": s["segment_id"], "reasons": s["stale_reasons"]} for s in stale
        ],
    }
    return service.create(
        session,
        kind="export",
        request=request,
        request_key=request_key,
        budget_limit=None,
        jobs=[
            JobSpec(
                kind="video",
                executor="local",
                integration="movie_export",
                step="export_movie",
                inputs=inputs,
                snapshot=snapshot,
            )
        ],
    )


def _segment_list(segments):
    return ", ".join(f"Segment {s['ordinal'] + 1} ({s['segment_id']})" for s in segments)


def run_export(runner, job):
    """No candidate is created. Asset registration and success share one transaction."""
    staging = runner.db.root / "staging" / "operations" / job["id"]
    if runner._cancelled(job):
        runner._transition(job, "cancelled")
        shutil.rmtree(staging, ignore_errors=True)
        return
    if job["state"] == "claimed":
        job = runner._transition(job, "running")
    sources = runner._sources(job)
    snapshot = job["snapshot"]

    def cancelled():
        if runner.stop.is_set():
            # Let the runner retain running state for recovery on restart.
            runner.check_stopping()
        return runner._cancelled(job)

    def progress(value):
        with runner.db.transaction() as session:
            runner.service.local_progress(session, job["id"], value)

    output = staging / "movie.mp4"
    try:
        if not output.exists():
            runner.library.media.export_sequence(
                [
                    {
                        "path": sources[s["asset_id"]][0],
                        "start_us": 0,
                        "end_us": round(s["frames"] * 1_000_000 / 24),
                        "has_audio": s["has_audio"],
                    }
                    for s in snapshot["items"]
                ],
                output,
                snapshot["width"],
                snapshot["height"],
                cancelled,
                progress,
            )
        if cancelled():
            raise ApplicationError("Movie export stopped.")
        prepared = runner.library.prepare(output, snapshot["title"] + ".mp4")
        runner.check_stopping()
        with runner.db.transaction() as session:
            if not runner.service.begin_local_completion(session, job["id"]):
                runner.service.transition(session, job["id"], "cancelled")
            else:
                asset = runner.library.register(
                    session,
                    prepared,
                    {
                        "type": "movie_export",
                        "operation_id": job["operation_id"],
                        "movie_id": snapshot["movie_id"],
                        "movie_revision": snapshot["movie_revision"],
                        "selections": [
                            {
                                key: s[key]
                                for key in ("segment_id", "selection_id", "asset_id", "frames")
                            }
                            for s in snapshot["items"]
                        ],
                        "stale_segments": snapshot["stale_segments"],
                    },
                )
                runner.service.transition(
                    session,
                    job["id"],
                    "collecting",
                    outputs=[
                        {
                            "asset_id": asset["id"],
                            "progress": 1,
                        }
                    ],
                )
                runner.service.transition(session, job["id"], "succeeded")
    except Exception:
        if runner._cancelled(job):
            shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(staging, ignore_errors=True)
