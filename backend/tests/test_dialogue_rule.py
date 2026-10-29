"""ADR 0016 §3: the pure dialogue rule, its policy and the sample grid (A1–A5). No I/O beyond the policy file."""

import math
import random
import time
from copy import deepcopy
from fractions import Fraction
from itertools import pairwise

import pytest

from gaiavfx.modules.direction import dialogue
from gaiavfx.modules.direction.application import default_voice_policy, voice_policy_text
from gaiavfx.modules.direction.dialogue import (
    attribute,
    line_edges,
    onset,
    parse_voice_policy,
    profiles,
    refine_edges,
    separated,
)
from gaiavfx.modules.direction.timebase import FrameGrid, SampleGrid

POLICY = default_voice_policy()
GRID = SampleGrid(48000, Fraction(24))
RATES = (Fraction(24), Fraction(25), Fraction(24000, 1001), Fraction(30000, 1001))


# --- A1 the sample grid ---------------------------------------------------------------------------------------------


def test_sample_grid_is_exact_at_every_supported_rate():
    expected = (2000, 1920, 2002, Fraction(8008, 5))
    for fps, per_frame in zip(RATES, expected, strict=True):
        grid = SampleGrid(48000, fps)
        assert grid.samples_per_frame == per_frame
        firsts = [grid.first_sample(f) for f in range(20000)]
        assert all(grid.frame_of(s) == f for f, s in enumerate(firsts))
        assert all(b > a for a, b in pairwise(firsts))
        assert grid.total_samples(20000) == grid.first_sample(20000)
    assert GRID.total_samples(17620) == 35_240_000
    assert GRID.sample_of_seconds(Fraction(1, 3)) == 16000
    assert SampleGrid(48000, Fraction(30000, 1001)).sample_of_seconds(Fraction(1, 3)) == 16000
    # Round half up on the exact value, and frames from samples by floor.
    assert GRID.sample_of_seconds(Fraction(1, 96000)) == 1
    assert GRID.sample_of_seconds(Fraction(-1, 96000)) == 0
    assert GRID.seconds_of_sample(24000) == Fraction(1, 2)
    assert SampleGrid(48000, Fraction(30000, 1001)).first_sample(1) == 1602
    assert SampleGrid(48000, Fraction(30000, 1001)).frame_of(1601) == 0
    for bad in ((0, Fraction(24)), (48000, Fraction(0)), (48000.0, Fraction(24))):
        with pytest.raises(ValueError):
            SampleGrid(*bad)
    # FrameGrid is untouched: integer fps only.
    assert FrameGrid(24, 5, 8, 0).frames(Fraction(1, 2)) == 12


# --- A2 the policy --------------------------------------------------------------------------------------------------


def test_voice_policy_parses_strictly():
    text = voice_policy_text()
    policy = parse_voice_policy(text)
    assert policy == POLICY
    assert policy.version == 1 and policy.sample_rate == 48000
    assert policy.attribution.in_shot_min_similarity == 0.10
    assert policy.edges.bridge_s == 0.15
    assert policy.audition_version == 1
    assert policy.audition == dialogue.Audition(count=8, min_lines=3, min_seconds=0.6)
    for broken in (
        text.replace("count = 8", "count = 0"),  # an audition plays at least one line
        text.replace("min_lines = 3", "min_lines = 9"),  # never more lines needed than played
        text.replace("min_seconds = 0.6", "min_seconds = -1"),
        text.replace("audition_version = 1", "audition_version = 1.5"),
        text.replace("[audition]", "[auditions]"),  # an unknown table, the audition missing
        text.replace("margin = 0.02\n", ""),  # a missing key
        text.replace("[timing]", "[timing]\nextra = 1"),  # an unknown key
        text.replace("margin = 0.02", "margin = -0.02"),  # a negative margin
        text.replace("floor_db = 45.0", "floor_db = nan"),  # not finite
        text.replace("decay_s = 0.8", "decay_s = inf"),
        text.replace("min_profile_segments = 2", "min_profile_segments = 2.5"),
        text.replace("schema = 1", "schema = 2"),
        text.replace("hop_s = 0.01", "hop_s = 0"),
        text.replace("sample_rate = 48000", "sample_rate = true"),
    ):
        assert broken != text
        with pytest.raises(ValueError):
            parse_voice_policy(broken)


# --- helpers --------------------------------------------------------------------------------------------------------


def unit(*values, dims=8):
    vector = list(values) + [0.0] * (dims - len(values))
    rest = 1.0 - math.fsum(v * v for v in values)
    vector[len(values)] = math.sqrt(max(rest, 0.0))
    return vector


def segment(index, start, end, ecapa=None, words=None, text="line"):
    words = words if words is not None else [{"start": start, "end": end, "word": text}]
    return {
        "key": f"{index:05d}-{round(start * 1000)}",
        "index": index,
        "start": start,
        "end": end,
        "text": text,
        "words": words,
        "ecapa": ecapa,
        "median_hz": 120.0,
    }


def shot(shot_id, start_s, end_s, *characters, unknown=False):
    return {
        "id": shot_id,
        "start_frame": round(start_s * 24),
        "end_frame": round(end_s * 24),
        "characters": list(characters),
        "unknown_faces": unknown,
    }


def envelope(seconds=20.0, value=-90.0):
    return {"hop_s": 0.01, "db": [value] * round(seconds * 100)}


def paint(env, start_s, end_s, value):
    env["db"][round(start_s * 100) : round(end_s * 100)] = [value] * (
        round(end_s * 100) - round(start_s * 100)
    )
    return env


PROFILES = {
    "A": {"vector": unit(1.0, dims=8), "segments": 3, "seconds": 3.0},
    "B": {"vector": [0.0, 1.0] + [0.0] * 6, "segments": 3, "seconds": 3.0},
}


def one(segment_, shots=(), decisions=(), visible=("A", "B"), known=PROFILES):
    return attribute(
        [segment_],
        list(shots),
        list(decisions),
        set(visible),
        envelope(),
        [],
        GRID,
        POLICY,
        profiles=known,
    )[0]


def scored(a, b):
    """An embedding scoring exactly `a` against A's profile and `b` against B's."""
    return unit(a, b)


# --- A3 attribution -------------------------------------------------------------------------------------------------


def test_attribution_in_shot_strict_floor_margin_and_embedding():
    solo_a = [shot("s0001", 0, 5, "A")]
    line = one(segment(0, 1.0, 2.0, scored(0.30, 0.20)), solo_a)
    assert (line["character_id"], line["state"], line["proposed_by"]) == (
        "A",
        "proposed",
        "analysis",
    )
    assert line["edges"] == "rule" and line["evidence"]["in_shot"] == ["A"]
    assert line["evidence"]["scores"] == {"A": 0.3, "B": 0.2}
    assert line["evidence"]["reason"] is None
    ambiguous = one(segment(0, 1.0, 2.0, scored(0.21, 0.20)), solo_a)
    assert ambiguous["character_id"] is None and ambiguous["evidence"]["reason"] == "ambiguous"
    low = one(segment(0, 1.0, 2.0, scored(0.08, 0.00)), solo_a)
    assert low["character_id"] is None and low["evidence"]["reason"] == "below_floor"
    strict = one(segment(0, 1.0, 2.0, scored(0.50, 0.10)))
    assert strict["character_id"] == "A" and strict["evidence"]["in_shot"] == []
    outside = one(segment(0, 1.0, 2.0, scored(0.40, 0.10)))
    assert outside["character_id"] is None and outside["evidence"]["reason"] == "not_in_shot"
    silent = one(segment(0, 1.0, 2.0, None), solo_a)
    assert silent["character_id"] is None and silent["evidence"]["reason"] == "no_embedding"
    assert silent["evidence"]["scores"] == {}
    # Shorter than the embedding minimum reads as no embedding even if a vector came back.
    short = one(segment(0, 1.0, 1.2, scored(0.9, 0.0)), solo_a)
    assert short["evidence"]["reason"] == "no_embedding"
    # Presence needs 0.3 s of overlap with the shot.
    edge = one(segment(0, 4.8, 6.0, scored(0.30, 0.20)), solo_a)
    assert edge["evidence"]["in_shot"] == [] and edge["character_id"] is None


def test_hidden_characters_compete_but_are_never_proposed():
    both = [shot("s0001", 0, 5, "A", "B")]
    hidden_wins = one(segment(0, 1.0, 2.0, scored(0.60, 0.30)), both, visible={"B"})
    assert hidden_wins["character_id"] is None
    assert hidden_wins["evidence"]["reason"] == "ambiguous"
    assert hidden_wins["evidence"]["scores"]["A"] == 0.6
    runner_up = one(segment(0, 1.0, 2.0, scored(0.30, 0.60)), both, visible={"B"})
    assert runner_up["character_id"] == "B"
    close = one(segment(0, 1.0, 2.0, scored(0.30, 0.31)), both, visible={"B"})
    assert close["character_id"] is None and close["evidence"]["reason"] == "ambiguous"
    # A hidden strict match anywhere proposes nobody.
    assert one(segment(0, 1.0, 2.0, scored(0.9, 0.0)), visible={"B"})["character_id"] is None


def test_profiles_need_enough_seed_segments():
    shots = [
        shot("s0001", 0, 5, "A"),
        shot("s0002", 5, 10, "B"),
        shot("s0003", 10, 15, "C"),
        shot("s0004", 15, 20, "A", "C"),
        shot("s0005", 20, 25, "B", unknown=True),
    ]
    segments = [
        segment(0, 0.5, 1.5, scored(0.9, 0.1)),
        segment(1, 2.0, 3.0, scored(0.8, 0.1)),
        segment(2, 5.5, 6.5, scored(0.1, 0.9)),
        segment(3, 7.0, 8.0, scored(0.1, 0.8)),
        segment(4, 11.0, 12.0, unit(0.0, 0.0, 1.0)),  # C's only seed
        segment(5, 13.0, 14.0, None),
        segment(6, 16.0, 17.0, unit(0.0, 0.0, 0.9)),  # A and C both seen: not a seed
        segment(7, 21.0, 22.0, scored(0.1, 0.9)),  # an unknown face: not a seed
        segment(8, 4.6, 5.4, scored(0.9, 0.1)),  # overlaps A's and B's solo shots: nobody's seed
    ]
    known = profiles(segments, shots, [], GRID, POLICY)
    assert set(known) == {"A", "B"}
    assert known["A"]["segments"] == 2 and known["B"]["segments"] == 2
    assert known["A"]["seconds"] == pytest.approx(2.2)
    lines = attribute(segments, shots, [], {"A", "B", "C"}, envelope(30), [], GRID, POLICY)
    by_index = {line["key"][:5]: line for line in lines}
    assert by_index["00004"]["character_id"] is None
    assert by_index["00004"]["evidence"]["reason"] == "no_profile"
    assert by_index["00004"]["evidence"]["seed_for"] == "C"
    assert by_index["00000"]["evidence"]["seed_for"] == "A"
    assert by_index["00008"]["evidence"]["seed_for"] is None
    assert by_index["00000"]["character_id"] == "A" and by_index["00002"]["character_id"] == "B"


# --- A4 decisions ---------------------------------------------------------------------------------------------------


def row(segment_, character, state, proposed_by="user", start=None, end=None):
    return {
        "segment_key": segment_["key"],
        "character_id": character,
        "state": state,
        "proposed_by": proposed_by,
        "start_sample": start,
        "end_sample": end,
        "evidence": {},
    }


def test_stored_decisions_win_and_feed_profiles_without_changing():
    solo_a = [shot("s0001", 0, 5, "A")]
    line = segment(0, 1.0, 2.0, scored(0.9, 0.1))
    confirmed = one(line, solo_a, [row(line, "B", "confirmed")])
    assert (confirmed["character_id"], confirmed["state"], confirmed["proposed_by"]) == (
        "B",
        "confirmed",
        "user",
    )
    rejected = one(line, solo_a, [row(line, "A", "rejected")])
    assert (rejected["character_id"], rejected["state"]) == ("A", "rejected")
    assert rejected["proposed_by"] == "user"
    agent = one(line, solo_a, [row(line, "B", "proposed", "agent")])
    assert (agent["character_id"], agent["state"], agent["proposed_by"]) == (
        "B",
        "proposed",
        "agent",
    )
    # A confirmed line joins its character's profile; rejected lines are evidence for nobody.
    shots = [shot("s0001", 0, 5, "A")]
    segments = [
        segment(0, 0.5, 1.5, scored(0.9, 0.1)),
        segment(1, 2.0, 3.0, scored(0.8, 0.1)),
        segment(2, 10.0, 11.0, scored(0.7, 0.2)),
    ]
    decisions = [row(segments[2], "A", "confirmed")]
    stored = deepcopy(decisions)
    assert profiles(segments, shots, [], GRID, POLICY)["A"]["segments"] == 2
    assert profiles(segments, shots, decisions, GRID, POLICY)["A"]["segments"] == 3
    assert decisions == stored
    rejecting = [row(segments[0], "A", "rejected")]
    assert "A" not in profiles(segments, shots, rejecting, GRID, POLICY)


def test_confirmed_lines_never_overlap():
    env = paint(envelope(), 0.5, 6.0, -20.0)
    segments = [
        segment(0, 1.0, 2.0, scored(0.9, 0.1)),
        segment(1, 2.6, 3.6, scored(0.9, 0.1)),
        segment(2, 4.2, 5.2, scored(0.9, 0.1)),
    ]
    decisions = [row(segments[0], "A", "confirmed"), row(segments[1], "A", "confirmed")]
    lines = attribute(segments, [], decisions, {"A"}, env, [], GRID, POLICY, profiles=PROFILES)
    assert lines[0]["end_sample"] <= lines[1]["start_sample"]
    # User edges are fixed first; the rule's spans of every other line are clipped to them.
    decisions.append(row(segments[2], "A", "confirmed", start=150_000, end=260_000))
    lines = attribute(segments, [], decisions, {"A"}, env, [], GRID, POLICY, profiles=PROFILES)
    assert lines[2]["edges"] == "user" and lines[2]["start_sample"] == 150_000
    assert lines[1]["end_sample"] <= 150_000
    spans = sorted((line["start_sample"], line["end_sample"]) for line in lines)
    assert all(a[1] <= b[0] for a, b in pairwise(spans))
    assert all(line["start_sample"] <= line["end_sample"] for line in lines)


# --- A5 edges -------------------------------------------------------------------------------------------------------


def test_short_dips_are_bridged_and_long_dips_end_the_line():
    env = paint(paint(paint(envelope(), 1.0, 2.0, -20.0), 2.0, 2.04, -75.0), 2.04, 2.3, -20.0)
    paint(env, 2.3, 2.5, -50.0)
    start, end = refine_edges(env, 1.0, 2.0, 0.0, 10.0, POLICY)
    assert start == pytest.approx(1.0) and end == pytest.approx(2.5)
    env = paint(paint(paint(envelope(), 1.0, 2.0, -20.0), 2.0, 2.2, -75.0), 2.2, 2.5, -20.0)
    assert refine_edges(env, 1.0, 2.0, 0.0, 10.0, POLICY) == pytest.approx((1.0, 2.0))


def test_backward_extension_stops_at_onset_and_the_previous_word():
    env = paint(envelope(), 0.0, 3.0, -20.0)
    assert refine_edges(env, 2.0, 2.5, 0.0, 2.5, POLICY)[0] == pytest.approx(1.5)
    assert refine_edges(env, 2.0, 2.5, 1.8, 2.5, POLICY)[0] == pytest.approx(1.8)


def test_forward_extension_stops_at_another_speakers_onset():
    env = paint(paint(paint(envelope(), 1.0, 2.0, -10.0), 2.0, 2.52, -45.0), 2.52, 2.6, -52.0)
    paint(env, 2.6, 3.5, -15.0)
    first = segment(0, 1.0, 2.0, words=[{"start": 1.0, "end": 2.0, "word": "line"}])
    inflated = segment(
        1,
        2.2,
        3.5,
        words=[{"start": 2.2, "end": 2.9, "word": "I"}, {"start": 3.0, "end": 3.5, "word": "know"}],
    )
    assert onset(env, 2.15, 3.0, 2.05, POLICY) == pytest.approx(2.58)
    other = line_edges([first, inflated], ["A", "B"], [None, None], env, POLICY)
    same = line_edges([first, inflated], ["A", "A"], [None, None], env, POLICY)
    assert other[0][1] == pytest.approx(2.58)
    assert same[0][1] == pytest.approx(2.15)


def heels_case(previous_words, envelope_):
    previous = segment(
        0, previous_words[0]["start"], previous_words[-1]["end"], words=previous_words
    )
    line = segment(
        1,
        2.15,
        3.2,
        words=[
            {"start": 2.15, "end": 2.6, "word": "I"},
            {"start": 2.7, "end": 3.2, "word": "know"},
        ],
    )
    return previous, line


def test_heels_trim_only_after_another_speaker_without_silence():
    env = paint(paint(paint(envelope(), 1.0, 2.0, -20.0), 2.0, 2.6, -25.0), 2.7, 3.2, -20.0)
    words = [{"start": 1.0, "end": 2.0, "word": "tail"}]
    previous, line = heels_case(words, env)
    assert not separated(env, 2.0, 2.6, POLICY)
    trimmed = line_edges([previous, line], ["B", "A"], [None, None], env, POLICY)
    assert trimmed[1][0] == pytest.approx(2.68)
    # The same speaker before: no trim (the start keeps its word start or extends back).
    kept = line_edges([previous, line], ["A", "A"], [None, None], env, POLICY)
    assert kept[1][0] <= 2.10 + 1e-9
    # A silence of 80 ms or more between the two speakers: no trim.
    parted = paint(deepcopy(env), 2.3, 2.4, -90.0)
    assert separated(parted, 2.0, 2.6, POLICY)
    assert (
        line_edges([previous, line], ["B", "A"], [None, None], parted, POLICY)[1][0] <= 2.10 + 1e-9
    )
    # Not on the heels (0.3 s or more after the previous line's end): no trim.
    early, line = heels_case([{"start": 0.5, "end": 1.7, "word": "tail"}], env)
    assert line_edges([early, line], ["B", "A"], [None, None], env, POLICY)[1][0] <= 2.10 + 1e-9


def test_edges_never_shrink_the_padded_word_span():
    assert refine_edges(envelope(), 1.0, 2.0, 1.5, 1.2, POLICY) == pytest.approx((1.0, 2.0))
    rng = random.Random(7)
    env = {"hop_s": 0.01, "db": [rng.uniform(-100, -10) for _ in range(3000)]}
    for _ in range(300):
        start = rng.uniform(0, 25)
        end = start + rng.uniform(0.05, 3)
        before = rng.uniform(max(0, start - 1), start + 0.5)
        after = rng.uniform(end - 0.5, end + 1)
        out = refine_edges(env, start, end, before, after, POLICY)
        assert out[0] <= start + 1e-12 and out[1] >= end - 1e-12
    segments = [segment(i, 1.0 + 2 * i, 1.8 + 2 * i) for i in range(6)]
    for span, item in zip(line_edges(segments, [None] * 6, [None] * 6, env, POLICY), segments):
        padded = dialogue.word_span(item, POLICY)
        assert span[0] <= padded[0] + 1e-12 and span[1] >= padded[1] - 1e-12


def test_user_edges_on_a_neighbour_clip_the_extension():
    env = paint(envelope(), 0.5, 5.0, -20.0)
    segments = [segment(0, 1.0, 2.0), segment(1, 3.0, 4.0)]
    free = line_edges(segments, ["A", "A"], [None, None], env, POLICY)
    assert free[0][1] == pytest.approx(2.85)  # the decay limit, 0.8 s after the padded word end
    clipped = line_edges(segments, ["A", "A"], [None, (2.5, 4.0)], env, POLICY)
    assert clipped[0][1] == pytest.approx(2.5) and clipped[1] == (2.5, 4.0)


def test_whispered_spans_and_samples():
    line = segment(0, 1.0, 2.0, scored(0.9, 0.1))
    spoken = paint(envelope(), 1.0, 2.0, -20.0)
    result = attribute(
        [line], [], [], {"A"}, spoken, [[0.9, 1.8]], GRID, POLICY, profiles=PROFILES
    )[0]
    assert result["whispered"] is True
    assert isinstance(result["start_sample"], int) and result["start_sample"] == 45_600  # 0.95 s
    assert result["end_sample"] == 98_400  # 2.05 s
    assert not attribute(
        [line], [], [], {"A"}, spoken, [[1.9, 2.5]], GRID, POLICY, profiles=PROFILES
    )[0]["whispered"]
    capped = attribute([line], [], [], {"A"}, envelope(), [], GRID, POLICY, total_samples=90_000)
    assert capped[0]["end_sample"] == 90_000


def test_two_thousand_segments_attribute_in_under_two_seconds():
    rng = random.Random(3)
    dims = 192

    def vector():
        return [rng.gauss(0, 1) for _ in range(dims)]

    known = {
        f"c{i}": {"vector": dialogue._unit(vector()), "segments": 5, "seconds": 9.0}
        for i in range(10)
    }
    segments = []
    for i in range(2000):
        start = 1.0 + 3.6 * i
        words = [
            {"start": start, "end": start + 0.4, "word": "a"},
            {"start": start + 0.5, "end": start + 1.4, "word": "b"},
        ]
        segments.append(segment(i, start, start + 1.5, vector(), words=words))
    shots = [shot(f"s{i:04d}", 7.2 * i, 7.2 * (i + 1), f"c{i % 10}") for i in range(1000)]
    env = {"hop_s": 0.01, "db": [rng.uniform(-90, -15) for _ in range(730_000)]}
    began = time.perf_counter()
    lines = attribute(segments, shots, [], set(known), env, [], GRID, POLICY, profiles=known)
    assert time.perf_counter() - began < 2.0
    assert len(lines) == 2000


# --- A30 the audition picks -----------------------------------------------------------------------------------------

RATE = 48000
# (key, start s, seconds, median Hz, whispered): L01 is the highest, L02 the loudest (painted below), L04 the one
# whispered line, L07 sits on the median of the medians (160 Hz), L03 is the shortest but under 0.6 s, L05 the
# shortest of at least 0.6 s.
AUDITION = [
    ("L00", 0, 1.0, 110.0, False),
    ("L01", 5, 1.2, 250.0, False),
    ("L02", 10, 1.5, 120.0, False),
    ("L03", 15, 0.4, 130.0, False),
    ("L04", 20, 2.0, None, True),
    ("L05", 25, 0.7, 140.0, False),
    ("L06", 30, 1.1, 150.0, False),
    ("L07", 35, 1.3, 160.0, False),
    ("L08", 40, 1.4, 170.0, False),
    ("L09", 45, 0.9, 180.0, False),
    ("L10", 50, 1.6, 190.0, False),
    ("L11", 55, 1.0, 200.0, False),
]


def audition_fixture(rows=AUDITION):
    lines, db = [], [-90.0] * 6000  # 60 s of 10 ms frames
    for key, start, seconds, median, whispered in rows:
        first, last = start * RATE, start * RATE + round(seconds * RATE)
        lines.append(
            {
                "segment_key": key,
                "start_sample": first,
                "end_sample": last,
                "median_hz": median,
                "whispered": whispered,
                "state": "confirmed",
                "character_id": "c1",
            }
        )
        db[first // 480 : -(-last // 480)] = [-5.0 if key == "L02" else -40.0] * (
            -(-last // 480) - first // 480
        )
    return lines, {"hop": 480, "db": db}


def keys(lines):
    return [line["segment_key"] for line in lines]


def test_the_audition_picks_each_kind_of_line_then_spreads_the_rest():
    lines, envelope_ = audition_fixture()
    picks = dialogue.audition_lines(lines, envelope_, count=8, min_seconds=0.6, sample_rate=RATE)
    # highest, loudest, whispered, nearest the median, shortest >= 0.6 s, then three spread over the rest in film
    # order (the middle of each of three equal runs of the seven left, L00 L03 L06 L08 L09 L10 L11: indices 1, 3, 5).
    assert keys(picks) == ["L01", "L02", "L04", "L07", "L05", "L03", "L08", "L10"]
    assert all(any(pick is line for line in lines) for pick in picks)  # the caller's own dicts
    # Deterministic: input order and repetition change nothing.
    shuffled = list(lines)
    random.Random(7).shuffle(shuffled)
    again = dialogue.audition_lines(shuffled, envelope_, count=8, min_seconds=0.6, sample_rate=RATE)
    assert keys(again) == keys(picks)
    # A line without audio is never picked, whatever its pitch.
    silent = {
        **lines[0],
        "segment_key": "L99",
        "end_sample": lines[0]["start_sample"],
        "median_hz": 900.0,
    }
    assert "L99" not in keys(
        dialogue.audition_lines(
            [*lines, silent], envelope_, count=8, min_seconds=0.6, sample_rate=RATE
        )
    )


def test_the_audition_takes_what_there_is_and_never_a_short_line_as_the_shortest():
    lines, envelope_ = audition_fixture(AUDITION[:5])
    picks = dialogue.audition_lines(lines, envelope_, count=8, min_seconds=0.6, sample_rate=RATE)
    # Five lines give five picks: the median of 110, 250, 120, 130 is 125 (L02 is taken, so L03), and the shortest
    # line of at least 0.6 s is L00, never the 0.4 s L03.
    assert keys(picks) == ["L01", "L02", "L04", "L03", "L00"]
    lines, envelope_ = audition_fixture()
    # No line is long enough to be the "shortest": that pick is passed over and a spread line takes the slot.
    picks = dialogue.audition_lines(lines, envelope_, count=5, min_seconds=10.0, sample_rate=RATE)
    assert keys(picks) == ["L01", "L02", "L04", "L07", "L08"]
    assert dialogue.audition_lines([], envelope_, count=8, min_seconds=0.6, sample_rate=RATE) == []
    # Without a whispered line or any measured pitch, the loudest and the shortest still pick.
    plain = [{**line, "median_hz": None, "whispered": False} for line in lines[:3]]
    assert keys(
        dialogue.audition_lines(plain, envelope_, count=2, min_seconds=0.6, sample_rate=RATE)
    ) == ["L02", "L00"]
