"""A recast character's derivatives (ADR 0016 §1, §4, §7): the face sheet made from its target picture and the voice
model built from its recordings.

Each follows one lifecycle. Its input (the target picture, the recordings) and the authority that chose it are the
user's cast edit on the character, journaled by `recast_edits`. Its work is Generation operations found by content key
(their attempts; no column stores an operation id). The feed advances it (`feed_references`, `feed_voices`) into the
feed-owned columns (`reference_state`/`reference_asset_id`, `voice_model_state`/`voice_model_artifact_id`), which never
block an undo. What waits is the human's to decide (`approve`, `reject`): approval records the derivation as the
asset's provenance, which survives an undo that restores the input. A changed input cancels the old work
(`cancel_reference`; the voice commands release a build no one else uses), and an undo reconciles the face sheet with
the restored target (`reconcile`).

The voice build and the dialogue analysis are reached through `import_module`, so the test world can replace them
through `sys.modules`.
"""

import logging
import statistics
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
from importlib import import_module

from gaiavfx.application import recast_lines as lining
from gaiavfx.application import recast_planning as planning
from gaiavfx.application import recast_projection as p
from gaiavfx.application.target_faces import (
    start_face_portraits,
    start_face_sheet,
    start_target_report,
    target_report,
)
from gaiavfx.infrastructure.database import timestamp
from gaiavfx.modules.generation.operations.attempts import attempts
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.modules.recast import dialogue as rule
from gaiavfx.modules.recast.application import default_voice_policy
from gaiavfx.shared.errors import ApplicationError

NOT_BUILT = "Build the voice to hear an audition."
PROVENANCE_MISSING = "Audition provenance is missing."


def _build():
    return import_module("gaiavfx.application.voice_build")


def _evidence():
    return import_module("gaiavfx.application.dialogue_analysis")


# --- the face sheet --------------------------------------------------------------------------------------------------


def reference_operations(session, recast, character):
    """Recover the latest durable attempt without storing operation ids in Editing."""
    asset = MediaLibrary.asset_info(session, character["target_asset_id"])
    identity = (recast["id"], character["id"], asset["sha256"])
    portraits, attempt = attempts(
        p.operations, session, lambda n: planning.key(*identity, "portraits", n)
    ).newest()
    # The sheet of an attempt shares its portraits' number.
    sheet = p.operations.for_request(session, planning.key(*identity, "sheet", attempt))
    return identity, attempt, portraits, sheet


def cancel_reference(session, recast, character):
    if not character["target_asset_id"]:
        return
    _, _, portraits, sheet = reference_operations(session, recast, character)
    for operation in (portraits, sheet):
        if operation and operation["state"] not in p.SETTLED:
            p.operations.request_cancel(session, operation["id"])


def _failed(operation):
    return operation and operation["state"] in p.SETTLED and operation["state"] != "succeeded"


def _feed_reference(session, library, recast, character, *, retry_failed=False):
    identity, attempt, portraits, sheet = reference_operations(session, recast, character)
    retry = retry_failed and (_failed(portraits) or _failed(sheet))
    if retry:
        attempt += 1
    elif retry_failed:
        # Mapping again does not restart pending, approved or rejected work.
        return False
    if not portraits or retry:
        report = target_report(session, library, character["target_asset_id"])
        if retry:
            # Same immutable target: reuse the attempt's face box if its report is being retried.
            box = portraits["request"]["box"]
        elif report and report["state"] == "face":
            box = report["box"]
        else:
            return False
        seed_key = planning.key(*identity, "portraits")
        seed = int(sha256(seed_key.encode()).hexdigest()[:8], 16)
        op = start_face_portraits(
            session,
            library,
            character["target_asset_id"],
            box,
            seed=seed,
            request_key=planning.key(*identity, "portraits", attempt),
            attempt=attempt,
        )
        character.update(reference_state="making", reference_asset_id=None)
        return op.get("_created", False)
    if _failed(portraits) or _failed(sheet):
        character["reference_state"] = "failed"
    elif sheet:
        if sheet["state"] == "succeeded":
            if len(sheet["candidates"]) == 1:
                character.update(
                    reference_state="waiting", reference_asset_id=sheet["candidates"][0]["asset_id"]
                )
            else:
                character["reference_state"] = "failed"
        else:
            character["reference_state"] = "making"
    elif portraits["state"] == "succeeded":
        if len(portraits["candidates"]) != 3:
            character["reference_state"] = "failed"
            return False
        op = start_face_sheet(
            session,
            library,
            character["target_asset_id"],
            portraits["id"],
            request_key=planning.key(*identity, "sheet", attempt),
        )
        character["reference_state"] = "making"
        return op.get("_created", False)
    else:
        character["reference_state"] = "making"
    return False


def advance_reference(session, library, recast, character, *, retry_failed=False):
    if character["target_state"] != "confirmed" or character["hidden"]:
        return False
    try:
        with session.begin_nested():
            return _feed_reference(session, library, recast, character, retry_failed=retry_failed)
    except Exception:
        logging.getLogger(__name__).exception(
            "Target face sheet failed: %s", character["target_asset_id"]
        )
        # Plain aggregate data is not rolled back by the SQL savepoint.
        character.update(reference_state="failed", reference_asset_id=None)
        return False


def feed_references(session, library, recast):
    queued = False
    for character in recast["characters"]:
        if character.get("reference_state") in {None, "making"}:
            queued |= advance_reference(session, library, recast, character)
    return queued


def _sheet_stands(session, recast, character, asset_id):
    _identity, _attempt, _portraits, sheet = reference_operations(session, recast, character)
    return bool(
        sheet
        and sheet["state"] == "succeeded"
        and any(c["asset_id"] == asset_id for c in sheet["candidates"])
    )


def reconcile(session, library, recast, current, entry, changed):
    """After an undo (`recast_edits.undo_edit`, its step 7): the face sheet follows each restored target, as the
    commands do; `changed` names the characters whose target the undo changed."""
    characters = {c["id"]: c for c in recast["characters"]}
    for character_id in changed:
        character = characters[character_id]
        image = entry["before"]["characters"][character_id]
        now = current["characters"].get(character_id)
        if (
            now
            and now["target_asset_id"]
            and now["target_asset_id"] != character["target_asset_id"]
            # A picture gone from the Library leaves no reference work to find and cancel.
            and p.in_library(session, now["target_asset_id"])
        ):
            cancel_reference(session, recast, now)
        character.update(reference_state=None, reference_asset_id=None)
        if character["target_state"] == "confirmed":
            start_target_report(session, library, character["target_asset_id"], retry_failed=True)
            if image["reference_state"] in {"approved", "waiting", "rejected"} and _sheet_stands(
                session, recast, character, image["reference_asset_id"]
            ):
                # The face sheet follows the target; the voice model's feed state is not the target's.
                character.update({k: image[k] for k in ("reference_state", "reference_asset_id")})
            else:
                advance_reference(session, library, recast, character, retry_failed=True)
        elif character["target_state"] == "proposed":
            start_target_report(session, library, character["target_asset_id"])


# --- the voice model -------------------------------------------------------------------------------------------------


def sample_assets(session, library, asset_ids) -> list[dict | None]:
    """Each sample asset, or None for one no longer in the Library."""
    found = []
    for asset_id in asset_ids:
        try:
            found.append(library.get(session, asset_id))
        except ApplicationError:
            found.append(None)
    return found


def sample_shas(session, library, asset_ids) -> list[str] | None:
    """The samples' content hashes in order (the build's key), or None when one left the Library."""
    assets = sample_assets(session, library, asset_ids)
    return None if any(a is None for a in assets) else [a["sha256"] for a in assets]


def current_build(session, library, character) -> tuple[dict | None, int]:
    """(the newest attempt of the shared build of the character's samples, its attempt); (None, 1) without one."""
    shas = sample_shas(session, library, character["voice_asset_ids"] or [])
    if not shas:
        return None, 1
    return _build().latest(session, shas)


def audition_key(recast, character, manifest_artifact_id, attempt):
    return planning.key(recast["id"], character["id"], manifest_artifact_id, "audition", attempt)


def current_audition(session, recast, character, manifest_artifact_id) -> tuple[dict | None, int]:
    """(the newest audition of this character with this voice model, its attempt); (None, 1) without one."""
    return attempts(
        p.operations,
        session,
        lambda n: audition_key(recast, character, manifest_artifact_id, n),
    ).newest()


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
        request_key=audition_key(recast, character, manifest["artifact_id"], attempt),
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


def _feed_voice(session, library, recast, character, report):
    """The feed-owned voice columns from the evidence (ADR 0016 §4): the shared build's state, its manifest, the
    audition (started here, once the build succeeded and enough lines are confirmed) and approval read from the reel's
    provenance. Only `build_voice` and `retry_voice` ever start a build."""
    build, _ = current_build(session, library, character)
    if build is None:
        # These recordings have no build: no model state, even when an undo restored them under another voice's
        # approval (the feed-owned columns survive undo; Astra's P5 review).
        character.update(voice_model_state=None, voice_model_artifact_id=None)
        return False
    if build["state"] != "succeeded":
        character.update(
            voice_model_state="failed" if build_failed(build) else "making",
            voice_model_artifact_id=None,
        )
        return False
    manifest = manifest_of(build)
    if manifest is None:
        character.update(voice_model_state="failed", voice_model_artifact_id=None)
        return False
    previous = character.get("voice_model_state")
    same = character.get("voice_model_artifact_id") == manifest["artifact_id"]
    character["voice_model_artifact_id"] = manifest["artifact_id"]
    audition, attempt = current_audition(session, recast, character, manifest["artifact_id"])
    if audition is None:
        if same and previous == "failed":
            return False  # this voice's audition could not start; the human retry starts it again
        started = start_audition(
            session, library, recast, character, build, attempt=attempt, report=report
        )
        # Without enough confirmed lines nothing starts; the view says what the audition waits for.
        character["voice_model_state"] = "making"
        return bool(started and started.get("_created"))
    if audition["state"] not in p.SETTLED:
        state = "making"
    elif audition_failed(audition):
        state = "failed"
    elif approval_recorded(session, library, recast, character, audition, manifest["artifact_id"]):
        state = "approved"
    elif same and previous in {"approved", "rejected"}:
        state = previous  # the human's decision on this voice persists; a rebuild's new manifest resets it
    else:
        state = "waiting"
    character["voice_model_state"] = state
    return False


def advance_voice(session, library, recast, character, *, report=None):
    """Advance one character's voice: a confirmed, visible voice with recordings. A failure is logged and reads
    failed; it never pauses the recast."""
    if (
        character.get("voice_state") != "confirmed"
        or character["hidden"]
        or not character.get("voice_asset_ids")
    ):
        return False
    try:
        with session.begin_nested():
            return _feed_voice(session, library, recast, character, report)
    except Exception:
        logging.getLogger(__name__).exception("Voice feed failed: %s", character["id"])
        # Plain aggregate data is not rolled back by the SQL savepoint.
        character["voice_model_state"] = "failed"
        return False


def feed_voices(session, library, recast, report):
    queued = False
    for character in recast["characters"]:
        queued |= advance_voice(session, library, recast, character, report=report)
    return queued


# --- the human's decision: one approval for both -------------------------------------------------------------------


@dataclass(frozen=True)
class _Kind:
    nothing: str  # refused when nothing of this kind waits
    waits: Callable[[dict], bool]
    # (session, library, recast, character) -> (the derived asset, its source asset, what made it, the approved columns)
    evidence: Callable


def _sheet_evidence(session, library, recast, character):
    sheet = character["reference_asset_id"]
    _, _, portraits, assembly = reference_operations(session, recast, character)
    if (
        not portraits
        or not assembly
        or not any(c["asset_id"] == sheet for c in assembly["candidates"])
    ):
        raise ApplicationError("Face sheet provenance is missing.", 409)
    made = {
        "type": "generated_reference",
        "subject": "recast_target",
        "source_asset": character["target_asset_id"],
        "portraits_operation": portraits["id"],
        "sheet_operation": assembly["id"],
    }
    return sheet, character["target_asset_id"], made, {"reference_state": "approved"}


def _voice_evidence(session, library, recast, character):
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
    request = audition.get("request") or {}
    made = {
        "type": "generated_voice",
        "subject": "recast_voice",
        "sample_assets": list(character["voice_asset_ids"]),
        "voice_model_artifact_id": manifest["artifact_id"],
        "build_operation": build["id"],
        "audition_operation": audition["id"],
        "source_median_hz": request.get("source_median_hz"),
        "target_median_hz": request.get("target_median_hz"),
        "lines": list(request.get("lines") or []),
    }
    approved = {"voice_model_state": "approved", "voice_model_artifact_id": manifest["artifact_id"]}
    return reel["asset_id"], character["voice_asset_ids"][0], made, approved


KINDS = {
    "reference": _Kind(
        "No face sheet is waiting for approval.",
        lambda c: c.get("reference_state") == "waiting" and c["target_state"] == "confirmed",
        _sheet_evidence,
    ),
    "voice": _Kind(
        "No voice audition is waiting for approval.",
        lambda c: c.get("voice_model_state") == "waiting",
        _voice_evidence,
    ),
}
STATES = {"reference": "reference_state", "voice": "voice_model_state"}


def approve(session, library, recast, character, kind: str) -> None:
    """Human only: approve the derivative of `kind` that waits ("reference" the face sheet, "voice" the audition's
    voice model). Its asset records the derivation (`generated_reference` or `generated_voice` provenance: the recast,
    the character, the operations that made it), which conform, line conversion and the feed read (ADR 0016 §7). An
    approved face sheet's own face report starts."""
    if not KINDS[kind].waits(character):
        raise ApplicationError(KINDS[kind].nothing, 409)
    asset_id, source, made, approved = KINDS[kind].evidence(session, library, recast, character)
    head = {k: made.pop(k) for k in ("type", "subject")}
    library.approve_derivative(
        session,
        asset_id,
        source,
        head
        | {"recast_id": recast["id"], "character_id": character["id"]}
        | made
        | {
            "approved_by": "user",
            "approved_at": timestamp(),
            "recast_revision": recast["revision"] + 1,
        },
    )
    character.update(approved)
    if kind == "reference":
        start_target_report(session, library, asset_id)


def reject(recast, character, kind: str) -> None:
    """Human only: the derivative that waits is not this character's; a retry makes it again."""
    if not KINDS[kind].waits(character):
        raise ApplicationError(KINDS[kind].nothing, 409)
    character[STATES[kind]] = "rejected"
