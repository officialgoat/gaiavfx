"""A character's voice as shared, immutable Library evidence produced by Generation (ADR 0016 §4, P5).

One `voice_build` operation per ordered sample set and recipe version, found by a content key over the samples'
hashes and shared by every recast whose character uses the same recordings; this module knows no recast. The build
grows 15-60 s of the target's recordings into a training set and trains an RVC voice, in seven stages that the
advancer appends one after another (`STAGES`): the recordings joined and transcribed, with the expressive recordings
library frozen beside them; the seed cut from them and the target's pitch measured; the material (the TTS
generators voice the expansion script, Seed-VC is fine-tuned on the seed, the library clips are measured); the
conversions (low-rate takes lifted, library clips and Maya1's designed takes converted into the target); the filter;
the training (prepare, eight chained chunks, the index); and the `voice_model` manifest. Every job carries
`snapshot["stage"] = {"name", "unit"}`; the newest job per stage unit is current, so a retry supersedes the job it
replaces.

A stopped job leaves the operation awaiting review (the settle rule keeps a reviewed operation open while one child
succeeded) and the advancer leaves it alone: `retry_stopped` resumes it in place, in the same scratch, reusing every
finished stage. The superseded jobs stay failed but no longer decide the operation's state (their replacements carry
`retry_of`), so a build finished after a retry settles `succeeded`. A build that settled without success (nothing succeeded, or cancelled) is
replaced by the next content-keyed attempt; the current build is the newest attempt that exists.

The audition operation (`voice_audition`: eight confirmed lines cut from the dialogue stem, converted by the built
voice and joined into a reel) is built here too, from plain data the caller passes.

Nothing here starts on its own: only the human commands in `recast_voice` call `start_voice_build`, and the voice
feed starts an audition only for a build that succeeded.
"""

from __future__ import annotations

import copy
import itertools
import json
import math
import statistics
from collections import OrderedDict
from hashlib import sha256
from weakref import WeakKeyDictionary

from gaiavfx.application.engine_jobs import engine_job
from gaiavfx.integrations.voice import engines as voice_engines
from gaiavfx.integrations.voice import spec as voice_spec
from gaiavfx.modules.generation.operations.lifecycle import OPERATION_TERMINAL, JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError

NAME = "voice_build"
AUDITION = "voice_audition"
BUILD_VERSION = voice_spec.build()["version"]
SAMPLE_RATE = 48000
HOP = 480  # samples per envelope frame: 10 ms at 48 kHz
GAP_SECONDS = 0.3  # silence between the recordings in the joined samples
# A decoded sample set may differ from the import's probe by a few frames; the bounds were checked on the probe.
SAMPLE_TOLERANCE_SECONDS = 0.1
# A long library clip is cut at its quietest frame within this many seconds of the even split.
PIECE_WINDOW_SECONDS = 2.0
STOPPED = frozenset({"failed", "cancelled", "needs_attention"})
STAGES = ("samples", "seed", "material", "convert", "filter", "train", "package")
STAGE_WORDS = {
    "samples": "reading the recordings",
    "seed": "preparing the seed",
    "material": "generating material",
    "convert": "converting performances",
    "filter": "filtering the material",
    "train": "training the voice",
    "package": "packaging the voice",
    "audition": "converting the audition",
}
# Generators split by `line_range` across jobs (slow), and generators that design a voice instead of cloning the
# seed, whose takes Seed-VC converts into the target without a shift (the research's maya_vc).
SPLIT_FAMILIES = frozenset({"maya1"})
DESIGNED_FAMILIES = frozenset({"maya1"})
# Library categories whose medians stand for a speaker's speaking voice when the build picks its pitch shift.
SPEECH_LIKE = ("shout", "whisper", "sad", "fear", "happy", "neutral", "highpitch")
LIBRARY_SPEAKER = "library"  # the speaker of library clips without a speaker folder
DATASET_KEYS = (
    "clips",
    "seed_clips",
    "synthetic_clips",
    "synthetic_minutes",
    "by_category",
    "slices",
    "dataset_seconds",
    "sample_rate",
    "identity",
)
CANCELLED = "The voice build was cancelled. Retry to build it again."
AUDITION_CANCELLED = "The audition was cancelled. Retry to run it again."
ENGINES = "The voice engines are not set up. Run the voice engine setup, then retry."
MEMORY = "The GPU ran out of memory while building the voice. Close other GPU work, then retry."
UNREADABLE = "The recordings could not be read. Choose other recordings."
LIBRARY_CHANGED = (
    "An expressive recording moved or changed during the build. Restore the folder, then retry."
)
operations = OperationService()
_CACHES = WeakKeyDictionary()


# --- pure rules ----------------------------------------------------------------------------------------------------


def key(shas, attempt) -> str:
    """The build's content key: the samples' hashes in order, the recipe's build version and the attempt."""
    return sha256(json.dumps([NAME, list(shas), BUILD_VERSION, attempt]).encode()).hexdigest()[:12]


def number(value: float) -> str:
    return f"{round(float(value), 1):g}"


def seconds_sentence(seconds: float, bounds: dict | None = None) -> str:
    bounds = bounds or voice_spec.build()["sample_seconds"]
    return (
        f"Choose between {number(bounds['min'])} and {number(bounds['max'])} seconds of recordings; "
        f"these are {number(seconds)} s."
    )


def maya_voice(median_hz: float, bands: list[dict] | None = None) -> str:
    """Maya1's voice description for a target of this median pitch: the first band whose limit exceeds it."""
    bands = bands if bands is not None else voice_spec.build()["maya_voice"]
    for band in bands:
        if float(median_hz) < float(band["below_hz"]):
            return band["text"]
    return bands[-1]["text"]


def chunks(items, size: int) -> list[list]:
    """Consecutive pieces of at most `size` items, in order; together they are exactly `items`."""
    if type(size) is not int or size < 1:
        raise ValueError("A chunk size is a whole number of at least 1.")
    items = list(items)
    return [items[n : n + size] for n in range(0, len(items), size)]


def line_ranges(count: int, size: int) -> list[tuple[int, int]]:
    """[first, stop) ranges of at most `size` lines tiling 0..count."""
    return [(piece[0], piece[-1] + 1) for piece in chunks(range(count), size)]


def train_ends(epochs: int, chunk: int) -> list[int]:
    """The epoch each training chunk ends at: multiples of `chunk`, the last at `epochs` (as rvc.chunk_ends)."""
    if chunk < 1 or epochs < 1:
        raise ValueError("Training needs epochs and a chunk of at least 1.")
    ends, at = [], 0
    while at < epochs:
        at = min(epochs, (at // chunk + 1) * chunk)
        ends.append(at)
    return ends


def seed_cuts(
    segments,
    *,
    min_s: float,
    max_s: float,
    pad_before: float,
    pad_after: float,
    total_s: float,
    bounds=None,
) -> list[tuple[float, float, str]]:
    """The research's seed cut (expand_seed.py): per recording, transcript segments are accumulated until they span at
    least `min_s` (a span over `max_s` ends at its segment anyway), then cut with the pads, inside the recording; a
    clip that would take the seed past `total_s` + 1 s ends that recording, and the cut stops once the seed holds
    `total_s`. A recording's unfinished accumulation is dropped. A recording that is already `min_s`-`max_s` long and
    has words is one whole clip: it is not cut again (the user, 23 Oct). `segments` are (start, end, text) in seconds;
    `bounds` the recordings' (start, end) spans in the same clock (one recording without them), and a segment
    belongs to the recording that holds its midpoint."""
    bounds = list(bounds) if bounds is not None else [(0.0, math.inf)]
    recordings = [(lo, hi, []) for lo, hi in bounds]
    for start, end, text in segments:
        middle = (float(start) + float(end)) / 2
        for lo, hi, rows in recordings:
            if lo <= middle < hi:
                rows.append((float(start), float(end), str(text).strip()))
                break
    cuts, total = [], 0.0
    for lo, hi, rows in recordings:
        if rows and min_s <= hi - lo <= max_s:
            if total + hi - lo <= total_s + 1:
                cuts.append((lo, hi, " ".join(text for _a, _b, text in sorted(rows) if text)))
                total += hi - lo
            if total >= total_s:
                break
            continue
        start, words = None, []
        for a, b, text in sorted(rows):
            if start is None:
                start = a
            words.append(text)
            if b - start >= min_s or b - start > max_s:
                clip = (max(lo, start - pad_before), min(hi, b + pad_after))
                if total + clip[1] - clip[0] > total_s + 1:
                    break
                cuts.append((clip[0], clip[1], " ".join(w for w in words if w)))
                total += clip[1] - clip[0]
                start, words = None, []
        if total >= total_s:
            break
    return cuts


def target_median(items) -> float | None:
    """The target's speaking median: the median of the voiced segments' medians."""
    medians = [float(i["median_hz"]) for i in items if i.get("median_hz") is not None]
    return round(statistics.median(medians), 2) if medians else None


def library_speakers(items, clips) -> dict[str, float]:
    """{speaker: median pitch} of the library's speakers, from the clips' measured medians (`items`, keyed by clip id):
    the speech-like categories when the speaker has any measured there, else all the speaker's clips. A speaker
    without any measured median is absent (the caller converts it without a shift). Clips without a speaker folder
    are the speaker "library"."""
    medians = {i["key"]: i.get("median_hz") for i in items}
    found: dict[str, dict[str, list[float]]] = {}
    for clip in clips:
        value = medians.get(clip["id"])
        if value is None:
            continue
        group = found.setdefault(clip.get("speaker") or LIBRARY_SPEAKER, {"speech": [], "all": []})
        group["all"].append(float(value))
        if clip.get("category") in SPEECH_LIKE:
            group["speech"].append(float(value))
    return {
        speaker: round(statistics.median(group["speech"] or group["all"]), 2)
        for speaker, group in sorted(found.items())
    }


def candidate_rows(material: dict, lines: dict, nonverbal) -> list[dict]:
    """The filter's candidate rows, ids only (no paths): every TTS take of a cloning generator, every Seed-VC
    conversion of a designed take (with the take it came from as `take_id`, so it is filtered and levelled like TTS,
    as the research's maya1_vc rows were) and every converted library clip (`generator = "bootstrap"`, with its source
    clip as `source_id`: compared with its source, kept at its own level). `material` holds `takes` and `designed`
    ({"id", "generator", "line"}, plus `take_id` for designed) and `bootstrap` ({"id", "source_id", "category",
    "speaker"}); `lines` maps line ids to the expansion's lines. Speech rows carry the line's text, nonverbal rows
    none (the research's candidates)."""
    rows = []
    for take in [*material.get("takes", []), *material.get("designed", [])]:
        line = lines[take["line"]]
        row = {
            "id": take["id"],
            "category": line["category"],
            "kind": line["kind"],
            "generator": take["generator"],
            "text": line["text"] if line["kind"] == "speech" else "",
        }
        if take.get("take_id"):
            row["take_id"] = take["take_id"]
        rows.append(row)
    for clip in material.get("bootstrap", []):
        rows.append(
            {
                "id": clip["id"],
                "category": clip["category"],
                "kind": "nonverbal" if clip["category"] in nonverbal else "speech",
                "generator": "bootstrap",
                "speaker": clip.get("speaker") or LIBRARY_SPEAKER,
                "text": "",
                "source_id": clip["source_id"],
            }
        )
    return rows


def piece_spans(
    db, samples: int, *, rate: int, hop: int, piece_seconds: float, window_seconds: float
):
    """[start, end) sample spans cutting a long clip into pieces of at most `piece_seconds`: as many as that needs,
    each cut at the quietest `hop` frame (`db` per frame) within `window_seconds` of the even split (of equally quiet
    frames, the nearest to the split), never so late that a piece or the rest would be too long."""
    count = math.ceil(samples / (piece_seconds * rate))
    if count <= 1:
        return [(0, samples)]
    piece, window = int(piece_seconds * rate), round(window_seconds * rate)
    cuts, previous = [], 0
    for k in range(1, count):
        split = round(k * samples / count)
        low = max(split - window, samples - (count - k) * piece, previous + 1)
        high = min(split + window, previous + piece)
        frames = range(max(0, -(-low // hop)), min(len(db) - 1, high // hop) + 1)
        if frames:
            cut = min(frames, key=lambda f, at=split: (db[f], abs(f * hop - at), f)) * hop
        else:
            cut = min(max(split, low), high)
        cuts.append(cut)
        previous = cut
    edges = [0, *cuts, samples]
    return list(itertools.pairwise(edges))


# --- reading builds ------------------------------------------------------------------------------------------------


def latest(session, shas) -> tuple[dict | None, int]:
    """(the newest attempt that exists, its attempt); (None, 1) without one. A rebuild after the expressive library
    changed is a newer attempt and wins even over a succeeded one."""
    found, attempt = None, 1
    while (following := operations.for_request(session, key(shas, attempt))) is not None:
        found, attempt = following, attempt + 1
    return (found, attempt - 1) if found else (None, 1)


def _stage_of(job) -> tuple[str | None, str | None]:
    stage = (job.get("snapshot") or {}).get("stage") or {}
    return stage.get("name"), stage.get("unit")


def _current(jobs) -> dict:
    """{(stage, unit): the newest job of that unit}: a retry supersedes the job it replaces."""
    found = {}
    for job in sorted(jobs, key=lambda j: j["ordinal"]):
        name, unit = _stage_of(job)
        if name in STAGES:
            found[(name, unit)] = job
    return found


def _newest(current) -> str | None:
    names = [name for name, _ in current]
    return max(names, key=STAGES.index) if names else None


def stopped_jobs(operation) -> list[dict]:
    """The current jobs (the newest per stage unit) that stopped: failed, cancelled or needing attention."""
    if operation is None:
        return []
    current = _current(operation["jobs"])
    return sorted(
        (job for job in current.values() if job["state"] in STOPPED), key=lambda j: j["ordinal"]
    )


def _output(job, output_key):
    matches = [
        o for o in job["outputs"] if o.get("output_key") == output_key and o.get("artifact_id")
    ]
    if len(matches) != 1:
        raise ApplicationError(f"Missing unique voice build artifact: {output_key}", 409)
    return matches[0]


def _frozen(output) -> dict:
    return {
        "source": "artifact",
        "artifact_id": output["artifact_id"],
        "sha256": output["sha256"],
        "directory": "input",
    }


def _stage(name: str, unit: str) -> dict:
    return {"name": name, "unit": unit}


def _token(operation) -> str:
    # A succeeded build never changes again: its id, state and last update identify what was read.
    return sha256(
        json.dumps([operation["id"], operation["state"], operation["updated_at"]]).encode()
    ).hexdigest()


def succeeded(operation) -> bool:
    """Whether this is a finished build (a retry in place settles `succeeded` too: superseded jobs do not count)."""
    return (
        operation is not None
        and operation.get("kind") == NAME
        and operation["state"] == "succeeded"
    )


def manifest_artifact(operation) -> dict | None:
    """{"artifact_id", "sha256"} of the `voice_model` manifest of a succeeded build; None otherwise."""
    if not succeeded(operation):
        return None
    job = _current(operation["jobs"]).get(("package", "package"))
    if job is None:
        return None
    output = _output(job, "model")
    return {"artifact_id": output["artifact_id"], "sha256": output["sha256"]}


def voice_manifest(session, library, operation_id: str) -> dict | None:
    """The parsed `voice_model` manifest of a succeeded build; None before it succeeds. Read-only and cached per
    operation token (P6 reads the weights and index through it), so callers must not change it."""
    operation = operations.operation(session, operation_id)
    if operation["kind"] != NAME:
        raise ApplicationError("Operation is not a voice build.", 422)
    found = manifest_artifact(operation)
    if found is None:
        return None
    manifests = _CACHES.setdefault(library, OrderedDict())
    token = _token(operation)
    if token in manifests:
        manifests.move_to_end(token)
        return manifests[token]
    try:
        manifest = json.loads(library.read_blob(session, found["artifact_id"]))
    except (ValueError, TypeError) as exc:
        raise ApplicationError("The voice model manifest is not valid JSON.", 409) from exc
    manifests[token] = manifest
    while len(manifests) > 8:
        manifests.popitem(last=False)
    return manifest


def audition_reel(operation) -> dict | None:
    """The reel candidate of a succeeded audition (its `asset_id` is the reviewed asset); None otherwise."""
    if operation is None or operation.get("kind") != AUDITION or operation["state"] != "succeeded":
        return None
    reels = [c for c in operation["candidates"] if c["output_key"] == "reel"]
    return reels[0] if len(reels) == 1 else None


# --- progress ------------------------------------------------------------------------------------------------------


def _root_failures(stopped) -> list[dict]:
    """The stopped jobs that failed on their own, not because a job they depend on did."""
    own = [j for j in stopped if not str(j.get("error") or "").startswith("Input job ")]
    return own or list(stopped)


def _sentence(operation, stage, current) -> str:
    """One actionable sentence: never a job id, path or worker output."""
    audition = stage == "audition"
    if operation["state"] == "cancelled" or operation["cancel_requested"]:
        return AUDITION_CANCELLED if audition else CANCELLED
    stopped = _root_failures([j for j in current.values() if j["state"] in STOPPED])
    errors = " ".join(str(j.get("error") or "") for j in stopped).lower()
    errors += " " + str(operation.get("error") or "").lower()
    if any(
        words in errors
        for words in ("not installed", "engines root", "engine worker", "no command for engine")
    ):
        return ENGINES
    if "out of memory" in errors:
        return MEMORY
    steps = {j["step"] for j in stopped}
    if "voice_library" in steps:
        return LIBRARY_CHANGED
    if stage == "samples" and "voice_samples" in steps:
        return UNREADABLE
    words = STAGE_WORDS.get(stage, "building the voice")
    return f"The voice build failed while {words}. Retry; if it fails again, the app log has the details."


def _planned(operation) -> dict[str, int]:
    """The fewest jobs each stage will hold (an estimate until the stage exists: the card reads "k of n")."""
    document = voice_spec.load()
    clips = int(((operation.get("request") or {}).get("library") or {}).get("clips") or 0)
    train = voice_spec.settings("rvc.train", document)
    return {
        "samples": 3,
        "seed": 2,
        "material": len(tts_units(document, voice_spec.expansion(document=document)))
        + 1
        + (1 if clips else 0),
        "convert": 1,
        "filter": 2,
        "train": len(train_ends(int(train["epochs"]), int(train["chunk_epochs"]))) + 2,
        "package": 1,
    }


def build_view(operation) -> dict:
    """Progress for the character card: {"state": not_started|running|failed|succeeded, "stage", "done", "total",
    "error"}. `stage` is the newest stage with a current job not finished (None once the build succeeded); `done`
    counts the finished current jobs, `total` the current jobs plus the planned minimum of the stages still to come.
    A stopped job reads failed while the build awaits its retry."""
    if operation is None:
        return {"state": "not_started", "stage": None, "done": 0, "total": 0, "error": None}
    current = _current(operation["jobs"])
    unfinished = [name for (name, _), job in current.items() if job["state"] != "succeeded"]
    stage = max(unfinished, key=STAGES.index) if unfinished else _newest(current)
    created = {name for name, _ in current}
    missing = [name for name in STAGES if name not in created]
    total = len(current) + (sum(_planned(operation)[name] for name in missing) if missing else 0)
    stopped = any(job["state"] in STOPPED for job in current.values())
    # The runner retries a failed advance on every pass; one that keeps failing must not read as running forever.
    stuck = (
        not unfinished
        and operation["state"] == "awaiting_review"
        and ((operation.get("review") or [{}])[-1] or {}).get("decided_by") == "advancer_error"
    )
    if succeeded(operation):
        state, stage = "succeeded", None
    elif (
        operation["state"] in OPERATION_TERMINAL
        or operation["cancel_requested"]
        or (stopped and operation["state"] == "awaiting_review")
        or stuck
    ):
        state = "failed"
    else:
        state = "running"
    return {
        "state": state,
        "stage": stage,
        "done": sum(job["state"] == "succeeded" for job in current.values()),
        "total": total,
        "error": _sentence(operation, stage, current) if state == "failed" else None,
    }


def audition_view(operation) -> dict:
    """The `build_view` shape for an audition: stage "audition", its three jobs."""
    if operation is None:
        return {"state": "not_started", "stage": None, "done": 0, "total": 0, "error": None}
    jobs = operation["jobs"]
    if operation["state"] == "succeeded":
        state = "succeeded"
    elif operation["state"] in OPERATION_TERMINAL or operation["cancel_requested"]:
        state = "failed"
    else:
        state = "running"
    current = {(None, job["id"]): job for job in jobs}
    return {
        "state": state,
        "stage": "audition",
        "done": sum(job["state"] == "succeeded" for job in jobs),
        "total": max(len(jobs), 3),
        "error": _sentence(operation, "audition", current) if state == "failed" else None,
    }


# --- starting and stopping -----------------------------------------------------------------------------------------


def _library_record(scan: dict) -> dict:
    """What the request records of the expressive library: never its folder or its files."""
    return {
        "state": scan.get("state") or "not_set",
        "digest": scan.get("digest") or "",
        "clips": len(scan.get("clips") or []),
        "categories": dict(scan.get("categories") or {}),
    }


def _samples_stage(assets: list[dict], scan: dict, document: dict) -> list[JobSpec]:
    """Stage 1: the recordings joined and transcribed, and the expressive library frozen as it was scanned when the
    build was asked for (the folder is recorded only in that step's spec). The library step waits for the samples so
    that unreadable recordings fail the whole first stage (the attempt path)."""
    build = voice_spec.build(document)
    bounds = build["sample_seconds"]
    filtering = voice_spec.settings("analysis.filter", document)
    ready = scan.get("state") == "ready"
    return [
        JobSpec(
            "analysis",
            "local",
            "voice",
            step="voice_samples",
            inputs={asset["id"]: {"sha256": asset["sha256"]} for asset in assets},
            snapshot={
                "step_spec": {
                    "assets": [asset["id"] for asset in assets],
                    "sample_rate": SAMPLE_RATE,
                    "gap_seconds": GAP_SECONDS,
                    "bounds": [bounds["min"], bounds["max"]],
                    "tolerance_seconds": SAMPLE_TOLERANCE_SECONDS,
                },
                "resumable_local": True,
                "stage": _stage("samples", "samples"),
            },
        ),
        engine_job(
            "analysis.transcribe",
            params=voice_spec.settings("analysis.transcribe", document),
            kind="analysis",
            depends_on=0,
            inputs={"audio": {"source": "job_artifact", "output_key": "samples"}},
            stage=_stage("samples", "transcribe"),
        ),
        JobSpec(
            "analysis",
            "local",
            "voice",
            step="voice_library",
            depends_on=0,
            snapshot={
                "step_spec": {
                    "folder": scan.get("folder") or "",
                    "state": scan.get("state") or "not_set",
                    "digest": scan.get("digest") or "",
                    "clips": copy.deepcopy(scan.get("clips") or []) if ready else [],
                    "piece_seconds": build["library_piece_seconds"],
                    "max_seconds": filtering["max_seconds"],
                    "min_seconds": filtering["min_seconds"],
                    "sample_rate": SAMPLE_RATE,
                    "hop": HOP,
                    "window_seconds": PIECE_WINDOW_SECONDS,
                },
                "resumable_local": True,
                "stage": _stage("samples", "library"),
            },
        ),
    ]


def start_voice_build(
    session, library, sample_asset_ids, *, library_scan: dict, attempt: int
) -> dict:
    """The shared build of these recordings, in this order: the existing operation of this attempt when there is one
    (idempotent through the content key), else a new one. Refuses anything but Library audio and a total outside the
    recipe's sample bounds. `library_scan` is `expressive.scan` of the Settings folder: the request records its
    digest and counts, the library step its clips."""
    from gaiavfx.application.operations import PRODUCERS, create_operation

    ids = list(sample_asset_ids)
    if not ids:
        raise ApplicationError("Choose recordings for the voice first.", 422)
    if type(attempt) is not int or attempt < 1:
        raise ApplicationError("A build attempt is a whole number of at least 1.", 422)
    assets = [library.get(session, asset_id) for asset_id in ids]
    if any(asset["kind"] != "audio" for asset in assets):
        raise ApplicationError("Choose Library audio recordings for the voice.", 422)
    document = voice_spec.load()
    bounds = voice_spec.build(document)["sample_seconds"]
    seconds = sum(float((asset.get("info") or {}).get("duration") or 0.0) for asset in assets)
    if not bounds["min"] <= seconds <= bounds["max"]:
        raise ApplicationError(seconds_sentence(seconds, bounds), 422)
    request_key = key([asset["sha256"] for asset in assets], attempt)
    existing = operations.for_request(session, request_key)
    if existing is not None:
        return existing | {"_created": False}
    return create_operation(
        session,
        operations,
        PRODUCERS,
        kind=NAME,
        request={
            "samples": ids,
            "version": BUILD_VERSION,
            "attempt": attempt,
            "library": _library_record(library_scan),
        },
        request_key=request_key,
        budget_limit=None,
        budget=0,
        awaits_review=True,
        jobs=_samples_stage(assets, library_scan, document),
    )


def cancel_build(session, shas) -> None:
    """Cancel the newest attempt of these recordings' build while it has not settled; a settled build stays."""
    operation, _ = latest(session, shas)
    if operation is not None and operation["state"] not in OPERATION_TERMINAL:
        operations.request_cancel(session, operation["id"])


def _replacement(job, depends_on) -> JobSpec:
    """The same step, params and inputs as a stopped job; an engine job's snapshot is taken again (the registry and
    the worker it will run with), its params stay frozen."""
    if job["executor"] == "engine":
        engine = job["snapshot"]["engine"]
        spec = engine_job(
            job["step"],
            params=copy.deepcopy(job["snapshot"].get("task_spec") or {}),
            inputs=copy.deepcopy(job["inputs"]),
            kind=job["kind"],
            integration=job["integration"],
            depends_on=depends_on,
            seed=job["seed"],
            artifact_kind=engine.get("artifact_kind"),
            stage=job["snapshot"].get("stage"),
        )
    else:
        spec = JobSpec(
            job["kind"],
            job["executor"],
            job["integration"],
            inputs=copy.deepcopy(job["inputs"]),
            snapshot=copy.deepcopy(job["snapshot"]),
            step=job["step"],
            seed=job["seed"],
            depends_on=depends_on,
        )
    spec.snapshot["retry_of"] = job["id"]
    return spec


def retry_stopped(session, operation) -> dict:
    """Resume a stopped build in place: a replacement for every stopped current job of its newest stage, chained as
    the originals were (a dependency that is replaced too is the replacement), in the same operation and scratch, so
    every finished stage, the Seed-VC model and the RVC checkpoints are reused. 409 when nothing stopped."""
    operation = operations.operation(session, operation["id"])
    if operation["kind"] != NAME:
        raise ApplicationError("Operation is not a voice build.", 422)
    current = _current(operation["jobs"])
    stage = _newest(current)
    stopped = [
        job for (name, _), job in current.items() if name == stage and job["state"] in STOPPED
    ]
    if not stopped or operation["state"] != "awaiting_review" or operation["cancel_requested"]:
        raise ApplicationError("The voice build has no stopped stage to retry.", 409)
    by_ordinal = {}
    specs = []
    base = len(operation["jobs"])
    for job in sorted(stopped, key=lambda j: j["ordinal"]):
        depends_on = job["depends_on"]
        if depends_on is not None:
            depends_on = by_ordinal.get(depends_on, depends_on)
        by_ordinal[job["ordinal"]] = base + len(specs)
        specs.append(_replacement(job, depends_on))
    return operations.resume(
        session,
        operation["id"],
        {"decided_by": NAME, "stage": stage, "retry": True},
        jobs=specs,
        awaits_review=True,
    )


# --- the advancer --------------------------------------------------------------------------------------------------


class _Build:
    """What the advancer reads of one build: its current jobs, their outputs, its reports and the recipe."""

    def __init__(self, session, library, operation):
        self.session, self.library, self.operation = session, library, operation
        self.current = _current(operation["jobs"])
        self.base = len(operation["jobs"])
        self.document = voice_spec.load()
        self._reports = {}

    def settings(self, step: str) -> dict:
        return voice_spec.settings(step, self.document)

    def job(self, stage: str, unit: str) -> dict:
        found = self.current.get((stage, unit))
        if found is None:
            raise ApplicationError(f"The voice build has no {stage} job {unit}.", 409)
        return found

    def jobs(self, stage: str, prefix: str = "") -> list[dict]:
        return sorted(
            (
                job
                for (name, unit), job in self.current.items()
                if name == stage and str(unit).startswith(prefix)
            ),
            key=lambda j: j["ordinal"],
        )

    def output(self, stage: str, unit: str, output_key: str) -> dict:
        return _output(self.job(stage, unit), output_key)

    def read(self, output) -> dict | list:
        artifact_id = output["artifact_id"]
        if artifact_id not in self._reports:
            try:
                self._reports[artifact_id] = json.loads(
                    self.library.read_blob(self.session, artifact_id)
                )
            except (ValueError, TypeError) as exc:
                raise ApplicationError("A voice build report is not valid JSON.", 409) from exc
        return self._reports[artifact_id]

    def seed(self) -> tuple[dict, dict, dict]:
        """(the seed manifest's output, the manifest, {clip id: clip output})."""
        manifest = self.output("seed", "seed", "seed")
        document = self.read(manifest)
        clips = {
            clip["id"]: self.output("seed", "seed", f"clip:{clip['id']}")
            for clip in document["clips"]
        }
        return manifest, document, clips

    def seed_inputs(self) -> dict:
        manifest, _, clips = self.seed()
        return {"seed": _frozen(manifest)} | {
            f"clip:{clip_id}": _frozen(output) for clip_id, output in clips.items()
        }

    def references(self) -> dict:
        """The target's voice for Seed-VC: the first seed clip, as the research converted (`expand_tts.maya_vc` and
        the bootstrap set used seed00 alone; the build's numbers are compared with theirs)."""
        _, _, clips = self.seed()
        first = next(iter(clips.values()), None)
        return {"reference:00": _frozen(first)} if first else {}

    def library_clips(self) -> list[dict]:
        """The frozen library clips: {"id", "category", "speaker", "output"} in the library step's order."""
        job = self.job("samples", "library")
        return [
            {
                "id": output["output_key"][5:],
                "category": (output.get("info") or {}).get("category"),
                "speaker": (output.get("info") or {}).get("speaker"),
                "output": output,
            }
            for output in job["outputs"]
            if str(output.get("output_key", "")).startswith("clip:") and output.get("artifact_id")
        ]

    def takes(self) -> list[dict]:
        """Every collected TTS take: {"id", "generator", "line", "take", "designed", "output", "info"}."""
        found = []
        for job in self.jobs("material", "tts:"):
            params = job["snapshot"].get("task_spec") or {}
            generator = params["generator"]
            family = (params.get("generators") or {}).get(generator, {}).get("family")
            for output in job["outputs"]:
                output_key = str(output.get("output_key", ""))
                if not output_key.startswith("take:") or not output.get("artifact_id"):
                    continue
                line, _, take = output_key[5:].rpartition("_")
                found.append(
                    {
                        "id": f"{generator}__{line}_{take}",
                        "generator": generator,
                        "line": line,
                        "take": take,
                        "designed": family in DESIGNED_FAMILIES,
                        "output": output,
                        "info": output.get("info") or {},
                    }
                )
        return found

    def lines(self) -> dict:
        """The expansion lines the TTS jobs voiced, by id (frozen in their params)."""
        found = {}
        for job in self.jobs("material", "tts:"):
            for line in (job["snapshot"].get("task_spec") or {}).get("lines") or []:
                found[line["id"]] = line
        return found

    def converted(self, prefix: str) -> dict:
        """{clip id: converted output} of the current convert jobs whose unit starts with `prefix`."""
        return {
            output["output_key"][5:]: output
            for job in self.jobs("convert", prefix)
            for output in job["outputs"]
            if str(output.get("output_key", "")).startswith("clip:") and output.get("artifact_id")
        }

    def target_median(self) -> float:
        _, seed, _ = self.seed()
        return float(seed["target_median_hz"])


def tts_units(document: dict, lines: list[dict]) -> list[dict]:
    """The TTS jobs of the material stage: one per generator that the allow lists name for any line, a splitting
    generator (Maya1) in `line_range` chunks of `tts_lines_per_job` lines that hold an allowed line."""
    generate = voice_spec.settings("tts.generate", document)
    allow, size = generate["allow"], voice_spec.build(document)["tts_lines_per_job"]
    units = []
    for name, generator in generate["generators"].items():
        wanted = [k for k, line in enumerate(lines) if name in allow.get(line["category"], [])]
        if not wanted:
            continue
        if generator.get("family") in SPLIT_FAMILIES:
            for first, stop in line_ranges(len(lines), size):
                if any(first <= k < stop for k in wanted):
                    units.append(
                        {
                            "unit": f"tts:{name}:{first}-{stop}",
                            "generator": name,
                            "line_range": [first, stop],
                        }
                    )
        else:
            units.append({"unit": f"tts:{name}", "generator": name, "line_range": None})
    return units


def _seed_stage(b: _Build) -> list[JobSpec]:
    """Stage 2: the target's pitch per transcript segment, then the seed cut from the recordings."""
    samples = b.output("samples", "samples", "samples")
    manifest = b.output("samples", "samples", "manifest")
    transcript = b.output("samples", "transcribe", "transcript")
    build = voice_spec.build(b.document)
    return [
        engine_job(
            "analysis.speakers",
            params=b.settings("analysis.speakers"),
            kind="analysis",
            inputs={"audio": _frozen(samples), "segments": _frozen(transcript)},
            stage=_stage("seed", "speakers"),
        ),
        JobSpec(
            "analysis",
            "local",
            "voice",
            step="voice_seed",
            depends_on=b.base,
            inputs={
                "samples": _frozen(samples),
                "transcript": _frozen(transcript),
                "manifest": _frozen(manifest),
                "speakers": {"source": "job_artifact", "output_key": "speakers"},
            },
            snapshot={
                "step_spec": {
                    "sample_rate": SAMPLE_RATE,
                    "cut": build["seed_clip_seconds"],
                    "total_seconds": build["sample_seconds"]["max"],
                },
                "resumable_local": True,
                "stage": _stage("seed", "seed"),
            },
        ),
    ]


def _material_stage(b: _Build) -> list[JobSpec]:
    """Stage 3: every TTS job of the expansion script, the seed-tuned Seed-VC model and the library's pitch."""
    lines = voice_spec.expansion(document=b.document)
    voice = maya_voice(b.target_median(), voice_spec.build(b.document)["maya_voice"])
    generate = b.settings("tts.generate")
    seed_inputs = b.seed_inputs()
    jobs = []
    for unit in tts_units(b.document, lines):
        params = generate | {
            "generator": unit["generator"],
            "lines": lines,
            "voice": voice,
            "clone_mode": "auto",
        }
        if unit["line_range"]:
            params["line_range"] = unit["line_range"]
        jobs.append(
            engine_job(
                "tts.generate",
                params=params,
                kind="audio",
                inputs=seed_inputs,
                artifact_kind="voice_material",
                stage=_stage("material", unit["unit"]),
            )
        )
    jobs.append(
        engine_job(
            "seedvc.finetune",
            params=b.settings("seedvc.finetune"),
            kind="analysis",
            inputs={k: v for k, v in seed_inputs.items() if k.startswith("clip:")},
            stage=_stage("material", "finetune"),
        )
    )
    clips = b.library_clips()
    if clips:
        jobs.append(
            engine_job(
                "analysis.speakers",
                params=b.settings("analysis.speakers"),
                kind="analysis",
                inputs={f"clip:{clip['id']}": _frozen(clip["output"]) for clip in clips},
                stage=_stage("material", "library_speakers"),
            )
        )
    return jobs


def _convert_stage(b: _Build) -> list[JobSpec]:
    """Stage 4: low-rate takes lifted to 48 kHz; library clips converted into the target with one pitch shift per
    speaker; designed takes converted without a shift."""
    build = voice_spec.build(b.document)
    superres = b.settings("tts.superres")
    convert = b.settings("seedvc.convert")
    takes = b.takes()
    jobs = []
    low = [
        t
        for t in takes
        if not t["designed"] and int(t["info"].get("sample_rate") or 0) < int(superres["below_hz"])
    ]
    for n, piece in enumerate(chunks(low, build["superres_clips_per_job"])):
        jobs.append(
            engine_job(
                "tts.superres",
                params=superres,
                kind="audio",
                inputs={f"clip:{t['id']}": _frozen(t["output"]) for t in piece},
                artifact_kind="voice_material",
                stage=_stage("convert", f"superres:{n}"),
            )
        )
    designed = [t for t in takes if t["designed"]]
    clips = b.library_clips()
    if not designed and not clips:
        return jobs
    shared = b.references() | {"model": _frozen(b.output("material", "finetune", "model"))}
    size = build["convert_clips_per_job"]
    if clips:
        target = b.target_median()
        items = b.read(b.output("material", "library_speakers", "speakers")).get("items") or []
        medians = library_speakers(items, clips)
        for n, piece in enumerate(chunks(clips, size)):
            speakers = {clip["id"]: clip["speaker"] or LIBRARY_SPEAKER for clip in piece}
            jobs.append(
                engine_job(
                    "seedvc.convert",
                    params=convert
                    | {
                        "target_median_hz": target,
                        # A speaker without a measured median keeps its pitch (no shift).
                        "source_median_hz": {
                            speaker: medians.get(speaker, target)
                            for speaker in sorted(set(speakers.values()))
                        },
                        "clip_speakers": speakers,
                    },
                    kind="audio",
                    inputs={f"clip:{c['id']}": _frozen(c["output"]) for c in piece} | shared,
                    artifact_kind="voice_material",
                    stage=_stage("convert", f"bootstrap:{n}"),
                )
            )
    for n, piece in enumerate(chunks(designed, size)):
        jobs.append(
            engine_job(
                "seedvc.convert",
                params=convert | {"pitch_shift": 0},
                kind="audio",
                inputs={f"clip:{t['id']}": _frozen(t["output"]) for t in piece} | shared,
                artifact_kind="voice_material",
                stage=_stage("convert", f"designed:{n}"),
            )
        )
    return jobs


def _filter_stage(b: _Build) -> list[JobSpec]:
    """Stage 5: the candidate manifests (ids only) and one filter job per chunk of them, each given every row's clip,
    every converted row's source and the seed."""
    lifted = b.converted("superres:")
    designed = b.converted("designed:")
    bootstrap = b.converted("bootstrap:")
    library = {clip["id"]: clip for clip in b.library_clips()}
    clip_outputs, material = {}, {"takes": [], "designed": [], "bootstrap": []}
    for take in b.takes():
        if take["designed"]:
            converted = designed.get(take["id"])
            if converted is None:
                continue
            row_id = f"{take['generator']}_vc__{take['line']}_{take['take']}"
            material["designed"].append(
                {"id": row_id, "generator": take["generator"], "line": take["line"]}
                | {"take_id": take["id"]}
            )
            clip_outputs[row_id] = converted
        else:
            material["takes"].append(
                {"id": take["id"], "generator": take["generator"], "line": take["line"]}
            )
            clip_outputs[take["id"]] = lifted.get(take["id"]) or take["output"]
    for clip_id, converted in bootstrap.items():
        clip = library[clip_id]
        row_id = f"bootstrap__{clip_id}"
        material["bootstrap"].append(
            {"id": row_id, "source_id": clip_id, "category": clip["category"]}
            | {"speaker": clip["speaker"]}
        )
        clip_outputs[row_id] = converted
        clip_outputs[clip_id] = clip["output"]
    rows = candidate_rows(material, b.lines(), b.settings("analysis.filter")["nonverbal"])
    if not rows:
        return []
    size = voice_spec.build(b.document)["filter_rows_per_job"]
    _, seed, _ = b.seed()
    params = b.settings("analysis.filter") | {
        "calibration": voice_spec.calibration(float(seed["seconds"]), b.document)[1]
    }
    seed_inputs = b.seed_inputs()
    jobs = [
        JobSpec(
            "analysis",
            "local",
            "voice",
            step="voice_candidates",
            snapshot={
                "step_spec": {"rows": rows, "per_job": size},
                "resumable_local": True,
                "stage": _stage("filter", "candidates"),
            },
        )
    ]
    for n, piece in enumerate(chunks(rows, size)):
        inputs = {"candidates": {"source": "job_artifact", "output_key": f"candidates:{n}"}}
        inputs |= seed_inputs
        for row in piece:
            inputs[f"clip:{row['id']}"] = _frozen(clip_outputs[row["id"]])
            if row.get("source_id"):
                inputs[f"clip:{row['source_id']}"] = _frozen(clip_outputs[row["source_id"]])
        jobs.append(
            engine_job(
                "analysis.filter",
                params=params,
                kind="analysis",
                depends_on=b.base,
                inputs=inputs,
                stage=_stage("filter", f"filter:{n}"),
            )
        )
    return jobs


def _train_stage(b: _Build) -> list[JobSpec]:
    """Stage 6: the training set prepared from the seed and the kept clips, the chained training chunks (each resumes
    the previous chunk's checkpoint in the scratch) and the index."""
    inputs = b.seed_inputs()
    for n, job in enumerate(b.jobs("filter", "filter:")):
        kept = _output(job, "kept")
        inputs[f"kept:{n}"] = _frozen(kept)
        for row in b.read(kept):
            if row.get("kept", True):
                inputs[f"clip:{row['id']}"] = copy.deepcopy(job["inputs"][f"clip:{row['id']}"])
    train = b.settings("rvc.train")
    ends = train_ends(int(train["epochs"]), int(train["chunk_epochs"]))
    jobs = [
        engine_job(
            "rvc.prepare",
            params=b.settings("rvc.prepare") | {"dataset": copy.deepcopy(b.document["dataset"])},
            kind="analysis",
            inputs=inputs,
            stage=_stage("train", "prepare"),
        )
    ]
    for n, end in enumerate(ends):
        jobs.append(
            engine_job(
                "rvc.train",
                params=train | {"epochs": end},
                kind="analysis",
                depends_on=b.base + n,
                stage=_stage("train", f"train:{end}"),
            )
        )
    jobs.append(
        engine_job(
            "rvc.index",
            params=b.settings("rvc.index"),
            kind="analysis",
            depends_on=b.base,
            stage=_stage("train", "index"),
        )
    )
    return jobs


def _package_stage(b: _Build) -> list[JobSpec]:
    """Stage 7: the `voice_model` manifest naming the weights and index artifacts (P6 reads them through it)."""
    train = b.settings("rvc.train")
    last = b.job(
        "train", f"train:{train_ends(int(train['epochs']), int(train['chunk_epochs']))[-1]}"
    )
    weights, index = _output(last, "model"), b.output("train", "index", "index")
    inputs = {
        "weights": _frozen(weights),
        "index": _frozen(index),
        "dataset": _frozen(b.output("train", "prepare", "dataset")),
        "speakers": _frozen(b.output("seed", "speakers", "speakers")),
        "seed": _frozen(b.output("seed", "seed", "seed")),
    }
    for n, job in enumerate(b.jobs("filter", "filter:")):
        inputs[f"scored:{n}"] = _frozen(_output(job, "scored"))
    request = b.operation["request"]
    samples_job = b.job("samples", "samples")
    return [
        JobSpec(
            "analysis",
            "local",
            "voice",
            step="voice_package",
            inputs=inputs,
            snapshot={
                "step_spec": {
                    "operation_id": b.operation["id"],
                    "request": copy.deepcopy(request),
                    "version": request["version"],
                    "samples": [
                        {"id": asset_id, "sha256": samples_job["inputs"][asset_id]["sha256"]}
                        for asset_id in request["samples"]
                    ],
                    "weights": {"artifact_id": weights["artifact_id"], "sha256": weights["sha256"]},
                    "index": {"artifact_id": index["artifact_id"], "sha256": index["sha256"]},
                    "epochs": (weights.get("info") or {}).get("epochs"),
                    "recipe_fingerprint": voice_spec.fingerprint(),
                    "engine_registry": voice_engines.fingerprint(),
                },
                "resumable_local": True,
                "stage": _stage("package", "package"),
            },
        )
    ]


BUILDERS = {
    "seed": _seed_stage,
    "material": _material_stage,
    "convert": _convert_stage,
    "filter": _filter_stage,
    "train": _train_stage,
    "package": _package_stage,
}


def advance_voice_build(session, operation, *, library):
    """After a stage succeeded, append the next one with its inputs frozen as artifacts (a stage with nothing to do
    is passed over); after the package, close the gate so the build settles succeeded. A stopped job leaves the gate
    open and the stage alone: the human retry resumes it."""
    current = _current(operation["jobs"])
    stage = _newest(current)
    if stage is None:
        raise ApplicationError("The voice build has no stage to advance.", 409)
    newest = [job for (name, _), job in current.items() if name == stage]
    if any(job["state"] in STOPPED for job in newest):
        return None
    if any(job["state"] != "succeeded" for job in newest):
        raise ApplicationError("The voice build cannot advance an incomplete stage.", 409)
    build = _Build(session, library, operation)
    for name in STAGES[STAGES.index(stage) + 1 :]:
        jobs = BUILDERS[name](build)
        if jobs:
            return operations.resume(
                session,
                operation["id"],
                {"decided_by": NAME, "stage": name},
                jobs=jobs,
                awaits_review=True,
            )
    return operations.resume(
        session, operation["id"], {"decided_by": NAME, "stage": "done"}, jobs=[]
    )


# --- the audition --------------------------------------------------------------------------------------------------


def _artifact_ref(value, what: str) -> dict:
    if not isinstance(value, dict) or not value.get("artifact_id") or not value.get("sha256"):
        raise ApplicationError(f"The audition needs the {what} artifact.", 422)
    return {"artifact_id": value["artifact_id"], "sha256": value["sha256"]}


def start_voice_audition(
    session,
    library,
    *,
    request_key: str,
    stem: dict,
    lines: list[dict],
    weights: dict,
    index: dict,
    source_median_hz: float,
    target_median_hz: float,
    sample_rate: int,
    voice_model: str | None = None,
    attempt: int = 1,
) -> dict:
    """The audition of a built voice: the lines cut from the dialogue stem at their integer samples, converted by the
    voice's weights and index from the source median to the target's, then joined with their originals into one reel
    (the operation's one candidate). Plain data only (spans, medians, artifact ids): it knows no recast. Idempotent
    through `request_key`; `voice_model` (the manifest artifact id) and `attempt` are recorded in the request."""
    from gaiavfx.application.operations import PRODUCERS, create_operation

    existing = operations.for_request(session, request_key)
    if existing is not None:
        return existing | {"_created": False}
    stem, weights, index = (
        _artifact_ref(stem, "dialogue stem"),
        _artifact_ref(weights, "voice weights"),
        _artifact_ref(index, "voice index"),
    )
    keys, cut = [], []
    for line in lines or []:
        start, end = line.get("start_sample"), line.get("end_sample")
        if (
            not isinstance(line.get("key"), str)
            or not line["key"]
            or type(start) is not int
            or type(end) is not int
            or not 0 <= start < end
        ):
            raise ApplicationError("Every audition line needs a key and a sample span.", 422)
        keys.append(line["key"])
        cut.append(
            {
                "key": line["key"],
                "start_sample": start,
                "end_sample": end,
                "whisper_spans": [[float(a), float(b)] for a, b in line.get("whisper_spans") or []],
            }
        )
    if not keys or len(set(keys)) != len(keys):
        raise ApplicationError("The audition needs distinct lines.", 422)
    if not (float(source_median_hz) > 0 and float(target_median_hz) > 0):
        raise ApplicationError("The audition needs the source and target medians.", 422)
    gaps = voice_spec.audition()["reel_gap_seconds"]
    frozen_stem = _frozen(stem)
    return create_operation(
        session,
        operations,
        PRODUCERS,
        kind=AUDITION,
        request={
            "lines": keys,
            "source_median_hz": float(source_median_hz),
            "target_median_hz": float(target_median_hz),
            "voice_model": voice_model,
            "attempt": attempt,
        },
        request_key=request_key,
        budget_limit=None,
        budget=0,
        awaits_review=False,
        jobs=[
            JobSpec(
                "analysis",
                "local",
                "voice",
                step="voice_audition_cut",
                inputs={"stem": frozen_stem},
                snapshot={
                    "step_spec": {"lines": cut, "sample_rate": int(sample_rate)},
                    "resumable_local": True,
                },
            ),
            engine_job(
                "rvc.convert",
                params=voice_spec.settings("rvc.convert")
                | {
                    "source_median_hz": float(source_median_hz),
                    "target_median_hz": float(target_median_hz),
                },
                kind="audio",
                depends_on=0,
                inputs={
                    f"clip:{k}": {"source": "job_artifact", "output_key": f"clip:{k}"} for k in keys
                }
                | {
                    "spans": {"source": "job_artifact", "output_key": "spans"},
                    "model": _frozen(weights),
                    "index": _frozen(index),
                },
                artifact_kind="voice_material",
            ),
            JobSpec(
                "audio",
                "local",
                "voice",
                step="voice_reel",
                depends_on=1,
                inputs={
                    f"converted:{k}": {"source": "job_artifact", "output_key": f"converted:{k}"}
                    for k in keys
                }
                | {"stem": frozen_stem},
                snapshot={
                    "step_spec": {
                        "lines": cut,
                        "sample_rate": int(sample_rate),
                        "gaps": {"between": gaps["between"], "after": gaps["after"]},
                    },
                    "resumable_local": True,
                },
            ),
        ],
    )
