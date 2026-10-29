"""Human-only frozen film conformance and local Library publication."""

import hashlib
import json
import shutil
import time
from fractions import Fraction

from gaiavfx.application import recast_planning as planning
from gaiavfx.application import recast_projection as p
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.shared.errors import ApplicationError


def _span(range, report):
    fps = Fraction(*report["fps"])

    def clock(frame):
        seconds = int(frame / fps)
        return f"{seconds // 3600:02d}.{seconds // 60 % 60:02d}.{seconds % 60:02d}"

    return f"{clock(range['start'])}-{clock(range['end'])}"


def _conform_error(op):
    """Why a conform failed, for the blind film page (Astra R5): an operation's error carries a job id and can carry
    FFmpeg output with file names and paths, so only an actionable sentence leaves the service."""
    if op["state"] != "failed":
        return None
    error = (op.get("error") or "").lower()
    if "missing" in error or "not found" in error or "no such file" in error:
        return (
            "A file the conform needs is missing. Check that the film and its versions are still in the "
            "Library, then conform again."
        )
    return "Encoding failed. Conform again; if it fails again, the app log has the details."


def conform_states(session, recast_id):
    result = []
    for op in p.operations.recent(session, kind="recast_conform", scope={"recast_id": [recast_id]}):
        outputs = op["jobs"][0]["outputs"]
        output = outputs[0] if outputs else {}
        result.append(
            {
                "operation_id": op["id"],
                "state": op["state"],
                "mode": op["request"]["mode"],
                "range": op["request"]["range"],
                "progress": output.get("progress", 0),
                "asset_id": output.get("asset_id"),
                "created_at": op["created_at"],
                # Usability round 1 (B15): rows tell conforms apart by codec, and a failure says why.
                "codec": op["request"].get("codec", "h264"),
                "error": _conform_error(op),
            }
        )
    return result


def start_conform(session, library, recast_id, *, mode, range, codec, request_key):
    from gaiavfx.application.recast import _load, _range

    intent = {"recast_id": recast_id, "mode": mode, "range": range, "codec": codec}
    existing = p.operations.for_request(session, request_key)
    if existing:
        if existing["kind"] != "recast_conform" or any(
            existing["request"].get(k) != v for k, v in intent.items()
        ):
            raise ApplicationError("Request key belongs to a different conform request.", 409)
        return existing | {"_created": False}
    if mode not in {"chosen", "preview_unreviewed"} or codec not in {"h264", "prores"}:
        raise ApplicationError("Unknown conform mode or codec.", 422)
    recast, report = _load(session, library, recast_id)
    if not report:
        raise ApplicationError("Finish film analysis before conforming.", 409)
    _range(range, report)
    source = library.get(session, recast["source_asset_id"])
    inputs = {source["id"]: {"sha256": source["sha256"]}}
    latest = {c["shot_id"]: c["version"] for c in recast["choices"]}
    mapped = set(planning.mapped(recast))
    choices, patches = [], []
    for shot in recast["shots"]:
        film_shot = next(s for s in report["shots"] if p.shot_id(s) == shot["shot_id"])
        if range and (film_shot["end"] <= range["start"] or film_shot["start"] >= range["end"]):
            continue
        selected = latest.get(shot["shot_id"])
        if selected == "original":
            continue
        versions = p.versions(session, library, shot)
        if selected in {None, "unchosen"}:
            # A preview never pastes drafts into shots the user excluded or unmapped.
            performers = set((shot["plan"] or {}).get("performers", []))
            if (
                mode != "preview_unreviewed"
                or not versions
                or shot["included"] is False
                or not performers & mapped
            ):
                continue
            # Projection orders assemblies by creation, then takes within each assembly.
            version = versions[-1]
        else:
            version = next((v for v in versions if v["id"] == selected), None)
            if version is None:
                raise ApplicationError(
                    "A chosen shot version is missing; choose again before conforming.", 409
                )
        artifact = library.artifact(session, version["patch_artifact"])
        if artifact["kind"] != "replacement_patch":
            raise ApplicationError("Shot version has no replacement patch.", 409)
        inputs[artifact["id"]] = {
            "source": "artifact",
            "sha256": artifact["sha256"],
            "directory": "input",
        }
        choices.append(
            {"shot_id": shot["shot_id"], "version": version["id"], "patch_artifact": artifact["id"]}
        )
        patches.append(
            {
                "artifact_id": artifact["id"],
                "start": film_shot["start"],
                "end": film_shot["end"],
                "source_asset": source["id"],
            }
        )
    if not choices:
        # Usability round 2 (C12): a chosen conform with no choice would be the original film, so the refusal says
        # so and points to Review and to the unreviewed preview.
        raise ApplicationError(
            "Nothing to conform: no shot"
            + (" in this range" if range else "")
            + " has a chosen version, so the film would be the original. Choose versions in Review, "
            "or conform a preview with unreviewed drafts."
            if mode == "chosen"
            else "Nothing to preview: no shot in this range has a version yet.",
            409,
        )
    provenance = {
        "type": "recast_conform",
        "recast_id": recast_id,
        "recast_revision": recast["revision"],
        "mode": mode,
        "choices": choices,
        "source_asset": source["id"],
        "range": range,
        "audio": "pcm_trimmed" if range is not None else "copied",
        # Every frame is re-encoded; only pixels inside patches change before encoding.
        "video": {"h264": "reencoded_h264_crf14", "prores": "reencoded_prores_hq"}[codec],
    }
    snapshot = {
        "provenance": provenance,
        "patches": patches,
        "codec": codec,
        # The name tells conforms apart in the Library: the excerpt's times and the codec (usability round 1, B15).
        "title": recast["title"]
        + (" - unreviewed preview" if mode == "preview_unreviewed" else " - conformed")
        + (f" - {_span(range, report)}" if range is not None else "")
        + (" - ProRes" if codec == "prores" else ""),
        "resumable_local": True,
    }
    return p.operations.create(
        session,
        kind="recast_conform",
        request={**intent, **provenance},
        request_key=request_key,
        budget_limit=None,
        budget=0,
        jobs=[
            JobSpec(
                kind="video",
                executor="local",
                integration="recast_conform",
                step="recast_conform",
                inputs=inputs,
                snapshot=snapshot,
            )
        ],
    )


def run_conform(runner, job):
    if runner._cancelled(job):
        runner._transition(job, "cancelled")
        return
    if job["state"] == "claimed":
        job = runner._transition(job, "running")
    sources = runner._sources(job)
    snapshot = job["snapshot"]
    provenance = snapshot["provenance"]
    # A new human request after cancellation can reuse completed parts when the
    # frozen choices and bytes are identical. Request-key replay itself stays inert.
    identity = hashlib.sha256(
        json.dumps({"snapshot": snapshot, "inputs": job["inputs"]}, sort_keys=True).encode()
    ).hexdigest()
    output = runner.db.root / "staging" / "recast-conform" / identity / "film.mkv"

    checked = {"at": 0.0, "value": False}

    def cancelled():
        runner.check_stopping()
        # The media step asks per frame; read the operation at most twice a second.
        now = time.monotonic()
        if not checked["value"] and now - checked["at"] >= 0.5:
            checked.update(at=now, value=runner._cancelled(job))
        return checked["value"]

    def progress(value):
        # Running progress stays below 1; completion is the job's success.
        with runner.db.transaction() as session:
            runner.service.local_progress(session, job["id"], min(value, 0.999))

    if not output.exists():
        runner.library.media.conform_patches(
            sources[provenance["source_asset"]][0],
            [{**patch, "path": sources[patch["artifact_id"]][0]} for patch in snapshot["patches"]],
            output,
            codec=snapshot["codec"],
            cancelled=cancelled,
            progress=progress,
            frame_range=provenance["range"],
        )
    if runner._cancelled(job):
        raise ApplicationError("Conform cancelled.")
    prepared = runner.library.prepare(output, snapshot["title"] + ".mkv")
    runner.check_stopping()
    with runner.db.transaction() as session:
        if not runner.service.begin_local_completion(session, job["id"]):
            runner.service.transition(session, job["id"], "cancelled")
            return
        asset = runner.library.register(
            session, prepared, {**provenance, "operation_id": job["operation_id"]}
        )
        runner.service.transition(
            session, job["id"], "collecting", outputs=[{"asset_id": asset["id"], "progress": 1}]
        )
        runner.service.transition(session, job["id"], "succeeded")
    shutil.rmtree(output.parent, ignore_errors=True)
