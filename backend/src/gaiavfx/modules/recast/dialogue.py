"""Dialogue attribution and line edges (ADR 0016 §3): a pure Direction rule with its policy as data.

Plain data in, plain data out. Inputs are transcript segments (seconds, as the transcriber wrote them) with word
times and a speaker embedding, the film's shots with the characters seen in them, the recast's stored line
decisions of the current dialogue analysis, the visible characters and the dialogue stem's 10 ms envelope. Times
inside the rule are seconds; every line edge leaves it as an integer sample through `SampleGrid`.

Ported from `scripts/community_research/voice/trial_voice.py` (`relabel`, `refine`, `onset`, `separated`), with
the failures in VOICE_EXPANSION_2026-10-19 and VOICE_V4_2026-10-20. Identity is the user's decision: a stored
decision always wins, an agent proposal is never re-scored, and the rule only proposes.

Shapes (all plain dicts):

- Segment: {"key", "index", "start", "end", "text", "words": [{"start", "end", "word"}], "ecapa": list | None,
  "median_hz": float | None}
- Shot: {"id", "start_frame", "end_frame", "characters": [ids], "unknown_faces": bool}
- Decision (a stored line row): {"segment_key", "character_id", "state", "proposed_by", "start_sample",
  "end_sample", ...}
- Envelope: {"hop_s", "db": list[float]}
- Attribution (the result per segment): {"key", "character_id", "state", "proposed_by", "start_sample",
  "end_sample", "edges": "rule" | "user", "whispered", "evidence": {"scores", "in_shot", "seed_for", "reason"}}

The voice audition's picks (ADR 0016 §4, P5) live here too: which of a character's confirmed lines a built voice is
auditioned on (`audition_lines`), with their numbers in the policy's `[audition]` table.
"""

import math
import statistics
import tomllib
from dataclasses import dataclass
from fractions import Fraction

from gaiavfx.modules.direction.timebase import SampleGrid

# Research constants that are not policy: a line starts 20 ms before the end of the silence before it, and the
# onset window reads 0.2 s past the anchor word (the rise into it) (trial_voice.onset).
ONSET_LEAD_S = 0.02
ONSET_RISE_S = 0.2
# A segment counts as whispered when at least this share of its word span lies in whispered stretches.
WHISPERED_SHARE = 0.5
REASONS = ("no_embedding", "no_profile", "below_floor", "ambiguous", "not_in_shot")
STATES = ("proposed", "confirmed", "rejected")


@dataclass(frozen=True)
class Attribution:
    """Who said a segment: presence in a shot plus closeness to a voice profile, by a margin."""

    word_padding_s: float
    in_shot_overlap_s: float
    in_shot_min_similarity: float
    margin: float
    strict_similarity: float
    min_profile_segments: int
    min_embedding_s: float
    evidence: str
    note: str


@dataclass(frozen=True)
class Edges:
    """Where a line starts and ends on the dialogue stem's envelope."""

    hop_s: float
    onset_s: float
    decay_s: float
    floor_db: float
    gap_s: float
    gap_below_peak_db: float
    bridge_s: float
    heels_s: float
    evidence: str
    note: str


@dataclass(frozen=True)
class Audition:
    """Which confirmed lines a built voice is auditioned on (P5): up to `count`, once a character has `min_lines`."""

    count: int
    min_lines: int
    min_seconds: float


@dataclass(frozen=True)
class VoicePolicy:
    schema: int
    version: int
    attribution: Attribution
    edges: Edges
    sample_rate: int
    audition_version: int
    audition: Audition


def _keys(table, expected, name):
    if not isinstance(table, dict) or set(table) != set(expected):
        raise ValueError(f"Invalid {name} keys")


def _number(value, name, low, high, *, integer=False, low_open=False):
    if type(value) is bool or type(value) not in ((int,) if integer else (int, float)):
        raise ValueError(f"{name} must be a {'whole ' if integer else ''}number")
    if not math.isfinite(value) or value < low or value > high or (low_open and value == low):
        raise ValueError(f"{name} is out of range")
    return value


def _text(value, name):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be text")
    return value


def parse_voice_policy(text: str) -> VoicePolicy:
    """Strict, model-free parsing: every key present and known, every number finite and in range."""
    data = tomllib.loads(text)
    _keys(
        data,
        {"schema", "version", "audition_version", "attribution", "edges", "timing", "audition"},
        "voice policy",
    )
    if type(data["schema"]) is not int or data["schema"] != 1:
        raise ValueError("The voice policy needs schema = 1")
    version = _number(data["version"], "version", 1, 10**6, integer=True)
    audition_version = _number(data["audition_version"], "audition_version", 1, 10**6, integer=True)
    a = data["attribution"]
    _keys(a, {f.name for f in Attribution.__dataclass_fields__.values()}, "attribution")
    attribution = Attribution(
        word_padding_s=_number(a["word_padding_s"], "word_padding_s", 0, 1),
        in_shot_overlap_s=_number(a["in_shot_overlap_s"], "in_shot_overlap_s", 0, 10),
        in_shot_min_similarity=_number(
            a["in_shot_min_similarity"], "in_shot_min_similarity", -1, 1
        ),
        margin=_number(a["margin"], "margin", 0, 2),
        strict_similarity=_number(a["strict_similarity"], "strict_similarity", -1, 1),
        min_profile_segments=_number(
            a["min_profile_segments"], "min_profile_segments", 1, 10**6, integer=True
        ),
        min_embedding_s=_number(a["min_embedding_s"], "min_embedding_s", 0, 10),
        evidence=_text(a["evidence"], "attribution evidence"),
        note=_text(a["note"], "attribution note"),
    )
    e = data["edges"]
    _keys(e, {f.name for f in Edges.__dataclass_fields__.values()}, "edges")
    edges = Edges(
        hop_s=_number(e["hop_s"], "hop_s", 0, 1, low_open=True),
        onset_s=_number(e["onset_s"], "onset_s", 0, 10),
        decay_s=_number(e["decay_s"], "decay_s", 0, 10),
        floor_db=_number(e["floor_db"], "floor_db", 0, 200, low_open=True),
        gap_s=_number(e["gap_s"], "gap_s", 0, 10, low_open=True),
        gap_below_peak_db=_number(
            e["gap_below_peak_db"], "gap_below_peak_db", 0, 200, low_open=True
        ),
        bridge_s=_number(e["bridge_s"], "bridge_s", 0, 10),
        heels_s=_number(e["heels_s"], "heels_s", 0, 10),
        evidence=_text(e["evidence"], "edges evidence"),
        note=_text(e["note"], "edges note"),
    )
    timing = data["timing"]
    _keys(timing, {"sample_rate"}, "timing")
    rate = _number(timing["sample_rate"], "sample_rate", 1, 10**6, integer=True)
    u = data["audition"]
    _keys(u, {f.name for f in Audition.__dataclass_fields__.values()}, "audition")
    count = _number(u["count"], "audition count", 1, 64, integer=True)
    audition = Audition(
        count=count,
        min_lines=_number(u["min_lines"], "audition min_lines", 1, count, integer=True),
        min_seconds=_number(u["min_seconds"], "audition min_seconds", 0, 60),
    )
    return VoicePolicy(
        schema=1,
        version=version,
        attribution=attribution,
        edges=edges,
        sample_rate=rate,
        audition_version=audition_version,
        audition=audition,
    )


# --- scores ---------------------------------------------------------------------------------------------------------


def _unit(vector):
    norm = math.sqrt(math.sumprod(vector, vector))
    return [v / norm for v in vector] if norm else None


def cosine(a, b) -> float:
    ua, ub = _unit(a), _unit(b)
    return math.sumprod(ua, ub) if ua and ub and len(ua) == len(ub) else 0.0


def word_span(segment, policy: VoicePolicy) -> tuple[float, float]:
    """The segment's own words, padded (whisper's segment can run into a neighbour's line); its edges without words."""
    words = segment.get("words") or []
    if not words:
        return float(segment["start"]), float(segment["end"])
    pad = policy.attribution.word_padding_s
    return max(0.0, float(words[0]["start"]) - pad), float(words[-1]["end"]) + pad


def _embedded(segment, policy):
    return bool(segment.get("ecapa")) and (
        float(segment["end"]) - float(segment["start"]) >= policy.attribution.min_embedding_s
    )


def _shot_index(shots, grid):
    """Shots by start, with exact bounds in seconds; a film's shots never overlap."""
    return sorted(
        (
            (Fraction(s["start_frame"]) / grid.fps, Fraction(s["end_frame"]) / grid.fps, s)
            for s in shots
        ),
        key=lambda row: row[0],
    )


def _overlapping(segment, index, policy):
    start, end = (Fraction(t) for t in word_span(segment, policy))
    need = Fraction(policy.attribution.in_shot_overlap_s)
    low, high = 0, len(index)
    while low < high:  # the first shot that starts at or after the span's end
        middle = (low + high) // 2
        if index[middle][0] < end:
            low = middle + 1
        else:
            high = middle
    found = []
    for first, last, shot in reversed(index[:low]):
        if last <= start:
            break
        if min(end, last) - max(start, first) >= need:
            found.append(shot)
    return found[::-1]


def _present(segment, index, policy):
    present = []
    for shot in _overlapping(segment, index, policy):
        present.extend(c for c in shot["characters"] if c not in present)
    return present


def in_shot(segment, shots, grid: SampleGrid, policy: VoicePolicy) -> list[str]:
    """Characters present where the segment is spoken: in a shot its word span overlaps by the policy's minimum."""
    return _present(segment, _shot_index(shots, grid), policy)


def _rows(decisions):
    return {row["segment_key"]: row for row in decisions or ()}


def seed_segments(segments, shots, decisions, grid: SampleGrid, policy: VoicePolicy) -> dict:
    """{segment key: character id} for the segments that build voice profiles.

    A seed has an embedding and either overlaps a shot where exactly one character and no unknown face is seen (a
    segment overlapping two such shots of different characters seeds nobody), or is a confirmed line of that
    character. A rejected line is evidence for nobody; an agent proposal is not a decision.
    """
    return _seeds(segments, _shot_index(shots, grid), decisions, policy)


def _seeds(segments, index, decisions, policy):
    rows = _rows(decisions)
    result = {}
    for segment in segments:
        if not _embedded(segment, policy):
            continue
        row = rows.get(segment["key"])
        if row and row["state"] == "rejected":
            continue
        if row and row["state"] == "confirmed":
            result[segment["key"]] = row["character_id"]
            continue
        solos = {
            shot["characters"][0]
            for shot in _overlapping(segment, index, policy)
            if len(set(shot["characters"])) == 1 and not shot["unknown_faces"]
        }
        if len(solos) == 1:
            result[segment["key"]] = solos.pop()
    return result


def _profiles_from(seeds, segments, policy):
    members = {}
    for segment in segments:
        character = seeds.get(segment["key"])
        if character is not None:
            members.setdefault(character, []).append(segment)
    result = {}
    for character, group in members.items():
        units = [u for u in (_unit(s["ecapa"]) for s in group) if u]
        if len(units) < policy.attribution.min_profile_segments:
            continue
        mean = [math.fsum(column) / len(units) for column in zip(*units, strict=True)]
        vector = _unit(mean)
        if vector is None:
            continue
        seconds = math.fsum(b - a for a, b in (word_span(s, policy) for s in group))
        result[character] = {"vector": vector, "segments": len(units), "seconds": seconds}
    return result


def profiles(segments, shots, decisions, grid: SampleGrid, policy: VoicePolicy) -> dict:
    """{character id: {"vector", "segments", "seconds"}} for characters with enough seed segments."""
    return _profiles_from(seed_segments(segments, shots, decisions, grid, policy), segments, policy)


# --- edges ----------------------------------------------------------------------------------------------------------


def _frame(seconds, hop):
    return math.floor(seconds / hop + 1e-6)


def _frame_up(seconds, hop):
    return math.ceil(seconds / hop - 1e-6)


def _frames(seconds, hop):
    return round(seconds / hop)


def refine_edges(envelope, start_s, end_s, before_s, after_s, policy: VoicePolicy):
    """Extend a span along the envelope: back toward its onset breath (at most `onset_s`, never before `before_s`)
    and on to its decay (at most `decay_s`, never past `after_s`) while the stem stays within `floor_db` of the
    span's peak. Dips shorter than `bridge_s` are bridged (the stop closure in "space"). Never shrinks the span."""
    edges, hop, db = policy.edges, envelope["hop_s"], envelope["db"]
    count = len(db)
    a = max(0, _frame(start_s, hop))
    b = max(a, _frame_up(end_s, hop))
    low = max(0, min(a, max(_frame_up(before_s, hop), a - _frames(edges.onset_s, hop))))
    high = min(count, max(b, min(_frame(after_s, hop), b + _frames(edges.decay_s, hop))))
    if high <= low:
        return float(start_s), float(end_s)
    a, b = min(max(a, low), high), min(max(b, low), high)
    floor = max(db[a:b] if b > a else db[low:high]) - edges.floor_db
    bridge = _frames(edges.bridge_s, hop)
    i = a
    while i > low:
        back = next(
            (k for k in range(i - 1, max(low - 1, i - 1 - bridge), -1) if db[k] > floor), None
        )
        if back is None:
            break
        i = back
    j = b
    while j < high:
        ahead = next((k for k in range(j, min(high, j + bridge)) if db[k] > floor), None)
        if ahead is None:
            break
        j = ahead + 1
    return min(float(start_s), i * hop), max(float(end_s), j * hop)


def onset(envelope, start_s, anchor_s, before_s, policy: VoicePolicy) -> float:
    """Where a line really starts: after the last silence of at least `gap_s` (`gap_below_peak_db` under the
    window's peak) before its second word (`anchor_s`); without one, at `start_s`. Whisper stretches a first word
    back over a neighbour's tail ("I" 388.40–389.36 s for an "I know" said at 389.3 s)."""
    edges, hop, db = policy.edges, envelope["hop_s"], envelope["db"]
    low = max(0, _frame(max(before_s, start_s - edges.onset_s), hop))
    high = min(len(db), _frame(anchor_s + ONSET_RISE_S, hop))
    if high - low < 3:
        return float(start_s)
    window = db[low:high]
    floor = max(window) - edges.gap_below_peak_db
    gap = _frames(edges.gap_s, hop)
    run, last = 0, None
    for i, value in enumerate(window):
        run = run + 1 if value < floor else 0
        if run >= gap and i + 1 < len(window) and window[i + 1] >= floor:
            last = i + 1
    return (low + last) * hop - ONSET_LEAD_S if last is not None else float(start_s)


def separated(envelope, t0, t1, policy: VoicePolicy) -> bool:
    """Whether a silence of at least `gap_s` (`gap_below_peak_db` under the stretch's peak) lies between t0 and t1."""
    edges, hop, db = policy.edges, envelope["hop_s"], envelope["db"]
    low, high = max(0, _frame(max(0.0, t0), hop)), min(len(db), _frame(t1, hop))
    if high - low < 3:
        return False
    window = db[low:high]
    floor = max(window) - edges.gap_below_peak_db
    gap = _frames(edges.gap_s, hop)
    run = 0
    for value in window:
        run = run + 1 if value < floor else 0
        if run >= gap:
            return True
    return False


def _anchor(segment, span):
    words = segment.get("words") or []
    if len(words) > 1:
        return float(words[1]["start"])
    return float(words[0]["end"]) if words else span[1]


def line_edges(
    segments, speakers, user, envelope, policy: VoicePolicy
) -> list[tuple[float, float]]:
    """Each segment's span in seconds, in transcript order.

    `speakers[i]` is the character the segment is attributed to (proposed or confirmed) or None; `user[i]` is the
    user's edges in seconds or None. User edges replace the rule's and bound the neighbours' extensions. A line never
    extends past the previous segment's padded word end or the next one's padded word start, except that before
    another speaker's line it runs on to that line's onset; its first word is trimmed to its onset only right on the
    heels of another speaker (within `heels_s`, no `gap_s` silence between the two).
    """
    edges = policy.edges
    spans = [word_span(s, policy) for s in segments]
    result = []
    for k, segment in enumerate(segments):
        if user[k] is not None:
            result.append(tuple(float(t) for t in user[k]))
            continue
        start, end = spans[k]
        previous = k - 1 if k else None
        following = k + 1 if k + 1 < len(segments) else None
        me = speakers[k]

        def other(i, me=me):
            return speakers[i] is not None and speakers[i] != me

        before = 0.0
        if previous is not None:
            before = float(user[previous][1]) if user[previous] else spans[previous][1]
        after = end + 2.0
        if following is not None:
            after = float(user[following][0]) if user[following] else spans[following][0]
            if user[following] is None and other(following):
                # The other speaker's inflated first word must not clip this line's decay.
                late = onset(
                    envelope,
                    spans[following][0],
                    _anchor(segments[following], spans[following]),
                    end,
                    policy,
                )
                after = max(after, late)
        words = segment.get("words") or []
        heels = previous is not None and other(previous) and start - before < edges.heels_s
        if heels and words and segments[previous].get("words"):
            heels = not separated(
                envelope,
                float(segments[previous]["words"][-1]["end"]),
                float(words[0]["end"]),
                policy,
            )
        first = (
            onset(envelope, start, _anchor(segment, spans[k]), before, policy) if heels else start
        )
        if first > start:
            # A silence before the second word: the line starts there, with no backward extension.
            result.append((first, refine_edges(envelope, first, end, first, after, policy)[1]))
        else:
            result.append(refine_edges(envelope, start, end, before, after, policy))
    return result


# --- attribution ----------------------------------------------------------------------------------------------------


def _whispered(span, whisper_spans):
    length = span[1] - span[0]
    if length <= 0:
        return False
    inside = math.fsum(
        max(0.0, min(span[1], float(b)) - max(span[0], float(a))) for a, b in whisper_spans or ()
    )
    return inside >= WHISPERED_SHARE * length


def _propose(scores, present, visible, policy):
    """(character, reason) for a segment with an embedding and no stored decision."""
    a = policy.attribution

    def runner_up(character):
        return max((s for c, s in scores.items() if c != character), default=-1.0)

    candidates = [c for c in present if c in scores and c in visible]
    reason = None
    if candidates:
        best = max(candidates, key=lambda c: (scores[c], c))
        if scores[best] < a.in_shot_min_similarity:
            reason = "below_floor"
        elif scores[best] < runner_up(best) + a.margin:
            reason = "ambiguous"
        else:
            return best, None
    elif present and not any(c in scores for c in present):
        reason = "no_profile"
    # The strict match proposes anywhere, in a shot or not.
    shown = [c for c in scores if c in visible]
    if shown:
        best = max(shown, key=lambda c: (scores[c], c))
        if scores[best] >= a.strict_similarity and scores[best] >= runner_up(best) + a.margin:
            return best, None
    return None, reason or ("not_in_shot" if scores else "no_profile")


def _overlaps(a, b):
    return a[0] < b[1] and b[0] < a[1]


def _clip(span, fixed):
    start, end = span
    for other in fixed:
        if not _overlaps((start, end), other):
            continue
        if other[0] <= start:
            start = max(start, other[1])
        else:
            end = min(end, other[0])
    return start, max(start, end)


def attribute(
    segments,
    shots,
    decisions,
    visible,
    envelope,
    whisper_spans,
    grid: SampleGrid,
    policy: VoicePolicy,
    *,
    profiles=None,
    total_samples=None,
) -> list[dict]:
    """One attribution per segment, in transcript order (see the module docstring for the shapes).

    A stored decision always wins: confirmed is that character with its edges, rejected proposes nobody, and an
    agent proposal is shown as the agent's. Otherwise the best visible character present in the shot is proposed
    when it clears `in_shot_min_similarity` and beats every other profile (hidden ones too) by `margin`; anywhere,
    a character is proposed only at `strict_similarity` and the same margin. Lines never overlap a confirmed line:
    user-edged lines are fixed first, then confirmed lines in transcript order, and every other span is clipped.
    """
    rows = _rows(decisions)
    visible = set(visible)
    index = _shot_index(shots, grid)
    seeds = _seeds(segments, index, decisions, policy)
    known = _profiles_from(seeds, segments, policy) if profiles is None else profiles
    units = {c: p["vector"] for c, p in known.items()}
    results = []
    for segment in segments:
        present = _present(segment, index, policy)
        scores = {}
        if _embedded(segment, policy) and units:
            vector = _unit(segment["ecapa"])
            if vector:
                scores = {
                    c: math.sumprod(vector, u) if len(u) == len(vector) else 0.0
                    for c, u in units.items()
                }
        row = rows.get(segment["key"])
        character = state = proposed_by = reason = None
        if row is not None:
            character, state, proposed_by = row["character_id"], row["state"], row["proposed_by"]
        elif not _embedded(segment, policy):
            reason = "no_embedding"
        else:
            character, reason = _propose(scores, present, visible, policy)
            if character is not None:
                state, proposed_by = "proposed", "analysis"
        results.append(
            {
                "key": segment["key"],
                "character_id": character,
                "state": state,
                "proposed_by": proposed_by,
                "evidence": {
                    "scores": {c: round(s, 4) for c, s in sorted(scores.items())},
                    "in_shot": present,
                    "seed_for": seeds.get(segment["key"]),
                    "reason": reason,
                },
            }
        )
    speakers = [
        r["character_id"] if r["state"] in {"proposed", "confirmed"} else None for r in results
    ]
    user_samples = []
    for segment, result in zip(segments, results, strict=True):
        row = rows.get(segment["key"])
        edged = (
            row is not None
            and result["state"] in {"proposed", "confirmed"}
            and row.get("start_sample") is not None
            and row.get("end_sample") is not None
        )
        user_samples.append((row["start_sample"], row["end_sample"]) if edged else None)
    user = [
        None
        if s is None
        else (float(grid.seconds_of_sample(s[0])), float(grid.seconds_of_sample(s[1])))
        for s in user_samples
    ]
    spans_s = line_edges(segments, speakers, user, envelope, policy)
    spans = []
    for span_s, edged in zip(spans_s, user_samples, strict=True):
        if edged is not None:
            spans.append(tuple(edged))
            continue
        start, end = (max(0, grid.sample_of_seconds(t)) for t in span_s)
        if total_samples is not None:
            start, end = min(start, total_samples), min(end, total_samples)
        spans.append((start, max(start, end)))
    fixed = [
        spans[k]
        for k, edged in enumerate(user_samples)
        if edged and results[k]["state"] == "confirmed"
    ]
    for k, result in enumerate(results):
        if result["state"] == "confirmed" and user_samples[k] is None:
            spans[k] = _clip(spans[k], fixed)
            fixed.append(spans[k])
    for k, result in enumerate(results):
        if result["state"] != "confirmed":
            spans[k] = _clip(spans[k], fixed)
    for segment, result, span, edged in zip(segments, results, spans, user_samples, strict=True):
        result.update(
            start_sample=span[0],
            end_sample=span[1],
            edges="user" if edged else "rule",
            whispered=_whispered(word_span(segment, policy), whisper_spans),
        )
        if result["proposed_by"] == "analysis" and span[0] >= span[1]:
            # A confirmed line's edges cover it completely: no audio is left to propose (Astra R-P4).
            result.update(character_id=None, state=None, proposed_by=None)
            result["evidence"]["reason"] = "covered"
    return results


# --- the voice audition ---------------------------------------------------------------------------------------------


def _film_order(line):
    return (int(line["start_sample"]), int(line["end_sample"]), str(line["segment_key"]))


def _length(line) -> int:
    return int(line["end_sample"]) - int(line["start_sample"])


def _peak_db(line, envelope) -> float:
    """The loudest 10 ms frame of the dialogue stem over the line's span (frames from integer samples)."""
    hop, db = int(envelope["hop"]), envelope["db"]
    first = int(line["start_sample"]) // hop
    last = max(first + 1, -(-int(line["end_sample"]) // hop))
    values = db[first:last]
    return float(max(values)) if len(values) else -math.inf


def _voiced(line) -> bool:
    return line.get("median_hz") is not None


def audition_lines(lines, envelope, *, count: int, min_seconds: float, sample_rate: int) -> list:
    """Up to `count` of a character's confirmed lines for a voice audition, in pick order.

    Each pick skips the lines already taken: the highest median pitch; the loudest (the dialogue stem's peak over
    the line); the longest whispered line, when there is one; the line whose median is nearest the character's (the
    median of the lines' medians); the shortest line of at least `min_seconds`; then the remaining slots spread evenly
    over the rest in film order. A pick with no candidate is passed over. Lines are `line_views` dicts (integer
    samples at `sample_rate`); the envelope is the stem's {"hop" (samples per frame), "db"}. A line without audio
    (end <= start) is never picked; ties go to the earlier line, so the result is deterministic.
    """
    pool = sorted((line for line in lines if _length(line) > 0), key=_film_order)
    taken: list[int] = []
    shortest = math.ceil(float(min_seconds) * int(sample_rate) - 1e-9)
    medians = [float(line["median_hz"]) for line in pool if _voiced(line)]
    centre = statistics.median(medians) if medians else None

    def pick(candidates, score):
        rest = [k for k in candidates if k not in taken]
        if rest and len(taken) < count:
            taken.append(min(rest, key=lambda k: (score(pool[k]), k)))

    indices = range(len(pool))
    pick([k for k in indices if _voiced(pool[k])], lambda line: -float(line["median_hz"]))
    pick(indices, lambda line: -_peak_db(line, envelope))
    pick([k for k in indices if pool[k].get("whispered")], lambda line: -_length(line))
    if centre is not None:
        pick(
            [k for k in indices if _voiced(pool[k])],
            lambda line: abs(float(line["median_hz"]) - centre),
        )
    pick([k for k in indices if _length(pool[k]) >= shortest], _length)
    rest = [k for k in indices if k not in taken]
    slots = min(max(0, count - len(taken)), len(rest))
    # Evenly spread: the middle of each of `slots` equal runs of the remaining lines (distinct while slots <= rest).
    fillers = [rest[(2 * n + 1) * len(rest) // (2 * slots)] for n in range(slots)]
    return [pool[k] for k in [*taken, *fillers]]
