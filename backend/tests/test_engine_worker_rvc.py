"""The RVC worker (ADR 0016 P3f), checked in the app's environment: its decision rules directly, and prepare -> train
(chunks, the 1-epoch resume, a failed chunk, cancellation, a changed training set) -> index against fake Applio stage
scripts that take the real scripts' positional arguments and leave their files and messages. No model runs here: the
conversion and real training wait for the GPU smoke batch."""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
import threading
import time
from importlib.machinery import PathFinder
from pathlib import Path

import pytest

from gaiavfx.integrations.voice import spec

WORKERS = Path(__file__).parents[2] / "engine_workers"
G_BASE = "G-f048k-TITAN-Medium.pth"
D_BASE = "D-f048k-TITAN-Medium.pth"

# Every fake stage script records how it was started: arguments, directory and the environment Applio would read.
HEADER = """
import json, os, re, sys, time
from pathlib import Path


def record(script, **extra):
    config = Path(os.environ["APPLIO_CONFIG_DIR"]) / "config.json"
    entry = {
        "script": script,
        "argv": sys.argv[1:],
        "cwd": os.getcwd(),
        "config": json.loads(config.read_text(encoding="utf-8")),
        "env": {key: os.environ.get(key) for key in (
            "APPLIO_LOGS_DIR", "APPLIO_CONFIG_DIR", "PYTHONDONTWRITEBYTECODE", "PYTHONPATH", "APPLIO_ROOT",
            "HF_HUB_OFFLINE", "NUMBA_CACHE_DIR", "MPLCONFIGDIR", "TORCH_HOME")},
        **extra,
    }
    with open(os.environ["FAKE_APPLIO_LOG"], "a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry) + "\\n")
"""

FAKE_PREPROCESS = """
experiment, dataset = Path(sys.argv[1]), Path(sys.argv[2])
record("preprocess")
(experiment / "sliced_audios").mkdir(parents=True, exist_ok=True)
files = sorted(p for p in dataset.iterdir() if p.suffix == ".wav")
for n, path in enumerate(files):
    (experiment / "sliced_audios" / f"0_{n}_0.wav").write_bytes(path.read_bytes())
(experiment / "model_info.json").write_text(json.dumps({"total_seconds": 2.0 * len(files)}))
print(f"Model {experiment.name} preprocessed successfully.")
"""

FAKE_EXTRACT = """
experiment = Path(sys.argv[1])
record("extract")
for sub in ("f0", "f0_voiced", "extracted"):
    (experiment / sub).mkdir(exist_ok=True)
names = sorted(p.name for p in (experiment / "sliced_audios").glob("*.wav"))
for name in names:
    (experiment / "f0" / f"{name}.npy").write_bytes(b"f0")
    (experiment / "f0_voiced" / f"{name}.npy").write_bytes(b"f0")
    (experiment / "extracted" / name.replace("wav", "npy")).write_bytes(b"features")
print(f"Pitch extraction completed in 0.01 seconds ({len(names)}/{len(names)} files successful).")
info = json.loads((experiment / "model_info.json").read_text())
info.update(embedder_model=sys.argv[6], speakers_id=1)
(experiment / "model_info.json").write_text(json.dumps(info))
(experiment / "config.json").write_text(json.dumps({"data": {"sample_rate": int(sys.argv[5])}}))
rows = [f"{(experiment / 'sliced_audios' / n).as_posix()}|{n}|0" for n in names]
(experiment / "filelist.txt").write_text("\\n".join(rows))
print(f"Model {experiment.name} extracted successfully.")
"""

FAKE_TRAIN = """
name, save_every, total = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
save_only_latest = sys.argv[9] == "True"
experiment = Path(os.environ["APPLIO_LOGS_DIR"]) / name
json.loads((experiment / "config.json").read_text())
assert list((experiment / "sliced_audios").glob("*.wav")), "train.py checks the first slice"


def newest(kind):
    found = sorted(experiment.glob(f"{kind}_*.pth"), key=lambda p: int(re.sub(r"\\D", "", p.name)))
    return json.loads(found[-1].read_text())["epoch"] if found else None


start = newest("G") or 0
assert start == (newest("D") or 0)
if not start:
    assert Path(sys.argv[4]).is_file() and Path(sys.argv[5]).is_file()
record("train", start=start)
mode = os.environ.get("FAKE_TRAIN_MODE", "")
if mode == "hang":
    Path(os.environ["FAKE_TRAIN_MARKER"]).write_text("started")
    time.sleep(120)
if mode == f"fail_at_{total}":
    print("Error: Training worker process failed with exit code 1.")
    sys.exit(1)
for epoch in range(start + 1, total + 1):
    step = epoch * 10
    print(f"{name} | epoch={epoch} | step={step} | time=00:00:{(epoch * 7) % 60:02d} | training_speed=0:00:07")
    if epoch % save_every == 0:
        suffix = 2333333 if save_only_latest else step
        for kind in "GD":
            (experiment / f"{kind}_{suffix}.pth").write_text(json.dumps({"epoch": epoch, "kind": kind}))
    if epoch >= total:
        (experiment / f"{name}_{epoch}e_{step}s.pth").write_text(json.dumps({"epoch": epoch, "weights": True}))
        print(f"Model {name} trained successfully.")
"""

FAKE_INDEX = """
experiment, algorithm = Path(sys.argv[1]), sys.argv[2]
record("index")
features = sorted((experiment / "extracted").glob("*.npy"))
(experiment / f"{experiment.name}.index").write_text(json.dumps({"features": len(features), "algorithm": algorithm}))
print(f"Index file for {experiment.name} generated successfully.")
"""


@pytest.fixture(scope="module")
def rvc():
    sys.path.insert(0, str(WORKERS))
    try:
        found = importlib.util.spec_from_file_location("rvc_worker", WORKERS / "rvc.py")
        module = importlib.util.module_from_spec(found)
        found.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(WORKERS))


@pytest.fixture
def home(tmp_path):
    """An rvc home with fake Applio stage scripts and placeholder TITAN bases."""
    home = tmp_path / "home"
    stages = home / "repo" / "rvc" / "train"
    scripts = {
        "preprocess/preprocess.py": FAKE_PREPROCESS,
        "extract/extract.py": FAKE_EXTRACT,
        "train.py": FAKE_TRAIN,
        "process/extract_index.py": FAKE_INDEX,
    }
    for relative, body in scripts.items():
        path = stages / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(HEADER + body, encoding="utf-8")
    (home / "models" / "titan").mkdir(parents=True)
    for name in (G_BASE, D_BASE):
        (home / "models" / "titan" / name).write_bytes(b"base")
    return home


@pytest.fixture
def applio(rvc, tmp_path, monkeypatch):
    """The stage scripts' call log; ffmpeg replaced by a copy that records each clip's gain."""
    log = tmp_path / "applio.jsonl"
    monkeypatch.setenv("FAKE_APPLIO_LOG", str(log))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "leak"))
    monkeypatch.setenv("APPLIO_ROOT", str(tmp_path / "elsewhere"))
    monkeypatch.setattr(rvc, "POLL_SECONDS", 0.02)
    monkeypatch.setattr(rvc, "find_ffmpeg", lambda job: "ffmpeg")
    monkeypatch.setattr(rvc, "measure", lambda ffmpeg, source: (-30.0, -3.0))
    gains = {}

    def resample(ffmpeg, source, target, gain, rate):
        gains[Path(source).name] = (gain, rate)
        Path(target).write_bytes(Path(source).read_bytes())

    monkeypatch.setattr(rvc, "resample", resample)

    def calls(script=None):
        if not log.exists():
            return []
        entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        return [e for e in entries if script is None or e["script"] == script]

    calls.gains = gains
    return calls


@pytest.fixture
def material(tmp_path):
    """A seed of two clips and three kept clips: two TTS takes and one converted real scream."""
    folder = tmp_path / "clips"
    folder.mkdir()

    def clip(name):
        path = folder / name
        path.write_bytes(b"RIFF" + name.encode())
        return path

    rows = [
        {
            "id": "fish_audio__neutral00_0",
            "path": str(clip("fish1.wav")),
            "category": "neutral",
            "kind": "speech",
            "generator": "fish_audio",
            "seconds": 2.5,
        },
        {
            "id": "voxcpm2__sob00_0",
            "path": str(clip("vox1.wav")),
            "category": "sob",
            "kind": "nonverbal",
            "generator": "voxcpm2",
            "seconds": 3.0,
        },
        {
            "id": "p041_scream",
            "path": str(clip("boot1.wav")),
            "category": "scream",
            "kind": "nonverbal",
            "generator": "bootstrap",
            "speaker": "p041",
            "source": "/real/p041.wav",
            "seconds": 4.0,
        },
    ]
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps([str(clip("seed_a.wav")), str(clip("seed_b.wav"))]))
    kept = tmp_path / "kept.json"
    kept.write_text(json.dumps(rows))
    return {"seed": seed, "kept:tts": kept}


def prepare_params(**dataset):
    params = spec.settings("rvc.prepare")
    params["dataset"] = {**spec.load()["dataset"], **dataset}
    return params


def train_params(**changes):
    return {**spec.settings("rvc.train"), **changes}


def make_job(rvc, root, name, task, params, inputs, home, scratch, seed=None):
    work = root / "work" / name
    work.mkdir(parents=True, exist_ok=True)
    document = {
        "schema": 1,
        "job_id": name,
        "step": f"rvc.{task}",
        "task": task,
        "params": params,
        "inputs": {key: str(path) for key, path in inputs.items()},
        "seed": seed,
        "engine": {"home": str(home) if home else None, "programs": {}},
        "scratch": str(scratch) if scratch else None,
    }
    (work / "job.json").write_text(json.dumps(document), encoding="utf-8")
    return rvc.Job(work / "job.json")


def launch(rvc, job):
    """Run the job as the executor does; (exit code, outputs by key)."""
    code = rvc.run(rvc.TASKS, argv=["--job", str(job.path)])
    result = job.work / "result.json"
    outputs = {}
    if code == 0:
        outputs = {o["key"]: o for o in json.loads(result.read_text(encoding="utf-8"))["outputs"]}
    return code, outputs


def snapshot(folder: Path) -> dict:
    return {
        path.relative_to(folder).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in folder.rglob("*")
    }


# --- module and spec -----------------------------------------------------------------------------------------------


def test_module_level_is_standard_library():
    tree = ast.parse((WORKERS / "rvc.py").read_text(encoding="utf-8"))
    top = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            top.add((node.module or "").split(".")[0])
    assert top <= {
        "__future__",
        "concurrent",
        "hashlib",
        "itertools",
        "json",
        "math",
        "os",
        "re",
        "shutil",
        "signal",
        "statistics",
        "subprocess",
        "sys",
        "threading",
        "time",
        "pathlib",
        "protocol",
    }, top


def test_the_worker_answers_its_probe(rvc, capsys):
    assert rvc.run(rvc.TASKS, argv=["--probe"]) == 0
    assert json.loads(capsys.readouterr().out)["tasks"] == ["convert", "index", "prepare", "train"]


def test_the_spec_carries_what_the_tasks_read_and_the_research_settings(rvc):
    """Every key a task reads is in its voice spec table, and the values the research's numbers depend on hold."""
    prepare = spec.settings("rvc.prepare")
    assert set(rvc.PREPARE_KEYS) <= set(prepare)
    assert prepare["sample_rate"] == 48000 and prepare["f0_labels"] == "rmvpe"
    assert prepare["rmvpe_high_register"] == {
        "enabled": True,
        "mode": "true_pitch",
        "f0_ceil": 1250,
    }
    dataset = spec.load()["dataset"]
    assert {
        "seed_copies",
        "max_synthetic_minutes",
        "max_category_share",
        "sample_rate",
        "tts_lufs",
    } <= set(dataset)
    train = spec.settings("rvc.train")
    assert set(rvc.TRAIN_KEYS) | {"epochs", "chunk_epochs", "save_only_latest"} <= set(train)
    assert train["pretrained_g"].endswith(G_BASE) and train["pretrained_d"].endswith(D_BASE)
    assert (train["epochs"], train["chunk_epochs"]) == (200, 50)
    assert spec.settings("rvc.index") == {"algorithm": "Auto"}
    convert = spec.settings("rvc.convert")
    settings = rvc.convert_settings(
        {**convert, "source_median_hz": 119.0, "target_median_hz": 188.3}, None
    )
    assert (settings["f0_method"], settings["index_rate"], settings["protect"]) == (
        "fcpe",
        0.3,
        0.33,
    )
    assert settings["volume_envelope"] == 0.0 and settings["split_audio"] is False
    assert (
        settings["pitch_map"]["knee_semitones"] == 5.0
        and settings["pitch_map"]["ceiling_hz"] == 870.0
    )
    assert settings["unvoiced_spans"] is True and settings["seed"] == 7


# --- the training set ----------------------------------------------------------------------------------------------


def test_water_filling_keeps_small_categories_and_caps_large_ones(rvc):
    alloc = rvc.allocation({"sigh": 10.0, "sob": 100.0, "scream": 200.0}, 150.0, 60.0)
    assert alloc == {"sigh": 10.0, "sob": 60.0, "scream": 60.0}


def test_balancing_takes_groups_round_robin_up_to_the_category_share(rvc):
    rows = [
        {"id": f"{g}{n}", "category": "scream", "generator": g, "seconds": 30.0}
        for g in ("higgs", "voxcpm2")
        for n in range(10)
    ]
    rows.append({"id": "s1", "category": "sigh", "generator": "fish", "seconds": 5.0})
    chosen = rvc.balanced(rows, minutes=10.0, share=0.2)  # 120 s per category at most
    assert [r["id"] for r in chosen if r["category"] == "scream"] == [
        "higgs0",
        "voxcpm20",
        "higgs1",
        "voxcpm21",
    ]
    assert [r["id"] for r in chosen if r["category"] == "sigh"] == ["s1"]


def test_the_plan_copies_the_seed_and_levels_only_tts(rvc):
    dataset = spec.load()["dataset"]
    rows = [
        {
            "id": "a",
            "path": "/t/a.wav",
            "category": "laugh",
            "generator": "maya1_vc",
            "seconds": 2.0,
        },
        {
            "id": "b",
            "path": "/t/b.wav",
            "category": "scream",
            "generator": "bootstrap",
            "seconds": 3.0,
        },
        {"id": "c", "path": "/t/c.wav", "category": "sob", "source": "/real/c.wav", "seconds": 3.0},
        {
            "id": "d",
            "path": "/t/d.wav",
            "category": "sob",
            "generator": "fish",
            "seconds": 3.0,
            "kept": False,
        },
    ]
    plan = rvc.dataset_plan([Path("/s/one.wav"), Path("/t/one.wav")], rows, dataset)
    seed = [item for item in plan if item["category"] == "seed"]
    assert len(seed) == 2 * dataset["seed_copies"] == 6
    assert all(item["lufs"] is None for item in seed)
    assert len({item["name"] for item in plan}) == len(plan), "file names stay unique"
    levels = {item.get("id"): item["lufs"] for item in plan if item["category"] != "seed"}
    assert levels == {"a": dataset["tts_lufs"], "b": None, "c": None}
    summary = rvc.dataset_summary(plan)
    assert (summary["seed_clips"], summary["synthetic_clips"]) == (6, 3)
    assert summary["by_category"] == {"laugh": 0.03, "scream": 0.05, "sob": 0.05}


def test_only_tts_clips_are_levelled(rvc):
    assert rvc.levelled({"generator": "fish_audio"})
    assert not rvc.levelled({"generator": "bootstrap"})
    assert not rvc.levelled({"source": "/real/p041.wav"})
    assert not rvc.levelled({"source_id": "lib0001"}), "a converted library clip keeps its level"


def test_manifest_rows_without_paths_name_their_clip_inputs(rvc, tmp_path):
    inputs = {"clip:seed00": tmp_path / "a.wav", "clip:x1": tmp_path / "b.wav"}
    document = {"seconds": 9.0, "clips": [{"id": "seed00", "text": "One."}, "/s/two.wav"]}
    assert rvc.seed_clips(document, inputs) == [
        {"name": "seed00", "path": tmp_path / "a.wav"},
        {"name": "two", "path": Path("/s/two.wav")},
    ]
    assert rvc.seed_clips([{"text": "unnamed"}], {"clip:seed00": tmp_path / "a.wav"}) == [
        {"name": "seed00", "path": tmp_path / "a.wav"}
    ], "an unnamed seed row is seed00, as tts.generate reads it"
    rows = [
        {"id": "x1", "category": "sob", "seconds": 1.0},
        {"id": "p", "path": "/t/p.wav", "category": "sob", "seconds": 1.0},
        {"id": "dropped", "category": "sob", "seconds": 1.0, "kept": False},
    ]
    assert [row["path"] for row in rvc.kept_rows(rows, inputs)] == [
        str(tmp_path / "b.wav"),
        "/t/p.wav",
    ]
    with pytest.raises(RuntimeError, match=r"kept clip x2 names no file \(no clip:x2 input\)"):
        rvc.kept_rows([{"id": "x2", "category": "sob", "seconds": 1.0}], inputs)
    with pytest.raises(RuntimeError, match="seed clip seed01"):
        rvc.seed_clips({"clips": [{"id": "seed01"}]}, inputs)


def test_levelling_follows_the_research_gain_rule(rvc):
    log = """[Parsed_ebur128_0 @ 0000] Summary:

  Integrated loudness:
    I:         -23.4 LUFS
    Threshold: -33.6 LUFS

  Sample peak:
    Peak:       -2.5 dBFS
"""
    assert rvc.loudness_of(log) == (-23.4, -2.5)
    assert rvc.loudness_of("no summary") == (None, None)
    assert rvc.loudness_of(log.replace("-2.5", "-inf")) == (-23.4, None)
    assert rvc.gain_db(-23.4, -2.5, -36.0) == -12.6
    assert rvc.gain_db(-40.0, -2.0, -36.0) == 1.0, "the peak stays at or below -1 dBFS"
    assert rvc.gain_db(-80.0, -60.0, -36.0) == 0.0, "silence is not lifted"
    assert rvc.gain_db(None, None, -36.0) == 0.0
    command = rvc.resample_command("ffmpeg", "in.wav", "out.partial", -6.0, 48000)
    assert command[command.index("-af") + 1] == "volume=-6.0dB"
    assert command[-7:] == ["-ac", "1", "-ar", "48000", "-f", "wav", "out.partial"]
    assert "-af" not in rvc.resample_command("ffmpeg", "in.wav", "out.partial", None, 48000)


# --- conversion rules ----------------------------------------------------------------------------------------------


def test_the_pitch_map_bends_high_pitch_and_keeps_the_ratio_below(rvc):
    """The V4 examples (119 -> 188.3 Hz medians): 300 Hz -> 413 Hz, an 806 Hz scream -> 622 Hz; with the low floor
    dropped, a 70 Hz creak takes the plain median ratio (111 Hz)."""

    def mapped(hz):
        return rvc.map_f0([hz], 119.0, 188.3, 5.0, 870.0)[0]

    assert mapped(300.0) == pytest.approx(413, abs=1)
    assert mapped(806.0) == pytest.approx(622, abs=1)
    assert mapped(70.0) == pytest.approx(70.0 * 188.3 / 119.0)
    assert mapped(150.0) == pytest.approx(150.0 * 188.3 / 119.0)
    assert mapped(5000.0) < 870.0
    knee = 119.0 * 2 ** (5 / 12)
    assert mapped(knee * 1.0001) == pytest.approx(knee * 1.0001 * 188.3 / 119.0, rel=1e-4)
    assert rvc.map_f0([0.0, float("nan"), -1.0], 119.0, 188.3, 5.0, 870.0) == [0.0, 0.0, 0.0]


def test_whispered_spans_become_frames_of_the_padded_clip(rvc):
    assert rvc.span_frames([[0.5, 1.0]], 16000, 16000, 160) == [(150, 201)]
    assert rvc.span_frames([[-2.0, 0.1]], 16000, 16000, 160) == [(0, 111)]
    assert rvc.spans_for(None, ["a"]) == {}
    assert rvc.spans_for({"a": [[1, 2]]}, ["a", "b"]) == {"a": [[1.0, 2.0]], "b": []}
    assert rvc.spans_for([[0.5, 1]], ["audio"]) == {"audio": [[0.5, 1.0]]}
    with pytest.raises(ValueError):
        rvc.spans_for([[0.5, 1]], ["a", "b"])


def test_conversion_settings_need_the_medians_and_are_checked(rvc):
    params = {**spec.settings("rvc.convert"), "source_median_hz": 119.0, "target_median_hz": 188.3}
    assert rvc.convert_settings(params, 11)["seed"] == 11, "the job's seed wins over the recipe's"
    with pytest.raises(ValueError, match="source_median_hz"):
        rvc.convert_settings(spec.settings("rvc.convert"), None)
    plain = rvc.convert_settings({**spec.settings("rvc.convert"), "pitch_map": None}, None)
    assert plain["pitch_map"] is None and plain["unvoiced_spans"] is False
    for change in ({"split_audio": True}, {"protect": 0.6}, {"f0_method": "pm"}, {"index_rate": 2}):
        with pytest.raises(ValueError):
            rvc.convert_settings({**params, **change}, None)


def test_converted_outputs_are_named_by_clip(rvc):
    assert rvc.output_of("audio") == ("converted", "converted.wav")
    assert rvc.output_of("line 7:a") == ("converted:line 7:a", "converted_line_7_a.wav")


def test_conversion_refuses_missing_inputs_before_loading_anything(rvc, tmp_path, home):
    clip = tmp_path / "line.wav"
    clip.write_bytes(b"RIFF")
    model = tmp_path / "voice.pth"
    model.write_bytes(b"weights")
    params = {**spec.settings("rvc.convert"), "source_median_hz": 119.0, "target_median_hz": 188.3}
    cases = [
        ({"audio": clip}, params, "model"),
        ({"audio": clip, "model": model}, params, "index"),
        ({"audio": clip, "model": model, "index": model}, spec.settings("rvc.convert"), "median"),
    ]
    for n, (inputs, job_params, message) in enumerate(cases):
        job = make_job(rvc, tmp_path, f"c{n}", "convert", job_params, inputs, home, None)
        with pytest.raises((RuntimeError, ValueError), match=message):
            rvc.convert(job)


# --- training rules ------------------------------------------------------------------------------------------------


def test_chunks_end_on_multiples_and_save_at_their_end(rvc):
    assert rvc.chunk_ends(0, 200, 25) == [25, 50, 75, 100, 125, 150, 175, 200]
    assert rvc.chunk_ends(25, 26, 25) == [26], "the 1-epoch resume"
    assert rvc.chunk_ends(26, 60, 25) == [50, 60]
    assert rvc.chunk_ends(200, 200, 25) == []
    with pytest.raises(ValueError):
        rvc.chunk_ends(0, 10, 0)
    for start, end in [(0, 25), (25, 50), (25, 26), (26, 50), (50, 60), (0, 1), (0, 200)]:
        interval = rvc.save_interval(start, end)
        assert end % interval == 0, (start, end)
        assert interval <= end - start
    assert (rvc.save_interval(0, 25), rvc.save_interval(25, 26), rvc.save_interval(26, 50)) == (
        25,
        1,
        10,
    )


def test_training_resumes_only_its_own_whole_state(rvc):
    files = {"G": "G_25.pth", "D": "D_25.pth", "model": "model_25.pth"}
    state = {"identity": "abc", "epoch": 25, "files": files}
    present = set(files.values())
    assert rvc.resume_epoch(state, "abc", present) == 25
    assert rvc.resume_epoch(state, "other", present) == 0, "another training set starts over"
    assert rvc.resume_epoch(state, "abc", present - {"D_25.pth"}) == 0
    assert rvc.resume_epoch(None, "abc", present) == 0
    assert rvc.resume_epoch({**state, "epoch": 0}, "abc", present) == 0
    names = [
        "G_25.pth",
        "D_25.pth",
        "model_25.pth",
        "G_50.pth",
        "model_50.pth",
        "state.json",
        "run",
    ]
    assert rvc.stray(names, state) == ["G_50.pth", "model_50.pth"], "an interrupted commit"
    assert rvc.newest_checkpoints(["G_0.pth", "D_0.pth", "G_2333333.pth", "D_2333333.pth"]) == {
        "G": "G_2333333.pth",
        "D": "D_2333333.pth",
    }
    assert rvc.newest_checkpoints(["G_0.pth", "G_103.pth", "G_206.pth", "D_206.pth"]) == {
        "G": "G_206.pth",
        "D": "D_206.pth",
    }


def test_epoch_lines_give_seconds_per_epoch(rvc):
    lines = [
        "voice | epoch=1 | step=103 | time=23:59:50 | training_speed=0:02:41",
        "voice | epoch=1 | step=103 | batch=10/103",
        "voice | epoch=2 | step=206 | time=00:00:18 | training_speed=0:00:28 | lowest_value=30.1",
        "voice | epoch=3 | step=309 | time=00:00:44 | training_speed=0:00:26 | lowest_value=29.9",
    ]
    assert rvc.epoch_stats(lines) == {
        "epochs_logged": 3,
        "seconds_per_epoch_mean": 27.0,
        "seconds_per_epoch_median": 27.0,
    }


def test_applio_gets_the_research_arguments_in_its_positional_order(rvc):
    params = {**spec.settings("rvc.prepare"), **spec.settings("rvc.train"), "algorithm": "Auto"}
    assert rvc.preprocess_arguments("E", "S", params) == [
        "E", "S", "48000", "8", "Automatic", "False", "False", "0.7", "3.0", "0.3", "none",
    ]  # fmt: skip
    assert rvc.extract_arguments("E", params) == [
        "E", "rmvpe", "8", "0", "48000", "contentvec", "None", "2",
    ]  # fmt: skip
    assert rvc.train_arguments(params, 25, 50, "G", "D", 48000) == [
        "voice", "25", "50", "G", "D", "0", "8", "48000", "True", "False", "False", "False", "HiFi-GAN", "False",
    ]  # fmt: skip
    assert rvc.index_arguments("E", params) == ["E", "Auto"]


def test_applio_s_package_wins_over_this_worker_s_own_name(rvc, home):
    """Run as `engine_workers/rvc.py`, the worker's folder is first on the path; the converter imports Applio's `rvc`
    package, so the checkout must come before it."""
    shadowed = PathFinder.find_spec("rvc", [str(WORKERS)])
    assert Path(shadowed.origin) == WORKERS / "rvc.py"
    (home / "repo" / "rvc" / "__init__.py").write_text("", encoding="utf-8")
    paths = rvc.applio_path([str(WORKERS), str(home / "repo"), "elsewhere"], home / "repo")
    assert paths == [str(home / "repo"), str(WORKERS), "elsewhere"]
    assert Path(PathFinder.find_spec("rvc", paths).origin) == home / "repo" / "rvc" / "__init__.py"


def test_applio_runs_offline_with_its_state_and_caches_redirected(rvc, tmp_path):
    base = {
        "PYTHONPATH": "x",
        "APPLIO_ROOT": "y",
        "VIRTUAL_ENV": "v",
        "PATH": "p",
        "SYSTEMROOT": "s",
    }
    work, logs, config = tmp_path / "work", tmp_path / "scratch" / "run", tmp_path / "work" / "cfg"
    env = rvc.applio_environment(base, work, logs, config)
    assert not {"PYTHONPATH", "APPLIO_ROOT", "VIRTUAL_ENV"} & set(env)
    assert (env["PATH"], env["SYSTEMROOT"]) == ("p", "s")
    assert (env["APPLIO_LOGS_DIR"], env["APPLIO_CONFIG_DIR"]) == (str(logs), str(config))
    for key in ("HF_HOME", "TORCH_HOME", "MPLCONFIGDIR", "NUMBA_CACHE_DIR"):
        assert Path(env[key]).is_relative_to(work), key
    assert env["HF_HUB_OFFLINE"] == env["TRANSFORMERS_OFFLINE"] == "1"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"


# --- prepare -> train -> index against fake Applio scripts ---------------------------------------------------------


def test_prepare_train_resume_and_index_run_end_to_end(rvc, home, applio, material, tmp_path):
    """The smoke batch's chain: prepare, one train chunk, a 1-epoch resume, the index. Nothing lands in the home."""
    before = snapshot(home)
    scratch = tmp_path / "scratch"
    job = make_job(rvc, tmp_path, "prepare", "prepare", prepare_params(), material, home, scratch)
    code, outputs = launch(rvc, job)
    assert code == 0
    info = outputs["dataset"]["info"]
    assert (info["clips"], info["seed_clips"], info["synthetic_clips"], info["slices"]) == (
        9,
        6,
        3,
        9,
    )
    assert outputs["dataset"]["kind"] == "analysis_report"
    gains = applio.gains
    assert gains["seed_a.wav"] == (None, 48000), "the seed keeps its level"
    assert gains["fish1.wav"] == (-6.0, 48000) and gains["vox1.wav"] == (-6.0, 48000)
    assert gains["boot1.wav"] == (None, 48000), "a converted performance keeps its level"
    manifest = json.loads((scratch / "prepare" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["experiment"] == "voice" and manifest["sample_rate"] == 48000
    (preprocess,) = applio("preprocess")
    assert preprocess["argv"][:2] == [str(scratch / "prepare" / "voice"), str(job.work / "dataset")]
    (extract,) = applio("extract")
    assert extract["config"] == {
        "rmvpe_high_register": {"enabled": True, "mode": "true_pitch", "f0_ceil": 1250}
    }

    first = make_job(rvc, tmp_path, "train1", "train", train_params(epochs=1), {}, home, scratch)
    code, outputs = launch(rvc, first)
    assert code == 0
    assert outputs["model"]["kind"] == "voice_weights", "the app's voice_model is the manifest"
    assert outputs["model"]["info"]["epochs"] == 1
    state = json.loads((scratch / "train" / "state.json").read_text(encoding="utf-8"))
    assert state["epoch"] == 1 and state["steps"] == 10

    resumed = make_job(rvc, tmp_path, "train2", "train", train_params(epochs=2), {}, home, scratch)
    code, outputs = launch(rvc, resumed)
    assert code == 0
    weights = json.loads((resumed.work / outputs["model"]["path"]).read_text(encoding="utf-8"))
    assert weights == {"epoch": 2, "weights": True}
    trains = applio("train")
    assert [t["start"] for t in trains] == [0, 1], "the second job resumed from epoch 1"
    assert [t["argv"][1:3] for t in trains] == [["1", "1"], ["1", "2"]]
    assert trains[0]["argv"][3] == str(home / "models" / "titan" / G_BASE)
    assert sorted(p.name for p in (scratch / "train").glob("*.pth")) == [
        "D_2.pth",
        "G_2.pth",
        "model_2.pth",
    ]
    assert not applio("index"), "training never builds the index"

    indexed = make_job(
        rvc, tmp_path, "index", "index", spec.settings("rvc.index"), {}, home, scratch
    )
    code, outputs = launch(rvc, indexed)
    assert code == 0
    built = json.loads((indexed.work / outputs["index"]["path"]).read_text(encoding="utf-8"))
    assert built == {"features": 9, "algorithm": "Auto"}
    assert outputs["index"]["kind"] == "voice_index"

    for call in applio():
        assert Path(call["cwd"]) == home / "repo", "Applio runs from its checkout"
        assert call["env"]["PYTHONPATH"] is None and call["env"]["APPLIO_ROOT"] is None
        assert (
            call["env"]["PYTHONDONTWRITEBYTECODE"] == "1" and call["env"]["HF_HUB_OFFLINE"] == "1"
        )
        for key in ("APPLIO_LOGS_DIR", "APPLIO_CONFIG_DIR", "NUMBA_CACHE_DIR", "MPLCONFIGDIR"):
            path = Path(call["env"][key])
            assert path.is_relative_to(scratch) or path.is_relative_to(tmp_path / "work"), key
    assert snapshot(home) == before, "nothing is written inside the home"

    # A relaunch of a finished job publishes again from its work directory without running Applio.
    count = len(applio())
    assert launch(rvc, resumed)[0] == 0 and len(applio()) == count


def test_prepare_reads_manifests_of_ids_through_clip_inputs(rvc, home, applio, tmp_path):
    """The app's manifests carry ids, never paths (ADR 0016 P5): the seed lists {"id", "text"} entries and the kept
    rows carry no path; every file arrives as an input `clip:<id>`, named as the artifact store names it."""
    store = tmp_path / "artifacts"
    store.mkdir()

    def artifact(name):
        path = store / f"{name}-sha.wav"
        path.write_bytes(b"RIFF" + name.encode())
        return path

    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps(
            {
                "seconds": 9.5,
                "clips": [{"id": "seed00", "text": "One."}, {"id": "seed01", "text": "Two."}],
            }
        )
    )
    rows = [
        {
            "id": "fish_audio__neutral00_0",
            "category": "neutral",
            "kind": "speech",
            "generator": "fish_audio",
            "text": "A line.",
            "seconds": 2.5,
        },
        {
            "id": "lib0001",
            "category": "scream",
            "kind": "nonverbal",
            "generator": "bootstrap",
            "speaker": "alex",
            "source_id": "lib0001-source",
            "seconds": 4.0,
        },
    ]
    kept = tmp_path / "kept.json"
    kept.write_text(json.dumps(rows))
    inputs = {
        "seed": seed,
        "kept:0": kept,
        "clip:seed00": artifact("seed00"),
        "clip:seed01": artifact("seed01"),
        "clip:fish_audio__neutral00_0": artifact("fish"),
        "clip:lib0001": artifact("boot"),
    }
    scratch = tmp_path / "scratch"
    job = make_job(rvc, tmp_path, "prepare", "prepare", prepare_params(), inputs, home, scratch)
    code, outputs = launch(rvc, job)
    assert code == 0
    info = outputs["dataset"]["info"]
    assert (info["clips"], info["seed_clips"], info["synthetic_clips"]) == (8, 6, 2)
    gains = applio.gains
    assert gains["seed00-sha.wav"] == (None, 48000) and gains["seed01-sha.wav"] == (None, 48000)
    assert gains["fish-sha.wav"] == (-6.0, 48000), "TTS is levelled"
    assert gains["boot-sha.wav"] == (None, 48000), "a converted library clip keeps its level"
    report = json.loads((job.work / outputs["dataset"]["path"]).read_text(encoding="utf-8"))
    names = [item["name"] for item in report["files"]]
    assert "seed_0_seed00.wav" in names and "bootstrap_lib0001.wav" in names
    for document in (report, json.loads((scratch / "prepare" / "manifest.json").read_text())):
        assert str(store) not in json.dumps(document), "no artifact path is persisted"

    without = {key: path for key, path in inputs.items() if key != "clip:lib0001"}
    missing = make_job(
        rvc, tmp_path, "missing", "prepare", prepare_params(), without, home, scratch
    )
    with pytest.raises(RuntimeError, match="kept clip lib0001 names no file"):
        rvc.prepare(missing)


def test_conversion_publishes_audio_the_app_classifies(rvc, home, tmp_path, monkeypatch):
    """rvc.convert's clips are plain `audio`: the app makes them candidates (P6 lines) or hidden material (the
    audition, through the job's artifact kind)."""

    class FakeConverter:
        def __init__(self, job, model):
            self.model = model

        def convert(self, job, source, target, settings, index, spans):
            info = {"sample_rate": 48000, "seconds": 0.5, "unvoiced_spans": spans}
            rvc.write_json(job, job.work / "converted" / f"{target.stem}.json", info)
            target.write_bytes(Path(source).read_bytes())
            return info

    monkeypatch.setattr(rvc, "Converter", FakeConverter)
    files = {}
    for name in ("line-a", "line-b", "model", "index"):
        files[name] = tmp_path / f"{name}.bin"
        files[name].write_bytes(name.encode())
    params = {**spec.settings("rvc.convert"), "source_median_hz": 119.0, "target_median_hz": 188.3}
    inputs = {
        "clip:a": files["line-a"],
        "clip:b": files["line-b"],
        "model": files["model"],
        "index": files["index"],
    }
    job = make_job(rvc, tmp_path, "convert", "convert", params, inputs, home, None)
    code, outputs = launch(rvc, job)
    assert code == 0
    assert set(outputs) == {"converted:a", "converted:b"}
    assert {output["kind"] for output in outputs.values()} == {"audio"}
    assert outputs["converted:a"]["info"]["source"] == "a"


def test_a_failed_chunk_keeps_the_last_commit_and_a_relaunch_resumes(
    rvc, home, applio, material, tmp_path, monkeypatch
):
    scratch = tmp_path / "scratch"
    assert (
        launch(
            rvc, make_job(rvc, tmp_path, "p", "prepare", prepare_params(), material, home, scratch)
        )[0]
        == 0
    )
    job = make_job(
        rvc, tmp_path, "t", "train", train_params(epochs=3, chunk_epochs=1), {}, home, scratch
    )
    monkeypatch.setenv("FAKE_TRAIN_MODE", "fail_at_2")
    assert launch(rvc, job)[0] == 1
    state = json.loads((scratch / "train" / "state.json").read_text(encoding="utf-8"))
    assert state["epoch"] == 1
    assert {p.name for p in (scratch / "train").glob("*.pth")} == {
        "G_1.pth",
        "D_1.pth",
        "model_1.pth",
    }
    (scratch / "train" / "G_7.pth").write_text(
        "half a commit"
    )  # what an interrupted commit could leave

    monkeypatch.delenv("FAKE_TRAIN_MODE")
    code, outputs = launch(rvc, job)
    assert code == 0
    assert [t["start"] for t in applio("train")] == [0, 1, 1, 2]
    state = json.loads((scratch / "train" / "state.json").read_text(encoding="utf-8"))
    assert state["epoch"] == 3 and [c["end"] for c in state["chunks"]] == [1, 2, 3]
    assert not (scratch / "train" / "G_7.pth").exists()
    assert outputs["model"]["info"]["chunks"] == 3


def test_cancelling_a_chunk_stops_applio_and_keeps_the_state(
    rvc, home, applio, material, tmp_path, monkeypatch
):
    scratch = tmp_path / "scratch"
    assert (
        launch(
            rvc, make_job(rvc, tmp_path, "p", "prepare", prepare_params(), material, home, scratch)
        )[0]
        == 0
    )
    assert (
        launch(
            rvc, make_job(rvc, tmp_path, "t1", "train", train_params(epochs=1), {}, home, scratch)
        )[0]
        == 0
    )
    marker = tmp_path / "started"
    monkeypatch.setenv("FAKE_TRAIN_MODE", "hang")
    monkeypatch.setenv("FAKE_TRAIN_MARKER", str(marker))
    job = make_job(rvc, tmp_path, "t2", "train", train_params(epochs=26), {}, home, scratch)

    def cancel():
        deadline = time.monotonic() + 30
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        (job.work / "cancel").write_text("cancel")

    canceller = threading.Thread(target=cancel)
    canceller.start()
    began = time.monotonic()
    code = rvc.run(rvc.TASKS, argv=["--job", str(job.path)])
    canceller.join()
    assert code == 3
    assert time.monotonic() - began < 60, "the hanging chunk was killed, not waited for"
    state = json.loads((scratch / "train" / "state.json").read_text(encoding="utf-8"))
    assert state["epoch"] == 1


def test_a_changed_training_set_prepares_and_trains_again(rvc, home, applio, material, tmp_path):
    scratch = tmp_path / "scratch"
    assert (
        launch(
            rvc, make_job(rvc, tmp_path, "p1", "prepare", prepare_params(), material, home, scratch)
        )[0]
        == 0
    )
    assert (
        launch(
            rvc, make_job(rvc, tmp_path, "t1", "train", train_params(epochs=1), {}, home, scratch)
        )[0]
        == 0
    )
    first = json.loads((scratch / "train" / "state.json").read_text(encoding="utf-8"))["identity"]

    # The same material in another job reuses the preparation.
    assert (
        launch(
            rvc, make_job(rvc, tmp_path, "p2", "prepare", prepare_params(), material, home, scratch)
        )[0]
        == 0
    )
    assert len(applio("preprocess")) == 1

    changed = prepare_params(seed_copies=1)
    code, outputs = launch(
        rvc, make_job(rvc, tmp_path, "p3", "prepare", changed, material, home, scratch)
    )
    assert code == 0 and outputs["dataset"]["info"]["slices"] == 5
    assert len(applio("preprocess")) == 2
    assert len(list((scratch / "prepare" / "voice" / "sliced_audios").glob("*.wav"))) == 5

    assert (
        launch(
            rvc, make_job(rvc, tmp_path, "t2", "train", train_params(epochs=2), {}, home, scratch)
        )[0]
        == 0
    )
    assert [t["start"] for t in applio("train")] == [0, 0], "the new set trains from the base"
    state = json.loads((scratch / "train" / "state.json").read_text(encoding="utf-8"))
    assert state["identity"] != first and state["epoch"] == 2


def test_tasks_refuse_without_their_home_scratch_or_preparation(rvc, home, tmp_path, material):
    scratch = tmp_path / "scratch"
    job = make_job(
        rvc, tmp_path, "a", "prepare", prepare_params(), material, tmp_path / "none", scratch
    )
    with pytest.raises(RuntimeError, match="not installed"):
        rvc.prepare(job)
    job = make_job(rvc, tmp_path, "b", "prepare", prepare_params(), material, home, None)
    with pytest.raises(RuntimeError, match="scratch"):
        rvc.prepare(job)
    job = make_job(
        rvc, tmp_path, "c", "prepare", spec.settings("rvc.prepare"), material, home, scratch
    )
    with pytest.raises(RuntimeError, match=r"\[dataset\]"):
        rvc.prepare(job)
    for task, params in (("train", train_params()), ("index", spec.settings("rvc.index"))):
        job = make_job(rvc, tmp_path, task, task, params, {}, home, scratch)
        with pytest.raises(RuntimeError, match="not prepared"):
            rvc.TASKS[task](job)
