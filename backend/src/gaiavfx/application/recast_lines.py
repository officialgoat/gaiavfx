"""Dialogue lines of a recast (ADR 0016 §1-§3 and §8): the film's shared dialogue analysis, the rule's computed
proposals and the user's line decisions.

A stored row exists only for an agent proposal or a user decision, keyed to one dialogue analysis; the analysis's
own proposals are computed views, cached against the operation, the rule, the shots' cast, the rows of that
operation and the visible characters, and never written. Confirm, reject, clear, edge and bulk confirmation are
human only and journaled like cast edits (recast_edits). Nothing here starts an analysis except the two human
commands; reading the state finds the analysis another recast of the film ran, it never starts one.

`recast` and the shared analysis module are imported at call time: `recast` imports this module, and the test world
replaces the analysis module through `sys.modules` (as it replaces film_analysis for `report_for`).
"""

import json
import logging
from bisect import bisect_right
from copy import deepcopy
from fractions import Fraction
from functools import cache
from hashlib import sha256
from importlib import import_module
from types import SimpleNamespace

from gaiavfx.application import recast_edits as journaling
from gaiavfx.application import recast_projection as p
from gaiavfx.infrastructure.database import timestamp
from gaiavfx.modules.direction import dialogue as rule
from gaiavfx.modules.direction.application import default_voice_policy, voice_policy_text
from gaiavfx.modules.direction.timebase import SampleGrid
from gaiavfx.shared.errors import ApplicationError

ANALYSE_FIRST = "Analyse the dialogue before deciding lines."
WAIT_FOR_FILM = "Wait for the film analysis to finish."
NOT_FOUND = "Line not found in this dialogue analysis."
COVERED = "A confirmed line's edges cover this line completely. Change those edges first."
STRENGTH = {"confirmed": 2, "rejected": 1, "proposed": 0}
ZERO = {"segments": 0, "proposed": 0, "confirmed": 0, "rejected": 0}


def _evidence():
    return import_module("gaiavfx.application.dialogue_analysis")


def _commands():
    from gaiavfx.application import recast

    return recast


@cache
def _policy_sha():
    return sha256(voice_policy_text().encode("utf-8")).hexdigest()


# --- reading ----------------------------------------------------------------------------------------------------------


def current_operation(session, library, recast):
    """(the film's current dialogue analysis or None, its attempt, the film's audio gap or None)."""
    evidence = _evidence()
    asset = library.get(session, recast["source_asset_id"])
    operation, attempt = evidence.latest(session, asset)
    return operation, attempt, evidence.audio_gap(asset)


def current_id(session, library, recast):
    operation = current_operation(session, library, recast)[0]
    return operation["id"] if operation else None


def _film_ready(session, recast):
    operation_id = recast["analysis_operation_id"]
    return (
        bool(operation_id) and p.operations.operation(session, operation_id)["state"] == "succeeded"
    )


def dialogue_status(session, library, recast, current=None):
    """DialogueStatusDTO: the shared analysis's progress, and why the Voice step cannot start yet (D2 text)."""
    evidence = _evidence()
    operation, attempt, gap = current or current_operation(session, library, recast)
    reason = None
    if operation is None:
        if gap:
            reason = gap["message"]
        elif not _film_ready(session, recast):
            reason = WAIT_FOR_FILM
    return {
        "operation_id": operation["id"] if operation else None,
        **evidence.dialogue_view(operation),
        "attempt": attempt,
        "version": evidence.DIALOGUE_VERSION,
        "sample_rate": evidence.SAMPLE_RATE,
        "reason": reason,
        "gap": gap,
    }


def shot_cast(session, library, recast, report, appearances=None):
    """Rule shots: the characters the recast sees in each shot (any coverage, confirmed or not) and whether an
    unknown face is there. `appearances` ({shot id: groups}) reuses what `recast_state` already computed."""
    shots = []
    for shot in report["shots"] if report else []:
        shot_id = p.shot_id(shot)
        groups = (appearances or {}).get(shot_id)
        if groups is None:
            stored = next(s for s in recast["shots"] if s["shot_id"] == shot_id)
            groups = p.appearances(session, library, recast, report, shot, stored)
        shots.append(
            {
                "id": shot_id,
                "start_frame": shot["start"],
                "end_frame": shot["end"],
                "characters": [g["character_id"] for g in groups if g["character_id"]],
                "unknown_faces": any(g["character_id"] is None for g in groups),
            }
        )
    return shots


def _grid(report):
    return SampleGrid(_evidence().SAMPLE_RATE, Fraction(*report["fps"]))


def _rows(recast, operation_id):
    return sorted(
        (r for r in recast.get("lines", []) if r["dialogue_operation_id"] == operation_id),
        key=lambda r: r["segment_key"],
    )


def _segments(dialogue):
    empty = {"ecapa": None, "median_hz": None}
    return [{**s, **dialogue["speakers"].get(s["key"], empty)} for s in dialogue["segments"]]


def attributions(session, library, recast, report, shots, operation, dialogue):
    """{"lines": the rule's attribution per segment, "profiles": {character: {segments, seconds}}}, cached.

    Read-only: the result is shared by every reader until a decision, the cast, the visible characters, the rule or
    the analysis changes.
    """
    policy = default_voice_policy()
    grid = _grid(report)
    rows = _rows(recast, operation["id"])
    visible = sorted(c["id"] for c in recast["characters"] if not c["hidden"])
    key = p.digest(
        [
            p.operation_token(operation),
            policy.version,
            _policy_sha(),
            shots,
            rows,
            visible,
            report["fps"],
            report["frame_count"],
        ]
    )
    cached = p.cache(library, "lines")
    if key not in cached:
        segments = _segments(dialogue)
        known = rule.profiles(segments, shots, rows, grid, policy)
        envelope = dialogue["envelope"]
        lines = rule.attribute(
            segments,
            shots,
            rows,
            set(visible),
            {"hop_s": envelope["hop"] / dialogue["sample_rate"], "db": envelope["db"]},
            dialogue["whisper_spans"],
            grid,
            policy,
            profiles=known,
            total_samples=grid.total_samples(report["frame_count"]),
        )
        summary = {
            c: {"segments": v["segments"], "seconds": round(v["seconds"], 3)}
            for c, v in known.items()
        }
        p.remember(cached, key, {"lines": lines, "profiles": summary}, limit=4)
    cached.move_to_end(key)
    return cached[key]


def counts(lines):
    return {
        "segments": len(lines),
        "proposed": sum(line["state"] == "proposed" for line in lines),
        "confirmed": sum(line["state"] == "confirmed" for line in lines),
        "rejected": sum(line["state"] == "rejected" for line in lines),
    }


def per_character(lines):
    """{character: {"lines_proposed", "lines_confirmed"}}; computed proposals count as proposed."""
    result = {}
    for line in lines:
        if line["character_id"] and line["state"] in {"proposed", "confirmed"}:
            entry = result.setdefault(
                line["character_id"], {"lines_proposed": 0, "lines_confirmed": 0}
            )
            entry["lines_" + line["state"]] += 1
    return result


def _ready(session, library, recast, report, current, appearances=None):
    """(operation, report, shots) when lines can be computed, else None."""
    operation = current[0]
    if operation is None or operation["state"] != "succeeded" or not report:
        return None
    dialogue = _evidence().dialogue_report(session, library, operation["id"])
    if dialogue is None:
        return None
    return operation, dialogue, shot_cast(session, library, recast, report, appearances)


def recast_parts(session, library, recast, report, appearances=None):
    """What `recast_state` adds: (DialogueStatusDTO, LineCountsDTO, {character: counts})."""
    current = current_operation(session, library, recast)
    status = dialogue_status(session, library, recast, current)
    try:
        ready = _ready(session, library, recast, report, current, appearances)
        if ready is None:
            return status, dict(ZERO), {}
        computed = attributions(session, library, recast, report, ready[2], ready[0], ready[1])
    except ApplicationError:
        logging.getLogger(__name__).exception("Dialogue lines unavailable: %s", recast["id"])
        return status, dict(ZERO), {}
    return status, counts(computed["lines"]), per_character(computed["lines"])


def line_views(session, library, recast, report, current=None, appearances=None):
    """LinesDTO: every transcript segment as a line, in samples at 48 kHz and in film frames; no artifact ids."""
    current = current or current_operation(session, library, recast)
    operation = current[0]
    result = {
        "dialogue_operation_id": operation["id"] if operation else None,
        "rule_version": default_voice_policy().version,
        "sample_rate": _evidence().SAMPLE_RATE,
        "lines": [],
        "profiles": {},
    }
    ready = _ready(session, library, recast, report, current, appearances)
    if ready is None:
        return result
    operation, dialogue, shots = ready
    computed = attributions(session, library, recast, report, shots, operation, dialogue)
    grid = _grid(report)
    rows = {r["segment_key"]: r for r in _rows(recast, operation["id"])}
    segments = {s["key"]: s for s in dialogue["segments"]}
    ordered = sorted(shots, key=lambda s: s["start_frame"])
    starts = [s["start_frame"] for s in ordered]
    for attribution in computed["lines"]:
        key = attribution["key"]
        segment = segments[key]
        start, end = attribution["start_sample"], attribution["end_sample"]
        start_frame = grid.frame_of(start)
        at = bisect_right(starts, start_frame) - 1
        shot = ordered[at] if at >= 0 and start_frame < ordered[at]["end_frame"] else None
        result["lines"].append(
            {
                "segment_key": key,
                "index": segment["index"],
                "shot_id": shot["id"] if shot else None,
                "start_sample": start,
                "end_sample": end,
                "start_frame": start_frame,
                "end_frame": grid.frame_of(end - 1) + 1 if end > start else start_frame,
                "text": segment["text"],
                "words": [
                    {
                        "start_sample": grid.sample_of_seconds(w["start"]),
                        "end_sample": grid.sample_of_seconds(w["end"]),
                        "word": w["word"],
                    }
                    for w in segment["words"]
                ],
                "character_id": attribution["character_id"],
                "state": attribution["state"],
                "proposed_by": attribution["proposed_by"],
                "edges": attribution["edges"],
                "whispered": attribution["whispered"],
                "median_hz": dialogue["speakers"].get(key, {}).get("median_hz"),
                "evidence": deepcopy(attribution["evidence"]),
                "decided_at": rows[key]["decided_at"] if key in rows else None,
            }
        )
    result["profiles"] = deepcopy(computed["profiles"])
    return result


def lines_state(session, library, recast_id):
    """GET /lines: read only, never locks, saves or starts anything."""
    recast, report = _commands()._load(session, library, recast_id)
    return line_views(session, library, recast, report)


# --- the analysis (human only) -----------------------------------------------------------------------------------------


def _startable(session, library, recast):
    operation, _attempt, gap = current_operation(session, library, recast)
    if gap:
        raise ApplicationError(gap["message"], 422)
    if not _film_ready(session, recast):
        raise ApplicationError(WAIT_FOR_FILM, 409)
    return operation


def start_dialogue_analysis(session, library, recast_id, *, expected_revision):
    """Human only: start (or find) the film's shared dialogue analysis. Asked for, never automatic."""
    commands = _commands()
    recast, _ = commands._load(session, library, recast_id, expected_revision)
    _startable(session, library, recast)
    _evidence().start_dialogue_analysis(session, library, recast["source_asset_id"])
    return commands._finish(session, library, recast)


def retry_dialogue_analysis(session, library, recast_id, *, expected_revision):
    """Human only: a new attempt after the current one settled without success."""
    commands = _commands()
    recast, _ = commands._load(session, library, recast_id, expected_revision)
    operation = _startable(session, library, recast)
    if (
        operation is None
        or operation["state"] not in p.SETTLED
        or operation["state"] == "succeeded"
    ):
        raise ApplicationError(
            "The dialogue analysis is running or finished; nothing to retry.", 409
        )
    _evidence().start_dialogue_analysis(
        session, library, recast["source_asset_id"], retry_failed=True
    )
    return commands._finish(session, library, recast)


# --- line decisions (human only, journaled) ----------------------------------------------------------------------------


def _deciding(session, library, recast_id, expected_revision):
    """Load under the revision lock and refuse before any write unless the current analysis has succeeded."""
    commands = _commands()
    recast, report = commands._load(session, library, recast_id, expected_revision)
    current = current_operation(session, library, recast)
    operation = current[0]
    if operation is None or operation["state"] != "succeeded":
        raise ApplicationError(ANALYSE_FIRST, 409)
    if not report:
        raise ApplicationError(WAIT_FOR_FILM, 409)
    dialogue = _evidence().dialogue_report(session, library, operation["id"])
    return SimpleNamespace(
        session=session,
        library=library,
        commands=commands,
        recast=recast,
        report=report,
        operation=operation,
        dialogue=dialogue,
        journal=journaling.capture(recast, report, dialogue_operation_id=operation["id"]),
    )


def _computed(ctx):
    shots = shot_cast(ctx.session, ctx.library, ctx.recast, ctx.report)
    return attributions(
        ctx.session, ctx.library, ctx.recast, ctx.report, shots, ctx.operation, ctx.dialogue
    )


def _segment(ctx, segment_key):
    found = next((s for s in ctx.dialogue["segments"] if s["key"] == segment_key), None)
    if found is None:
        raise ApplicationError(NOT_FOUND, 404)
    return found


def _character(ctx, character_id, *, visible):
    character = ctx.commands._character(ctx.recast, character_id)
    if visible and character["hidden"]:
        raise ApplicationError("Show the character before giving it lines.", 422)
    return character


def _row(ctx, segment_key):
    return next(
        (
            r
            for r in ctx.recast["lines"]
            if r["dialogue_operation_id"] == ctx.operation["id"] and r["segment_key"] == segment_key
        ),
        None,
    )


def _new(ctx, segment_key, character_id, state, proposed_by="user", start=None, end=None):
    now = timestamp()
    row = {
        "recast_id": ctx.recast["id"],
        "dialogue_operation_id": ctx.operation["id"],
        "segment_key": segment_key,
        "character_id": character_id,
        "state": state,
        "proposed_by": proposed_by,
        "start_sample": start,
        "end_sample": end,
        "evidence": {},
        "created_at": now,
        "decided_at": None if state == "proposed" else now,
    }
    ctx.recast["lines"].append(row)
    return row


def _decide(ctx, segment_key, character_id, state):
    """The row now names this character in this state; a row for another character is replaced."""
    row = _row(ctx, segment_key)
    if row is not None and row["character_id"] == character_id:
        row.update(state=state, decided_at=timestamp())
        return row
    if row is not None:
        ctx.recast["lines"].remove(row)
    return _new(ctx, segment_key, character_id, state)


def _journaled(ctx, command, segment=None, **facts):
    return ctx.commands._journaled(
        ctx.session,
        ctx.library,
        ctx.recast,
        ctx.journal,
        command,
        text=segment["text"] if segment else "",
        **facts,
    )


def _uncovered(ctx, segment_key):
    """Refuse a line a confirmed line's edges cover completely: it has no audio left (Astra R-P4)."""
    line = next(item for item in _computed(ctx)["lines"] if item["key"] == segment_key)
    if line["start_sample"] >= line["end_sample"]:
        raise ApplicationError(COVERED, 409)


def _no_overlap(ctx, decided):
    """Refuse a decision that brings back user edges over another confirmed line's (Astra R-P4); the sentence names
    the line in the way, not the one being decided."""
    if pair := journaling.overlapping_edges(ctx.recast["lines"]):
        other = pair[1] if pair[0]["segment_key"] in decided else pair[0]
        raise ApplicationError(
            f"These edges overlap the confirmed line {other['segment_key']}. Change those edges first.",
            409,
        )


def confirm_line(session, library, recast_id, segment_key, character_id, *, expected_revision):
    """Human only: this line is the character's."""
    ctx = _deciding(session, library, recast_id, expected_revision)
    segment = _segment(ctx, segment_key)
    _character(ctx, character_id, visible=True)
    _uncovered(ctx, segment_key)
    _decide(ctx, segment_key, character_id, "confirmed")
    _no_overlap(ctx, {segment_key})
    return _journaled(ctx, "confirm_line", segment, character_id=character_id)


def reject_line(session, library, recast_id, segment_key, character_id, *, expected_revision):
    """Human only: this line is not the character's."""
    ctx = _deciding(session, library, recast_id, expected_revision)
    segment = _segment(ctx, segment_key)
    _character(ctx, character_id, visible=False)
    _decide(ctx, segment_key, character_id, "rejected")
    return _journaled(ctx, "reject_line", segment, character_id=character_id)


def clear_line(session, library, recast_id, segment_key, *, expected_revision):
    """Human only: remove the line's decision (or agent proposal); the computed proposal returns."""
    ctx = _deciding(session, library, recast_id, expected_revision)
    segment = _segment(ctx, segment_key)
    row = _row(ctx, segment_key)
    if row is None:
        raise ApplicationError("This line has no decision to clear.", 404)
    ctx.recast["lines"].remove(row)
    return _journaled(ctx, "clear_line", segment)


def _transcript_span(ctx, segment):
    grid = _grid(ctx.report)
    words = rule.word_span(segment, default_voice_policy())
    return (
        grid.sample_of_seconds(min(segment["start"], words[0])),
        grid.sample_of_seconds(max(segment["end"], words[1])),
    )


def edge_line(
    session, library, recast_id, segment_key, *, expected_revision, start_sample, end_sample
):
    """Human only: the line's edges in samples at 48 kHz. Edges confirm the line; both null remove the user's
    edges and keep the decision. Lines of one analysis never overlap a confirmed line."""
    ctx = _deciding(session, library, recast_id, expected_revision)
    segment = _segment(ctx, segment_key)
    row = _row(ctx, segment_key)
    if row is not None and row["state"] == "rejected":
        raise ApplicationError("This line is rejected; confirm it before setting its edges.", 409)
    if start_sample is None and end_sample is None:
        if row is None or row["start_sample"] is None:
            raise ApplicationError("This line has no edges to remove.", 409)
        row.update(start_sample=None, end_sample=None)
        return _journaled(ctx, "edge_line", segment, removed=True)
    if start_sample is None or end_sample is None:
        raise ApplicationError("Set both edges, or neither.", 422)
    total = _grid(ctx.report).total_samples(ctx.report["frame_count"])
    if (
        type(start_sample) is not int
        or type(end_sample) is not int
        or not 0 <= start_sample < end_sample <= total
    ):
        raise ApplicationError(
            "The edges must be inside the film, with the start before the end.", 422
        )
    first, last = _transcript_span(ctx, segment)
    if not (start_sample < last and first < end_sample):
        raise ApplicationError("The edges must overlap the line's words.", 422)
    computed = _computed(ctx)
    for line in computed["lines"]:
        if (
            line["key"] != segment_key
            and line["state"] == "confirmed"
            and start_sample < line["end_sample"]
            and line["start_sample"] < end_sample
        ):
            raise ApplicationError(f"These edges overlap the confirmed line {line['key']}.", 409)
    if row is None:
        proposal = next(line for line in computed["lines"] if line["key"] == segment_key)
        if proposal["character_id"] is None or proposal["state"] != "proposed":
            raise ApplicationError(
                "Confirm the line for a character before setting its edges.", 409
            )
        _character(ctx, proposal["character_id"], visible=True)
        _new(
            ctx,
            segment_key,
            proposal["character_id"],
            "confirmed",
            start=start_sample,
            end=end_sample,
        )
    else:
        _character(ctx, row["character_id"], visible=True)
        row.update(
            state="confirmed",
            start_sample=start_sample,
            end_sample=end_sample,
            decided_at=timestamp(),
        )
    return _journaled(ctx, "edge_line", segment, removed=False)


def confirm_lines(
    session, library, recast_id, *, expected_revision, character_id, segment_keys=None
):
    """Human only: confirm a character's proposed lines in one journaled edit: every computed proposal for the
    character that has no row, or exactly the listed lines (each proposed for the character)."""
    ctx = _deciding(session, library, recast_id, expected_revision)
    _character(ctx, character_id, visible=True)
    lines = _computed(ctx)["lines"]
    if segment_keys is None:
        keys = [
            line["key"]
            for line in lines
            if line["state"] == "proposed"
            and line["proposed_by"] == "analysis"
            and line["character_id"] == character_id
        ]
    else:
        by_key = {line["key"]: line for line in lines}
        keys = list(dict.fromkeys(segment_keys))
        for key in keys:
            if key not in by_key:
                raise ApplicationError(NOT_FOUND, 404)
            if by_key[key]["state"] != "proposed" or by_key[key]["character_id"] != character_id:
                raise ApplicationError(f"Line {key} is not proposed for this character.", 422)
    if not keys:
        raise ApplicationError("No lines are proposed for this character.", 409)
    for key in keys:
        _decide(ctx, key, character_id, "confirmed")
    _no_overlap(ctx, set(keys))
    return _journaled(ctx, "confirm_lines", character_id=character_id, count=len(keys))


def propose_line(
    session,
    library,
    recast_id,
    segment_key,
    character_id,
    *,
    expected_revision,
    proposed_by,
    evidence,
    user_message="",
):
    """A proposal (P9's agent tool; no route): never over a decision; an agent must have the user's naming."""
    ctx = _deciding(session, library, recast_id, expected_revision)
    _segment(ctx, segment_key)
    _character(ctx, character_id, visible=True)
    if proposed_by not in {"agent", "user"}:
        raise ApplicationError("Unknown line proposer.", 422)
    if proposed_by == "agent" and not ctx.commands.named_character(
        ctx.recast, character_id, user_message
    ):
        raise ApplicationError("The user must name the character before an agent proposal.", 422)
    if not isinstance(evidence, dict):
        raise ApplicationError("Line evidence must be a JSON object.", 422)
    try:
        json.dumps(evidence, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ApplicationError("Line evidence must be finite JSON.", 422) from exc
    row = _row(ctx, segment_key)
    if row is not None and row["state"] in {"confirmed", "rejected"}:
        raise ApplicationError("Clear the line decision before proposing again.", 409)
    if row is None:
        row = _new(ctx, segment_key, character_id, "proposed", proposed_by)
    row.update(
        character_id=character_id,
        proposed_by=proposed_by,
        start_sample=None,
        end_sample=None,
        evidence=deepcopy(evidence),
        decided_at=None,
    )
    return ctx.commands._finish(session, library, ctx.recast)  # a proposal is not the user's edit


# --- merges -----------------------------------------------------------------------------------------------------------


def merge_rows(rows, sources, into):
    """Line rows after merging `sources` into `into`: the sources' rows name `into`; when two rows meet on one
    segment of one analysis, the stronger state stays (confirmed, then rejected, then proposed; a tie keeps the
    row that already named `into`). The store's key allows one row per segment, so a meeting cannot come from
    stored rows today; the rule stays explicit for P5+ writers."""
    kept = {}
    for row in rows:
        moved = {**row, "character_id": into} if row["character_id"] in sources else row
        identity = (row["dialogue_operation_id"], row["segment_key"])
        other = kept.get(identity)
        if other is None:
            kept[identity] = moved
            continue
        stronger = STRENGTH[moved["state"]] - STRENGTH[other["state"]]
        if stronger > 0 or (stronger == 0 and row["character_id"] == into):
            kept[identity] = moved
    return list(kept.values())


def merge_touches_lines(recast, sources):
    return any(row["character_id"] in sources for row in recast.get("lines", []))
