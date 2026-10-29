"""The Seed-VC engine's worker (ADR 0015, ADR 0016 P3e). Runs in the seedvc home's interpreter (Python 3.10).

Tasks, each with its settings from the voice spec (`integrations/voice/knowledge/voice.toml`) in `params`:

- `finetune` inputs `clip:<id>` (the target's clips) -> `model`: Seed-VC v1 fine-tuned by the repository's own
             `train.py` (its Trainer and loss, AdamW at its fixed lr 1e-5, from `params["checkpoint"]`). The state
             lives in `scratch/finetune/`: `identity.json` (the clips' hashes and the training settings), the prepared
             `dataset/`, train.py's checkpoints and `ft_model.pth`, all written by atomic rename. A relaunch with the
             same identity republishes a finished model or resumes from the newest checkpoint; a different identity
             starts again. `params["save_every"]` (default: none within the run, as in the research) sets how often
             train.py writes a resumable checkpoint (~2.2 GB with optimizer state).
- `convert`  inputs `clip:<id>` (or one `audio`) sources, `reference` or `reference:<n>` (the target's clips,
             joined with 0.25 s gaps and cut at `max_reference_seconds`) and optionally `model` (a fine-tuned
             `ft_model.pth`; without it, the operation's finished fine-tune in scratch) -> `clip:<id>`: each source in
             the target voice, mono 16-bit WAV at the model's rate, as long as the source. One pitch shift per source
             speaker: `pitch_shift` semitones for the whole job, or `target_median_hz` with `source_median_hz` (one
             median, or medians by speaker plus `clip_speakers`: {clip id: speaker}), round(12 log2(target/source)).

Ported from `scripts/community_research/voice/{vc_seedvc_train,vc_seedvc}.py`, keeping the settings the research's
numbers depend on. The repository's code is imported from the home and never changed; every model loads from the
home's files (no network, no downloads); caches and bytecode never write into the home. Module level is standard
library only, so the decision rules below are tested in the app's own environment.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
import types
from pathlib import Path

from protocol import Job, run

# The base checkpoint and config of each supported version (relative to the home); convert uses the config.
VERSIONS = {
    "v1_44k_f0": {
        "checkpoint": "models/Plachta_Seed-VC/"
        "DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth",
        "config": "models/Plachta_Seed-VC/config_dit_mel_seed_uvit_whisper_base_f0_44k.yml",
    },
}
# What train.py fetches through `hf_utils.load_custom_model_from_hf`, installed as models/<org>_<name>/<file>.
TRAINING_WEIGHTS = (
    ("funasr/campplus", "campplus_cn_common.bin"),
    ("lj1995/VoiceConversionWebUI", "rmvpe.pt"),
    ("myshell-ai/OpenVoiceV2", "converter/checkpoint.pth"),
    ("myshell-ai/OpenVoiceV2", "converter/config.json"),
    ("Plachta/Seed-VC", "se_db.pt"),
)
REPO_LEARNING_RATE = (
    1e-5  # train.py's scheduler_params["base_lr"], fixed in the repository
)
DATASET_RATE = (
    44100  # prepared clips; train.py resamples to the model's rate when it loads them
)
MIN_PIECE_SECONDS = 0.5  # a cut piece shorter than this is dropped
SAVE_EVERY = 100000  # the research's ft600 runs wrote no intermediate checkpoint
MAX_EPOCHS = 100000
NUM_WORKERS = 0  # Windows-safe data loading
REFERENCE_GAP_SECONDS = 0.25
# The training settings a fine-tuned model depends on; anything else in params leaves the state valid.
IDENTITY_KEYS = (
    "version",
    "checkpoint",
    "config",
    "steps",
    "batch_size",
    "learning_rate",
    "min_clip_seconds",
    "max_clip_seconds",
)
STATE = "finetune"
FINAL = "ft_model.pth"
CHECKPOINT = re.compile(r"^DiT_epoch_(\d+)_step_(\d+)\.pth$")


# --- decision rules (standard library) -----------------------------------------------------------------------------


def dataset_plan(
    samples: int, rate: int, min_clip_seconds: float, max_clip_seconds: float
) -> tuple[int, list[tuple[int, int, int]], int]:
    """How one clip enters the fine-tune set (the research's `pad` mode): zero-padded up to `min_clip_seconds`
    (Seed-VC skips clips under 1 s), then cut into pieces of at most `max_clip_seconds` (it skips clips over 30 s);
    a piece under 0.5 s is dropped. Returns (padded length, [(index, start, end)], pieces cut off the first)."""
    padded = max(samples, int(min_clip_seconds * rate))
    size = int(max_clip_seconds * rate)
    pieces, cuts = [], 0
    for index, start in enumerate(range(0, padded, size)):
        end = min(start + size, padded)
        if index:
            cuts += 1
        if end - start >= int(MIN_PIECE_SECONDS * rate):
            pieces.append((index, start, end))
    return padded, pieces, cuts


def identity(params: dict, digests: dict) -> dict:
    """What a fine-tuned state was made from: the training settings and each input clip's sha256."""
    return {
        "schema": 1,
        "params": {key: params.get(key) for key in IDENTITY_KEYS},
        "inputs": dict(sorted(digests.items())),
    }


def digest(document) -> str:
    return hashlib.sha256(
        json.dumps(document, sort_keys=True).encode("utf-8")
    ).hexdigest()


def file_sha256(path: Path) -> str:
    value = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def latest_checkpoint(names) -> tuple[str, int] | None:
    """The intermediate checkpoint train.py resumes from: the highest step (its own rule); `.partial` never counts."""
    found = [
        (int(match.group(2)), name)
        for name in names
        if (match := CHECKPOINT.match(name))
    ]
    if not found:
        return None
    step, name = max(found)
    return name, step


def atomic_text(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def claim_state(folder: Path, wanted: dict) -> str:
    """Make the fine-tune's scratch folder hold state for `wanted`: a different or missing identity wipes it and
    records `wanted`. Returns 'finished' (the model is there), 'resume' (a checkpoint is there) or 'start'."""
    manifest = folder / "identity.json"
    try:
        found = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        found = None
    if found != wanted:
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        atomic_text(manifest, json.dumps(wanted, indent=1, sort_keys=True) + "\n")
        return "start"
    for stale in folder.glob("*.partial"):
        if stale.is_dir():
            shutil.rmtree(stale)
        else:
            stale.unlink()
    if (folder / FINAL).is_file():
        return "finished"
    if latest_checkpoint(path.name for path in folder.iterdir()):
        return "resume"
    return "start"


def check_learning_rate(params: dict) -> None:
    if not math.isclose(float(params["learning_rate"]), REPO_LEARNING_RATE):
        raise ValueError(
            f"train.py fixes AdamW's learning rate at {REPO_LEARNING_RATE}; "
            f"the spec asks for {params['learning_rate']}."
        )


def train_stats(
    timings: list[float],
    batch_size: int,
    total: float,
    setup: float,
    ema_loss,
    peak_gb: float | None,
    reserved_gb: float | None,
    resumed_from: int | None,
) -> dict:
    """The research's training measures (steady state skips the first three steps of runs longer than six)."""
    warm = timings[3:] if len(timings) > 6 else timings
    loss = float(ema_loss) if ema_loss is not None else float("nan")
    return {
        "steps": len(timings),
        "batch_size": batch_size,
        "seconds_total": round(total, 2),
        "seconds_setup": round(setup, 2),
        "seconds_in_steps": round(sum(timings), 2),
        "steps_per_second_total_loop": round(len(timings) / max(sum(timings), 1e-9), 3),
        "steps_per_second_steady": round(len(warm) / max(sum(warm), 1e-9), 3)
        if warm
        else None,
        "seconds_per_step_steady": round(sum(warm) / len(warm), 3) if warm else None,
        "final_ema_loss": round(loss, 5) if math.isfinite(loss) else None,
        "peak_vram_gb": peak_gb,
        "peak_vram_reserved_gb": reserved_gb,
        "resumed_from_step": resumed_from,
    }


def convert_settings(params: dict) -> dict:
    """The conversion settings: the spec's, plus the research defaults the spec leaves out."""
    version = params.get("version", "v1_44k_f0")
    if version not in VERSIONS:
        raise ValueError(
            f"Unsupported Seed-VC version {version!r}; use one of {sorted(VERSIONS)}."
        )
    if params.get("shift", "per_speaker") != "per_speaker":
        raise ValueError(f"Unknown pitch shift rule {params.get('shift')!r}.")
    return {
        "version": version,
        "steps": int(params["steps"]),
        "cfg": float(params["cfg"]),
        "fp16": bool(params["fp16"]),
        "seed": params.get("seed"),
        "max_reference_seconds": float(params["max_reference_seconds"]),
        "length_adjust": float(params.get("length_adjust", 1.0)),
        "auto_pitch": bool(params.get("auto_pitch", False)),
    }


def speaker_shift(target_hz, source_hz) -> int:
    """Semitones that move a speaker's median onto the target's: round(12 log2(target / source))."""
    if not target_hz or not source_hz or target_hz <= 0 or source_hz <= 0:
        raise ValueError("A pitch shift needs positive target and source medians.")
    return round(12 * math.log2(float(target_hz) / float(source_hz)))


def clip_shifts(params: dict, keys) -> dict:
    """One shift per source speaker, never per clip, by input key: an explicit `pitch_shift`, or the speakers'
    medians (`clip_speakers` maps clip ids, the part of `clip:<id>` after the colon, to speakers)."""
    keys = list(keys)
    if params.get("pitch_shift") is not None:
        return {key: float(params["pitch_shift"]) for key in keys}
    target, sources = params.get("target_median_hz"), params.get("source_median_hz")
    if target is None or sources is None:
        raise ValueError(
            "Seed-VC needs one pitch shift per source speaker: `pitch_shift`, or `target_median_hz` with "
            "`source_median_hz` (one median, or medians by speaker with `clip_speakers`)."
        )
    if isinstance(sources, (int, float)):
        shift = speaker_shift(target, sources)
        return {key: shift for key in keys}
    speakers = params.get("clip_speakers") or {}
    shifts = {}
    for key in keys:
        speaker = speakers.get(key.split(":", 1)[-1])
        if speaker not in sources:
            raise ValueError(f"Clip {key} has no source speaker with a median.")
        shifts[key] = speaker_shift(target, sources[speaker])
    return shifts


def fitted_samples(seconds: float, rate: int) -> int:
    return round(seconds * rate)


def peak_scale(peak: float) -> float:
    """A converted clip that clips is scaled to a 0.99 peak; anything else keeps its level."""
    return 0.99 / peak if peak > 1.0 else 1.0


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", text) or "clip"


def clip_names(keys) -> dict:
    """Output file stems by input key (`clip:<id>` -> id)."""
    names = {key: safe_name(key.split(":", 1)[-1]) for key in keys}
    if len(set(names.values())) != len(names):
        raise ValueError("Two clip ids map to the same output file name.")
    return names


def clips(job: Job) -> dict:
    """The job's `clip:<id>` inputs by key, or its one `audio` input."""
    found = {key: path for key, path in job.inputs.items() if key.startswith("clip:")}
    if not found and "audio" in job.inputs:
        found = {"audio": job.inputs["audio"]}
    if not found:
        raise RuntimeError("The task needs `clip:<id>` or `audio` inputs.")
    return dict(sorted(found.items()))


def references(inputs: dict) -> list:
    keys = sorted(
        key for key in inputs if key == "reference" or key.startswith("reference:")
    )
    if not keys:
        raise RuntimeError(
            "The convert task needs a `reference` input (the target's voice)."
        )
    return [inputs[key] for key in keys]


def fine_tuned(job: Job) -> tuple[Path, str]:
    """The fine-tuned model to convert with: the `model` input, else the operation's finished fine-tune."""
    if "model" in job.inputs:
        return job.inputs["model"], "input"
    if job.scratch is not None and (job.scratch / STATE / FINAL).is_file():
        return job.scratch / STATE / FINAL, "scratch"
    raise RuntimeError(
        "The convert task needs a fine-tuned model: a `model` input or a finished fine-tune in the scratch."
    )


def publish(source: Path, target: Path) -> None:
    """A scratch file appears in the work directory by atomic rename: a hard link where the volume allows one."""
    if target.exists() and os.path.samefile(source, target):
        return
    temporary = target.with_name(target.name + ".partial")
    temporary.unlink(missing_ok=True)
    try:
        os.link(source, temporary)
    except OSError:
        shutil.copyfile(source, temporary)
    os.replace(temporary, target)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --- the home: files, caches, the repository -----------------------------------------------------------------------


def installed(job: Job) -> Path:
    if job.home is None or not job.home.is_dir():
        raise RuntimeError("The Seed-VC engine's home is not installed.")
    if not (job.home / "repo" / "train.py").is_file():
        raise RuntimeError("The Seed-VC repository is missing from the engine's home.")
    return job.home


def require(path: Path) -> Path:
    if not Path(path).is_file():
        raise FileNotFoundError(f"Seed-VC needs {path}; the engine setup installs it.")
    return Path(path)


def local_weights(home: Path, repo_id: str, filename: str) -> Path:
    return home / "models" / repo_id.replace("/", "_") / filename


def snapshot(home: Path, repo_id: str) -> Path:
    """The single Hugging Face snapshot of `repo_id` installed under models/hf/hub."""
    folder = (
        home
        / "models"
        / "hf"
        / "hub"
        / f"models--{repo_id.replace('/', '--')}"
        / "snapshots"
    )
    found = sorted(p for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else []
    if len(found) != 1:
        raise RuntimeError(
            f"Expected one installed snapshot of {repo_id}, found {len(found)}."
        )
    return found[0]


def local_loader(home: Path):
    """`hf_utils.load_custom_model_from_hf` for train.py: the home's files, never a download."""

    def load(repo_id, model_filename="pytorch_model.bin", config_filename=None):
        model = require(local_weights(home, repo_id, model_filename))
        if config_filename is None:
            return str(model)
        return str(model), str(require(local_weights(home, repo_id, config_filename)))

    return load


def cache_environment(work: Path) -> dict:
    """Every model loads by an explicit path inside the home, so no cache needs the home: Hugging Face and the
    other libraries get empty, offline caches in the work directory (a stray hub lookup fails, never downloads)."""
    cache = work / "cache"
    return {
        "HF_HOME": str(cache / "hf"),
        "HF_HUB_CACHE": str(cache / "hf" / "hub"),
        "TRANSFORMERS_CACHE": str(cache / "hf" / "hub"),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
        "TORCH_HOME": str(cache / "torch"),
        "NUMBA_CACHE_DIR": str(cache / "numba"),
        "MPLCONFIGDIR": str(cache / "matplotlib"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def environment(job: Job) -> Path:
    """Prepare this process for the repository's code; returns the home."""
    home = installed(job)
    # Importing the repository must not leave __pycache__ in the home.
    sys.dont_write_bytecode = True
    os.environ.update(cache_environment(job.work))
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # tqdm bars
        except Exception:  # noqa: BLE001, S110 - streams without reconfigure
            pass
    repo = str(home / "repo")
    if repo not in sys.path:
        sys.path.insert(0, repo)
    return home


def training_config(config: dict, home: Path, scratch: Path) -> dict:
    """The version's config for train.py: output in `scratch/finetune` (train.py writes <log_dir>/<run name>), no
    pretrained download, the vocoder and the speech tokenizer from the home's snapshots."""
    config = copy.deepcopy(config)
    model = config["model_params"]
    if (
        model["vocoder"]["type"] != "bigvgan"
        or model["speech_tokenizer"]["type"] != "whisper"
    ):
        raise ValueError(
            "Only BigVGAN vocoders and whisper tokenizers are supported (v1)."
        )
    config["log_dir"] = str(scratch)
    config["pretrained_model"] = ""
    model["vocoder"]["name"] = str(snapshot(home, model["vocoder"]["name"]))
    model["speech_tokenizer"]["name"] = str(
        snapshot(home, model["speech_tokenizer"]["name"])
    )
    return config


def atomic_save(save, folder: Path):
    """torch.save for train.py: a file inside the state folder is written by atomic rename."""
    folder = folder.resolve()

    def wrapped(obj, f, *args, **kwargs):
        if isinstance(f, (str, os.PathLike)) and Path(f).resolve().parent == folder:
            target = Path(f)
            temporary = target.with_name(target.name + ".partial")
            save(obj, str(temporary), *args, **kwargs)
            os.replace(temporary, target)
            return None
        return save(obj, f, *args, **kwargs)

    return wrapped


def atomic_copyfile(source, target):
    """train.py copies its config into the state folder; the copy lands by atomic rename."""
    temporary = Path(str(target) + ".partial")
    shutil.copyfile(source, temporary)
    os.replace(temporary, target)
    return str(target)


def resuming_load(load, folder: Path):
    """train.py reloads a checkpoint's weights only and restarts its step count; resuming one of the state's own
    checkpoints also restores the optimizer, its scheduler and the step, so a resumed run ends at the same total."""
    folder = folder.resolve()

    def wrapped(model, optimizer, path, load_only_params=True, **kwargs):
        ours = bool(path) and Path(path).resolve().parent == folder
        ours = ours and bool(CHECKPOINT.match(Path(path).name))
        return load(
            model,
            optimizer,
            path,
            load_only_params=load_only_params and not ours,
            **kwargs,
        )

    return wrapped


# --- fine-tuning ---------------------------------------------------------------------------------------------------


def prepare_dataset(job: Job, inputs: dict, folder: Path, params: dict) -> None:
    """The clips at 44.1 kHz in `folder`, padded and cut so that Seed-VC's 1-30 s rule keeps every one of them;
    written to `<folder>.partial` and renamed whole."""
    import librosa
    import numpy as np
    import soundfile as sf

    partial = folder.with_name(folder.name + ".partial")
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True)
    stats = {"input_files": len(inputs), "written_files": 0, "padded": 0, "cut": 0}
    for key, path in inputs.items():
        job.check()
        wave = librosa.load(str(path), sr=DATASET_RATE)[0].astype(np.float32)
        padded, pieces, cuts = dataset_plan(
            len(wave),
            DATASET_RATE,
            float(params["min_clip_seconds"]),
            float(params["max_clip_seconds"]),
        )
        if padded > len(wave):
            wave = np.pad(wave, (0, padded - len(wave)))
            stats["padded"] += 1
        stats["cut"] += cuts
        stem = safe_name(key.split(":", 1)[-1])
        for index, start, end in pieces:
            sf.write(
                str(partial / f"{stem}_{index:02d}.wav"),
                wave[start:end],
                DATASET_RATE,
                subtype="PCM_16",
            )
            stats["written_files"] += 1
    # train.py's dataset reads audio files only, so the stats travel inside the folder.
    (partial / "stats.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    os.replace(partial, folder)


def train_in_scratch(job: Job, inputs: dict, folder: Path) -> None:
    """Run the repository's train.py in this process, its state in `folder`."""
    params = job.params
    home = environment(job)
    import torch
    import yaml

    if not torch.cuda.is_available():
        raise RuntimeError("Seed-VC fine-tuning needs a CUDA GPU.")
    base = require(home / params["checkpoint"])
    config_path = require(home / params["config"])
    for repo_id, filename in TRAINING_WEIGHTS:
        require(local_weights(home, repo_id, filename))
    dataset = folder / "dataset"
    if not dataset.is_dir():
        job.progress(0.03, "Preparing the clips")
        prepare_dataset(job, inputs, dataset, params)
    job.check()
    config = training_config(
        yaml.safe_load(config_path.read_text(encoding="utf-8")), home, job.scratch
    )
    settings = job.work / "seedvc"
    settings.mkdir(exist_ok=True)
    config_copy = settings / config_path.name
    config_copy.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    found = latest_checkpoint(path.name for path in folder.iterdir())
    if found:
        print(f"resuming from {found[0]}", flush=True)

    job.progress(0.08, "Loading Seed-VC")
    import hf_utils

    # train.py binds `load_custom_model_from_hf` when it is imported, and points HF_HUB_CACHE at ./checkpoints.
    loader = local_loader(home)
    hf_utils.load_custom_model_from_hf = loader
    hub = os.environ["HF_HUB_CACHE"]
    import train as repo_train

    os.environ["HF_HUB_CACHE"] = hub
    repo_train.load_custom_model_from_hf = loader
    repo_train.shutil = types.SimpleNamespace(copyfile=atomic_copyfile)
    repo_train.load_checkpoint = resuming_load(repo_train.load_checkpoint, folder)
    timings: list[float] = []
    seen: dict = {"shown": 0.0}
    original_step = repo_train.Trainer.train_one_step
    original_init = repo_train.Trainer.__init__

    def timed_step(self, batch):
        torch.cuda.synchronize()
        begun = time.time()
        loss = original_step(self, batch)
        torch.cuda.synchronize()
        timings.append(time.time() - begun)
        done, now = self.iters + 1, time.time()
        if now - seen["shown"] >= 2.0 or done >= self.max_steps:
            seen["shown"] = now
            job.progress(
                0.1 + 0.85 * min(done / self.max_steps, 1.0),
                f"Step {done} of {self.max_steps}",
                loss=round(float(loss), 4),
            )
        job.check()
        return loss

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        seen["trainer"] = self
        seen["setup"] = time.time() - started
        seen["resumed_from"] = self.iters or None

    repo_train.Trainer.train_one_step = timed_step
    repo_train.Trainer.__init__ = init
    repo_train.torch.multiprocessing.set_start_method("spawn", force=True)
    arguments = types.SimpleNamespace(
        config=str(config_copy),
        pretrained_ckpt=None if found else str(base),
        dataset_dir=str(dataset),
        run_name=STATE,
        batch_size=int(params["batch_size"]),
        max_steps=int(params["steps"]),
        max_epochs=MAX_EPOCHS,
        save_every=int(params.get("save_every", SAVE_EVERY)),
        num_workers=NUM_WORKERS,
        gpu=0,
        device="cuda:0",
    )
    original_save = torch.save
    torch.save = atomic_save(original_save, folder)
    try:
        torch.cuda.reset_peak_memory_stats()
        started = time.time()
        repo_train.main(arguments)
        total = time.time() - started
    finally:
        torch.save = original_save
    if not (folder / FINAL).is_file():
        raise RuntimeError(f"train.py finished without {FINAL}.")
    stats = train_stats(
        timings,
        int(params["batch_size"]),
        total,
        seen.get("setup", 0.0),
        getattr(seen.get("trainer"), "ema_loss", None),
        round(torch.cuda.max_memory_allocated() / 2**30, 3),
        round(torch.cuda.max_memory_reserved() / 2**30, 3),
        seen.get("resumed_from"),
    )
    atomic_text(folder / "train_stats.json", json.dumps(stats, indent=1) + "\n")
    print("TRAIN_STATS " + json.dumps(stats), flush=True)


def finetune(job: Job) -> None:
    params = job.params
    installed(job)
    if params["version"] not in VERSIONS:
        raise ValueError(f"Unsupported Seed-VC version {params['version']!r}.")
    check_learning_rate(params)
    if job.scratch is None:
        raise RuntimeError(
            "The fine-tune keeps its state in the operation's scratch folder."
        )
    inputs = clips(job)
    folder = job.scratch / STATE
    job.progress(0.01, "Hashing the clips")
    wanted = identity(params, {key: file_sha256(path) for key, path in inputs.items()})
    state = claim_state(folder, wanted)
    if state != "finished":
        job.check()
        train_in_scratch(job, inputs, folder)
    job.check()
    job.progress(0.97, "Publishing the model")
    target = job.target(FINAL)
    publish(folder / FINAL, target)
    job.output(
        "model",
        target,
        "seedvc_model",
        version=params["version"],
        config=params["config"],
        steps=int(params["steps"]),
        batch_size=int(params["batch_size"]),
        learning_rate=float(params["learning_rate"]),
        identity=digest(wanted),
        clips=len(inputs),
        reused=state == "finished",
        dataset=read_json(folder / "dataset" / "stats.json"),
        train=read_json(folder / "train_stats.json"),
    )
    job.progress(1.0, "Fine-tuned")


# --- conversion ----------------------------------------------------------------------------------------------------


class Converter:
    """Seed-VC v1 (DiT + CFM): `inference.py`'s models, loaded once per job from the home's files."""

    def __init__(self, job: Job, checkpoint: Path, settings: dict):
        home = environment(job)
        import torch
        import yaml

        if not torch.cuda.is_available():
            raise RuntimeError("Seed-VC conversion needs a CUDA GPU.")
        from modules.commons import build_model, load_checkpoint, recursive_munch

        self.torch = torch
        self.device = torch.device("cuda")
        config_path = require(home / VERSIONS[settings["version"]]["config"])
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        model_params = recursive_munch(config["model_params"])
        model_params.dit_type = "DiT"
        if (
            model_params.vocoder.type != "bigvgan"
            or model_params.speech_tokenizer.type != "whisper"
        ):
            raise ValueError(
                "Only BigVGAN vocoders and whisper tokenizers are supported (v1)."
            )
        self.f0_condition = bool(
            config["model_params"]["DiT"].get("f0_condition", False)
        )
        self.sr = config["preprocess_params"]["sr"]
        self.hop_length = config["preprocess_params"]["spect_params"]["hop_length"]
        model = build_model(model_params, stage="DiT")
        model, _, _, _ = load_checkpoint(
            model,
            None,
            str(require(checkpoint)),
            load_only_params=True,
            ignore_modules=[],
            is_distributed=False,
        )
        for key in model:
            model[key].eval()
            model[key].to(self.device)
        model.cfm.estimator.setup_caches(max_batch_size=1, max_seq_length=8192)
        self.model = model

        from modules.campplus.DTDNN import CAMPPlus

        campplus = require(
            local_weights(home, "funasr/campplus", "campplus_cn_common.bin")
        )
        self.campplus = CAMPPlus(feat_dim=80, embedding_size=192)
        self.campplus.load_state_dict(torch.load(str(campplus), map_location="cpu"))
        self.campplus.eval().to(self.device)

        self.rmvpe = None
        if self.f0_condition:
            from modules.rmvpe import RMVPE

            rmvpe = require(
                local_weights(home, "lj1995/VoiceConversionWebUI", "rmvpe.pt")
            )
            self.rmvpe = RMVPE(str(rmvpe), is_half=False, device=self.device)

        from modules.bigvgan import bigvgan

        vocoder = snapshot(home, model_params.vocoder.name)
        self.vocoder = bigvgan.BigVGAN.from_pretrained(
            str(vocoder), use_cuda_kernel=False
        )
        self.vocoder.remove_weight_norm()
        self.vocoder = self.vocoder.eval().to(self.device)

        from transformers import AutoFeatureExtractor, WhisperModel

        whisper = str(snapshot(home, model_params.speech_tokenizer.name))
        self.whisper = WhisperModel.from_pretrained(
            whisper, torch_dtype=torch.float16
        ).to(self.device)
        del self.whisper.decoder
        self.whisper_extractor = AutoFeatureExtractor.from_pretrained(whisper)

        from modules.audio import mel_spectrogram

        spect = config["preprocess_params"]["spect_params"]
        mel_args = {
            "n_fft": spect["n_fft"],
            "win_size": spect["win_length"],
            "hop_size": spect["hop_length"],
            "num_mels": spect["n_mels"],
            "sampling_rate": self.sr,
            "fmin": spect.get("fmin", 0),
            "fmax": None if spect.get("fmax", "None") == "None" else 8000,
            "center": False,
        }
        self.to_mel = lambda x: mel_spectrogram(x, **mel_args)

    def reference(self, paths: list, max_seconds: float):
        """The reference clips joined with 0.25 s of silence, cut at the longest prompt Seed-VC uses."""
        import librosa
        import numpy as np

        pieces = []
        gap = np.zeros(int(REFERENCE_GAP_SECONDS * self.sr), dtype=np.float32)
        for index, path in enumerate(paths):
            if index:
                pieces.append(gap)
            pieces.append(librosa.load(str(path), sr=self.sr)[0].astype(np.float32))
        return np.concatenate(pieces)[: int(max_seconds * self.sr)]

    def semantic(self, waves_16k):
        torch = self.torch
        inputs = self.whisper_extractor(
            [waves_16k.squeeze(0).cpu().numpy()],
            return_tensors="pt",
            return_attention_mask=True,
        )
        features = self.whisper._mask_input_features(
            inputs.input_features, attention_mask=inputs.attention_mask
        ).to(self.device)
        with torch.no_grad():
            outputs = self.whisper.encoder(
                features.to(self.whisper.encoder.dtype),
                head_mask=None,
                output_attentions=False,
                output_hidden_states=False,
                return_dict=True,
            )
        hidden = outputs.last_hidden_state.to(torch.float32)
        return hidden[:, : waves_16k.size(-1) // 320 + 1]

    def semantic_long(self, waves_16k):
        """Whisper sees at most 30 s; longer audio goes through in 30 s windows with 5 s overlap."""
        torch = self.torch
        if waves_16k.size(-1) <= 16000 * 30:
            return self.semantic(waves_16k)
        overlap = 5
        parts, buffer, traversed = [], None, 0
        while traversed < waves_16k.size(-1):
            if buffer is None:
                chunk = waves_16k[:, traversed : traversed + 16000 * 30]
            else:
                chunk = torch.cat(
                    [
                        buffer,
                        waves_16k[:, traversed : traversed + 16000 * (30 - overlap)],
                    ],
                    dim=-1,
                )
            features = self.semantic(chunk)
            parts.append(features if traversed == 0 else features[:, 50 * overlap :])
            buffer = chunk[:, -16000 * overlap :]
            traversed += (
                30 * 16000 if traversed == 0 else chunk.size(-1) - 16000 * overlap
            )
        return torch.cat(parts, dim=1)

    @staticmethod
    def crossfade(chunk1, chunk2, overlap):
        import numpy as np

        fade_out = np.cos(np.linspace(0, np.pi / 2, overlap)) ** 2
        fade_in = np.cos(np.linspace(np.pi / 2, 0, overlap)) ** 2
        if len(chunk2) < overlap:
            chunk2[:overlap] = (
                chunk2[:overlap] * fade_in[: len(chunk2)]
                + (chunk1[-overlap:] * fade_out)[: len(chunk2)]
            )
        else:
            chunk2[:overlap] = chunk2[:overlap] * fade_in + chunk1[-overlap:] * fade_out
        return chunk2

    def convert(self, source: Path, reference_paths: list, p: dict):
        """`inference.py`'s conversion of one source: F0 from RMVPE shifted by `pitch_shift` semitones, the CFM in
        context windows next to the reference prompt, BigVGAN, crossfaded chunks."""
        import librosa
        import numpy as np
        import torchaudio

        torch = self.torch
        device, sr, hop = self.device, self.sr, self.hop_length
        steps, cfg = int(p["steps"]), float(p["cfg"])
        length_adjust, fp16 = float(p["length_adjust"]), bool(p["fp16"])
        source_audio = librosa.load(str(source), sr=sr)[0]
        ref_audio = self.reference(reference_paths, float(p["max_reference_seconds"]))
        max_context_window = sr // hop * 30
        overlap_frame_len = 16
        overlap_wave_len = overlap_frame_len * hop

        source_audio = torch.tensor(source_audio).unsqueeze(0).float().to(device)
        ref_audio = torch.tensor(ref_audio).unsqueeze(0).float().to(device)
        source_16k = torchaudio.functional.resample(source_audio, sr, 16000)
        ref_16k = torchaudio.functional.resample(ref_audio, sr, 16000)
        s_alt = self.semantic_long(source_16k)
        s_ori = self.semantic(ref_16k)

        mel = self.to_mel(source_audio)
        mel2 = self.to_mel(ref_audio)
        target_lengths = torch.LongTensor([int(mel.size(2) * length_adjust)]).to(device)
        target2_lengths = torch.LongTensor([mel2.size(2)]).to(device)

        feat2 = torchaudio.compliance.kaldi.fbank(
            ref_16k, num_mel_bins=80, dither=0, sample_frequency=16000
        )
        feat2 = feat2 - feat2.mean(dim=0, keepdim=True)
        style2 = self.campplus(feat2.unsqueeze(0))

        f0_ori = f0_shifted = None
        if self.f0_condition:
            f0_ori_np = self.rmvpe.infer_from_audio(ref_16k[0], thred=0.03)
            f0_alt_np = self.rmvpe.infer_from_audio(source_16k[0], thred=0.03)
            f0_ori = torch.from_numpy(f0_ori_np).to(device)[None]
            f0_alt = torch.from_numpy(f0_alt_np).to(device)[None]
            voiced_ori = f0_ori[f0_ori > 1]
            voiced_alt = f0_alt[f0_alt > 1]
            log_f0_alt = torch.log(f0_alt + 1e-5)
            shifted_log = log_f0_alt.clone()
            if p["auto_pitch"]:
                if voiced_ori.numel() and voiced_alt.numel():
                    median_ori = torch.median(torch.log(voiced_ori + 1e-5))
                    median_alt = torch.median(torch.log(voiced_alt + 1e-5))
                    shifted_log[f0_alt > 1] = (
                        log_f0_alt[f0_alt > 1] - median_alt + median_ori
                    )
                else:
                    print("  note: auto_pitch skipped, no voiced frames", flush=True)
            f0_shifted = torch.exp(shifted_log)
            semitones = float(p["pitch_shift"])
            if semitones != 0:
                f0_shifted[f0_alt > 1] = f0_shifted[f0_alt > 1] * (
                    2 ** (semitones / 12)
                )

        cond, _, _, _, _ = self.model.length_regulator(
            s_alt, ylens=target_lengths, n_quantizers=3, f0=f0_shifted
        )
        prompt_condition, _, _, _, _ = self.model.length_regulator(
            s_ori, ylens=target2_lengths, n_quantizers=3, f0=f0_ori
        )

        max_source_window = max_context_window - mel2.size(2)
        if max_source_window <= overlap_frame_len:
            raise ValueError("The reference is too long for the context window.")
        processed, chunks, previous = 0, [], None
        while processed < cond.size(1):
            chunk_cond = cond[:, processed : processed + max_source_window]
            is_last = processed + max_source_window >= cond.size(1)
            cat_condition = torch.cat([prompt_condition, chunk_cond], dim=1)
            with torch.autocast(
                device_type=device.type, dtype=torch.float16 if fp16 else torch.float32
            ):
                vc_target = self.model.cfm.inference(
                    cat_condition,
                    torch.LongTensor([cat_condition.size(1)]).to(mel2.device),
                    mel2,
                    style2,
                    None,
                    steps,
                    inference_cfg_rate=cfg,
                )
                vc_target = vc_target[:, :, mel2.size(-1) :]
            vc_wave = self.vocoder(vc_target.float()).squeeze()[None, :]
            if processed == 0:
                if is_last:
                    chunks.append(vc_wave[0].cpu().numpy())
                    break
                chunks.append(vc_wave[0, :-overlap_wave_len].cpu().numpy())
                previous = vc_wave[0, -overlap_wave_len:]
                processed += vc_target.size(2) - overlap_frame_len
            elif is_last:
                chunks.append(
                    self.crossfade(
                        previous.cpu().numpy(),
                        vc_wave[0].cpu().numpy(),
                        overlap_wave_len,
                    )
                )
                break
            else:
                chunks.append(
                    self.crossfade(
                        previous.cpu().numpy(),
                        vc_wave[0, :-overlap_wave_len].cpu().numpy(),
                        overlap_wave_len,
                    )
                )
                previous = vc_wave[0, -overlap_wave_len:]
                processed += vc_target.size(2) - overlap_frame_len
        return np.concatenate(chunks), sr


def write_clip(job: Job, path: Path, wave, rate: int, seconds: float) -> dict:
    """Mono 16-bit WAV trimmed or padded to `seconds`, scaled only when it clips."""
    import numpy as np
    import soundfile as sf

    wave = np.asarray(wave, dtype=np.float32).reshape(-1)
    target = fitted_samples(seconds, rate)
    if len(wave) > target:
        wave = wave[:target]
    elif len(wave) < target:
        wave = np.pad(wave, (0, target - len(wave)))
    peak = float(np.max(np.abs(wave))) if len(wave) else 0.0
    scale = peak_scale(peak)
    if scale != 1.0:
        wave = wave * scale
    with job.writing(path) as temporary:
        sf.write(str(temporary), wave, rate, subtype="PCM_16", format="WAV")
    rms = float(np.sqrt(np.mean(np.square(wave)))) if len(wave) else 0.0
    return {
        "raw_peak": round(peak, 4),
        "peak_scale_applied": round(scale, 4),
        "rms": round(rms, 5),
    }


def convert(job: Job) -> None:
    settings = convert_settings(job.params)
    sources = clips(job)
    shifts = clip_shifts(job.params, sources)
    reference_paths = references(job.inputs)
    names = clip_names(sources)
    seed = settings["seed"] if settings["seed"] is not None else job.seed

    def paths(key):
        return job.target(names[key] + ".wav"), job.target(names[key] + ".json")

    pending = [key for key in sources if not all(p.exists() for p in paths(key))]
    if pending:
        installed(job)
        model, origin = fine_tuned(job)
        job.progress(0.05, "Loading Seed-VC")
        started = time.time()
        engine = Converter(job, model, settings)
        load_seconds = round(time.time() - started, 2)
        torch = engine.torch
        import soundfile as sf

        for n, key in enumerate(pending):
            job.check()
            job.progress(
                0.15 + 0.8 * n / len(pending), f"Converting {n + 1} of {len(pending)}"
            )
            wav_path, info_path = paths(key)
            info = sf.info(str(sources[key]))
            seconds = info.frames / info.samplerate * settings["length_adjust"]
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()
            if seed is not None:
                torch.manual_seed(int(seed))
                torch.cuda.manual_seed_all(int(seed))
            begun = time.time()
            with torch.no_grad():
                wave, rate = engine.convert(
                    sources[key],
                    reference_paths,
                    {**settings, "pitch_shift": shifts[key]},
                )
            torch.cuda.synchronize()
            wall = time.time() - begun
            levels = write_clip(job, wav_path, wave, rate, seconds)
            sidecar = {
                "model": f"seedvc/{settings['version']}",
                # A name, not a path: the sidecar becomes artifact info (no absolute path persists, ADR 0016 P5).
                "checkpoint": Path(model).name,
                "model_source": origin,
                "params": settings,
                "seed": seed,
                "pitch_shift": shifts[key],
                "references": [Path(p).name for p in reference_paths],
                "sample_rate": rate,
                "seconds": round(seconds, 3),
                "seconds_wall": round(wall, 3),
                "load_seconds": load_seconds if n == 0 else None,
                "rtf": round(wall / seconds, 3) if seconds else None,
                "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 2**30, 3),
                "peak_vram_reserved_gb": round(
                    torch.cuda.max_memory_reserved() / 2**30, 3
                ),
                **levels,
            }
            with job.writing(info_path) as temporary:
                temporary.write_text(
                    json.dumps(sidecar, indent=1) + "\n", encoding="utf-8"
                )
    for key in sources:
        wav_path, info_path = paths(key)
        job.output(
            key, wav_path, "audio", **json.loads(info_path.read_text(encoding="utf-8"))
        )
    job.progress(1.0, f"Converted {len(sources)}")


TASKS = {"finetune": finetune, "convert": convert}

if __name__ == "__main__":
    sys.exit(run(TASKS))
