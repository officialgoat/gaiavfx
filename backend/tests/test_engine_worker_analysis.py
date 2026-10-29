"""The analysis worker's decision rules (ADR 0016 P3c), checked in the app's environment: the worker's module level is
standard library only, and its settings are the voice spec's. The models themselves run in the GPU smoke batch."""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from gaiavfx.integrations.voice import spec

WORKERS = Path(__file__).parents[2] / "engine_workers"


@pytest.fixture(scope="module")
def analysis():
    sys.path.insert(0, str(WORKERS))
    try:
        found = importlib.util.spec_from_file_location("analysis_worker", WORKERS / "analysis.py")
        module = importlib.util.module_from_spec(found)
        found.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(WORKERS))


@pytest.fixture
def rules():
    params = spec.settings("analysis.filter")
    _, chosen = spec.calibration(14.0)
    return params, chosen["categories"]


def test_module_level_is_standard_library():
    tree = ast.parse((WORKERS / "analysis.py").read_text(encoding="utf-8"))
    top = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            top.add((node.module or "").split(".")[0])
    assert top <= {"__future__", "json", "os", "re", "sys", "time", "pathlib", "protocol"}, top


def test_whispered_windows_merge_into_spans(analysis):
    whisper = analysis.AST_VOCAL_CLASSES.index("Whispering")
    vocal = [0.0] * len(analysis.AST_VOCAL_CLASSES)
    vocal[0], vocal[whisper] = 0.6, 0.3
    assert analysis.whispered(vocal, whisper, 0.25, 2)
    assert not analysis.whispered(vocal, whisper, 0.35, 2)
    vocal[1] = 0.5
    assert not analysis.whispered(vocal, whisper, 0.25, 2), "a third-ranked whisper is speech"
    hits = [[0.0, 1.0], [0.25, 1.25], [0.5, 1.5], [3.0, 3.4]]
    assert analysis.merge_spans(hits, 0.5) == [[0.0, 1.5]]


def test_character_error_rate_ignores_case_punctuation_and_tags(analysis):
    assert analysis.cer("Hello, world!", "hello world") == 0
    assert analysis.cer("", "anything") == 0.0
    assert analysis.cer("abc", "abd") == pytest.approx(1 / 3)
    line = analysis.clean_text("(sighs) I [laugh] told you <whisper>so")
    assert analysis.cer(line, "I told you so") == 0


def test_quiet_frames_and_the_longest_quiet_run(analysis):
    fraction, longest = analysis.quiet_stats([0, 0, -50, -50, -50, 0], 45.0, -65.0, 0.01)
    assert fraction == 0.5
    assert longest == pytest.approx(0.03)
    # A very quiet clip is measured against the floor, not its own peak.
    fraction, _ = analysis.quiet_stats([-60, -70, -70], 45.0, -65.0, 0.01)
    assert fraction == pytest.approx(2 / 3)


def test_identity_thresholds_follow_the_calibration(analysis, rules):
    params, categories = rules
    assert analysis.identity_threshold("scream", False, categories, params) == 0.055
    # gasp borrows fear's calibration, capped for nonverbal clips
    assert analysis.identity_threshold("gasp", False, categories, params) == 0.20
    assert analysis.identity_threshold("neutral", True, categories, params) == 0.35
    assert analysis.identity_threshold("unknown", True, categories, params) == 0.35


def test_a_nonverbal_category_does_not_fit_plain_talk(analysis, rules):
    params, categories = rules
    sob = analysis.fitting("sob", categories, params)
    assert {"Crying, sobbing", "Gasp", "Snort"} <= sob
    assert not sob & {"Speech", "Conversation", "Narration, monologue"}
    assert "Speech" in analysis.fitting("sad", categories, params)


def keep_scores():
    return {
        "seconds": 3.0,
        "silent_fraction": 0.1,
        "longest_silence_s": 0.2,
        "ecapa_to_seed": 0.6,
        "ast_top3": ["Speech", "Conversation", "Sigh"],
        "cer": 0.05,
        "mos": 3.1,
    }


@pytest.mark.parametrize(
    ("change", "check"),
    [
        ({"seconds": 0.2}, "duration"),
        ({"seconds": 25.0}, "duration"),
        ({"silent_fraction": 0.7}, "silence"),
        ({"longest_silence_s": 2.0}, "silence"),
        ({"ecapa_to_seed": 0.1}, "identity"),
        ({"ast_top3": ["Laughter", "Giggle", "Snicker"]}, "delivery"),
        ({"cer": 0.5}, "words"),
        ({"mos": 1.2}, "quality"),
    ],
)
def test_each_filter_check_fails_on_its_own(analysis, rules, change, check):
    params, categories = rules
    row = {"id": "c1", "category": "neutral", "kind": "speech", "text": "A line."}
    assert analysis.filter_checks(row, keep_scores(), params, categories) == []
    assert analysis.filter_checks(row, {**keep_scores(), **change}, params, categories) == [check]


def test_a_converted_clip_must_move_toward_the_target_and_keep_its_delivery(analysis, rules):
    params, categories = rules
    row = {"id": "c2", "category": "scream", "kind": "nonverbal", "source": "/clips/source.wav"}
    scores = {
        **keep_scores(),
        "ecapa_to_seed": 0.30,
        "source_ecapa_to_seed": 0.27,
        "ast_top3": ["Screaming", "Yell", "Shout"],
        "source_ast_top3": ["Gasp", "Breathing", "Pant"],
        "ast_vocal_cos_source": 0.5,
    }
    assert analysis.filter_checks(row, scores, params, categories) == [
        "identity_not_moved",
        "delivery",
    ]
    moved = {**scores, "source_ecapa_to_seed": 0.10, "ast_vocal_cos_source": 0.9}
    assert analysis.filter_checks(row, moved, params, categories) == []


def test_a_source_id_makes_a_converted_clip(analysis, rules):
    """ADR 0016 P5: a converted library clip names its source by id; it gets the converted-clip checks."""
    params, categories = rules
    row = {"id": "lib0001", "category": "scream", "kind": "nonverbal", "source_id": "lib0001-src"}
    assert analysis.converted(row) and analysis.converted({"source": "/clips/s.wav"})
    assert not analysis.converted({"id": "tts"})
    scores = {
        **keep_scores(),
        "ecapa_to_seed": 0.30,
        "source_ecapa_to_seed": 0.27,
        "ast_top3": ["Screaming", "Yell", "Shout"],
        "source_ast_top3": ["Screaming", "Shout", "Yell"],
        "ast_vocal_cos_source": 0.95,
    }
    assert analysis.filter_checks(row, scores, params, categories) == ["identity_not_moved"]


def test_manifest_rows_without_paths_read_their_clip_inputs(analysis, tmp_path):
    inputs = {
        "clip:seed00": tmp_path / "seed00.wav",
        "clip:tts1": tmp_path / "tts1.wav",
        "clip:lib0001": tmp_path / "lib0001.wav",
        "clip:lib0001-src": tmp_path / "source.wav",
    }
    seed = {"seconds": 9.0, "clips": [{"id": "seed00", "text": "One."}, "/s/two.wav"]}
    assert analysis.seed_paths(seed, inputs) == [tmp_path / "seed00.wav", Path("/s/two.wav")]
    assert analysis.seed_paths([{"text": "unnamed"}], inputs) == [tmp_path / "seed00.wav"]
    assert analysis.row_files({"id": "tts1", "category": "sob"}, inputs) == (
        tmp_path / "tts1.wav",
        None,
    )
    converted = {"id": "lib0001", "category": "scream", "source_id": "lib0001-src"}
    assert analysis.row_files(converted, inputs) == (
        tmp_path / "lib0001.wav",
        tmp_path / "source.wav",
    ), "the source comparison reads clip:<source_id>"
    old = {"id": "x", "path": "/t/x.wav", "source": "/real/x.wav"}
    assert analysis.row_files(old, inputs) == (Path("/t/x.wav"), Path("/real/x.wav"))
    with pytest.raises(RuntimeError, match=r"candidate tts2 names no file \(no clip:tts2 input\)"):
        analysis.row_files({"id": "tts2"}, inputs)
    with pytest.raises(RuntimeError, match="source of candidate lib0002 names no file"):
        analysis.row_files({"id": "lib0002", "source_id": "gone"}, {"clip:lib0002": "x.wav"})
    with pytest.raises(RuntimeError, match="seed clip seed01"):
        analysis.seed_paths({"clips": [{"id": "seed01"}]}, inputs)


def test_the_filter_refuses_an_unresolved_manifest_before_loading_a_model(analysis, tmp_path):
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps({"clips": [{"id": "seed00", "text": "One."}]}), encoding="utf-8")
    candidates = tmp_path / "candidates.json"
    rows = [{"id": "tts1", "category": "sob", "kind": "nonverbal", "text": ""}]
    candidates.write_text(json.dumps(rows), encoding="utf-8")
    document = {
        "schema": 1,
        "job_id": "j2",
        "step": "analysis.filter",
        "task": "filter",
        "params": spec.settings("analysis.filter"),
        "inputs": {
            "seed": str(seed),
            "candidates": str(candidates),
            "clip:seed00": str(tmp_path / "seed00.wav"),
        },
        "seed": None,
        "engine": {"home": str(tmp_path / "missing"), "programs": {}},
        "scratch": None,
    }
    (tmp_path / "job.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(RuntimeError, match="candidate tts1 names no file"):
        analysis.filter_clips(analysis.Job(tmp_path / "job.json"))


def test_segments_come_from_pairs_or_a_transcript(analysis):
    assert analysis.segments_of([[0, 1.5], [2, 3]]) == [[0.0, 1.5], [2.0, 3.0]]
    transcript = {"segments": [{"start": 0.5, "end": 1.0, "text": "Hi"}]}
    assert analysis.segments_of(transcript) == [[0.5, 1.0]]


def test_the_worker_answers_its_probe_and_refuses_a_missing_home(analysis, tmp_path, capsys):
    assert analysis.run(analysis.TASKS, argv=["--probe"]) == 0
    assert json.loads(capsys.readouterr().out)["tasks"] == [
        "filter",
        "separate",
        "speakers",
        "tag",
        "transcribe",
    ]
    document = {
        "schema": 1,
        "job_id": "j1",
        "step": "analysis.tag",
        "task": "tag",
        "params": spec.settings("analysis.tag"),
        "inputs": {},
        "seed": None,
        "engine": {"home": str(tmp_path / "missing"), "programs": {}},
        "scratch": None,
    }
    (tmp_path / "job.json").write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(RuntimeError, match="not installed"):
        analysis.Models(analysis.Job(tmp_path / "job.json"))


def test_the_spec_settings_carry_what_the_tasks_read():
    """Every key a task reads from `params` is in its voice spec table."""
    assert {"checkpoint", "chunk_seconds", "hop_seconds", "batch", "sample_rate"} <= set(
        spec.settings("analysis.separate")
    )
    assert {
        "model",
        "compute_type",
        "beam_size",
        "vad_filter",
        "word_timestamps",
        "language",
    } <= set(spec.settings("analysis.transcribe"))
    assert {
        "sample_rate",
        "pitch_floor_hz",
        "pitch_ceiling_hz",
        "pitch_frame_length",
        "min_voiced_frames",
    } <= set(spec.settings("analysis.speakers"))
    assert {
        "class",
        "window_seconds",
        "hop_seconds",
        "min_probability",
        "top",
        "min_span_seconds",
    } <= set(spec.settings("analysis.tag"))
