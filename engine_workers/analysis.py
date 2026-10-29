"""The analysis engine's worker (ADR 0015, ADR 0016 P3c). Runs in the analysis home's interpreter.

Tasks, each with its settings from the voice spec (`integrations/voice/knowledge/voice.toml`) in `params`:

- `separate`  inputs `audio`: the film mix -> `dialogue` and `remainder` stems (Bandit v2, every channel on its own,
              float WAV at the mix level; remainder = mix - dialogue, so the two rebuild the mix).
- `transcribe` inputs `audio` -> `transcript`: faster-whisper segments with word times.
- `speakers`  inputs `audio` and optionally `segments` (a JSON list of [start, end] seconds or a transcript), or any
              number of `clip:<id>` files -> `speakers`: an ECAPA embedding and the median pitch (pyin) per segment
              or clip, and their mean profile.
- `tag`       inputs `audio` or `clip:<id>` files -> `spans`: whispered stretches (AudioSet tagger).
- `filter`    inputs `candidates` (a JSON list of clips: `id`, `category`, `kind`, `text`, optionally `path` and the
              converted clip's source as a `source` path or a `source_id`), `seed` (the seed's clips: a JSON list, or
              `{"clips": [...]}`, of paths or of rows) and `clip:<id>` files -> `kept` and `scored`: each clip kept only
              when it is the target voice, a delivery worth learning and clean audio, with the calibration the app
              chose for the seed in `params["calibration"]`. The app's manifests carry ids, never paths: a seed entry or
              candidate row without a `path` reads its input `clip:<id>`, and a `source_id` reads `clip:<source_id>`.
              A converted clip (with a source) is compared with its source; `kept` and `scored` keep the rows as given.

Ported from `scripts/community_research/voice/{separate,whisper_spans,measure_vc,filter_synthetic}.py`, keeping the
settings that the research's numbers depend on. Models load only from the home's files (no network); caches go to
the job's work directory, never into the home. Module level is standard library only, so the decision rules below
are tested in the app's own environment.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from protocol import Job, run

# AudioSet ontology names of human vocal sounds a conversion should keep (gender- and age-specific classes left out).
AST_VOCAL_CLASSES = (
    "Speech",
    "Conversation",
    "Narration, monologue",
    "Babbling",
    "Whispering",
    "Shout",
    "Bellow",
    "Whoop",
    "Yell",
    "Battle cry",
    "Screaming",
    "Laughter",
    "Giggle",
    "Snicker",
    "Belly laugh",
    "Chuckle, chortle",
    "Crying, sobbing",
    "Whimper",
    "Wail, moan",
    "Sigh",
    "Singing",
    "Humming",
    "Yodeling",
    "Chant",
    "Rapping",
    "Groan",
    "Grunt",
    "Breathing",
    "Wheeze",
    "Gasp",
    "Pant",
    "Snort",
    "Cough",
    "Throat clearing",
    "Sneeze",
    "Sniff",
    "Hiccup",
)
TALK = {"Speech", "Conversation", "Narration, monologue"}
# configs/models/bandit-mus64.yaml of the Bandit v2 repository
BANDIT_KWARGS = {
    "in_channels": 1,
    "band_type": "musical",
    "n_bands": 64,
    "normalize_channel_independently": False,
    "treat_channel_as_feature": True,
    "n_sqm_modules": 8,
    "emb_dim": 128,
    "rnn_dim": 256,
    "bidirectional": True,
    "rnn_type": "GRU",
    "mlp_dim": 512,
    "hidden_activation": "Tanh",
    "hidden_activation_kwargs": None,
    "complex_mask": True,
    "use_freq_weights": True,
    "n_fft": 2048,
    "win_length": 2048,
    "hop_length": 512,
    "window_fn": "hann_window",
    "wkwargs": None,
    "power": None,
    "center": True,
    "normalized": True,
    "pad_mode": "reflect",
    "onesided": True,
}


# --- decision rules (standard library) -----------------------------------------------------------------------------


def merge_spans(hits: list[list[float]], min_span: float) -> list[list[float]]:
    """Overlapping windows merge into spans; spans shorter than `min_span` seconds are dropped."""
    spans: list[list[float]] = []
    for start, end in sorted(hits):
        if spans and start <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    return [[round(a, 2), round(b, 2)] for a, b in spans if b - a >= min_span]


def whispered(vocal: list[float], index: int, min_probability: float, top: int) -> bool:
    """A window is whispered when the class is likely enough and among the `top` strongest human-vocal classes."""
    ranked = sorted(range(len(vocal)), key=lambda i: -vocal[i])[:top]
    return vocal[index] >= min_probability and index in ranked


def clean_text(text: str) -> str:
    """A TTS line without its delivery tags: (sighs), [laugh], <whisper>."""
    return re.sub(r"\([^)]*\)|\[[^\]]*\]|<[^>]*>", " ", text or "")


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate on lower-cased letters, apostrophes and single spaces; 0 when the reference is empty."""

    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^a-z' ]", " ", s.lower())).strip()

    a, b = norm(reference), norm(hypothesis)
    if not a:
        return 0.0
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
            )
        previous = current
    return previous[-1] / len(a)


def quiet_stats(
    frame_db: list[float], below_peak_db: float, floor_db: float, hop_seconds: float
):
    """(fraction of quiet frames, longest quiet run in seconds); quiet is below max(peak - below_peak, floor)."""
    if not frame_db:
        return 1.0, 0.0
    limit = max(max(frame_db) - below_peak_db, floor_db)
    run_length = best = quiet = 0
    for value in frame_db:
        if value < limit:
            quiet += 1
            run_length += 1
            best = max(best, run_length)
        else:
            run_length = 0
    return quiet / len(frame_db), best * hop_seconds


def real_of(category: str, categories: dict, aliases: dict) -> dict:
    return categories.get(category) or categories.get(aliases.get(category, "")) or {}


def identity_threshold(
    category: str, speech: bool, categories: dict, params: dict
) -> float:
    """The calibrated 10th percentile of the target's own clips of the category, capped for speech and nonverbal."""
    cap = params["identity_cap_speech"] if speech else params["identity_cap_nonverbal"]
    real = real_of(category, categories, params.get("aliases", {}))
    return min(cap, real["p10"]) if real else cap


def fitting(category: str, categories: dict, params: dict) -> set[str]:
    """The vocal classes a generated clip of the category may read as: the category's own, and what the target's real
    clips of it read as (minus plain talk for nonverbal categories)."""
    fits = set(params.get("fits", {}).get(category, params["speechy"]))
    real = set(
        real_of(category, categories, params.get("aliases", {})).get("classes", [])
    )
    if category in params["nonverbal"]:
        real -= TALK
    return fits | real


def converted(row: dict) -> bool:
    """A converted performance names its source: a `source` path or a `source_id` clip."""
    return bool(row.get("source") or row.get("source_id"))


def filter_checks(row: dict, scores: dict, params: dict, categories: dict) -> list[str]:
    """The checks a scored clip fails (empty: keep). Pure: every measurement is in `scores`."""
    checks = []
    speech = row.get("kind") == "speech"
    if not params["min_seconds"] <= scores["seconds"] <= params["max_seconds"]:
        checks.append("duration")
    if (
        scores["silent_fraction"] > params["max_silent_fraction"]
        or scores["longest_silence_s"] > params["max_silent_run_seconds"]
    ):
        checks.append("silence")
    if scores["ecapa_to_seed"] < identity_threshold(
        row["category"], speech, categories, params
    ):
        checks.append("identity")
    if converted(row):
        if (
            scores["ecapa_to_seed"] - scores["source_ecapa_to_seed"]
            < params["converted_gain"]
        ):
            checks.append("identity_not_moved")
        if (
            scores["source_ast_top3"][0] != scores["ast_top3"][0]
            and scores["ast_vocal_cos_source"] < params["delivery_cosine"]
        ):
            checks.append("delivery")
    elif not set(scores["ast_top3"]) & fitting(row["category"], categories, params):
        checks.append("delivery")
    if speech and "cer" in scores and scores["cer"] > params["max_cer"]:
        checks.append("words")
    if speech and "mos" in scores and scores["mos"] < params["min_mos"]:
        checks.append("quality")
    return checks


def segments_of(document) -> list[list[float]]:
    """[start, end] pairs from a list of pairs or a transcript document."""
    items = document.get("segments", []) if isinstance(document, dict) else document
    return [
        [float(s["start"]), float(s["end"])]
        if isinstance(s, dict)
        else [float(s[0]), float(s[1])]
        for s in items
    ]


# --- models (inside the home) --------------------------------------------------------------------------------------


def snapshot(job: Job, repo: str, cache: str = "hf/hub") -> Path:
    """The single Hugging Face snapshot of `repo` the registry installed under `models/<cache>`."""
    folder = (
        job.home / "models" / cache / f"models--{repo.replace('/', '--')}" / "snapshots"
    )
    found = sorted(p for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else []
    if len(found) != 1:
        raise RuntimeError(
            f"Expected one installed snapshot of {repo}, found {len(found)}."
        )
    return found[0]


class Models:
    """The analysis models of one job, loaded once, from the home's files only."""

    def __init__(self, job: Job):
        if job.home is None or not job.home.is_dir():
            raise RuntimeError("The analysis engine's home is not installed.")
        os.environ["HF_HOME"] = str(job.home / "models" / "hf")
        os.environ["HF_HUB_CACHE"] = str(job.home / "models" / "hf" / "hub")
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["TORCH_HOME"] = str(job.work / "torch")
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        import torch  # also loads the CUDA DLLs ctranslate2 needs on Windows

        self.torch = torch
        self.job = job
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self._loaded: dict = {}

    def audio(self, path: Path, rate: int = 16000):
        """Mono float32 at `rate`."""
        import soundfile as sf
        import torchaudio

        data, native = sf.read(str(path), dtype="float32", always_2d=True)
        mono = self.torch.from_numpy(data.mean(axis=1))[None]
        if native != rate:
            mono = torchaudio.functional.resample(mono, native, rate)
        return mono[0].numpy()

    def _model(self, name):
        if name not in self._loaded:
            self._loaded[name] = getattr(self, f"_load_{name}")()
        return self._loaded[name]

    def _load_ecapa(self):
        from hyperpyyaml import load_hyperpyyaml
        from speechbrain.inference.speaker import EncoderClassifier
        from speechbrain.utils.fetching import LocalStrategy

        local = snapshot(self.job, "speechbrain/spkrec-ecapa-voxceleb")
        with open(local / "hyperparams.yaml", encoding="utf-8") as stream:
            hparams = load_hyperpyyaml(stream, {"pretrained_path": local.as_posix()})
        pretrainer = hparams["pretrainer"]
        pretrainer.set_collect_in(str(self.job.work / "ecapa"))
        pretrainer.collect_files(local_strategy=LocalStrategy.COPY)
        pretrainer.load_collected()
        return EncoderClassifier(
            modules=hparams["modules"],
            hparams=hparams,
            run_opts={"device": self.device},
            freeze_params=True,
        )

    def _load_ast(self):
        from transformers import ASTFeatureExtractor, ASTForAudioClassification

        local = snapshot(self.job, "MIT/ast-finetuned-audioset-10-10-0.4593")
        extractor = ASTFeatureExtractor.from_pretrained(str(local))
        model = (
            ASTForAudioClassification.from_pretrained(str(local)).to(self.device).eval()
        )
        names = {v: int(k) for k, v in model.config.id2label.items()}
        return extractor, model, [names[c] for c in AST_VOCAL_CLASSES]

    def _load_whisper(self):
        from faster_whisper import WhisperModel

        params = self.job.params
        # The transcribe task names its model; the filter uses the same installed large-v3.
        if params.get("model"):
            folder = self.job.home / params["model"]
        else:
            folder = snapshot(
                self.job, "Systran/faster-whisper-large-v3", "faster-whisper"
            )
        cuda = self.device == "cuda"
        return WhisperModel(
            str(folder),
            device="cuda" if cuda else "cpu",
            compute_type=params.get("compute_type", "float16") if cuda else "int8",
        )

    def _load_utmos(self):
        import utmosv2

        checkpoint = (
            self.job.home
            / "models"
            / "utmosv2"
            / "fusion_stage3"
            / "fold0_s42_best_model.pth"
        )
        return utmosv2.create_model(
            pretrained=True, checkpoint_path=str(checkpoint), device=self.device
        )

    def ecapa(self, x16):
        import numpy as np

        with self.torch.no_grad():
            tensor = self.torch.from_numpy(x16)[None].to(self.device)
            embedding = (
                self._model("ecapa")
                .encode_batch(tensor)
                .squeeze()
                .float()
                .cpu()
                .numpy()
            )
        return embedding / np.linalg.norm(embedding)

    def ast_window(self, x16):
        """Sigmoid scores of every AudioSet class for one window."""
        extractor, model, _ = self._model("ast")
        with self.torch.no_grad():
            inputs = extractor(x16, sampling_rate=16000, return_tensors="pt")
            logits = model(**{k: v.to(self.device) for k, v in inputs.items()}).logits
        return self.torch.sigmoid(logits)[0].float().cpu().numpy()

    def ast_vocal(self, x16):
        """The vocal classes' scores over the clip: 10 s windows, 5 s hop, the maximum per class."""
        import numpy as np

        win, hop = 10 * 16000, 5 * 16000
        starts = (
            [0]
            if len(x16) <= int(10.24 * 16000)
            else list(range(0, len(x16) - win // 2, hop))
        )
        best = None
        for start in starts:
            probs = self.ast_window(x16[start : start + win])
            best = probs if best is None else np.maximum(best, probs)
        return np.array([float(best[i]) for i in self._model("ast")[2]])

    def transcript(self, x16, language=None) -> str:
        segments, _ = self._model("whisper").transcribe(
            x16,
            language=language,
            beam_size=5,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segments).strip()

    def mos(self, x16) -> float:
        import numpy as np

        model = self._model("utmos")
        self.torch.manual_seed(0)
        np.random.seed(0)
        value = model.predict(
            data=x16,
            sr=16000,
            device=self.device,
            num_workers=0,
            num_repetitions=3,
            verbose=False,
        )
        return float(np.asarray(value).ravel()[0])


def peak_vram():
    import torch

    return (
        round(torch.cuda.max_memory_allocated() / 2**30, 2)
        if torch.cuda.is_available()
        else None
    )


def write_json(job: Job, key: str, name: str, document, **info) -> None:
    path = job.target(name)
    with job.writing(path) as temporary:
        temporary.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    job.output(key, path, "analysis_report", **info)


def clip_input(clip_id, inputs: dict, what: str) -> Path:
    """The file of a manifest row that carries no path: the job's input `clip:<id>` (`what` names the row)."""
    key = f"clip:{clip_id}"
    if clip_id in (None, "") or key not in inputs:
        raise RuntimeError(f"The {what} names no file (no {key} input).")
    return Path(inputs[key])


def seed_paths(document, inputs: dict) -> list[Path]:
    """The seed's clip files in order, from a list (or a {"clips": [...]} document) of paths or rows; a row without a
    `path` names its input `clip:<id>` (a row without an id is `seed00`, `seed01` ..., as tts.generate reads it)."""
    items = document.get("clips", []) if isinstance(document, dict) else document
    paths = []
    for n, item in enumerate(items or []):
        entry = {"path": item} if isinstance(item, str) else dict(item)
        if entry.get("path"):
            paths.append(Path(entry["path"]))
        else:
            clip_id = str(entry.get("id") or f"seed{n:02d}")
            paths.append(clip_input(clip_id, inputs, f"seed clip {clip_id}"))
    return paths


def row_files(row: dict, inputs: dict) -> tuple[Path, Path | None]:
    """(the clip, its source or None) of a candidate row: `path` or the input `clip:<id>`; the source from `source` or
    the input `clip:<source_id>`."""
    name = row.get("id")
    path = (
        Path(row["path"])
        if row.get("path")
        else clip_input(name, inputs, f"candidate {name}")
    )
    source = None
    if row.get("source"):
        source = Path(row["source"])
    elif row.get("source_id"):
        source = clip_input(row["source_id"], inputs, f"source of candidate {name}")
    return path, source


def clips(job: Job) -> dict[str, Path]:
    """The job's `clip:<id>` inputs by id, or its one `audio` input."""
    found = {
        key[5:]: path for key, path in job.inputs.items() if key.startswith("clip:")
    }
    if not found and "audio" in job.inputs:
        found = {"audio": job.inputs["audio"]}
    if not found:
        raise RuntimeError("The task needs an audio or clip input.")
    return found


# --- tasks ---------------------------------------------------------------------------------------------------------


def separate(job: Job) -> None:
    params = job.params
    if params.get("method", "bandit") != "bandit":
        raise RuntimeError(f"Unknown separation method {params.get('method')!r}.")
    dialogue_path, remainder_path = (
        job.target("dialogue.wav"),
        job.target("remainder.wav"),
    )
    rate = int(params.get("sample_rate", 48000))
    if not (dialogue_path.exists() and remainder_path.exists()):
        models = Models(job)
        torch = models.torch
        import soundfile as sf
        import torchaudio

        job.progress(0.05, "Reading the mix")
        source = job.inputs["audio"]
        info = sf.info(str(source))
        first = round(float(params.get("start") or 0.0) * info.samplerate)
        frames = (
            -1
            if params.get("duration") is None
            else round(float(params["duration"]) * info.samplerate)
        )
        data, native = sf.read(
            str(source), start=first, frames=frames, dtype="float32", always_2d=True
        )
        mix = torch.from_numpy(data.T.copy())
        if native != rate:
            mix = torchaudio.functional.resample(mix, native, rate)
        job.check()
        job.progress(0.15, "Loading Bandit")
        started = time.time()
        bandit = str(job.home / "bandit")
        if bandit not in sys.path:
            sys.path.insert(0, bandit)
        from src.models.bandit.bandit import Bandit
        from src.system.inference_handler import StandardTensorChunkedInferenceHandler

        checkpoint = torch.load(
            str(job.home / params["checkpoint"]), map_location="cpu", weights_only=False
        )
        state = checkpoint.get("state_dict", checkpoint)
        state = {
            k[len("model.") :]: v for k, v in state.items() if k.startswith("model.")
        }
        stems = []
        for key in state:
            if key.startswith("mask_estim.") and key.split(".")[1] not in stems:
                stems.append(key.split(".")[1])
        model = Bandit(fs=rate, stems=stems, **BANDIT_KWARGS)
        missing, unexpected = model.load_state_dict(state, strict=False)
        missing = [k for k in missing if not k.startswith(("stft.", "istft."))]
        if missing or unexpected:
            raise RuntimeError(
                f"Bandit checkpoint mismatch: missing={missing[:5]} unexpected={unexpected[:5]}"
            )
        handler = StandardTensorChunkedInferenceHandler(
            chunk_size_seconds=float(params.get("chunk_seconds", 8.0)),
            hop_size_seconds=float(params.get("hop_seconds", 1.0)),
            inference_batch_size=int(params.get("batch", 4)),
            fs=rate,
        )
        model, handler = model.to(models.device).eval(), handler.to(models.device)
        job.check()
        job.progress(0.3, "Separating dialogue")
        loaded = time.time()
        with torch.inference_mode():
            output = handler(mix[None].to(models.device), model)
        dialogue = output["estimates"]["speech"]["audio"][0].float().cpu()
        length = min(dialogue.shape[-1], mix.shape[-1])
        dialogue, remainder = (
            dialogue[..., :length],
            mix[..., :length] - dialogue[..., :length],
        )
        timing = {
            "seconds_model_load": round(loaded - started, 2),
            "seconds_separation": round(time.time() - loaded, 2),
            "peak_vram_gb": peak_vram(),
        }
        # The timing first: a relaunch that finds both stems also finds it.
        (job.work / "separate.json").write_text(json.dumps(timing), encoding="utf-8")
        with job.writing(dialogue_path) as temporary:
            sf.write(
                str(temporary), dialogue.numpy().T, rate, subtype="FLOAT", format="WAV"
            )
        with job.writing(remainder_path) as temporary:
            sf.write(
                str(temporary), remainder.numpy().T, rate, subtype="FLOAT", format="WAV"
            )
    import soundfile as sf

    timing = json.loads((job.work / "separate.json").read_text(encoding="utf-8"))
    for key, path in (("dialogue", dialogue_path), ("remainder", remainder_path)):
        info = sf.info(str(path))
        job.output(
            key,
            path,
            "audio_stem",
            sample_rate=info.samplerate,
            channels=info.channels,
            seconds=round(info.frames / info.samplerate, 3),
            checkpoint=params["checkpoint"],
            **timing,
        )
    job.progress(1.0, "Separated")


def transcribe(job: Job) -> None:
    params = job.params
    path = job.target("transcript.json")
    if not path.exists():
        models = Models(job)
        job.progress(0.1, "Loading whisper")
        x16 = models.audio(job.inputs["audio"])
        job.check()
        job.progress(0.3, "Transcribing")
        segments, info = models._model("whisper").transcribe(
            x16,
            language=params.get("language"),
            beam_size=int(params.get("beam_size", 5)),
            vad_filter=bool(params.get("vad_filter", False)),
            word_timestamps=bool(params.get("word_timestamps", True)),
            condition_on_previous_text=False,
        )
        document = {
            "language": info.language,
            "word_padding_seconds": params.get("word_padding_seconds"),
            "segments": [
                {
                    "start": round(s.start, 3),
                    "end": round(s.end, 3),
                    "text": s.text.strip(),
                    "no_speech_prob": round(s.no_speech_prob, 4),
                    "avg_logprob": round(s.avg_logprob, 4),
                    "words": [
                        {
                            "start": round(w.start, 3),
                            "end": round(w.end, 3),
                            "word": w.word,
                            "probability": round(w.probability, 4),
                        }
                        for w in (s.words or [])
                    ],
                }
                for s in segments
            ],
        }
        with job.writing(path) as temporary:
            temporary.write_text(
                json.dumps(document, indent=1) + "\n", encoding="utf-8"
            )
    document = json.loads(path.read_text(encoding="utf-8"))
    job.output(
        "transcript",
        path,
        "analysis_report",
        language=document["language"],
        segments=len(document["segments"]),
    )
    job.progress(1.0, "Transcribed")


def speakers(job: Job) -> None:
    params = job.params
    path = job.target("speakers.json")
    if not path.exists():
        import librosa
        import numpy as np

        models = Models(job)
        rate = int(params.get("sample_rate", 16000))
        pieces = []
        if "segments" in job.inputs:
            whole = models.audio(job.inputs["audio"], rate)
            spans = segments_of(
                json.loads(job.inputs["segments"].read_text(encoding="utf-8"))
            )
            pieces = [
                (f"{a:.3f}-{b:.3f}", whole[int(a * rate) : int(b * rate)], [a, b])
                for a, b in spans
            ]
        else:
            pieces = [
                (key, models.audio(clip, rate), None)
                for key, clip in clips(job).items()
            ]
        items, embeddings = [], []
        for n, (key, x, span) in enumerate(pieces):
            job.check()
            job.progress(
                0.1 + 0.85 * n / max(1, len(pieces)),
                f"Speaker {n + 1} of {len(pieces)}",
            )
            item = {"key": key, **({"start": span[0], "end": span[1]} if span else {})}
            if len(x) < rate // 4:
                items.append(
                    {**item, "ecapa": None, "median_hz": None, "voiced_frames": 0}
                )
                continue
            embedding = models.ecapa(x)
            embeddings.append(embedding)
            f0, voiced, _ = librosa.pyin(
                x,
                fmin=float(params["pitch_floor_hz"]),
                fmax=float(params["pitch_ceiling_hz"]),
                frame_length=int(params.get("pitch_frame_length", 1024)),
                sr=rate,
            )
            hz = f0[voiced & np.isfinite(f0)]
            enough = len(hz) >= int(params["min_voiced_frames"])
            items.append(
                {
                    **item,
                    "ecapa": [round(float(v), 6) for v in embedding],
                    "median_hz": round(float(np.median(hz)), 2) if enough else None,
                    "voiced_frames": len(hz),
                }
            )
        profile = None
        if embeddings:
            mean = np.mean(embeddings, axis=0)
            profile = [round(float(v), 6) for v in mean / np.linalg.norm(mean)]
        document = {
            "embedding": "ecapa",
            "sample_rate": rate,
            "items": items,
            "profile": profile,
        }
        with job.writing(path) as temporary:
            temporary.write_text(json.dumps(document) + "\n", encoding="utf-8")
    document = json.loads(path.read_text(encoding="utf-8"))
    job.output("speakers", path, "speaker_embeddings", items=len(document["items"]))
    job.progress(1.0, "Speakers measured")


def tag(job: Job) -> None:
    params = job.params
    path = job.target("spans.json")
    if not path.exists():
        models = Models(job)
        window, hop = float(params["window_seconds"]), float(params["hop_seconds"])
        index = AST_VOCAL_CLASSES.index(params.get("class", "Whispering"))
        vocal_index = None
        out = {}
        for n, (key, clip) in enumerate(clips(job).items()):
            job.check()
            job.progress(0.1 + 0.85 * n / max(1, len(job.inputs)), f"Tagging {key}")
            x = models.audio(clip)
            seconds = len(x) / 16000
            hits, start = [], 0.0
            while start <= max(seconds - window, 0) + 1e-6:
                probs = models.ast_window(
                    x[int(start * 16000) : int((start + window) * 16000)]
                )
                vocal_index = vocal_index or models._model("ast")[2]
                vocal = [float(probs[i]) for i in vocal_index]
                if whispered(
                    vocal, index, float(params["min_probability"]), int(params["top"])
                ):
                    hits.append([start, min(start + window, seconds)])
                start += hop
            out[key] = merge_spans(hits, float(params["min_span_seconds"]))
        with job.writing(path) as temporary:
            temporary.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    job.output(
        "spans",
        path,
        "analysis_report",
        clips=len(json.loads(path.read_text(encoding="utf-8"))),
    )
    job.progress(1.0, "Tagged")


def filter_clips(job: Job) -> None:
    params = job.params
    kept_path, scored_path = job.target("kept.json"), job.target("scored.json")
    if not (kept_path.exists() and scored_path.exists()):
        import numpy as np

        # Every file is resolved before a model loads: a manifest naming a clip the job was not given is a planning
        # error of the whole job, not one broken clip.
        seed = seed_paths(
            json.loads(job.inputs["seed"].read_text(encoding="utf-8")), job.inputs
        )
        if not seed:
            raise RuntimeError("analysis.filter needs the seed's clips.")
        rows = json.loads(job.inputs["candidates"].read_text(encoding="utf-8"))
        files = [row_files(row, job.inputs) for row in rows]
        models = Models(job)
        categories = (params.get("calibration") or {}).get("categories", {})
        job.progress(0.05, "Profiling the seed")
        profile = np.mean([models.ecapa(models.audio(p)) for p in seed], axis=0)
        profile = profile / np.linalg.norm(profile)
        out = []
        for n, (row, (path, source_path)) in enumerate(zip(rows, files)):
            job.check()
            job.progress(
                0.1 + 0.85 * n / max(1, len(rows)), f"Clip {n + 1} of {len(rows)}"
            )
            scores: dict = {}
            try:
                x = models.audio(path)
                scores["seconds"] = round(len(x) / 16000, 2)
                scores["ecapa_to_seed"] = round(
                    float(np.dot(models.ecapa(x), profile)), 3
                )
                speech = row.get("kind") == "speech"
                scores["identity_threshold"] = identity_threshold(
                    row["category"], speech, categories, params
                )
                vocal = models.ast_vocal(x)
                scores["ast_top3"] = [
                    AST_VOCAL_CLASSES[i] for i in np.argsort(-vocal)[:3]
                ]
                hop = 16000 // 100
                frames = x[: len(x) // hop * hop].reshape(-1, hop)
                db = (10 * np.log10((frames**2).mean(1) + 1e-12)).tolist()
                quiet, longest = quiet_stats(
                    db,
                    float(params["silence_below_peak_db"]),
                    float(params["silence_floor_db"]),
                    0.01,
                )
                scores["silent_fraction"], scores["longest_silence_s"] = (
                    round(quiet, 2),
                    round(longest, 2),
                )
                x48 = models.audio(path, 48000)[: 48000 * 10]
                spectrum = np.abs(np.fft.rfft(x48)) ** 2
                cumulative = np.cumsum(spectrum) / (spectrum.sum() + 1e-12)
                scores["rolloff_hz"] = round(
                    float(np.searchsorted(cumulative, 0.95) * 48000 / 2 / len(spectrum))
                )
                if source_path is not None:
                    source = models.audio(source_path)
                    scores["source_ecapa_to_seed"] = round(
                        float(np.dot(models.ecapa(source), profile)), 3
                    )
                    source_vocal = models.ast_vocal(source)
                    cosine = float(
                        np.dot(source_vocal, vocal)
                        / (np.linalg.norm(source_vocal) * np.linalg.norm(vocal) + 1e-9)
                    )
                    scores["source_ast_top3"] = [
                        AST_VOCAL_CLASSES[i] for i in np.argsort(-source_vocal)[:3]
                    ]
                    scores["ast_vocal_cos_source"] = round(cosine, 3)
                if speech and row.get("text"):
                    scores["cer"] = round(
                        cer(clean_text(row["text"]), models.transcript(x)), 3
                    )
                if speech:
                    scores["mos"] = round(models.mos(x), 2)
                checks = filter_checks(row, scores, params, categories)
            except Exception as exc:  # noqa: BLE001 - one broken clip must not stop the filter
                checks = [f"error: {type(exc).__name__}: {exc}"[:200]]
            out.append({**row, **scores, "failed": checks, "kept": not checks})
        kept = [r for r in out if r["kept"]]
        with job.writing(scored_path) as temporary:
            temporary.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
        with job.writing(kept_path) as temporary:
            temporary.write_text(json.dumps(kept, indent=1) + "\n", encoding="utf-8")
    kept = json.loads(kept_path.read_text(encoding="utf-8"))
    scored = json.loads(scored_path.read_text(encoding="utf-8"))
    minutes = round(sum(r.get("seconds", 0) for r in kept) / 60, 2)
    job.output(
        "kept",
        kept_path,
        "analysis_report",
        kept=len(kept),
        of=len(scored),
        minutes=minutes,
    )
    job.output("scored", scored_path, "analysis_report", clips=len(scored))
    job.progress(1.0, f"Kept {len(kept)} of {len(scored)}")


TASKS = {
    "separate": separate,
    "transcribe": transcribe,
    "speakers": speakers,
    "tag": tag,
    "filter": filter_clips,
}

if __name__ == "__main__":
    sys.exit(run(TASKS))
