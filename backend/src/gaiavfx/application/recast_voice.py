"""A recast character's voice (ADR 0016 §1, §4, §8): the user's sample recordings, the build they ask for, the audition
and its approval.

The samples are the user's cast edits, journaled like a picture (recast_edits); the build and approval states
(`voice_model_state`, `voice_model_artifact_id`) are the feed's and never block an undo. The build is one shared
`voice_build` operation per sample set and recipe version, found by its content key (no column stores its id); only
`build_voice` and `retry_voice` start one. Reading the state never starts anything. Changing or clearing the samples
cancels the old samples' build only while it runs and no other confirmed character still uses those recordings.

The audition (P5d) is started by the voice feed once a build succeeded and the character has enough confirmed lines
(`start_audition`); approval is read back from the reel's `generated_voice` provenance (`approval_recorded`), so it
survives an undo that restores the recordings even though undo leaves the feed's columns alone.

`recast` is imported at call time (it imports this module), and the build and dialogue analysis modules through
`import_module`, so the test world can replace them through `sys.modules` (as `recast_lines` does with the dialogue
analysis).
Human only: the harness never imports the commands here (test_boundaries).
"""

import json
import statistics
import time
from fractions import Fraction
from functools import cache
from importlib import import_module

from gaiavfx.application import recast_edits as journaling
from gaiavfx.application import recast_lines as lining
from gaiavfx.application import recast_planning as planning
from gaiavfx.application import recast_projection as p
from gaiavfx.infrastructure.database import timestamp
from gaiavfx.integrations.voice import expressive
from gaiavfx.integrations.voice import spec as voice_spec
from gaiavfx.modules.direction import dialogue as rule
from gaiavfx.modules.direction.application import default_voice_policy
from gaiavfx.shared.errors import ApplicationError

MAX_SAMPLES = 12
AUDIO_ONLY = "Choose Library audio recordings for the voice."
HIDDEN = "A hidden character has no voice built."
PROPOSED = "Confirm the proposed recordings first."
NOT_BUILT = "Build the voice to hear an audition."
NOTHING_TO_RETRY = "Nothing to retry."
PROVENANCE_MISSING = "Audition provenance is missing."
# The audition could not start, or settled without a reel to review; the retry's own refusal names the cause.
AUDITION_FAILED = (
    "The audition could not be made. Retry; if it fails again, the app log has the details."
)


def _build():
    return import_module("gaiavfx.application.voice_build")


def _evidence():
    return import_module("gaiavfx.application.dialogue_analysis")


def _commands():
    from gaiavfx.application import recast

    return recast


@cache
def _bounds() -> tuple[float, float]:
    bounds = voice_spec.build()["sample_seconds"]
    return float(bounds["min"]), float(bounds["max"])


def _number(value: float) -> str:
    return f"{round(value, 1):g}"


def _outside(seconds: float) -> str | None:
    """The 15-60 s sentence, or None when the recordings fit."""
    low, high = _bounds()
    if low <= seconds <= high:
        return None
    return (
        f"Choose between {_number(low)} and {_number(high)} seconds of recordings; "
        f"these are {_number(seconds)} s."
    )


def _min_lines() -> int:
    """Confirmed lines an audition needs (the Direction policy's `[audition] min_lines`)."""
    return default_voice_policy().audition.min_lines


def _waiting_for_lines(label: str) -> str:
    return f"Confirm at least {_min_lines()} of {label}'s lines to hear an audition."


# --- reading ----------------------------------------------------------------------------------------------------------


def _assets(session, library, asset_ids) -> list[dict | None]:
    """Each sample asset, or None for one no longer in the Library."""
    found = []
    for asset_id in asset_ids:
        try:
            found.append(library.get(session, asset_id))
        except ApplicationError:
            found.append(None)
    return found


def _seconds(asset: dict | None) -> float:
    return float((asset or {}).get("info", {}).get("duration") or 0.0) if asset else 0.0


def samples_seconds(session, library, asset_ids) -> float:
    """The recordings' total duration; refuses anything that is not a Library audio asset (422)."""
    total = 0.0
    for asset in _assets(session, library, asset_ids):
        if asset is None or asset["kind"] != "audio":
            raise ApplicationError(AUDIO_ONLY, 422)
        total += _seconds(asset)
    return total


def _shas(session, library, asset_ids) -> list[str] | None:
    """The samples' content hashes in order (the build's key), or None when one left the Library."""
    assets = _assets(session, library, asset_ids)
    return None if any(a is None for a in assets) else [a["sha256"] for a in assets]


def current_build(session, library, character) -> tuple[dict | None, int]:
    """(the newest attempt of the shared build of the character's samples, its attempt); (None, 1) without one."""
    shas = _shas(session, library, character["voice_asset_ids"] or [])
    if not shas:
        return None, 1
    return _build().latest(session, shas)


def _audition_key(recast, character, manifest_artifact_id, attempt):
    return planning.key(recast["id"], character["id"], manifest_artifact_id, "audition", attempt)


def current_audition(session, recast, character, manifest_artifact_id) -> tuple[dict | None, int]:
    """(the newest audition of this character with this voice model, its attempt); (None, 1) without one."""
    found, attempt = None, 1
    while (
        following := p.operations.for_request(
            session, _audition_key(recast, character, manifest_artifact_id, attempt)
        )
    ) is not None:
        found, attempt = following, attempt + 1
    return (found, attempt - 1) if found else (None, 1)


def build_failed(build) -> bool:
    """Whether the build reads failed on the card: settled without success, or a stopped stage awaiting its retry."""
    return _build().build_view(build)["state"] == "failed"


def manifest_of(build) -> dict | None:
    """{"artifact_id", "sha256"} of a succeeded build's `voice_model` manifest; None otherwise."""
    return _build().manifest_artifact(build)


def audition_failed(audition) -> bool:
    """A settled audition with no reel to review: failed, cancelled or partial, or succeeded without its reel."""
    return audition["state"] in p.SETTLED and _build().audition_reel(audition) is None


def approval_recorded(session, library, recast, character, audition, artifact_id) -> bool:
    """Whether the audition's reel carries this character's `generated_voice` approval of this voice model. Approval
    is read from this evidence, not only from the feed's column: an undo that brings the recordings back brings the
    approval back too (undo never restores feed-owned columns). A rejection leaves no evidence."""
    reel = _build().audition_reel(audition)
    if not reel or not reel.get("asset_id"):
        return False
    try:
        asset = library.get(session, reel["asset_id"])
    except ApplicationError:
        return False
    return any(
        entry.get("type") == "generated_voice"
        and entry.get("recast_id") == recast["id"]
        and entry.get("character_id") == character["id"]
        and entry.get("voice_model_artifact_id") == artifact_id
        for entry in asset.get("provenance") or ()
    )


def _settled_unsuccessful(operation) -> bool:
    return operation["state"] in p.SETTLED and operation["state"] != "succeeded"


def _library_digest(operation) -> str:
    return str(((operation.get("request") or {}).get("library") or {}).get("digest") or "")


# A read's library_changed may lag a change of the folder by this much; build_voice and retry_voice scan afresh.
DIGEST_SECONDS = 60.0
_DIGESTS: dict[str, tuple[float, str]] = {}
_clock = time.monotonic


def _remember(folder: str, digest: str) -> str:
    if len(_DIGESTS) >= 8 and folder not in _DIGESTS:
        _DIGESTS.clear()
    _DIGESTS[folder] = (_clock(), digest)
    return digest


def folder_digest(folder: str) -> str:
    """The expressive folder's digest for reads, scanned at most once a minute per folder (Astra's P5 review: the
    film's 2 s poll scanned the folder on every read)."""
    found = _DIGESTS.get(folder)
    if found is not None and _clock() - found[0] < DIGEST_SECONDS:
        return found[1]
    return _remember(folder, expressive.scan(folder)["digest"])


def voice_view(session, library, recast, character, lines_confirmed, expressive_library=None):
    """VoiceDTO, or None while the character has no recordings. `expressive_library` (the Settings folder) is given
    only by the page's read; its digest comes from `folder_digest`."""
    asset_ids = character.get("voice_asset_ids") or []
    if not asset_ids:
        return None
    assets = _assets(session, library, asset_ids)
    samples = [
        {
            "asset_id": asset_id,
            "name": asset["name"] if asset else "Recording no longer in the Library",
            "seconds": round(_seconds(asset), 3),
        }
        for asset_id, asset in zip(asset_ids, assets)
    ]
    seconds = round(sum(_seconds(a) for a in assets), 3)
    view = {
        "samples": samples,
        "seconds": seconds,
        "state": character["voice_state"],
        "proposed_by": character["voice_proposed_by"],
        "model_state": character.get("voice_model_state"),
        "stage": None,
        "done": 0,
        "total": 0,
        "error": None,
        "reason": None,
        "audition_asset_id": None,
        "library_changed": False,
        # Python decides what blocks Build voice (the client only reads it): recordings outside the bounds, proposed
        # recordings or a hidden character. The next step ("Build the voice …") and waiting for lines block nothing.
        "blocked": bool(_outside(seconds))
        or character["voice_state"] == "proposed"
        or bool(character["hidden"]),
    }
    build = audition = None
    if all(asset is not None for asset in assets):
        builds = _build()
        build, _attempt = builds.latest(session, [a["sha256"] for a in assets])
        if build is not None and build["state"] != "succeeded":
            view.update(_progress(builds.build_view(build)))
        elif build is not None:
            manifest = builds.manifest_artifact(build)
            if manifest:
                audition, _ = current_audition(session, recast, character, manifest["artifact_id"])
            if audition is not None:
                view.update(_progress(builds.audition_view(audition)))
                reel = builds.audition_reel(audition) if audition["state"] == "succeeded" else None
                view["audition_asset_id"] = reel["asset_id"] if reel else None
            if view["model_state"] == "failed" and not view["error"]:
                # The feed could not start the audition, or it settled without a reel.
                view.update(stage="audition", error=AUDITION_FAILED)
            if expressive_library is not None:
                view["library_changed"] = _library_digest(build) != folder_digest(
                    expressive_library
                )
    view["reason"] = _reason(character, seconds, build, audition, lines_confirmed)
    return view


def _progress(found: dict) -> dict:
    return {key: found.get(key) for key in ("stage", "error")} | {
        "done": int(found.get("done") or 0),
        "total": int(found.get("total") or 0),
    }


def _reason(character, seconds, build, audition, lines_confirmed):
    """Why nothing can start now (D2 text): the first that applies, else None."""
    if outside := _outside(seconds):
        return outside
    if character["voice_state"] == "proposed":
        return PROPOSED
    if character["hidden"]:
        return HIDDEN
    if build is None:
        return NOT_BUILT
    if build["state"] == "succeeded" and audition is None and lines_confirmed < _min_lines():
        return _waiting_for_lines(character["label"])
    return None


def shared_elsewhere(session, recast_id, asset_ids) -> bool:
    """Another recast has a confirmed, visible character with these recordings in this order."""
    for other_id in p.store.ids(session):
        if other_id == recast_id:
            continue
        if _uses(p.store.load(session, other_id)["characters"], asset_ids):
            return True
    return False


def _uses(characters, asset_ids, *, besides=None) -> bool:
    return any(
        c["id"] != besides
        and not c["hidden"]
        and c.get("voice_state") == "confirmed"
        and list(c.get("voice_asset_ids") or []) == list(asset_ids)
        for c in characters
    )


def _release(session, library, recast, character, new_ids=None):
    """Changing the samples resets the model state (ADR 0016 §1). The old samples' build is cancelled only while it
    runs and no other confirmed, visible character (in this recast or another) still uses those recordings."""
    old = list(character.get("voice_asset_ids") or [])
    if old == list(new_ids or []):
        return
    character.update(voice_model_state=None, voice_model_artifact_id=None)
    if not old or _uses(recast["characters"], old, besides=character["id"]):
        return
    if shared_elsewhere(session, recast["id"], old):
        return
    if shas := _shas(session, library, old):
        _build().cancel_build(session, shas)


# --- the user's sample decisions (journaled) ---------------------------------------------------------------------------


def _choose(session, library, recast, character, asset_ids):
    """Refusals before any write: visible character, 1-12 distinct audio assets, 15-60 s. Returns the seconds."""
    if character["hidden"]:
        raise ApplicationError("Show the character before giving it a voice.", 422)
    if not 1 <= len(asset_ids) <= MAX_SAMPLES:
        raise ApplicationError(f"Choose 1 to {MAX_SAMPLES} recordings for the voice.", 422)
    if len(set(asset_ids)) != len(asset_ids):
        raise ApplicationError("Choose each recording once.", 422)
    seconds = samples_seconds(session, library, asset_ids)
    if outside := _outside(seconds):
        raise ApplicationError(outside, 422)
    return seconds


def _sampled(session, library, recast_id, character_id, expected_revision):
    commands = _commands()
    recast, report = commands._load(session, library, recast_id, expected_revision)
    journal = journaling.capture(recast, report)
    return commands, recast, journal, commands._character(recast, character_id)


def set_voice(session, library, recast_id, character_id, *, expected_revision, asset_ids):
    """Human only: the character's voice recordings, confirmed at once (a human's direct choice, ADR 0017 §10)."""
    commands, recast, journal, character = _sampled(
        session, library, recast_id, character_id, expected_revision
    )
    asset_ids = list(asset_ids)
    seconds = _choose(session, library, recast, character, asset_ids)
    _release(session, library, recast, character, asset_ids)
    character.update(voice_asset_ids=asset_ids, voice_state="confirmed", voice_proposed_by="user")
    return commands._journaled(
        session,
        library,
        recast,
        journal,
        "set_voice",
        character_id=character_id,
        count=len(asset_ids),
        seconds=seconds,
    )


def clear_voice(session, library, recast_id, character_id, *, expected_revision):
    """Human only: remove the recordings (or reject proposed ones); a running build nobody else uses is cancelled."""
    commands, recast, journal, character = _sampled(
        session, library, recast_id, character_id, expected_revision
    )
    if not character.get("voice_asset_ids") and not character.get("voice_state"):
        raise ApplicationError("This character has no voice recordings to clear.", 409)
    _release(session, library, recast, character)
    character.update(dict.fromkeys(journaling.VOICE))
    return commands._journaled(
        session, library, recast, journal, "clear_voice", character_id=character_id
    )


def confirm_voice(session, library, recast_id, character_id, *, expected_revision):
    """Human only: the proposed recordings become the character's (the proposer is kept)."""
    commands, recast, journal, character = _sampled(
        session, library, recast_id, character_id, expected_revision
    )
    if character.get("voice_state") != "proposed":
        raise ApplicationError("No voice recordings are proposed for this character.", 409)
    character["voice_state"] = "confirmed"
    return commands._journaled(
        session, library, recast, journal, "confirm_voice", character_id=character_id
    )


def propose_voice(
    session,
    library,
    recast_id,
    character_id,
    *,
    expected_revision,
    asset_ids,
    proposed_by="agent",
    evidence=None,
    user_message="",
):
    """A proposal (P9's agent tool; no route): never over confirmed recordings; an agent needs the user's naming.
    The evidence is checked as finite JSON; the store has no column for it yet."""
    commands, recast, journal, character = _sampled(
        session, library, recast_id, character_id, expected_revision
    )
    if proposed_by not in {"agent", "user"}:
        raise ApplicationError("Unknown voice proposer.", 422)
    if proposed_by == "agent" and not commands.named_character(recast, character_id, user_message):
        raise ApplicationError("The user must name the character before an agent proposal.", 422)
    evidence = {} if evidence is None else evidence
    if not isinstance(evidence, dict):
        raise ApplicationError("Voice evidence must be a JSON object.", 422)
    try:
        json.dumps(evidence, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ApplicationError("Voice evidence must be finite JSON.", 422) from exc
    if character.get("voice_state") == "confirmed":
        raise ApplicationError("Clear the confirmed voice recordings before proposing others.", 409)
    asset_ids = list(asset_ids)
    _choose(session, library, recast, character, asset_ids)
    _release(session, library, recast, character, asset_ids)
    character.update(
        voice_asset_ids=asset_ids, voice_state="proposed", voice_proposed_by=proposed_by
    )
    return commands._journaled(
        session,
        library,
        recast,
        journal,
        "propose_voice",
        character_id=character_id,
        count=len(asset_ids),
    )


# --- the build, the audition and approval (human only; feed-owned states, not journaled) -------------------------------


def _buildable(session, library, character):
    """Refusals before any write; returns the samples' seconds."""
    if character["hidden"]:
        raise ApplicationError(HIDDEN, 422)
    if not character.get("voice_asset_ids"):
        raise ApplicationError("Choose recordings for the voice first.", 409)
    if character.get("voice_state") != "confirmed":
        raise ApplicationError(PROPOSED, 409)
    seconds = samples_seconds(session, library, character["voice_asset_ids"])
    if outside := _outside(seconds):
        raise ApplicationError(outside, 422)
    return seconds


def _start(session, library, character, scan, attempt):
    return _build().start_voice_build(
        session, library, list(character["voice_asset_ids"]), library_scan=scan, attempt=attempt
    )


def build_voice(
    session,
    library,
    recast_id,
    character_id,
    *,
    expected_revision,
    expressive_library,
    rebuild=False,
):
    """Human only: start the shared voice build of the character's confirmed recordings (2.5-3.5 GPU hours; the
    client asks first). A build that settled without success gets the next attempt; a finished build is built again
    only with `rebuild` after the expressive recordings changed. Never automatic."""
    commands = _commands()
    recast, _ = commands._load(session, library, recast_id, expected_revision)
    character = commands._character(recast, character_id)
    _buildable(session, library, character)
    builds = _build()
    operation, attempt = current_build(session, library, character)
    scan = expressive.scan(expressive_library)
    _remember(expressive_library, scan["digest"])
    if operation is None:
        _start(session, library, character, scan, attempt)
    elif _settled_unsuccessful(operation):
        _start(session, library, character, scan, attempt + 1)
    elif operation["state"] != "succeeded":
        if operation["state"] == "awaiting_review" and builds.stopped_jobs(operation):
            raise ApplicationError("The last build stopped; retry it.", 409)
        raise ApplicationError("This voice is already being built.", 409)
    elif _library_digest(operation) == scan["digest"]:
        raise ApplicationError("This voice is already built.", 409)
    elif not rebuild:
        raise ApplicationError(
            "The expressive recordings changed since this voice was built. Build again to use them.",
            409,
        )
    else:
        _start(session, library, character, scan, attempt + 1)
    character["voice_model_state"] = "making"
    return commands._finish(session, library, recast)


def _clip_spans(line, whisper_spans, rate: int) -> list[list[float]]:
    """The film's whispered stretches inside a line, in seconds from the line's first sample."""
    start, end = Fraction(int(line["start_sample"]), rate), Fraction(int(line["end_sample"]), rate)
    spans = []
    for a, b in whisper_spans or ():
        low, high = max(start, Fraction(a)), min(end, Fraction(b))
        if high > low:
            spans.append([round(float(low - start), 6), round(float(high - start), 6)])
    return spans


def start_audition(session, library, recast, character, build, *, attempt, report=None):
    """The audition of the built voice on up to `[audition] count` of the character's confirmed lines, picked by the
    Direction rule (`audition_lines`): the feed starts attempt 1 after the build succeeded, `retry_voice` the next.
    The build module gets plain data only: the lines' integer sample spans with their whispered stretches relative to
    each clip, the dialogue stem, the voice's weights and index, the source median (the median pitch of all the
    character's confirmed lines now) and the target median from the build's manifest. Returns the operation (with
    `_created`), or None while the character has fewer than `[audition] min_lines` confirmed lines: the audition
    waits for them (the view says so). 409 when no confirmed line has a measured pitch."""
    builds = _build()
    manifest = builds.manifest_artifact(build)
    document = builds.voice_manifest(session, library, build["id"]) if manifest else None
    if manifest is None or document is None:
        raise ApplicationError(NOT_BUILT, 409)
    policy = default_voice_policy().audition
    current = lining.current_operation(session, library, recast)
    operation = current[0]
    # A confirmed line is always a stored decision: count those before computing every line (the feed asks on each
    # pass while the audition waits for lines).
    decided = sum(
        row["state"] == "confirmed"
        and row["character_id"] == character["id"]
        and operation is not None
        and row["dialogue_operation_id"] == operation["id"]
        for row in recast.get("lines", [])
    )
    if decided < policy.min_lines:
        return None
    if report is None:
        report = p.report_for(session, library, recast)
    views = lining.line_views(session, library, recast, report, current)
    confirmed = [
        line
        for line in views["lines"]
        if line["state"] == "confirmed" and line["character_id"] == character["id"]
    ]
    if len(confirmed) < policy.min_lines:
        return None
    medians = [float(line["median_hz"]) for line in confirmed if line.get("median_hz") is not None]
    if not medians:
        raise ApplicationError(
            f"None of {character['label']}'s confirmed lines has a measured pitch. "
            "Confirm a spoken line, then retry.",
            409,
        )
    evidence = _evidence()
    dialogue = evidence.dialogue_report(session, library, operation["id"])
    rate = int(views["sample_rate"])
    picks = rule.audition_lines(
        confirmed,
        dialogue["envelope"],
        count=policy.count,
        min_seconds=policy.min_seconds,
        sample_rate=rate,
    )
    return builds.start_voice_audition(
        session,
        library,
        request_key=_audition_key(recast, character, manifest["artifact_id"], attempt),
        stem=evidence.stem_artifact(operation),
        lines=[
            {
                "key": line["segment_key"],
                "start_sample": int(line["start_sample"]),
                "end_sample": int(line["end_sample"]),
                "whisper_spans": _clip_spans(line, dialogue["whisper_spans"], rate),
            }
            for line in picks
        ],
        weights=document["weights"],
        index=document["index"],
        source_median_hz=statistics.median(medians),
        target_median_hz=float(document["target_median_hz"]),
        sample_rate=rate,
        voice_model=manifest["artifact_id"],
        attempt=attempt,
    )


def _audition_retryable(character, manifest, audition) -> bool:
    """A succeeded build's audition can run again when it failed or was rejected, or when the feed could not start
    it (the character reads failed with no audition); never while one runs or after approval."""
    if manifest is None:
        return False
    state = character.get("voice_model_state")
    if audition is None:
        return state == "failed"
    if audition["state"] not in p.SETTLED:
        return False
    return audition_failed(audition) or state == "rejected"


def retry_voice(
    session, library, recast_id, character_id, *, expected_revision, expressive_library
):
    """Human only: resume a stopped build in place, start the next attempt of a build that settled without success,
    or audition again (with the lines confirmed now) after a failed, cancelled or rejected audition, or one the feed
    could not start."""
    commands = _commands()
    recast, report = commands._load(session, library, recast_id, expected_revision)
    character = commands._character(recast, character_id)
    _buildable(session, library, character)
    builds = _build()
    operation, attempt = current_build(session, library, character)
    if operation is None:
        raise ApplicationError(NOTHING_TO_RETRY, 409)
    if operation["state"] == "awaiting_review" and builds.stopped_jobs(operation):
        builds.retry_stopped(session, operation)
    elif _settled_unsuccessful(operation):
        scan = expressive.scan(expressive_library)
        _remember(expressive_library, scan["digest"])
        _start(session, library, character, scan, attempt + 1)
    elif operation["state"] == "succeeded":
        manifest = builds.manifest_artifact(operation)
        audition, tried = (
            current_audition(session, recast, character, manifest["artifact_id"])
            if manifest
            else (None, 1)
        )
        if not _audition_retryable(character, manifest, audition):
            raise ApplicationError(NOTHING_TO_RETRY, 409)
        started = start_audition(
            session,
            library,
            recast,
            character,
            operation,
            attempt=tried + 1 if audition is not None else 1,
            report=report,
        )
        if started is None:
            raise ApplicationError(_waiting_for_lines(character["label"]), 409)
        character["voice_model_artifact_id"] = manifest["artifact_id"]
    else:
        raise ApplicationError(NOTHING_TO_RETRY, 409)
    character["voice_model_state"] = "making"
    return commands._finish(session, library, recast)


def _reviewed(session, library, recast_id, character_id, expected_revision):
    """The waiting audition: (commands, recast, character, build, audition, reel); refusals before any write."""
    commands = _commands()
    recast, _ = commands._load(session, library, recast_id, expected_revision)
    character = commands._character(recast, character_id)
    if character.get("voice_model_state") != "waiting":
        raise ApplicationError("No voice audition is waiting for approval.", 409)
    builds = _build()
    build, _ = current_build(session, library, character)
    manifest = builds.manifest_artifact(build) if build and build["state"] == "succeeded" else None
    audition = (
        current_audition(session, recast, character, manifest["artifact_id"])[0]
        if manifest
        else None
    )
    reel = builds.audition_reel(audition) if audition and audition["state"] == "succeeded" else None
    if reel is None or not reel.get("asset_id"):
        raise ApplicationError(PROVENANCE_MISSING, 409)
    return commands, recast, character, build, manifest, audition, reel


def approve_voice(session, library, recast_id, character_id, *, expected_revision):
    """Human only: the audition reel records the generated voice (`generated_voice` provenance); conform and line
    conversion use only an approved voice (ADR 0016 §7)."""
    commands, recast, character, build, manifest, audition, reel = _reviewed(
        session, library, recast_id, character_id, expected_revision
    )
    request = audition.get("request") or {}
    library.approve_derivative(
        session,
        reel["asset_id"],
        character["voice_asset_ids"][0],
        {
            "type": "generated_voice",
            "subject": "recast_voice",
            "recast_id": recast_id,
            "character_id": character_id,
            "sample_assets": list(character["voice_asset_ids"]),
            "voice_model_artifact_id": manifest["artifact_id"],
            "build_operation": build["id"],
            "audition_operation": audition["id"],
            "source_median_hz": request.get("source_median_hz"),
            "target_median_hz": request.get("target_median_hz"),
            "lines": list(request.get("lines") or []),
            "approved_by": "user",
            "approved_at": timestamp(),
            "recast_revision": recast["revision"] + 1,
        },
    )
    character.update(voice_model_state="approved", voice_model_artifact_id=manifest["artifact_id"])
    return commands._finish(session, library, recast)


def reject_voice(session, library, recast_id, character_id, *, expected_revision):
    """Human only: the audition is not this voice; `retry_voice` auditions again."""
    commands, recast, character, *_rest = _reviewed(
        session, library, recast_id, character_id, expected_revision
    )
    character["voice_model_state"] = "rejected"
    return commands._finish(session, library, recast)
