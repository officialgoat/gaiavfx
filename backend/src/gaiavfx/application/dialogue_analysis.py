"""A film's dialogue analysis as shared, immutable Library evidence produced by Generation (ADR 0016 §2).

One `dialogue_analysis` operation per film audio and analysis version, found by a content key and shared by every
recast of the film; this module knows no recast. Stage 1 extracts the mix on the picture's clock, separates it into a
dialogue stem and its remainder and transcribes the dialogue with word times; stage 2 measures each transcript
segment's speaker embedding and pitch, finds whispered stretches and computes the dialogue stem's 10 ms envelope.
The engine tasks run through ADR 0015's `engine` executor; the extraction and envelope are local steps.

Nothing here starts on its own: only the human commands in `recast_lines` call `start_dialogue_analysis`.
"""

import io
import json
from collections import OrderedDict
from hashlib import sha256
from weakref import WeakKeyDictionary

import numpy as np

from gaiavfx.application.engine_jobs import engine_job
from gaiavfx.integrations.voice import spec as voice_spec
from gaiavfx.modules.generation.operations.lifecycle import OPERATION_TERMINAL, JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError

NAME = "dialogue_analysis"
DIALOGUE_VERSION = 1
SAMPLE_RATE = 48000
HOP = 480  # samples per envelope frame: 10 ms at 48 kHz
STEPS = (
    "dialogue_audio",
    "analysis.separate",
    "analysis.transcribe",
    "analysis.speakers",
    "analysis.tag",
    "dialogue_envelope",
)
STAGES = {
    "dialogue_audio": "audio",
    "analysis.separate": "separate",
    "analysis.transcribe": "transcribe",
    "analysis.speakers": "speakers",
    "analysis.tag": "speakers",
    "dialogue_envelope": "speakers",
}
STOPPED = frozenset({"failed", "cancelled", "needs_attention"})
operations = OperationService()
_CACHES = WeakKeyDictionary()


def _key(asset, attempt):
    return sha256(
        json.dumps([NAME, asset["sha256"], DIALOGUE_VERSION, attempt]).encode()
    ).hexdigest()[:12]


def latest(session, asset) -> tuple[dict | None, int]:
    """The current attempt: walk the attempts until one is not settled-failed or has no successor."""
    attempt = 1
    operation = operations.for_request(session, _key(asset, attempt))
    while operation is not None:
        if operation["state"] not in OPERATION_TERMINAL or operation["state"] == "succeeded":
            return operation, attempt
        following = operations.for_request(session, _key(asset, attempt + 1))
        if following is None:
            return operation, attempt
        operation, attempt = following, attempt + 1
    return None, attempt


def gap_for(info: dict) -> dict | None:
    """Why a film cannot have a dialogue analysis, as a typed gap; None when it can."""
    if not info.get("has_audio"):
        return {"code": "no_audio", "message": "This film has no audio track."}
    channels = int(info.get("audio_channels") or 0)
    if channels > 2:
        return {
            "code": "channels",
            "message": f"This film's audio has {channels} channels; dialogue analysis supports mono and "
            "stereo for now.",
        }
    return None


def audio_gap(asset: dict) -> dict | None:
    return gap_for(asset.get("info") or {})


def start_dialogue_analysis(session, library, asset_id: str, *, retry_failed=False) -> dict:
    """The film's shared analysis: the current attempt when there is one, a new attempt only on an explicit retry
    after a settled attempt that did not succeed. Idempotent through the content key."""
    from gaiavfx.application.operations import PRODUCERS, create_operation

    asset = library.get(session, asset_id)
    if asset["kind"] != "video":
        raise ApplicationError("Dialogue analysis requires a Library video.", 422)
    gap = audio_gap(asset)
    if gap:
        raise ApplicationError(gap["message"], 422)
    existing, attempt = latest(session, asset)
    if existing:
        if not (
            retry_failed
            and existing["state"] in OPERATION_TERMINAL
            and existing["state"] != "succeeded"
        ):
            return existing | {"_created": False}
        attempt += 1
    return create_operation(
        session,
        operations,
        PRODUCERS,
        kind=NAME,
        request={"source_asset": asset_id, "version": DIALOGUE_VERSION, "attempt": attempt},
        request_key=_key(asset, attempt),
        budget_limit=None,
        budget=0,
        awaits_review=True,
        jobs=[
            JobSpec(
                "analysis",
                "local",
                "voice",
                step="dialogue_audio",
                inputs={asset_id: {"sha256": asset["sha256"]}},
                snapshot={
                    "step_spec": {"source_asset": asset_id, "sample_rate": SAMPLE_RATE},
                    "resumable_local": True,
                },
            ),
            engine_job(
                "analysis.separate",
                params=voice_spec.settings("analysis.separate"),
                kind="analysis",
                depends_on=0,
                inputs={"audio": {"source": "job_artifact", "output_key": "mix"}},
            ),
            engine_job(
                "analysis.transcribe",
                params=voice_spec.settings("analysis.transcribe"),
                kind="analysis",
                depends_on=1,
                inputs={"audio": {"source": "job_artifact", "output_key": "dialogue"}},
            ),
        ],
    )


def _output(job, key):
    matches = [o for o in job["outputs"] if o.get("output_key") == key and o.get("artifact_id")]
    if len(matches) != 1:
        raise ApplicationError(f"Missing unique dialogue analysis artifact: {key}", 409)
    return matches[0]


def _frozen(output):
    return {
        "source": "artifact",
        "artifact_id": output["artifact_id"],
        "sha256": output["sha256"],
        "directory": "input",
    }


def advance_dialogue_analysis(session, operation, *, library):
    """After stage 1, freeze stage 2 on the settled stems and transcript; after stage 2, close the gate. A stopped
    stage closes the gate too, so the operation settles failed or partial and the Voice step offers a retry."""
    jobs = operation["jobs"]
    if not jobs:
        raise ApplicationError("Dialogue analysis has no stage to advance.", 409)
    if any(job["state"] != "succeeded" for job in jobs):
        if any(job["state"] not in STOPPED for job in jobs if job["state"] != "succeeded"):
            raise ApplicationError("Dialogue analysis cannot advance an incomplete stage.", 409)
        return operations.resume(
            session, operation["id"], {"decided_by": NAME, "stage": "stopped"}, jobs=[]
        )
    steps = tuple(job["step"] for job in jobs)
    if steps == STEPS[:3]:
        dialogue = _output(jobs[1], "dialogue")
        transcript = _output(jobs[2], "transcript")
        return operations.resume(
            session,
            operation["id"],
            {"decided_by": NAME, "stage": "speakers"},
            jobs=[
                engine_job(
                    "analysis.speakers",
                    params=voice_spec.settings("analysis.speakers"),
                    kind="analysis",
                    inputs={"audio": _frozen(dialogue), "segments": _frozen(transcript)},
                ),
                engine_job(
                    "analysis.tag",
                    params=voice_spec.settings("analysis.tag"),
                    kind="analysis",
                    inputs={"audio": _frozen(dialogue)},
                ),
                JobSpec(
                    "analysis",
                    "local",
                    "voice",
                    step="dialogue_envelope",
                    inputs={"dialogue": _frozen(dialogue)},
                    snapshot={
                        "step_spec": {"sample_rate": SAMPLE_RATE, "hop": HOP},
                        "resumable_local": True,
                    },
                ),
            ],
            awaits_review=True,
        )
    if steps == STEPS:
        return operations.resume(
            session, operation["id"], {"decided_by": NAME, "stage": "done"}, jobs=[]
        )
    raise ApplicationError("Unexpected dialogue analysis stage.", 409)


def _token(operation):
    return sha256(
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


def stem_artifact(operation) -> dict:
    """{"artifact_id", "sha256"} of a succeeded analysis's dialogue stem, on the picture's clock (the voice audition
    cuts its lines from it at their integer samples); 409 before the analysis succeeded."""
    if operation is None or operation.get("kind") != NAME or operation["state"] != "succeeded":
        raise ApplicationError("The dialogue analysis has not finished.", 409)
    separate = [job for job in operation["jobs"] if job["step"] == "analysis.separate"]
    if len(separate) != 1:
        raise ApplicationError("Missing unique dialogue analysis artifact: dialogue", 409)
    output = _output(separate[0], "dialogue")
    return {"artifact_id": output["artifact_id"], "sha256": output["sha256"]}


def segment_key(index: int, start: float) -> str:
    """Unique inside one analysis and deterministic for one transcript artifact."""
    return f"{index:05d}-{round(start * 1000)}"


def _json(library, session, job, key):
    try:
        return json.loads(library.read_blob(session, _output(job, key)["artifact_id"]))
    except (ValueError, TypeError) as exc:
        raise ApplicationError(f"Invalid dialogue analysis report: {key}", 409) from exc


def dialogue_report(session, library, operation_id: str) -> dict | None:
    """The parsed analysis of a succeeded operation; None before it succeeds. Read-only: the result is cached per
    operation token (an envelope of a two-hour film holds about 720 000 values), so callers must not change it."""
    operation = operations.operation(session, operation_id)
    if operation["kind"] != NAME:
        raise ApplicationError("Operation is not a dialogue analysis.", 422)
    if operation["state"] != "succeeded":
        return None
    reports = _CACHES.setdefault(library, OrderedDict())
    token = _token(operation)
    if token in reports:
        reports.move_to_end(token)
        return reports[token]
    jobs = {job["step"]: job for job in operation["jobs"]}
    mix = library.artifact(session, _output(jobs["dialogue_audio"], "mix")["artifact_id"])["info"]
    transcript = _json(library, session, jobs["analysis.transcribe"], "transcript")
    speakers = _json(library, session, jobs["analysis.speakers"], "speakers")
    spans = _json(library, session, jobs["analysis.tag"], "spans")
    rows = transcript.get("segments") or []
    items = speakers.get("items") or []
    if len(items) != len(rows):
        raise ApplicationError(
            "The speaker measurements do not match the transcript; retry the dialogue analysis.",
            409,
        )
    envelope_id = _output(jobs["dialogue_envelope"], "envelope")["artifact_id"]
    with np.load(io.BytesIO(library.read_blob(session, envelope_id)), allow_pickle=False) as data:
        db = data["db"].astype(np.float64).tolist()
        hop = int(data["hop"])
    segments = []
    by_key = {}
    for index, (row, item) in enumerate(zip(rows, items, strict=True)):
        key = segment_key(index, float(row["start"]))
        segments.append(
            {
                "key": key,
                "index": index,
                "start": float(row["start"]),
                "end": float(row["end"]),
                "text": str(row.get("text") or ""),
                "words": [
                    {"start": float(w["start"]), "end": float(w["end"]), "word": str(w["word"])}
                    for w in row.get("words") or []
                ],
            }
        )
        by_key[key] = {"ecapa": item.get("ecapa"), "median_hz": item.get("median_hz")}
    report = {
        "version": operation["request"].get("version", DIALOGUE_VERSION),
        "sample_rate": int(mix.get("sample_rate", SAMPLE_RATE)),
        "offset_samples": int(mix.get("offset_samples", 0)),
        "samples": int(mix.get("samples", 0)),
        "channels": int(mix.get("channels", 0)),
        "segments": segments,
        "speakers": by_key,
        "whisper_spans": [[float(a), float(b)] for a, b in spans.get("audio") or []],
        "envelope": {"hop": hop, "db": db},
    }
    reports[token] = report
    while len(reports) > 2:
        reports.popitem(last=False)
    return report


def _sentence(operation, stage):
    """One actionable sentence for the Voice step: never a job id, path or worker output (as `_conform_error`)."""
    if operation["state"] == "cancelled" or operation["cancel_requested"]:
        return "The dialogue analysis was cancelled. Retry to run it again."
    errors = " ".join(
        str(j.get("error") or "") for j in operation["jobs"] if j["state"] in STOPPED
    ).lower()
    errors += " " + str(operation.get("error") or "").lower()
    if "channels" in errors:
        return "This film's audio has more than two channels; dialogue analysis supports mono and stereo for now."
    if "not installed" in errors or "engines root" in errors or "engine worker" in errors:
        return "The voice engines are not set up. Run the voice engine setup, then retry."
    if "out of memory" in errors:
        return "The GPU ran out of memory during the dialogue analysis. Close other GPU work, then retry."
    step = {
        "audio": "reading the film's audio",
        "separate": "separating the dialogue",
        "transcribe": "transcribing",
        "speakers": "measuring the speakers",
    }.get(stage, "analysis")
    return f"The dialogue analysis failed while {step}. Retry; if it fails again, the app log has the details."


def dialogue_view(operation) -> dict:
    """Progress for the Voice step: state, stage, jobs done of the six, and a blind-safe error sentence."""
    if operation is None:
        return {"state": "not_started", "stage": None, "done": 0, "total": 0, "error": None}
    jobs = operation["jobs"]
    first = next((j for j in jobs if j["state"] != "succeeded"), None)
    if first is not None:
        stage = STAGES.get(first["step"], "speakers")
    elif operation["state"] == "succeeded":
        stage = "done"
    else:
        stage = STAGES.get(STEPS[min(len(jobs), len(STEPS) - 1)], "speakers")
    if operation["state"] == "succeeded":
        state = "succeeded"
    elif operation["state"] in OPERATION_TERMINAL:
        state = "failed"
    else:
        state = "running"
    return {
        "state": state,
        "stage": stage,
        "done": sum(j["state"] == "succeeded" for j in jobs),
        "total": len(STEPS),
        "error": _sentence(operation, stage) if state == "failed" else None,
    }
