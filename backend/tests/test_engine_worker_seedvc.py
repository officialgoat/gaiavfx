"""The Seed-VC worker's decision rules (ADR 0016 P3e), checked in the app's environment: the worker's module level is
standard library only, its settings are the voice spec's and its model files the registry's. Fine-tuning and
conversion themselves run in the GPU smoke batch."""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from gaiavfx.integrations.voice import engines as registry
from gaiavfx.integrations.voice import spec

WORKERS = Path(__file__).parents[2] / "engine_workers"


@pytest.fixture(scope="module")
def seedvc():
    sys.path.insert(0, str(WORKERS))
    try:
        found = importlib.util.spec_from_file_location("seedvc_worker", WORKERS / "seedvc.py")
        module = importlib.util.module_from_spec(found)
        found.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(WORKERS))


def make_job(seedvc, tmp_path, task, params, inputs=None, home=None, scratch=None):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    document = {
        "schema": 1,
        "job_id": "j1",
        "step": f"seedvc.{task}",
        "task": task,
        "params": params,
        "inputs": {key: str(path) for key, path in (inputs or {}).items()},
        "seed": None,
        "engine": {"home": str(home) if home else None, "programs": {}},
        "scratch": str(scratch) if scratch else None,
    }
    (work / "job.json").write_text(json.dumps(document), encoding="utf-8")
    return seedvc.Job(work / "job.json")


def fake_home(tmp_path) -> Path:
    home = tmp_path / "home"
    (home / "repo").mkdir(parents=True)
    (home / "repo" / "train.py").write_text("# Seed-VC's train.py\n", encoding="utf-8")
    return home


def write(path: Path, data: bytes = b"audio") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_module_level_is_standard_library():
    tree = ast.parse((WORKERS / "seedvc.py").read_text(encoding="utf-8"))
    top = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            top.add((node.module or "").split(".")[0])
    allowed = {"__future__", "copy", "hashlib", "json", "math", "os", "re", "shutil", "sys"}
    assert top <= allowed | {"time", "types", "pathlib", "protocol"}, top


def test_the_worker_answers_its_probe_and_refuses_a_missing_home(seedvc, tmp_path, capsys):
    assert seedvc.run(seedvc.TASKS, argv=["--probe"]) == 0
    assert json.loads(capsys.readouterr().out)["tasks"] == ["convert", "finetune"]
    clip = write(tmp_path / "clip.wav")
    params = spec.settings("seedvc.finetune")
    job = make_job(seedvc, tmp_path, "finetune", params, {"clip:a": clip}, tmp_path / "missing")
    with pytest.raises(RuntimeError, match="not installed"):
        seedvc.finetune(job)
    params = {**spec.settings("seedvc.convert"), "pitch_shift": 0}
    job = make_job(
        seedvc, tmp_path, "convert", params, {"clip:a": clip, "reference": clip}, tmp_path / "nope"
    )
    with pytest.raises(RuntimeError, match="not installed"):
        seedvc.convert(job)


def test_short_clips_are_padded_and_long_clips_cut(seedvc):
    rate = 44100
    padded, pieces, cuts = seedvc.dataset_plan(rate // 2, rate, 1.05, 25.0)
    assert padded == 46305, "padded to min_clip_seconds (the research's 1.0 s + 0.05 s)"
    assert pieces == [(0, 0, 46305)] and cuts == 0
    padded, pieces, cuts = seedvc.dataset_plan(60 * rate, rate, 1.05, 25.0)
    assert padded == 60 * rate
    assert pieces == [(0, 0, 25 * rate), (1, 25 * rate, 50 * rate), (2, 50 * rate, 60 * rate)]
    assert cuts == 2
    # A trailing piece under 0.5 s is dropped but still counts as cut.
    _, pieces, cuts = seedvc.dataset_plan(int(50.3 * rate), rate, 1.05, 25.0)
    assert [index for index, _, _ in pieces] == [0, 1] and cuts == 2
    assert seedvc.dataset_plan(25 * rate, rate, 1.05, 25.0)[1] == [(0, 0, 25 * rate)]


def test_the_identity_follows_the_clips_and_the_training_settings(seedvc):
    params = spec.settings("seedvc.finetune")
    first = seedvc.identity(params, {"clip:a": "1" * 64, "clip:b": "2" * 64})
    assert seedvc.identity(params, {"clip:b": "2" * 64, "clip:a": "1" * 64}) == first
    assert seedvc.digest(first) == seedvc.digest(json.loads(json.dumps(first)))
    assert seedvc.identity({**params, "steps": 300}, {"clip:a": "1" * 64, "clip:b": "2" * 64}) != (
        first
    )
    assert seedvc.identity(params, {"clip:a": "1" * 64, "clip:b": "3" * 64}) != first
    # How often checkpoints are written, or a label the app adds, leaves the state valid.
    unrelated = {**params, "save_every": 100, "label": "seed 15 s"}
    assert seedvc.identity(unrelated, {"clip:a": "1" * 64, "clip:b": "2" * 64}) == first


def test_the_scratch_state_resumes_restarts_or_is_finished(seedvc, tmp_path):
    folder = tmp_path / "scratch" / "finetune"
    wanted = seedvc.identity(spec.settings("seedvc.finetune"), {"clip:a": "1" * 64})
    assert seedvc.claim_state(folder, wanted) == "start"
    assert json.loads((folder / "identity.json").read_text(encoding="utf-8")) == wanted
    write(folder / "dataset" / "a_00.wav")
    assert seedvc.claim_state(folder, wanted) == "start", "a prepared dataset alone is no progress"
    assert (folder / "dataset" / "a_00.wav").exists(), "the same identity keeps its dataset"
    write(folder / "DiT_epoch_00004_step_00500.pth")
    write(folder / "DiT_epoch_00009_step_01000.pth.partial")
    assert seedvc.claim_state(folder, wanted) == "resume"
    assert not (folder / "DiT_epoch_00009_step_01000.pth.partial").exists(), "half a save is gone"
    write(folder / "ft_model.pth")
    assert seedvc.claim_state(folder, wanted) == "finished"
    other = seedvc.identity(spec.settings("seedvc.finetune"), {"clip:a": "9" * 64})
    assert seedvc.claim_state(folder, other) == "start"
    assert sorted(path.name for path in folder.iterdir()) == ["identity.json"], "wiped"
    (folder / "identity.json").write_text("{broken", encoding="utf-8")
    write(folder / "ft_model.pth")
    assert seedvc.claim_state(folder, other) == "start"
    assert not (folder / "ft_model.pth").exists()


def test_a_resume_picks_the_highest_step(seedvc):
    names = [
        "DiT_epoch_00009_step_00900.pth",
        "DiT_epoch_00010_step_01000.pth",
        "DiT_epoch_00011_step_01100.pth.partial",
        "ft_model.pth",
        "config_dit_mel_seed_uvit_whisper_base_f0_44k.yml",
    ]
    assert seedvc.latest_checkpoint(names) == ("DiT_epoch_00010_step_01000.pth", 1000)
    assert seedvc.latest_checkpoint(["ft_model.pth"]) is None


def test_train_py_writes_its_state_atomically_and_resumes_fully(seedvc, tmp_path):
    folder = tmp_path / "finetune"
    folder.mkdir()
    seen = []

    def save(obj, f):
        seen.append(Path(f).name)
        Path(f).write_text(obj, encoding="utf-8")

    atomic = seedvc.atomic_save(save, folder)
    atomic("weights", str(folder / "ft_model.pth"))
    assert seen == ["ft_model.pth.partial"]
    assert (folder / "ft_model.pth").read_text(encoding="utf-8") == "weights"
    assert not (folder / "ft_model.pth.partial").exists()
    atomic("elsewhere", str(tmp_path / "other.pth"))
    assert seen[-1] == "other.pth", "files outside the state folder are left to torch.save"

    calls = []

    def load(model, optimizer, path, load_only_params=True, **kwargs):
        calls.append(load_only_params)
        return model, optimizer, 0, 0

    resuming = seedvc.resuming_load(load, folder)
    resuming(None, None, str(folder / "DiT_epoch_00004_step_00500.pth"), load_only_params=True)
    resuming(None, None, str(tmp_path / "base.pth"), load_only_params=True, ignore_modules=[])
    resuming(None, None, str(folder / "ft_model.pth"), load_only_params=True)
    assert calls == [False, True, True], "only the state's own checkpoints restore the optimizer"

    source = write(tmp_path / "config.yml", b"log_dir: x\n")
    seedvc.atomic_copyfile(str(source), str(folder / "config.yml"))
    assert (folder / "config.yml").read_bytes() == b"log_dir: x\n"
    assert not (folder / "config.yml.partial").exists()


def test_the_learning_rate_is_the_repositorys(seedvc):
    seedvc.check_learning_rate(spec.settings("seedvc.finetune"))
    with pytest.raises(ValueError, match="fixes AdamW"):
        seedvc.check_learning_rate({"learning_rate": 2e-5})


def test_every_model_file_is_a_registered_file_of_the_home(seedvc):
    registered = {model["path"] for model in registry.load()["seedvc"].models}
    home = Path("home")
    for repo_id, filename in seedvc.TRAINING_WEIGHTS:
        path = seedvc.local_weights(home, repo_id, filename).relative_to(home).as_posix()
        assert path in registered, path
    for entry in seedvc.VERSIONS.values():
        assert {entry["checkpoint"], entry["config"]} <= registered
    for repo_id in ("nvidia/bigvgan_v2_44khz_128band_512x", "openai/whisper-small"):
        prefix = f"models/hf/hub/models--{repo_id.replace('/', '--')}/snapshots/"
        assert any(path.startswith(prefix) for path in registered), repo_id
    finetune = spec.settings("seedvc.finetune")
    version = seedvc.VERSIONS[finetune["version"]]
    assert (finetune["checkpoint"], finetune["config"]) == (
        version["checkpoint"],
        version["config"],
    )
    assert spec.settings("seedvc.convert")["version"] == finetune["version"]


def test_train_py_gets_its_output_in_scratch_and_its_models_from_the_home(seedvc, tmp_path):
    home = fake_home(tmp_path)
    hub = home / "models" / "hf" / "hub"
    vocoder = hub / "models--nvidia--bigvgan_v2_44khz_128band_512x" / "snapshots" / "95a9"
    whisper = hub / "models--openai--whisper-small" / "snapshots" / "973a"
    vocoder.mkdir(parents=True)
    whisper.mkdir(parents=True)
    base = {
        "log_dir": "./runs/run_dit_mel_seed_uvit_whisper_base_f0_44k",
        "pretrained_model": "DiT.pth",
        "model_params": {
            "vocoder": {"type": "bigvgan", "name": "nvidia/bigvgan_v2_44khz_128band_512x"},
            "speech_tokenizer": {"type": "whisper", "name": "openai/whisper-small"},
        },
    }
    config = seedvc.training_config(base, home, tmp_path / "scratch")
    assert config["log_dir"] == str(tmp_path / "scratch") and config["pretrained_model"] == ""
    assert config["model_params"]["vocoder"]["name"] == str(vocoder)
    assert config["model_params"]["speech_tokenizer"]["name"] == str(whisper)
    assert base["model_params"]["vocoder"]["name"] == "nvidia/bigvgan_v2_44khz_128band_512x"
    (whisper.parent / "other").mkdir()
    with pytest.raises(RuntimeError, match="one installed snapshot"):
        seedvc.training_config(base, home, tmp_path / "scratch")
    hifigan = {**base, "model_params": {**base["model_params"], "vocoder": {"type": "hifigan"}}}
    with pytest.raises(ValueError, match="BigVGAN"):
        seedvc.training_config(hifigan, home, tmp_path / "scratch")


def test_train_py_loads_the_homes_files_and_never_downloads(seedvc, tmp_path):
    home = fake_home(tmp_path)
    campplus = write(home / "models" / "funasr_campplus" / "campplus_cn_common.bin")
    converter = write(home / "models" / "myshell-ai_OpenVoiceV2" / "converter" / "checkpoint.pth")
    settings = write(home / "models" / "myshell-ai_OpenVoiceV2" / "converter" / "config.json")
    load = seedvc.local_loader(home)
    assert load("funasr/campplus", "campplus_cn_common.bin", config_filename=None) == str(campplus)
    pair = load("myshell-ai/OpenVoiceV2", "converter/checkpoint.pth", "converter/config.json")
    assert pair == (str(converter), str(settings))
    with pytest.raises(FileNotFoundError, match="engine setup"):
        load("Plachta/Seed-VC", "se_db.pt", None)


def test_caches_stay_in_the_work_directory(seedvc, tmp_path):
    work = tmp_path / "work"
    env = seedvc.cache_environment(work)
    assert env["HF_HUB_OFFLINE"] == env["TRANSFORMERS_OFFLINE"] == "1"
    paths = [value for key, value in env.items() if not value.isdigit()]
    assert {
        "HF_HOME",
        "HF_HUB_CACHE",
        "TRANSFORMERS_CACHE",
        "TORCH_HOME",
        "NUMBA_CACHE_DIR",
    } <= set(env)
    assert paths and all(Path(value).is_relative_to(work) for value in paths), paths


def test_one_pitch_shift_per_source_speaker(seedvc):
    assert seedvc.speaker_shift(188.0, 118.0) == 8, "a man's 118 Hz onto p011's 188 Hz"
    assert seedvc.speaker_shift(188.0, 188.0) == 0
    assert seedvc.speaker_shift(118.0, 188.0) == -8
    keys = ["clip:a1", "clip:a2", "clip:b1"]
    assert seedvc.clip_shifts({"pitch_shift": 0}, keys) == dict.fromkeys(keys, 0.0)
    one = {"target_median_hz": 188.0, "source_median_hz": 118.0}
    assert seedvc.clip_shifts(one, keys) == dict.fromkeys(keys, 8)
    many = {
        "target_median_hz": 188.0,
        "source_median_hz": {"man": 118.0, "woman": 210.0},
        "clip_speakers": {"a1": "man", "a2": "man", "b1": "woman"},
    }
    assert seedvc.clip_shifts(many, keys) == {"clip:a1": 8, "clip:a2": 8, "clip:b1": -2}
    with pytest.raises(ValueError, match="no source speaker"):
        seedvc.clip_shifts({**many, "clip_speakers": {"a1": "man"}}, keys)
    with pytest.raises(ValueError, match="one pitch shift per source speaker"):
        seedvc.clip_shifts(spec.settings("seedvc.convert"), keys)
    unmeasured = {**many, "source_median_hz": {"man": None, "woman": 210.0}}
    with pytest.raises(ValueError, match="positive"):
        seedvc.clip_shifts(unmeasured, keys)


def test_conversion_settings_are_the_spec_and_the_research_defaults(seedvc):
    settings = seedvc.convert_settings(spec.settings("seedvc.convert"))
    assert settings == {
        "version": "v1_44k_f0",
        "steps": 50,
        "cfg": 0.7,
        "fp16": True,
        "seed": 7,
        "max_reference_seconds": 25.0,
        "length_adjust": 1.0,
        "auto_pitch": False,
    }
    with pytest.raises(ValueError, match="Unsupported"):
        seedvc.convert_settings({**spec.settings("seedvc.convert"), "version": "v2"})
    with pytest.raises(ValueError, match="pitch shift rule"):
        seedvc.convert_settings({**spec.settings("seedvc.convert"), "shift": "per_clip"})


def test_a_converted_clip_keeps_the_source_length_and_its_level(seedvc):
    assert seedvc.fitted_samples(2.0, 44100) == 88200
    assert seedvc.fitted_samples(1.23456, 44100) == 54444
    assert seedvc.peak_scale(0.5) == 1.0
    assert seedvc.peak_scale(1.0) == 1.0
    assert seedvc.peak_scale(2.0) == pytest.approx(0.495)


def test_inputs_name_clips_references_and_the_model(seedvc, tmp_path):
    assert seedvc.clip_names(["clip:shot 1/line", "clip:b"]) == {
        "clip:shot 1/line": "shot_1_line",
        "clip:b": "b",
    }
    with pytest.raises(ValueError, match="same output file"):
        seedvc.clip_names(["clip:a b", "clip:a_b"])
    a, b = Path("a.wav"), Path("b.wav")
    assert seedvc.references({"reference:1": b, "reference": a, "clip:x": a}) == [a, b]
    with pytest.raises(RuntimeError, match="reference"):
        seedvc.references({"clip:x": a})
    scratch = tmp_path / "scratch"
    job = make_job(seedvc, tmp_path, "convert", {}, {"audio": a}, scratch=scratch)
    assert seedvc.clips(job) == {"audio": a}
    with pytest.raises(RuntimeError, match="fine-tuned model"):
        seedvc.fine_tuned(job)
    tuned = write(scratch / "finetune" / "ft_model.pth")
    assert seedvc.fine_tuned(job) == (tuned, "scratch")
    job = make_job(seedvc, tmp_path, "convert", {}, {"audio": a, "model": b}, scratch=scratch)
    assert seedvc.fine_tuned(job) == (b, "input")


def test_publishing_links_or_copies_by_atomic_rename(seedvc, tmp_path, monkeypatch):
    source = write(tmp_path / "scratch" / "ft_model.pth", b"weights")
    target = tmp_path / "work" / "outputs" / "ft_model.pth"
    target.parent.mkdir(parents=True)
    seedvc.publish(source, target)
    seedvc.publish(source, target)
    assert (
        target.read_bytes() == b"weights" and not target.with_name("ft_model.pth.partial").exists()
    )
    target.unlink()

    def no_links(*args):
        raise OSError("no hard links on this volume")

    monkeypatch.setattr(seedvc.os, "link", no_links)
    seedvc.publish(source, target)
    assert target.read_bytes() == b"weights"


def test_training_measures_are_the_researchs(seedvc):
    stats = seedvc.train_stats([1.0, 1.0, 1.0] + [0.25] * 8, 2, 30.0, 5.0, 0.4, 6.1, 6.5, None)
    assert stats["steps"] == 11 and stats["seconds_per_step_steady"] == 0.25
    assert stats["steps_per_second_steady"] == 4.0 and stats["final_ema_loss"] == 0.4
    assert (
        seedvc.train_stats([0.3], 2, 1.0, 0.5, float("nan"), None, None, 500)["final_ema_loss"]
        is None
    )


def test_a_finished_fine_tune_is_republished_without_training(seedvc, tmp_path):
    home, scratch = fake_home(tmp_path), tmp_path / "scratch"
    params = spec.settings("seedvc.finetune")
    clips = {"clip:a": write(tmp_path / "a.wav", b"first"), "clip:b": write(tmp_path / "b.wav")}
    wanted = seedvc.identity(params, {k: seedvc.file_sha256(p) for k, p in clips.items()})
    folder = scratch / "finetune"
    assert seedvc.claim_state(folder, wanted) == "start"
    write(folder / "ft_model.pth", b"tuned weights")
    write(folder / "dataset" / "stats.json", json.dumps({"written_files": 2}).encode())
    write(folder / "train_stats.json", json.dumps({"steps": 600, "seconds_total": 307.7}).encode())
    job = make_job(seedvc, tmp_path, "finetune", params, clips, home, scratch)
    seedvc.finetune(job)
    [output] = job.outputs
    assert (output["key"], output["kind"], output["path"]) == (
        "model",
        "seedvc_model",
        "outputs/ft_model.pth",
    )
    assert output["info"]["reused"] and output["info"]["identity"] == seedvc.digest(wanted)
    assert output["info"]["train"]["seconds_total"] == 307.7
    assert output["info"]["dataset"] == {"written_files": 2}
    assert (job.work / "outputs" / "ft_model.pth").read_bytes() == b"tuned weights"
    assert (folder / "ft_model.pth").read_bytes() == b"tuned weights"


def test_finished_conversions_are_registered_again_without_a_model(seedvc, tmp_path):
    clip, reference = write(tmp_path / "line.wav"), write(tmp_path / "seed.wav")
    params = {**spec.settings("seedvc.convert"), "pitch_shift": 0}
    job = make_job(
        seedvc, tmp_path, "convert", params, {"clip:l1": clip, "reference": reference}, None
    )
    write(job.target("l1.wav"))
    write(job.target("l1.json"), json.dumps({"sample_rate": 44100, "rms": 0.087}).encode())
    seedvc.convert(job)
    assert job.outputs == [
        {
            "key": "clip:l1",
            "path": "outputs/l1.wav",
            "kind": "audio",
            "info": {"sample_rate": 44100, "rms": 0.087},
        }
    ]


def test_the_spec_settings_carry_what_the_tasks_read():
    """Every key a task reads from `params` without a research default is in its voice spec table."""
    assert {
        "version",
        "checkpoint",
        "config",
        "steps",
        "batch_size",
        "learning_rate",
        "min_clip_seconds",
        "max_clip_seconds",
    } <= set(spec.settings("seedvc.finetune"))
    assert {"version", "steps", "cfg", "seed", "fp16", "max_reference_seconds", "shift"} <= set(
        spec.settings("seedvc.convert")
    )
