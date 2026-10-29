"""The RVC engine's worker (ADR 0015, ADR 0016 P3f). Runs in the rvc home's interpreter and drives Applio (`<home>/repo`).

Tasks, each with its settings from the voice spec (`integrations/voice/knowledge/voice.toml`) in `params`:

- `prepare`  inputs `seed` (the seed's clips: a JSON list, or `{"clips": [...]}`, of paths or of rows; a row without a
             `path`, such as `{"id", "text"}`, names its input `clip:<id>`), any number of `kept` / `kept:<name>` (JSON
             lists of filtered clips, as `analysis.filter` writes them; a row without a `path` names its input
             `clip:<id>`) and those `clip:<id>` files; params `[tasks.rvc.prepare]` with the spec's `[dataset]` table
             under `params["dataset"]` -> `dataset` (a report). The app's manifests carry ids, never paths. Builds the
             48 kHz training set (the seed `seed_copies` times; the clips pooled and balanced to
             `max_synthetic_minutes`, no category above `max_category_share`; TTS levelled to `tts_lufs`, converted
             real performances, which name their `source` or `source_id`, at their own level), then runs Applio's
             preprocess and feature extraction (rmvpe labels with the high-register corrector, ContentVec) into
             `scratch/prepare/`.
- `train`    params `[tasks.rvc.train]` -> `model`, kind `voice_weights` (the bare inference weights). Applio's
             `rvc/train/train.py` from the TITAN-Medium base, in chunks of `chunk_epochs` up to `epochs`, each chunk
             resuming from the checkpoint the previous one committed in `scratch/train/`. It never builds the index.
- `index`    params `[tasks.rvc.index]` -> `index`, kind `voice_index`: Applio's FAISS index of the prepared features
             (CPU).
- `convert`  inputs `model`, `index` and `audio` or any number of `clip:<id>` files, optionally `spans` (whispered
             spans per clip, `analysis.tag`'s report); params `[tasks.rvc.convert]` with the job's `source_median_hz`
             and `target_median_hz` (and optionally `whisper_spans`, shaped like `spans`) -> `converted` or
             `converted:<id>`, kind `audio`: mono 16-bit WAV at the model's rate, exactly the source's duration. The app
             decides whether a converted clip becomes a candidate or hidden material.

Ported from `scripts/community_research/voice/{vc_rvc_env,vc_rvc_train,vc_rvc,expand_train}.py`, keeping the settings
the research's numbers depend on. Applio runs from its own checkout through its stage scripts (argument lists, the
checkout as the current directory, as Applio expects) with its logs, settings and caches redirected to the job's work
directory or the operation's scratch: nothing is written inside the home, and nothing is downloaded.

Scratch (`engine_workers/protocol.py`): `scratch/prepare/` holds Applio's prepared experiment, trusted only while
`manifest.json` (written last, by atomic rename) names the identity of its inputs and settings. `scratch/train/` holds
the committed checkpoint (`G_<epoch>.pth`, `D_<epoch>.pth`, `model_<epoch>.pth`, moved in by rename) and `state.json`
(written after them, by atomic rename), plus Applio's live experiment `run/`, rebuilt before every chunk from the
prepared files and the committed checkpoint (hard-linked under a name Applio never writes). A stopped chunk therefore
leaves the previous checkpoint whole; a different identity starts the state again.

Module level is standard library only, so the decision rules below are tested in the app's own environment.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from protocol import Cancelled, Job, run

MODEL_NAME = "voice"  # Applio's experiment folder and the name inside the weights
CPU_CORES = 8  # Applio's preprocess and extraction workers (the research's --cpu-cores)
GPU = "0"
INCLUDE_MUTES = 2  # core.py's default: two silent rows per speaker in the filelist
# core.py's defaults for the preprocess arguments the recipe leaves alone
CHUNK_LEN, OVERLAP_LEN, NOISE_REDUCTION_STRENGTH = "3.0", "0.3", "0.7"
PEAK_CEILING_DB = (
    -1.0
)  # a levelled clip's sample peak stays at or below -1 dBFS (expand_train.py)
F0_METHODS = ("rmvpe", "fcpe", "crepe", "swift")
POLL_SECONDS = 0.5
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PROGRESS_BAR = re.compile(r"\d+%\|")
EPOCH_LINE = re.compile(
    r"epoch=(\d+) \| step=(\d+) \| time=(\d\d):(\d\d):(\d\d) \| training_speed=(\d+):(\d\d):(\d\d)"
)
CHECKPOINT = re.compile(r"^([GD])_(\d+)\.pth$")
WEIGHTS = re.compile(r"_(\d+)e_(\d+)s\.pth$")
COMMITTED = re.compile(r"^(G|D|model)_\d+\.pth$")
# What Applio must not inherit: another environment's Python paths, and Applio's own location overrides.
STRIPPED = (
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "APPLIO_CONFIG_FILE",
    "APPLIO_ROOT",
    "APPLIO_CODE_ROOT",
    "APPLIO_EXPERIMENTAL_ONNX_INFERENCE",
    "APPLIO_ONNX_PRECISION",
    "APPLIO_ONNX_CACHE_DIR",
)
# The settings of each task that make up the identity of its scratch state. `epochs` and `chunk_epochs` stay out:
# raising the epochs resumes a run, as Applio's own resume does.
PREPARE_KEYS = (
    "sample_rate",
    "cut",
    "normalization",
    "noise_reduction",
    "f0_labels",
    "rmvpe_high_register",
    "embedder",
)
TRAIN_KEYS = ("pretrained_g", "pretrained_d", "vocoder", "batch_size")


# --- the training set (standard library) ---------------------------------------------------------------------------


def allocation(
    available: dict[str, float], budget: float, cap: float
) -> dict[str, float]:
    """Seconds per category by water-filling: small categories keep everything, large ones share the rest equally,
    none above `cap`, the total at most `budget`."""
    alloc, remaining = {}, budget
    order = sorted(available, key=available.get)
    for i, category in enumerate(order):
        level = min(cap, remaining / (len(order) - i))
        alloc[category] = min(available[category], level)
        remaining -= alloc[category]
    return alloc


def balanced(rows: list[dict], minutes: float, share: float) -> list[dict]:
    """Per category its water-filled share of `minutes` (at most `share` of them), taken round-robin across groups
    (generators for TTS, speakers for converted performances) so no group is cut off as a whole; a clip that would
    overshoot its category's share is skipped and a shorter one may still fit."""
    by_category: dict[str, dict[str, list[dict]]] = {}
    for row in rows:
        group = row.get("generator") or row.get("speaker", "")
        by_category.setdefault(row["category"], {}).setdefault(group, []).append(row)
    available = {
        category: sum(float(r["seconds"]) for g in groups.values() for r in g)
        for category, groups in by_category.items()
    }
    alloc = allocation(available, minutes * 60, minutes * 60 * share)
    out = []
    for category, groups in by_category.items():
        total, queues = 0.0, [list(g) for g in groups.values()]
        while any(queues):
            for queue in queues:
                if (
                    queue
                    and total + float(queue[0]["seconds"]) <= alloc[category] + 1.0
                ):
                    row = queue.pop(0)
                    out.append(row)
                    total += float(row["seconds"])
                elif queue:
                    queue.pop(0)
    return out


def levelled(row: dict) -> bool:
    """TTS clips get a static gain to the set's level; converted real performances (they name their `source` file or
    their `source_id` clip) keep their own level."""
    return (
        row.get("generator") != "bootstrap"
        and not row.get("source")
        and not row.get("source_id")
    )


def safe_name(text) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(text)).strip("._") or "clip"


def clip_input(clip_id, inputs: dict, what: str) -> Path:
    """The file of a manifest row that carries no path: the job's input `clip:<id>`."""
    key = f"clip:{clip_id}"
    if clip_id in (None, "") or key not in inputs:
        raise RuntimeError(
            f"rvc.prepare: the {what} {clip_id} names no file (no {key} input)."
        )
    return Path(inputs[key])


def seed_clips(document, inputs: dict | None = None) -> list[dict]:
    """The seed's clips in order as {"name", "path"}: from a list (or a {"clips": [...]} document) of paths or rows. A
    row with a `path` is named by its file; a row without one, such as {"id", "text"}, takes its input `clip:<id>` and
    is named by its id (a row without an id is `seed00`, `seed01` ..., as tts.generate reads the seed)."""
    items = document.get("clips", []) if isinstance(document, dict) else document
    clips = []
    for n, item in enumerate(items or []):
        entry = {"path": item} if isinstance(item, str) else dict(item)
        if entry.get("path"):
            path = Path(entry["path"])
            clips.append({"name": path.stem, "path": path})
            continue
        clip_id = str(entry.get("id") or f"seed{n:02d}")
        clips.append(
            {"name": clip_id, "path": clip_input(clip_id, inputs or {}, "seed clip")}
        )
    return clips


def kept_rows(rows: list[dict], inputs: dict) -> list[dict]:
    """The kept clips with their files: a row without a `path` takes its input `clip:<id>`. Rows the filter did not
    keep are dropped first, so they need no file."""
    return [
        row
        if row.get("path")
        else {**row, "path": str(clip_input(row.get("id"), inputs, "kept clip"))}
        for row in rows
        if row.get("kept", True)
    ]


def dataset_plan(seed: list, rows: list[dict], dataset: dict) -> list[dict]:
    """The training set's files in order: the seed `seed_copies` times at its own level, then the kept clips balanced
    to the synthetic cap, TTS levelled to `tts_lufs`. `seed` holds paths or `seed_clips` entries. Each item names its
    `source`, a unique file `name`, the loudness to level to (`lufs`, None keeps the level), its `category` and
    `seconds`."""
    seed = [
        entry
        if isinstance(entry, dict)
        else {"name": Path(entry).stem, "path": Path(entry)}
        for entry in seed
    ]
    items = [
        {
            "source": str(entry["path"]),
            "name": f"seed_{copy}_{safe_name(entry['name'])}.wav",
            "lufs": None,
            "category": "seed",
            "seconds": None,
        }
        for copy in range(int(dataset["seed_copies"]))
        for entry in seed
    ]
    rows = [row for row in rows if row.get("kept", True)]
    chosen = balanced(
        rows,
        float(dataset["max_synthetic_minutes"]),
        float(dataset["max_category_share"]),
    )
    for row in chosen:
        items.append(
            {
                "source": str(row["path"]),
                "name": f"{safe_name(row.get('generator', 'boot'))}_{safe_name(row['id'])}.wav",
                "lufs": float(dataset["tts_lufs"]) if levelled(row) else None,
                "category": row["category"],
                "seconds": float(row["seconds"]),
                "id": row["id"],
                "generator": row.get("generator"),
            }
        )
    seen: dict[str, int] = {}
    for item in items:
        count = seen.get(item["name"], 0)
        seen[item["name"]] = count + 1
        if count:
            stem = item["name"][: -len(".wav")]
            item["name"] = f"{stem}_{count}.wav"
    return items


def dataset_summary(plan: list[dict]) -> dict:
    synthetic = [item for item in plan if item["category"] != "seed"]
    minutes: dict[str, float] = {}
    for item in synthetic:
        minutes[item["category"]] = round(
            minutes.get(item["category"], 0) + item["seconds"] / 60, 2
        )
    return {
        "clips": len(plan),
        "seed_clips": len(plan) - len(synthetic),
        "synthetic_clips": len(synthetic),
        "synthetic_minutes": round(sum(item["seconds"] for item in synthetic) / 60, 2),
        "by_category": minutes,
    }


def loudness_of(log: str) -> tuple[float | None, float | None]:
    """(integrated LUFS, sample peak dBFS) from the summary of ffmpeg's ebur128 filter; None where it gives none."""
    if "Summary:" not in log:
        return None, None
    summary = log[log.rfind("Summary:") :]
    found = re.search(r"I:\s+(-?[\d.]+) LUFS", summary)
    peak = re.search(r"Peak:\s+(-?[\d.inf]+) dBFS", summary)
    integrated = float(found.group(1)) if found else None
    peak_db = (
        float(peak.group(1)) if peak and peak.group(1) not in ("-inf", "inf") else None
    )
    return integrated, peak_db


def gain_db(integrated: float | None, peak_db: float | None, lufs: float) -> float:
    """The static gain to `lufs` integrated, the peak kept at or below -1 dBFS; none for silence."""
    if integrated is None or integrated < -70:
        return 0.0
    gain = lufs - integrated
    if peak_db is not None:
        gain = min(gain, PEAK_CEILING_DB - peak_db)
    return round(gain, 2)


def loudness_command(ffmpeg: str, source) -> list[str]:
    return [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-i",
        str(source),
        "-af",
        "ebur128=peak=sample",
        "-f",
        "null",
        "-",
    ]


def resample_command(ffmpeg: str, source, target, gain: float | None, rate: int):
    """Mono 16-bit WAV at `rate`, with the gain when the clip is levelled (the research's `to48k`)."""
    level = ["-af", f"volume={gain}dB"] if gain is not None else []
    return [
        ffmpeg,
        "-v",
        "error",
        "-y",
        "-i",
        str(source),
        *level,
        "-ac",
        "1",
        "-ar",
        str(rate),
        "-f",
        "wav",
        str(target),
    ]


# --- training state (standard library) -----------------------------------------------------------------------------


def identity(document) -> str:
    text = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_ends(done: int, epochs: int, chunk: int) -> list[int]:
    """The epochs at which each remaining chunk ends: on multiples of `chunk`, the last at `epochs`."""
    if chunk < 1:
        raise ValueError("chunk_epochs must be at least 1.")
    ends, at = [], done
    while at < epochs:
        at = min(epochs, (at // chunk + 1) * chunk)
        ends.append(at)
    return ends


def save_interval(start: int, end: int) -> int:
    """Applio's save interval for a chunk: it saves when epoch % interval == 0, so the largest divisor of `end` not
    above the chunk's length saves at the end of the chunk and as rarely as possible before it."""
    return max(d for d in range(1, end - start + 1) if end % d == 0)


def resume_epoch(state: dict | None, wanted: str, present: set[str]) -> int:
    """The committed epoch training resumes from; 0 starts over (no state, another identity, or missing files)."""
    if not state or state.get("identity") != wanted:
        return 0
    files = state.get("files") or {}
    epoch = int(state.get("epoch") or 0)
    if epoch <= 0 or set(files) != {"G", "D", "model"}:
        return 0
    return epoch if set(files.values()) <= present else 0


def stray(names: list[str], state: dict) -> list[str]:
    """Committed-looking files the state does not name: what an interrupted commit left behind."""
    keep = set((state.get("files") or {}).values())
    return [name for name in names if COMMITTED.match(name) and name not in keep]


def newest_checkpoints(names: list[str]) -> dict[str, str]:
    """Applio's newest G and D checkpoints in a run folder, leaving out the restored `*_0.pth`."""
    best: dict[str, tuple[int, str]] = {}
    for name in names:
        match = CHECKPOINT.match(name)
        if match and int(match.group(2)) > 0:
            kind, number = match.group(1), int(match.group(2))
            if kind not in best or number > best[kind][0]:
                best[kind] = (number, name)
    return {kind: name for kind, (_, name) in best.items()}


def epoch_stats(lines: list[str]) -> dict:
    """Seconds per epoch from Applio's end-of-epoch lines (the wall clock in them wraps at midnight)."""
    rows = []
    for line in lines:
        match = EPOCH_LINE.search(line)
        if match:
            epoch, step, hh, mm, ss, *_ = map(int, match.groups())
            rows.append((epoch, step, hh * 3600 + mm * 60 + ss))
    stats: dict = {"epochs_logged": len(rows)}
    gaps = []
    for previous, current in itertools.pairwise(rows):
        gap = current[2] - previous[2]
        if gap < 0:
            gap += 86400
        if current[0] > previous[0]:
            gaps.append(gap / (current[0] - previous[0]))
    if gaps:
        stats["seconds_per_epoch_mean"] = round(sum(gaps) / len(gaps), 2)
        stats["seconds_per_epoch_median"] = round(statistics.median(gaps), 2)
    return stats


# --- Applio's stage scripts (standard library) ---------------------------------------------------------------------


def preprocess_arguments(experiment, dataset, params: dict) -> list[str]:
    """`rvc/train/preprocess/preprocess.py`, positional, exactly as `core.py preprocess` passes them."""
    return [
        str(experiment),
        str(dataset),
        str(int(params["sample_rate"])),
        str(CPU_CORES),
        str(params["cut"]),
        "False",  # process_effects (core.py's --process-effects, off)
        str(bool(params["noise_reduction"])),
        NOISE_REDUCTION_STRENGTH,
        CHUNK_LEN,
        OVERLAP_LEN,
        str(params["normalization"]),
    ]


def extract_arguments(experiment, params: dict) -> list[str]:
    """`rvc/train/extract/extract.py`, positional, as `core.py extract` passes them (no custom embedder)."""
    return [
        str(experiment),
        str(params["f0_labels"]),
        str(CPU_CORES),
        GPU,
        str(int(params["sample_rate"])),
        str(params["embedder"]),
        "None",
        str(INCLUDE_MUTES),
    ]


def train_arguments(
    params: dict,
    save_every: int,
    end: int,
    pretrained_g,
    pretrained_d,
    sample_rate: int,
) -> list[str]:
    """`rvc/train/train.py`, positional, in the order `core.py train` passes them. `core.py train` is not used: it
    builds the index after every run, and the index is its own task."""
    return [
        MODEL_NAME,
        str(save_every),
        str(end),
        str(pretrained_g),
        str(pretrained_d),
        GPU,
        str(int(params["batch_size"])),
        str(int(sample_rate)),
        str(bool(params["save_only_latest"])),
        "False",  # save_every_weights: the chunk's last epoch writes the inference weights anyway
        "False",  # cache_data_in_gpu
        "False",  # cleanup
        str(params["vocoder"]),
        "False",  # checkpointing
    ]


def index_arguments(experiment, params: dict) -> list[str]:
    """`rvc/train/process/extract_index.py`, as `core.py index` passes them."""
    return [str(experiment), str(params["algorithm"])]


def applio_path(paths: list[str], repo: Path) -> list[str]:
    """The import path with Applio's checkout first: this worker's own folder holds `rvc.py`, which would otherwise
    shadow Applio's `rvc` package."""
    return [str(repo), *(path for path in paths if path != str(repo))]


def applio_environment(base: dict, work: Path, logs: Path, config: Path) -> dict:
    """Applio's process environment: its experiments under `logs`, its settings file under `config`, its caches in the
    job's work directory, offline, and no bytecode written next to its sources (they live in the home)."""
    env = {key: value for key, value in base.items() if key.upper() not in STRIPPED}
    caches = work / "cache"
    env.update(
        {
            "APPLIO_LOGS_DIR": str(logs),
            "APPLIO_CONFIG_DIR": str(config),
            "HF_HOME": str(caches / "hf"),
            "TORCH_HOME": str(caches / "torch"),
            "MPLCONFIGDIR": str(caches / "matplotlib"),
            "NUMBA_CACHE_DIR": str(caches / "numba"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
            "TQDM_MININTERVAL": "5",
        }
    )
    return env


# --- conversion (standard library) ---------------------------------------------------------------------------------


def map_f0(
    f0: list[float],
    source_median_hz: float,
    target_median_hz: float,
    knee_semitones: float,
    ceiling_hz: float,
) -> list[float]:
    """The V4 pitch map: up to `knee_semitones` above the source's median a frame moves by the median ratio; above,
    it bends smoothly (slope 1 at the knee) toward `ceiling_hz`. Below the median the ratio applies unchanged (the
    research's low floor hurt and is dropped). Unvoiced frames (0, or not a number) stay 0."""
    knee = float(knee_semitones)
    top = max(knee + 0.5, 12 * math.log2(float(ceiling_hz) / float(target_median_hz)))
    out = []
    for value in f0:
        if not value > 0:
            out.append(0.0)
            continue
        d = 12 * math.log2(value / float(source_median_hz))
        if d > knee:
            d = knee + (top - knee) * (1 - math.exp(-(d - knee) / (top - knee)))
        out.append(float(target_median_hz) * 2 ** (d / 12))
    return out


def span_frames(
    spans: list[list[float]], sample_rate: int, pad: int, hop: int
) -> list[tuple[int, int]]:
    """F0 frame slices of source-time spans: Applio tracks pitch once on the clip padded by `pad` samples, so frame k
    sits at (k * hop - pad) / sample_rate seconds."""
    frames = []
    for start, end in spans:
        a = int((float(start) * sample_rate + pad) // hop)
        b = int((float(end) * sample_rate + pad) // hop) + 1
        frames.append((max(0, a), b))
    return frames


def spans_for(document, keys: list[str]) -> dict[str, list[list[float]]]:
    """Whispered spans per clip key: a document keyed by clip (`analysis.tag`'s report) or, for one clip, a list."""
    if document is None:
        return {}
    if isinstance(document, dict):
        return {
            key: [[float(a), float(b)] for a, b in document.get(key, [])]
            for key in keys
        }
    if len(keys) != 1:
        raise ValueError("A plain list of whispered spans needs exactly one clip.")
    return {keys[0]: [[float(a), float(b)] for a, b in document]}


def convert_settings(params: dict, job_seed: int | None) -> dict:
    """The conversion's settings, checked; the pitch map needs the job's source and target medians."""
    method = params["f0_method"]
    if method not in F0_METHODS:
        raise ValueError(f"f0_method must be one of {F0_METHODS}, got {method!r}.")
    protect = float(params["protect"])
    if not 0 <= protect <= 0.5:
        raise ValueError("protect must be within 0..0.5.")
    index_rate, volume = float(params["index_rate"]), float(params["volume_envelope"])
    if not (0 <= index_rate <= 1 and 0 <= volume <= 1):
        raise ValueError("index_rate and volume_envelope must be within 0..1.")
    table = params.get("pitch_map") or None
    pitch_map, unvoiced = None, False
    if table:
        try:
            medians = (
                float(params["source_median_hz"]),
                float(params["target_median_hz"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "The pitch map needs the job's source_median_hz and target_median_hz "
                "(or params pitch_map = null for none)."
            ) from exc
        if min(medians) <= 0:
            raise ValueError("Pitch medians must be positive.")
        pitch_map = {
            "source_median_hz": medians[0],
            "target_median_hz": medians[1],
            "knee_semitones": float(table["knee_semitones"]),
            "ceiling_hz": float(table["ceiling_hz"]),
        }
        unvoiced = bool(table.get("unvoiced_spans", False))
    split = bool(params.get("split_audio", False))
    if split and (pitch_map or unvoiced):
        raise ValueError(
            "The pitch map and whispered spans time frames on the whole clip: split_audio must be false."
        )
    seed = job_seed if job_seed is not None else params.get("seed")
    return {
        "f0_method": method,
        "index_rate": index_rate,
        "protect": protect,
        "volume_envelope": volume,
        "split_audio": split,
        "seed": None if seed is None else int(seed),
        "pitch_map": pitch_map,
        "unvoiced_spans": unvoiced,
    }


# --- files ---------------------------------------------------------------------------------------------------------


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(job: Job, path: Path, document) -> None:
    with job.writing(path) as temporary:
        temporary.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def link_or_copy(source: Path, target: Path) -> None:
    """A hard link where the volume allows it (no copy of gigabytes), else a copy."""
    target.unlink(missing_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def publish(job: Job, source: Path, target: Path) -> None:
    with job.writing(target) as temporary:
        link_or_copy(source, temporary)


def require_home(job: Job) -> Path:
    if job.home is None or not (job.home / "repo" / "rvc").is_dir():
        raise RuntimeError("The rvc engine's home is not installed.")
    return job.home


def task_scratch(job: Job, task: str) -> Path:
    if job.scratch is None:
        raise RuntimeError(
            f"rvc.{task} keeps its state in the operation's scratch folder."
        )
    folder = job.scratch / task
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def clear(job: Job, folder: Path) -> None:
    """Remove a task's scratch state (never anything outside the scratch folder)."""
    if not folder.resolve().is_relative_to(job.scratch.resolve()):
        raise ValueError(f"Refusing to clear {folder}: outside the scratch folder.")
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)


def applio_config(job: Job, folder: Path, settings: dict) -> Path:
    """Applio's per-user settings file for this job (otherwise it reads and writes %APPDATA%)."""
    folder.mkdir(parents=True, exist_ok=True)
    write_json(job, folder / "config.json", settings)
    return folder


def prepared(experiment: Path) -> bool:
    """Applio writes config.json and filelist.txt as the very last step of extraction."""
    if read_json(experiment / "config.json") is None:
        return False
    filelist = experiment / "filelist.txt"
    return (
        filelist.is_file()
        and filelist.stat().st_size > 0
        and any((experiment / "extracted").glob("*.npy"))
    )


def prepared_state(job: Job) -> tuple[dict, Path]:
    """The committed preparation: its manifest and Applio's experiment folder."""
    folder = (job.scratch / "prepare") if job.scratch else None
    manifest = read_json(folder / "manifest.json") if folder else None
    experiment = folder / manifest["experiment"] if manifest else None
    if not manifest or not prepared(experiment):
        raise RuntimeError(
            "The voice's training set is not prepared; run rvc.prepare first."
        )
    return manifest, experiment


# --- processes -----------------------------------------------------------------------------------------------------


def kill_tree(process: subprocess.Popen) -> None:
    """Applio's stages start processes of their own (training workers, data loaders): stop all of them."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            creationflags=NO_WINDOW,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()


def run_script(
    job: Job, script: Path, arguments: list[str], env: dict, on_line=None
) -> tuple[int, list[str]]:
    """Run one of Applio's scripts from its checkout with this environment's interpreter; its output goes to the job's
    log (progress bars left out). A cancelled job stops the script's whole process tree."""
    command = [sys.executable, "-u", str(script), *arguments]
    print(f"[rvc] {' '.join(command)}", flush=True)
    process = subprocess.Popen(
        command,
        cwd=str(job.home / "repo"),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW,
        start_new_session=os.name != "nt",
    )
    lines: list[str] = []

    def read() -> None:
        for raw in process.stdout:
            line = raw.rstrip()
            if not line or PROGRESS_BAR.search(line):
                continue
            lines.append(line)
            try:
                print(line, flush=True)
                if on_line is not None:
                    on_line(line)
            except Exception:  # noqa: BLE001, S112 - keep draining the pipe, or Applio blocks on a full one
                continue

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        while process.poll() is None:
            if job.cancelled():
                raise Cancelled
            time.sleep(POLL_SECONDS)
    finally:
        kill_tree(process)
        reader.join(timeout=30)
    return process.returncode, lines


def find_ffmpeg(job: Job) -> str:
    """ffmpeg measures and resamples the training set, as in the research: the engine's program, else the PATH."""
    found = str(job.programs["ffmpeg"]) if "ffmpeg" in job.programs else None
    found = found or shutil.which("ffmpeg")
    if not found:
        raise RuntimeError(
            "rvc.prepare needs ffmpeg (registered for the engine or on the PATH)."
        )
    return found


def measure(ffmpeg: str, source) -> tuple[float | None, float | None]:
    result = subprocess.run(
        loudness_command(ffmpeg, source),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW,
        check=True,
    )
    return loudness_of(result.stderr)


def resample(ffmpeg: str, source, target, gain: float | None, rate: int) -> None:
    subprocess.run(
        resample_command(ffmpeg, source, target, gain, rate),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        creationflags=NO_WINDOW,
        check=True,
    )


# --- tasks ---------------------------------------------------------------------------------------------------------


def build_dataset(job: Job, plan: list[dict], folder: Path, rate: int) -> list:
    """The training set's 48 kHz mono files, levelled where the plan says; the gains, in plan order."""
    ffmpeg = find_ffmpeg(job)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)

    def make(item: dict):
        if job.cancelled():
            return None
        gain = None
        if item["lufs"] is not None:
            gain = gain_db(*measure(ffmpeg, item["source"]), item["lufs"])
        with job.writing(folder / item["name"]) as temporary:
            resample(ffmpeg, item["source"], temporary, gain, rate)
        return gain

    with ThreadPoolExecutor(max_workers=CPU_CORES) as pool:
        gains = list(pool.map(make, plan))
    job.check()
    return gains


def prepare(job: Job) -> None:
    params = job.params
    dataset = params.get("dataset")
    if not dataset:
        raise RuntimeError(
            "rvc.prepare needs the voice spec's [dataset] table in params['dataset']."
        )
    report_path = job.target("dataset.json")
    if not report_path.exists():
        home = require_home(job)
        scratch = task_scratch(job, "prepare")
        if "seed" not in job.inputs:
            raise RuntimeError(
                "rvc.prepare needs the seed's clips as its `seed` input."
            )
        seed = seed_clips(
            json.loads(job.inputs["seed"].read_text(encoding="utf-8")), job.inputs
        )
        if not seed:
            raise RuntimeError("rvc.prepare needs the seed's clips.")
        rows = kept_rows(
            [
                row
                for key, path in sorted(job.inputs.items())
                if key == "kept" or key.startswith("kept:")
                for row in json.loads(path.read_text(encoding="utf-8"))
            ],
            job.inputs,
        )
        plan = dataset_plan(seed, rows, dataset)
        job.progress(0.02, "Hashing the training material")
        wanted = identity(
            {
                "params": {key: params[key] for key in PREPARE_KEYS},
                "dataset": dataset,
                "files": [
                    [item["name"], item["lufs"], digest(Path(item["source"]))]
                    for item in plan
                ],
            }
        )
        manifest = read_json(scratch / "manifest.json")
        experiment = scratch / MODEL_NAME
        if manifest and manifest.get("identity") == wanted and prepared(experiment):
            report = manifest["report"]
        else:
            clear(job, scratch)  # another or an unfinished preparation starts over
            job.progress(0.05, "Building the training set")
            folder = job.work / "dataset"
            gains = build_dataset(job, plan, folder, int(dataset["sample_rate"]))
            config = applio_config(
                job,
                job.work / "applio" / "prepare",
                {"rmvpe_high_register": params["rmvpe_high_register"]},
            )
            env = applio_environment(os.environ, job.work, scratch, config)
            stages = home / "repo" / "rvc" / "train"
            job.progress(0.3, "Slicing the training set")
            code, lines = run_script(
                job,
                stages / "preprocess" / "preprocess.py",
                preprocess_arguments(experiment, folder, params),
                env,
            )
            slices = list((experiment / "sliced_audios").glob("*.wav"))
            if (
                code != 0
                or not slices
                or not any("preprocessed successfully" in x for x in lines)
            ):
                raise RuntimeError(
                    f"Applio's preprocess failed (exit {code}); see the log."
                )
            job.check()

            def extracted(line: str) -> None:
                if "Pitch extraction completed" in line:
                    job.progress(0.7, "Extracting features")

            job.progress(0.4, "Extracting pitch")
            code, _ = run_script(
                job,
                stages / "extract" / "extract.py",
                extract_arguments(experiment, params),
                env,
                extracted,
            )
            if code != 0 or not prepared(experiment):
                raise RuntimeError(
                    f"Applio's feature extraction failed (exit {code}); see the log."
                )
            info = read_json(experiment / "model_info.json") or {}
            report = {
                **dataset_summary(plan),
                "seed_copies": int(dataset["seed_copies"]),
                "slices": len(list((experiment / "sliced_audios").glob("*.wav"))),
                "dataset_seconds": info.get("total_seconds"),
                "sample_rate": int(params["sample_rate"]),
                "identity": wanted,
                "files": [
                    {
                        key: item.get(key)
                        for key in ("name", "category", "seconds", "id", "generator")
                    }
                    | {"gain_db": gain}
                    for item, gain in zip(plan, gains)
                ],
            }
            # The commit: written last, so the experiment is trusted only once it is whole.
            write_json(
                job,
                scratch / "manifest.json",
                {
                    "schema": 1,
                    "identity": wanted,
                    "experiment": MODEL_NAME,
                    "sample_rate": int(params["sample_rate"]),
                    "embedder": params["embedder"],
                    "report": report,
                },
            )
        write_json(job, report_path, report)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    job.output(
        "dataset",
        report_path,
        "analysis_report",
        **{
            key: report.get(key)
            for key in (
                "clips",
                "seed_clips",
                "synthetic_clips",
                "synthetic_minutes",
                "slices",
                "dataset_seconds",
                "sample_rate",
                "identity",
            )
        },
    )
    job.progress(1.0, "Prepared")


def stage_run(run_dir: Path, experiment: Path, scratch: Path, state: dict) -> None:
    """Applio's live experiment for one chunk: the prepared files (config.json is copied, Applio rewrites it), and the
    committed checkpoint linked in as `G_0.pth` / `D_0.pth`, names Applio never saves to."""
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in ("config.json", "model_info.json", "filelist.txt"):
        shutil.copyfile(experiment / name, run_dir / name)
    sliced = run_dir / "sliced_audios"
    sliced.mkdir(exist_ok=True)
    if not any(sliced.glob("*.wav")):  # train.py checks the first slice's sample rate
        first = min((experiment / "sliced_audios").glob("*.wav"))
        link_or_copy(first, sliced / first.name)
    for path in run_dir.glob("*.pth"):  # an interrupted chunk's checkpoints and weights
        path.unlink()
    if state["epoch"] > 0:
        link_or_copy(scratch / state["files"]["G"], run_dir / "G_0.pth")
        link_or_copy(scratch / state["files"]["D"], run_dir / "D_0.pth")


def commit(
    job: Job, scratch: Path, state: dict, end: int, run_dir: Path, chunk: dict
) -> dict:
    """Move the chunk's checkpoint and weights into the state under new names, then write the state that names them."""
    newest = newest_checkpoints([path.name for path in run_dir.glob("*.pth")])
    weights = sorted(run_dir.glob(f"{MODEL_NAME}_{end}e_*s.pth"))
    if set(newest) != {"G", "D"} or not weights:
        raise RuntimeError(f"Applio's training left no checkpoint for epoch {end}.")
    steps = int(WEIGHTS.search(weights[-1].name).group(2))
    files = {"G": f"G_{end}.pth", "D": f"D_{end}.pth", "model": f"model_{end}.pth"}
    os.replace(run_dir / newest["G"], scratch / files["G"])
    os.replace(run_dir / newest["D"], scratch / files["D"])
    os.replace(weights[-1], scratch / files["model"])
    committed = {
        **state,
        "epoch": end,
        "steps": steps,
        "files": files,
        "chunks": [*state["chunks"], chunk],
    }
    write_json(job, scratch / "state.json", committed)
    for name in stray([path.name for path in scratch.iterdir()], committed):
        (scratch / name).unlink(missing_ok=True)
    return committed


def train(job: Job) -> None:
    params = job.params
    target = job.target(f"{MODEL_NAME}.pth")
    summary_path = job.work / "train.json"
    if not target.exists():
        home = require_home(job)
        manifest, experiment = prepared_state(job)
        bases = {key: home / params[key] for key in ("pretrained_g", "pretrained_d")}
        for path in bases.values():
            if not path.is_file():
                raise RuntimeError(f"The pretrained base {path} is not installed.")
        sample_rate = int(manifest["sample_rate"])
        wanted = identity(
            {
                "prepare": manifest["identity"],
                "sample_rate": sample_rate,
                "params": {key: params[key] for key in TRAIN_KEYS},
                "bases": {key: path.stat().st_size for key, path in bases.items()},
            }
        )
        scratch = task_scratch(job, "train")
        state = read_json(scratch / "state.json")
        done = resume_epoch(state, wanted, {path.name for path in scratch.iterdir()})
        if done == 0:
            # Another identity, or nothing committed yet: start again.
            clear(job, scratch)
            state = {
                "schema": 1,
                "identity": wanted,
                "epoch": 0,
                "steps": 0,
                "files": {},
                "chunks": [],
            }
            write_json(job, scratch / "state.json", state)
        for name in stray([path.name for path in scratch.iterdir()], state):
            (scratch / name).unlink(missing_ok=True)
        epochs = int(params["epochs"])
        config = applio_config(job, job.work / "applio" / "train", {})
        run_dir = scratch / "run" / MODEL_NAME
        env = applio_environment(os.environ, job.work, run_dir.parent, config)
        script = home / "repo" / "rvc" / "train" / "train.py"
        for end in chunk_ends(state["epoch"], epochs, int(params["chunk_epochs"])):
            job.check()
            start = state["epoch"]
            job.progress(
                start / epochs, f"Training epochs {start + 1}-{end} of {epochs}"
            )
            stage_run(run_dir, experiment, scratch, state)

            def epoch_done(line: str) -> None:
                match = EPOCH_LINE.search(line)
                if match:
                    epoch = int(match.group(1))
                    job.progress(epoch / epochs, f"Epoch {epoch} of {epochs}")

            began = time.time()
            code, lines = run_script(
                job,
                script,
                train_arguments(
                    params,
                    save_interval(start, end),
                    end,
                    bases["pretrained_g"],
                    bases["pretrained_d"],
                    sample_rate,
                ),
                env,
                epoch_done,
            )
            if code != 0 or not any("trained successfully" in line for line in lines):
                raise RuntimeError(
                    f"Applio's training to epoch {end} failed (exit {code}); see the log."
                )
            chunk = {
                "start": start,
                "end": end,
                "seconds": round(time.time() - began, 1),
                **epoch_stats(lines),
            }
            state = commit(job, scratch, state, end, run_dir, chunk)
        if state["epoch"] <= 0:
            raise RuntimeError("Training asked for no epochs.")
        report = manifest["report"]
        means = [
            c["seconds_per_epoch_mean"]
            for c in state["chunks"]
            if "seconds_per_epoch_mean" in c
        ]
        write_json(
            job,
            summary_path,
            {
                "epochs": state["epoch"],
                "steps": state["steps"],
                "sample_rate": sample_rate,
                "vocoder": params["vocoder"],
                "base": Path(params["pretrained_g"]).name,
                "batch_size": int(params["batch_size"]),
                "chunks": len(state["chunks"]),
                "seconds_per_epoch_mean": round(sum(means) / len(means), 2)
                if means
                else None,
                "dataset_seconds": report.get("dataset_seconds"),
                "slices": report.get("slices"),
            },
        )
        publish(job, scratch / state["files"]["model"], target)
    summary = read_json(summary_path) or {}
    job.output("model", target, "voice_weights", **summary)
    job.progress(1.0, f"Trained {summary.get('epochs')} epochs")


def index(job: Job) -> None:
    params = job.params
    target = job.target(f"{MODEL_NAME}.index")
    if not target.exists():
        home = require_home(job)
        _, prepared_dir = prepared_state(job)
        # The index is built beside links to the prepared features, never inside the scratch state.
        experiment = job.work / "index" / prepared_dir.name
        if experiment.exists():
            shutil.rmtree(experiment)
        (experiment / "extracted").mkdir(parents=True)
        for feature in sorted((prepared_dir / "extracted").glob("*.npy")):
            link_or_copy(feature, experiment / "extracted" / feature.name)
        job.check()
        job.progress(0.1, "Building the index")
        config = applio_config(job, job.work / "applio" / "index", {})
        env = applio_environment(os.environ, job.work, experiment.parent, config)
        code, _ = run_script(
            job,
            home / "repo" / "rvc" / "train" / "process" / "extract_index.py",
            index_arguments(experiment, params),
            env,
        )
        built = experiment / f"{experiment.name}.index"
        if code != 0 or not built.is_file():
            raise RuntimeError(f"Applio's index failed (exit {code}); see the log.")
        os.replace(built, target)
    job.output(
        "index",
        target,
        "voice_index",
        algorithm=params["algorithm"],
        bytes=target.stat().st_size,
    )
    job.progress(1.0, "Indexed")


def clips(job: Job) -> dict[str, Path]:
    """The job's `clip:<id>` inputs by id, or its one `audio` input."""
    found = {
        key[5:]: path for key, path in job.inputs.items() if key.startswith("clip:")
    }
    if not found and "audio" in job.inputs:
        found = {"audio": job.inputs["audio"]}
    if not found:
        raise RuntimeError("rvc.convert needs an audio or clip input.")
    return found


def output_of(key: str) -> tuple[str, str]:
    """(output key, file name) of a converted clip."""
    if key == "audio":
        return "converted", "converted.wav"
    return f"converted:{key}", f"converted_{safe_name(key)}.wav"


def hook_f0(pipeline, pitch_map: dict | None, spans: list, np):
    """Wrap this clip's `Pipeline.get_f0`: the pitch map in place of a constant shift, the whispered spans unvoiced,
    and the coarse (quantised) curve recomputed the way Applio does. Returns the function that removes the hook."""
    original = pipeline.get_f0
    frames = span_frames(spans, pipeline.sample_rate, pipeline.t_pad, pipeline.window)

    def get_f0(
        x,
        p_len,
        f0_method="rmvpe",
        pitch=0,
        f0_autotune=False,
        f0_autotune_strength=1.0,
        proposed_pitch=False,
        proposed_pitch_threshold=155.0,
    ):
        _, f0 = original(
            x,
            p_len,
            f0_method,
            0 if pitch_map else pitch,
            f0_autotune,
            f0_autotune_strength,
            proposed_pitch,
            proposed_pitch_threshold,
        )
        f0 = np.asarray(f0, dtype=np.float64).copy()
        if pitch_map:
            f0 = np.asarray(map_f0(f0.tolist(), **pitch_map), dtype=np.float64)
        for a, b in frames:
            f0[a:b] = 0.0
        mel = 1127 * np.log(1 + f0 / 700)
        mel[mel > 0] = (mel[mel > 0] - pipeline.f0_mel_min) * 254 / (
            pipeline.f0_mel_max - pipeline.f0_mel_min
        ) + 1
        mel[mel <= 1] = 1
        mel[mel > 255] = 255
        return np.rint(mel).astype(int), f0

    pipeline.get_f0 = get_f0
    return lambda: pipeline.__dict__.pop("get_f0", None)


class Converter:
    """Applio's VoiceConverter with one model, loaded once per job from the home's files (in this process: the pitch
    hook needs the pipeline itself)."""

    def __init__(self, job: Job, model: Path):
        repo = require_home(job) / "repo"
        sys.dont_write_bytecode = True  # Applio's sources live in the home
        for key in STRIPPED:
            os.environ.pop(key, None)
        config = applio_config(job, job.work / "applio" / "convert", {})
        os.environ.update(applio_environment({}, job.work, job.work / "logs", config))
        os.chdir(repo)  # Applio resolves its predictors and embedders from its checkout
        sys.path[:] = applio_path(sys.path, repo)
        import numpy as np
        import soundfile as sf
        import torch
        from rvc.configs.config import inference_config
        from rvc.infer.infer import VoiceConverter
        from rvc.lib.utils import load_audio_infer

        self.np, self.sf, self.torch = np, sf, torch
        self.load_audio_infer = load_audio_infer
        started = time.time()
        self.vc = VoiceConverter(inference_config())
        self.vc.get_vc(str(model), 0)
        self.embedder = (self.vc.cpt or {}).get("embedder_model") or "contentvec"
        self.vc.load_hubert(self.embedder)
        self.load_seconds = round(time.time() - started, 2)

    def convert(
        self, job: Job, source: Path, target: Path, settings: dict, index, spans: list
    ) -> dict:
        np, sf, torch, vc = self.np, self.sf, self.torch, self.vc
        audio = self.load_audio_infer(str(source), 16000)
        source_seconds = len(audio) / 16000
        peak = np.abs(audio).max() / 0.95
        if peak > 1:
            audio /= peak
        if settings["split_audio"]:
            from rvc.lib.tools.split_audio import merge_audio, process_audio

            chunks, intervals = process_audio(audio, 16000)
        else:
            chunks = [audio]
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        if settings["seed"] is not None:
            torch.manual_seed(settings["seed"])
            torch.cuda.manual_seed_all(settings["seed"])
        applied = spans if settings["unvoiced_spans"] else []
        unhook = None
        if settings["pitch_map"] or applied:
            unhook = hook_f0(vc.vc, settings["pitch_map"], applied, np)
        pieces = []
        try:
            for chunk in chunks:
                pieces.append(
                    vc.vc.pipeline(
                        model=vc.hubert_model,
                        net_g=vc.net_g,
                        sid=0,
                        audio=chunk,
                        pitch=0,
                        f0_method=settings["f0_method"],
                        file_index=str(index) if index else "",
                        index_rate=settings["index_rate"],
                        pitch_guidance=vc.use_f0,
                        volume_envelope=settings["volume_envelope"],
                        version=vc.version,
                        protect=settings["protect"],
                        f0_autotune=False,
                        f0_autotune_strength=1.0,
                        proposed_pitch=False,
                        proposed_pitch_threshold=155.0,
                    )
                )
        finally:
            if unhook:
                unhook()  # the next clip must not inherit this clip's spans
        if settings["split_audio"]:
            result = merge_audio(chunks, pieces, intervals, 16000, vc.tgt_sr)
        else:
            result = pieces[0]
        result = np.asarray(result, dtype=np.float32).flatten()
        rate = int(vc.tgt_sr)
        want = round(source_seconds * rate)
        adjust = want - len(result)
        if adjust > 0:
            result = np.pad(result, (0, adjust))
        elif adjust < 0:
            result = result[:want]
        info = {
            "sample_rate": rate,
            "seconds": round(len(result) / rate, 3),
            "rms": float(np.sqrt(np.mean(result.astype(np.float64) ** 2))),
            "peak": float(np.abs(result).max()),
            "length_adjust_samples": int(adjust),
            "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 2**30, 3)
            if torch.cuda.is_available()
            else None,
            "seconds_model_load": self.load_seconds,
            "embedder": self.embedder,
            **{key: settings[key] for key in ("f0_method", "index_rate", "protect")},
            "volume_envelope": settings["volume_envelope"],
            "seed": settings["seed"],
            "pitch_map": settings["pitch_map"],
            "unvoiced_spans": applied,
            "index": bool(index),
        }
        # The facts first: a relaunch that finds the clip also finds them.
        write_json(job, job.work / "converted" / f"{target.stem}.json", info)
        with job.writing(target) as temporary:
            sf.write(str(temporary), result, rate, subtype="PCM_16", format="WAV")
        return info


def convert(job: Job) -> None:
    params = job.params
    settings = convert_settings(params, job.seed)
    sources = clips(job)
    if "model" not in job.inputs or not job.inputs["model"].is_file():
        raise RuntimeError("rvc.convert needs the trained model as its `model` input.")
    index_path = job.inputs.get("index")
    if settings["index_rate"] > 0 and index_path is None:
        raise RuntimeError(
            "rvc.convert needs the voice's `index` input (index_rate > 0)."
        )
    keys = list(sources)
    document = None
    if "spans" in job.inputs:
        document = json.loads(job.inputs["spans"].read_text(encoding="utf-8"))
    spans = spans_for(document, keys)
    for key, extra in spans_for(params.get("whisper_spans"), keys).items():
        spans[key] = [*spans.get(key, []), *extra]
    (job.work / "converted").mkdir(exist_ok=True)
    pending = [key for key in keys if not job.target(output_of(key)[1]).exists()]
    if pending:
        job.progress(0.05, "Loading the voice")
        converter = Converter(job, job.inputs["model"])
        for n, key in enumerate(pending):
            job.check()
            job.progress(
                0.1 + 0.85 * n / len(pending), f"Converting {n + 1} of {len(pending)}"
            )
            converter.convert(
                job,
                sources[key],
                job.target(output_of(key)[1]),
                settings,
                index_path,
                spans.get(key, []),
            )
    for key in keys:
        output_key, name = output_of(key)
        path = job.target(name)
        info = read_json(job.work / "converted" / f"{path.stem}.json") or {}
        job.output(output_key, path, "audio", source=key, **info)
    job.progress(1.0, f"Converted {len(keys)}")


TASKS = {"prepare": prepare, "train": train, "index": index, "convert": convert}

if __name__ == "__main__":
    sys.exit(run(TASKS))
