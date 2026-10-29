"""The TTS engine's worker (ADR 0015, ADR 0016 P3d). Runs in the tts home's interpreter (numpy) and drives the audio.cpp
CLI the registry lists (`job.program("cli")`) on the GGUF packages in the home's `models/`.

Tasks, each with its settings from the voice spec (`integrations/voice/knowledge/voice.toml`) in `params`:

- `generate`  `params["generator"]` (a key of `params["generators"]`) voices `params["lines"]` (each {"id", "text",
              "category", "kind"}), or the slice `params["line_range"]` = [first, stop) of them, so a long generator
              such as Maya1 can be split across jobs without changing any take. Only lines whose category's `allow`
              list names the generator are voiced, `takes_per_line` times each. Take t of line k clones seed clip
              (k * takes + t) mod n; input `seed` is a JSON list (or {"clips": [...]}) of clips, each a path or
              {"id", "path" (or an input `clip:<id>`), "text"}. IndexTTS takes its emotion from an optional input
              `emotion:<category>`. -> `take:<line id>_<t>`: mono PCM16 WAV at the model's native rate, levelled to
              `lufs` (BS.1770 integrated) with its sample peak at most `peak_db`; and `report`.
- `superres`  inputs `clip:<id>` (or `audio`), WAV: every clip below `below_hz` -> the same key, lifted to
              `sample_rate` by UniverSR (mono PCM16); and `report` (lifted, kept as they are, failed).

Per-job keys the app adds to the spec's settings: `generator`, `lines`, optionally `line_range`, `voice` (Maya1's voice
description) and `clone_mode` (VoxCPM2: auto, ultimate or controllable).

Seeds follow the research's rule, crc32 of the take's id `<generator>__<line id>_<t>`, so a rerun reproduces a take; a
line may carry its own `seed`, and a job seed (a retake) salts the rule. One CLI session per job loads the model once;
the CLI aborts a sequence at its first failed request, so the session restarts after it: an out-of-memory failure
waits and retries, a Higgs run into max_tokens retries with the next seed (recorded), and a session with no finished
request for `stall_seconds` (plus a load grace before the first) is killed. A take that still fails is listed in the
report; the task fails only when it had work and finished none of it.

Ported from `scripts/community_research/voice/{tts_audiocpp,expand_tts}.py`. The category controls, hint rendering and
written vocalisations below are each model's own syntax (with the doc each comes from). Nothing is written inside the
home: the CLI runs in the job's work directory and unpacks its GGUF resources under TEMP, which the executor points
outside the homes. Module level is standard library only, so the rules below are tested in the app's environment.
"""

from __future__ import annotations

import json
import math
import os
import re
import struct
import subprocess
import sys
import threading
import time
import wave
import zlib
from pathlib import Path

from protocol import Cancelled, Job, run

SEED_RULE = "crc32(job id)"
# The research's session limits, for settings the spec does not carry (the load grace; superres's limits): loading a
# 7 GB GGUF from the busy D: HDD took 14 minutes once, so the first request gets 30 minutes on top of the stall limit.
LIMITS = {
    "stall_seconds": 900,
    "load_grace_seconds": 1800,
    "oom_retries": 3,
    "oom_wait_seconds": 120,
    "seed_retries": 2,
}
POLL_SECONDS = 0.5
SUPERRES_TASK = "s2s"

MAYA_VOICE = "Realistic female voice in her twenties with an American accent. Clear, mid-pitched voice."

# What a line that is only a hint, like "(sobbing)", is voiced as; also replaces a hint the model has no control for.
VOCAL = {
    "scream": "Aaaaaaah!",
    "sob": "Hhuh... hhuh... hhuh...",
    "gasp": "Hah!",
    "pain": "Nnngh... aagh...",
    "moan": "Mmmmh... ohhh...",
    "pant": "Hah... hah... hah... hah...",
    "sigh": "Haaah...",
    "laugh": "Hahahaha!",
}

# The one category -> control table per model family; `render` applies it.
CONTROLS = {
    # Higgs TTS 3 4B -- bosonai/higgs-tts-3-4b README "Inline control tokens" and PROMPTING.md (the 43 tokens are
    # special tokens in its tokenizer): <|emotion:x|>, <|style:x|> and <|prosody:x|> lead the sentence; an inline
    # <|sfx:x|> is followed directly (no space) by its onomatopoeia (crying "Sob", screaming "Aaah", laughter "Haha",
    # sigh "Haah"). There is no gasp, pain, pant or moan token: those use the nearest emotion/prosody token and VOCAL.
    "higgs_audio_tts": {
        "neutral": {},
        "shout": {"lead": "<|style:shouting|><|prosody:expressive_high|>"},
        "whisper": {"lead": "<|style:whispering|>"},
        "sad": {"lead": "<|emotion:sadness|>", "sfx": ("<|sfx:crying|>", "Sob, ")},
        "fear": {"lead": "<|emotion:fear|><|prosody:expressive_high|>"},
        "happy": {
            "lead": "<|emotion:amusement|>",
            "sfx": ("<|sfx:laughter|>", "Haha, "),
        },
        "scream": {
            "lead": "<|emotion:fear|>",
            "sfx": ("<|sfx:screaming|>", "Aaah! "),
            "has_sound": r"^a+h",
        },
        "sob": {"lead": "<|emotion:sadness|>", "sfx": ("<|sfx:crying|>", "Sob, ")},
        "gasp": {"lead": "<|emotion:surprise|>"},
        "pain": {"lead": "<|emotion:helplessness|><|prosody:expressive_high|>"},
        "moan": {"lead": "<|emotion:arousal|><|prosody:speed_slow|>"},
        "pant": {"lead": "<|emotion:fear|><|prosody:speed_fast|>"},
        "sigh": {"sfx": ("<|sfx:sigh|>", "Haah, ")},
        "laugh": {
            "lead": "<|emotion:amusement|>",
            "sfx": ("<|sfx:laughter|>", "Haha, "),
            "has_sound": r"^(ha|he)",
        },
    },
    # VoxCPM2 -- audio.cpp docs/tts.md#voxcpm2 and openbmb/VoxCPM2 card "Controllable Voice Cloning": a style
    # description in parentheses at the start of the text steers emotion, pace and expression while the reference keeps
    # the timbre. A line hint is appended to the style.
    "voxcpm2": {
        "neutral": {},
        "shout": {"style": "shouting loudly, angry and intense"},
        "whisper": {"style": "whispering softly, breathy and very quiet"},
        "sad": {
            "style": "crying while speaking, voice trembling and breaking with sobs"
        },
        "fear": {"style": "terrified and panicked, voice shaking"},
        "happy": {"style": "laughing while speaking, joyful and giggly"},
        "scream": {"style": "screaming in terror, extremely loud and high-pitched"},
        "sob": {"style": "sobbing uncontrollably"},
        "gasp": {"style": "gasping sharply in shock"},
        "pain": {"style": "groaning and crying out in pain"},
        "moan": {"style": "moaning softly and slowly"},
        "pant": {"style": "panting heavily, out of breath"},
        "sigh": {"style": "sighing deeply"},
        "laugh": {"style": "laughing heartily"},
    },
    # OmniVoice -- audio.cpp docs/models/omnivoice.md "Tags" ([laughter] [sigh] [surprise-ah] [dissatisfaction-hnn]
    # ...) and the instruct vocabulary in src/models/omnivoice/prompt_builder.cpp (gender, age, pitch, "whisper"); the
    # instruct goes with the clone. No shout, scream, cry, fear, moan or pant control exists: those lines rely on their
    # words and punctuation (and VOCAL for hints).
    "omnivoice": {
        "neutral": {},
        "shout": {},
        "whisper": {"instruct": "whisper"},
        "sad": {},
        "fear": {},
        "happy": {"tag": "[laughter]"},
        "scream": {},
        "sob": {},
        "gasp": {"tag": "[surprise-ah]"},
        "pain": {"tag": "[dissatisfaction-hnn]"},
        "moan": {},
        "pant": {},
        "sigh": {"tag": "[sigh]"},
        "laugh": {"tag": "[laughter]"},
    },
    # Fish Audio S2 Pro -- fishaudio/s2-pro card: free-form [tag] text anywhere in a line ([whisper], [screaming],
    # [shouting], [laughing], [sigh], [panting], [moaning], [sad] ...; 15,000+ tags). audio.cpp passes the text to the
    # tokenizer unchanged (src/models/fish_audio/prompt_builder.cpp; docs/models/fish_audio.md). A line hint becomes
    # a tag in place of the category tag.
    "fish_audio": {
        "neutral": {},
        "shout": {"tag": "[shouting]"},
        "whisper": {"tag": "[whisper]"},
        "sad": {"tag": "[crying]"},
        "fear": {"tag": "[terrified]"},
        "happy": {"tag": "[laughing tone]"},
        "scream": {"tag": "[screaming]"},
        "sob": {"tag": "[sobbing]"},
        "gasp": {"tag": "[gasping]"},
        "pain": {"tag": "[groaning in pain]"},
        "moan": {"tag": "[moaning]"},
        "pant": {"tag": "[panting]"},
        "sigh": {"tag": "[sigh]"},
        "laugh": {"tag": "[laughing]"},
    },
    # Maya1 -- audio.cpp docs/models/maya1.md and maya-research/maya1 card: voice design only (request option
    # `instruct` = natural-language voice description; it cannot clone), inline tags from its tokenizer: <angry> <cry>
    # <gasp> <giggle> <laugh> <laugh_harder> <scream> <sigh> <whisper> <exhale> ... The description carries the
    # delivery; line hints are translated to tags (MAYA_HINT_TAGS).
    "maya1": {
        "neutral": {"delivery": "Calm, natural delivery."},
        "shout": {"tag": "<angry>", "delivery": "Shouting loudly, angry and urgent."},
        "whisper": {"tag": "<whisper>", "delivery": "Whispering quietly."},
        "sad": {"tag": "<cry>", "delivery": "Crying, voice trembling with sobs."},
        "fear": {"tag": "<gasp>", "delivery": "Terrified, voice shaking with fear."},
        "happy": {"tag": "<laugh>", "delivery": "Joyful, laughing while speaking."},
        "scream": {"tag": "<scream>", "delivery": "Screaming in terror."},
        "sob": {"tag": "<cry>", "delivery": "Sobbing uncontrollably."},
        "gasp": {"tag": "<gasp>", "delivery": "Shocked, gasping."},
        "pain": {"delivery": "In pain, groaning."},
        "moan": {"tag": "<exhale>", "delivery": "Moaning softly."},
        "pant": {"tag": "<exhale>", "delivery": "Out of breath, panting heavily."},
        "sigh": {"tag": "<sigh>", "delivery": "Weary, sighing."},
        "laugh": {"tag": "<laugh_harder>", "delivery": "Laughing hard."},
    },
    # IndexTTS-2.5 -- audio.cpp docs/models/index_tts2.md: emotion from an emotion-reference WAV (request `audio`,
    # strength emotion_alpha), an 8-float emotion_vector [happy, angry, sad, afraid, disgusted, melancholic,
    # surprised, calm] (index-tts README order) or Qwen emotion text. The emotion reference wins; the vector is the
    # fallback. A vector cannot make a whisper; whisper lines need a whispered emotion reference.
    "index_tts2": {
        "neutral": {},
        "shout": {"vector": [0, 0.8, 0, 0, 0, 0, 0.2, 0]},
        "whisper": {"vector": [0, 0, 0, 0, 0, 0, 0, 0.6]},
        "sad": {"vector": [0, 0, 0.8, 0, 0, 0.2, 0, 0]},
        "fear": {"vector": [0, 0, 0, 0.8, 0, 0, 0.2, 0]},
        "happy": {"vector": [0.8, 0, 0, 0, 0, 0, 0.2, 0]},
        "scream": {"vector": [0, 0, 0, 0.7, 0, 0, 0.3, 0]},
        "sob": {"vector": [0, 0, 0.9, 0, 0, 0.1, 0, 0]},
        "gasp": {"vector": [0, 0, 0, 0.3, 0, 0, 0.7, 0]},
        "pain": {"vector": [0, 0.2, 0.5, 0.3, 0, 0, 0, 0]},
        "moan": {"vector": [0, 0, 0.2, 0, 0, 0.6, 0, 0]},
        "pant": {"vector": [0, 0, 0, 0.6, 0, 0, 0.2, 0]},
        "sigh": {"vector": [0, 0, 0.2, 0, 0, 0.6, 0, 0]},
        "laugh": {"vector": [0.9, 0, 0, 0, 0, 0, 0.1, 0]},
    },
}
MAYA_HINT_TAGS = (
    (r"sob|cry|weep", "<cry>"),
    (r"gasp", "<gasp>"),
    (r"sigh", "<sigh>"),
    (r"giggl", "<giggle>"),
    (r"laugh", "<laugh>"),
    (r"pant|breath", "<exhale>"),
)
CATEGORIES = tuple(CONTROLS["higgs_audio_tts"])
# IndexTTS clones from audio only; Maya1 designs its voice (it takes no reference at all).
NO_REFERENCE_TEXT = {"index_tts2", "maya1"}
HINT_RE = re.compile(r"\(([^)]*)\)")
SEED_RETRY = re.compile(r"reached max_tokens", re.IGNORECASE)
ERROR_RE = re.compile(r"fail|error|exception|out of memory", re.IGNORECASE)
OOM_RE = re.compile(
    r"out of memory|cudaMalloc|failed to allocate|CUDA error: out|ggml_backend.*alloc",
    re.IGNORECASE,
)


# --- text rendering (standard library) -----------------------------------------------------------------------------


def split_hints(text: str) -> tuple[str, list[str]]:
    """The spoken words of a line and its parenthesised hints."""
    hints = [h.strip() for h in HINT_RE.findall(text) if h.strip()]
    spoken = re.sub(r"\s+", " ", HINT_RE.sub(" ", text)).strip()
    return spoken, hints


def voiced(category: str, spoken: str, hints: list[str], realised: bool) -> str:
    """The words to speak: the line without hints, VOCAL for a hint-only line, VOCAL + words for a hint the model
    cannot realise."""
    if not spoken:
        return VOCAL.get(category, "")
    if hints and not realised and category in VOCAL:
        return f"{VOCAL[category]} {spoken}"
    return spoken


def new_hints(hints: list[str], cue: str) -> list[str]:
    """Hints that add something to a style (all content-word stems already in it: dropped)."""
    keep = []
    for hint in hints:
        stems = [
            w[:3]
            for w in re.findall(r"[a-z]+", hint.lower())
            if w not in ("of", "for", "in", "the", "a")
        ]
        if not all(stem in cue.lower() for stem in stems):
            keep.append(hint)
    return keep


def render(family: str, take: dict, voice: str = MAYA_VOICE, clone_mode: str = "auto"):
    """-> (request fields for the CLI JSON, rendered text, extra report fields) for one take of a line."""
    category = take["category"]
    if category not in CATEGORIES:
        raise ValueError(f"Unknown category {category!r}.")
    if family not in CONTROLS:
        raise ValueError(f"No category controls for the {family!r} family.")
    control = CONTROLS[family][category]
    spoken, hints = split_hints(take["text"])
    fields, extra = {}, {}
    if family == "higgs_audio_tts":
        words = voiced(category, spoken, hints, "sfx" in control)
        text = control.get("lead", "")
        if "sfx" in control:
            tag, onomatopoeia = control["sfx"]
            has_sound = not spoken or (
                control.get("has_sound")
                and re.match(control["has_sound"], words, re.IGNORECASE)
            )
            # A hint-only line: VOCAL is the onomatopoeia.
            text += tag + ("" if has_sound else onomatopoeia) + words
        else:
            text += words
    elif family == "voxcpm2":
        words = spoken or VOCAL.get(category, "")
        style = ", ".join(
            ([control["style"]] if "style" in control else [])
            + new_hints(hints, control.get("style", ""))
        )
        if clone_mode not in ("auto", "ultimate", "controllable"):
            raise ValueError(
                "VoxCPM2's clone_mode must be auto, ultimate or controllable."
            )
        # Ultimate cloning continues the reference's transcript, so a "(style)" after it is read aloud: styled lines
        # clone from the audio alone (controllable).
        if (
            take.get("reference")
            and take.get("reference_text")
            and (clone_mode == "ultimate" or (clone_mode == "auto" and not style))
        ):
            extra["clone_mode"] = "ultimate"
            if style:
                extra["style_dropped"] = style
            style = ""
        elif take.get("reference"):
            extra["clone_mode"] = "controllable"
        text = f"({style}){words}" if style else words
    elif family == "omnivoice":
        words = voiced(category, spoken, hints, "tag" in control)
        text = f"{control['tag']} {words}" if "tag" in control else words
        if "instruct" in control:
            fields["instruct"] = control["instruct"]
    elif family == "fish_audio":
        if hints:
            text = HINT_RE.sub(
                lambda m: f"[{m.group(1).strip()}]", take["text"]
            ).strip()
            if not spoken:
                text = f"{text} {VOCAL.get(category, '')}".strip()
        else:
            text = f"{control['tag']} {spoken}" if "tag" in control else spoken
    elif family == "maya1":
        from_hints = []
        for hint in hints:
            for pattern, tag in MAYA_HINT_TAGS:
                if re.search(pattern, hint, re.IGNORECASE):
                    from_hints.append(tag)
                    break
        tags = from_hints or ([control["tag"]] if "tag" in control else [])
        text = " ".join(tags + [voiced(category, spoken, hints, bool(tags))])
        fields["instruct"] = f"{voice} {control['delivery']}"
    elif family == "index_tts2":
        text = voiced(category, spoken, hints, False)
        if take.get("emotion_reference"):
            fields["audio"] = str(take["emotion_reference"])
            extra["emotion_mode"] = "reference"
        elif "vector" in control:
            extra["emotion_mode"] = "vector"
            extra["emotion_vector"] = control["vector"]
        else:
            extra["emotion_mode"] = "none"
    else:  # pragma: no cover - CONTROLS and the branches above list the same families
        raise ValueError(f"No renderer for the {family!r} family.")
    return fields, text, extra


def higgs_max_tokens(text: str, limits: dict) -> int:
    """Higgs's token budget (25 codec frames/s): a base plus a share per word, clamped. Reaching it is a CLI error
    (a loop), retried with the next seed."""
    words = max(1, len(re.findall(r"[\w']+", text.split("|>")[-1])))
    budget = limits["per_word"] * (limits["base_words"] + words)
    return int(min(limits["max"], max(limits["min"], budget)))


def build_request(
    spec: dict,
    take: dict,
    rid: str,
    higgs_limits: dict,
    voice: str = MAYA_VOICE,
    clone_mode: str = "auto",
):
    """-> (CLI request, report info) for one take: the generator's defaults as request options, the take's seed,
    its reference and transcript where the model takes them."""
    family = spec["family"]
    fields, text, extra = render(family, take, voice=voice, clone_mode=clone_mode)
    options = dict(spec.get("defaults") or {})
    reference, reference_text = take.get("reference"), take.get("reference_text")
    if family == "maya1":
        reference = reference_text = None  # voice design only
    if extra.get("clone_mode") == "controllable" or family in NO_REFERENCE_TEXT:
        reference_text = None
    if family == "omnivoice" and reference and not reference_text:
        raise ValueError("OmniVoice cloning needs the reference's transcript.")
    if family == "higgs_audio_tts":
        options["max_tokens"] = higgs_max_tokens(text, higgs_limits)
    if family == "index_tts2":
        if extra.get("emotion_mode") == "reference":
            options["emotion_alpha"] = 1.0
        elif extra.get("emotion_mode") == "vector":
            options["emotion_vector"] = ",".join(
                f"{v:g}" for v in extra.pop("emotion_vector")
            )
            options["emotion_alpha"] = 1.0
    options = {
        k: (("true" if v else "false") if isinstance(v, bool) else v)
        for k, v in options.items()
    }
    seed = int(take["seed"])
    request = {"id": rid, "text": text, "seed": seed, "options": options, **fields}
    if reference:
        request["voice_ref"] = str(reference)
    if extra.get("clone_mode") == "ultimate":
        # audio.cpp reads reference_text only together with prompt audio (`audio`, src/models/voxcpm2/audiovae.cpp
        # encode_prompt_audio); upstream ultimate cloning passes the same clip as prompt and reference.
        request["audio"] = request["voice_ref"]
    if reference_text:
        request["reference_text"] = reference_text
    resolved = {"seed": seed, **options}
    if "instruct" in fields:
        resolved["instruct"] = fields["instruct"]
    info = {
        "rendered_text": text,
        "params": resolved,
        "reference": take.get("reference_id") if reference else None,
        "reference_text": reference_text,
        "emotion_reference": Path(fields["audio"]).name if "audio" in fields else None,
        **extra,
    }
    return request, info


# --- planning (standard library) -----------------------------------------------------------------------------------


def take_seed(take_id: str, line_seed=None, job_seed=None) -> int:
    """The research's rule: crc32 of the take's id. A line's own seed wins; a job seed (a retake) salts the rule."""
    if line_seed is not None:
        return int(line_seed)
    salted = take_id if job_seed is None else f"{take_id}#{job_seed}"
    return zlib.crc32(salted.encode("utf-8")) & 0x7FFFFFFF


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(text)).strip("._") or "line"


def line_span(value, count: int) -> tuple[int, int]:
    """[first, stop) of the lines to voice; the whole list when no range is given."""
    if value is None:
        return 0, count
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or not all(isinstance(v, int) and not isinstance(v, bool) for v in value)
    ):
        raise ValueError("line_range must be [first, stop).")
    first, stop = value
    if not 0 <= first <= stop <= count:
        raise ValueError(f"line_range {list(value)} lies outside the {count} lines.")
    return first, stop


def seed_clips(document, base: Path, inputs: dict) -> list[dict]:
    """The seed's clips in order: [{"id", "path", "text"}] from a list (or {"clips": [...]}) of paths or dicts; a dict
    without a path names an input `clip:<id>`; a relative path is relative to the seed document."""
    items = document.get("clips", []) if isinstance(document, dict) else document
    clips = []
    for n, item in enumerate(items or []):
        entry = {"path": item} if isinstance(item, str) else dict(item)
        clip_id = str(entry.get("id") or f"seed{n:02d}")
        source = entry.get("path") or inputs.get(f"clip:{clip_id}")
        if not source:
            raise ValueError(f"Seed clip {clip_id} names no file.")
        path = Path(source)
        clips.append(
            {
                "id": clip_id,
                "path": path if path.is_absolute() else Path(base) / path,
                "text": entry.get("text") or "",
            }
        )
    return clips


def plan_takes(
    params: dict, clips: list[dict], job_seed=None, emotions: dict | None = None
):
    """-> (takes to voice, lines skipped) for the job's generator: allowed lines in the range, `takes_per_line` takes
    each, references rotating through the seed by the line's place in the whole list."""
    name = params["generator"]
    lines = params.get("lines")
    if not isinstance(lines, list):
        raise TypeError("The generate task needs params['lines'] (a list).")
    for line in lines:
        if not isinstance(line, dict) or not all(
            line.get(k) for k in ("id", "text", "category")
        ):
            raise ValueError("Every line needs an id, a text and a category.")
    if len({safe_name(line["id"]) for line in lines}) != len(lines):
        raise ValueError("Line ids must be unique (also as file names).")
    takes = int(params["takes_per_line"])
    first, stop = line_span(params.get("line_range"), len(lines))
    allow = params.get("allow") or {}
    planned, skipped = [], []
    for k in range(first, stop):
        line = lines[k]
        if name not in allow.get(line["category"], []):
            skipped.append(
                {
                    "line": line["id"],
                    "category": line["category"],
                    "reason": "not allowed",
                }
            )
            continue
        for t in range(takes):
            take_id = f"{name}__{line['id']}_{t}"
            clip = clips[(k * takes + t) % len(clips)] if clips else None
            planned.append(
                {
                    "id": take_id,
                    "line": line["id"],
                    "take": t,
                    "index": k,
                    "text": line["text"],
                    "category": line["category"],
                    "line_kind": line.get("kind"),
                    "seed": take_seed(take_id, line.get("seed"), job_seed),
                    "reference": str(clip["path"]) if clip else None,
                    "reference_id": clip["id"] if clip else None,
                    "reference_text": clip["text"] if clip else None,
                    "emotion_reference": (emotions or {}).get(line["category"]),
                }
            )
    return planned, skipped


def session_order(family: str, items: list) -> list:
    """Requests in plan order; OmniVoice keeps the first request's instruct as a session default, so plain requests
    go first."""
    return sorted(
        items,
        key=lambda item: (family == "omnivoice" and "instruct" in item[1], item[0]),
    )


def cli_args(cli, task, family, package, sequence, out_dir, session=None) -> list[str]:
    args = [
        cli,
        "--task",
        task,
        "--family",
        family,
        "--model",
        package,
        "--backend",
        "cuda",
        "--request-sequence",
        sequence,
        "--out-dir",
        out_dir,
        "--out-format",
        "float32",
        "--metrics",
    ]
    for key, value in (session or {}).items():
        args += ["--session-option", f"{key}={value}"]
    return [str(a) for a in args]


def universr_input_rate(rate: int, rates) -> int:
    """UniverSR's effective input bandwidth: the clip's rate when it is one of the model's, else the next lower one
    (or the lowest)."""
    rates = sorted(int(r) for r in rates)
    if rate in rates:
        return rate
    lower = [r for r in rates if r <= rate]
    return lower[-1] if lower else rates[0]


def session_limits(params: dict) -> dict:
    return {key: params.get(key, default) for key, default in LIMITS.items()}


def parse_metrics(lines: list[str]) -> dict:
    metrics: dict = {}
    for line in lines:
        found = re.match(r"metrics\[([^\]]+)\]\.([\w.]+)=(.*)$", line)
        if found:
            metrics.setdefault(found.group(1), {})[found.group(2)] = found.group(3)
    return metrics


def triage(outcome: dict, ooms: int, reseeds: int, can_reseed: bool, limits: dict):
    """What a session that ended with requests unwritten means for the first of them: ("missing", why) fails them
    all, ("oom", tail) waits and retries, ("reseed", tail) retries with the next seed, ("fail", why) drops it."""
    lines = outcome["lines"]
    errors = [line for line in lines if ERROR_RE.search(line)]
    tail = "\n".join(errors[-4:] or lines[-12:])
    if outcome["rc"] == 0 and not outcome["killed"]:
        return "missing", "the CLI finished without writing this output"
    if OOM_RE.search(tail) and ooms < int(limits["oom_retries"]):
        return "oom", tail
    if can_reseed and SEED_RETRY.search(tail) and reseeds < int(limits["seed_retries"]):
        return "reseed", tail
    reason = "stalled and killed" if outcome["killed"] else f"exit code {outcome['rc']}"
    last = tail.splitlines()[-1] if tail else ""
    return "fail", f"{reason}: {last}"


# --- audio (numpy inside) ------------------------------------------------------------------------------------------


def k_weighting(rate: int):
    """The BS.1770 pre-filter (high shelf) and RLB high-pass as biquads ((b0, b1, b2), (1, a1, a2)) at `rate`, by
    libebur128's formulas (as ffmpeg's ebur128 computes them)."""
    f0, gain, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / rate)
    vh = 10 ** (gain / 20)
    vb = vh**0.4996667741545416
    a0 = 1 + k / q + k * k
    shelf = (
        (
            (vh + vb * k / q + k * k) / a0,
            2 * (k * k - vh) / a0,
            (vh - vb * k / q + k * k) / a0,
        ),
        (1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0),
    )
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / rate)
    a0 = 1 + k / q + k * k
    highpass = ((1.0, -2.0, 1.0), (1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0))
    return shelf, highpass


def biquad(samples, b, a) -> list[float]:
    out, x1, x2, y1, y2 = [], 0.0, 0.0, 0.0, 0.0
    for x in samples:
        y = b[0] * x + b[1] * x1 + b[2] * x2 - a[1] * y1 - a[2] * y2
        x2, x1, y2, y1 = x1, x, y1, y
        out.append(y)
    return out


_RESPONSES: dict = {}


def k_weighted(x, rate: int):
    """The K-weighted signal: the two biquads' impulse response (half a second; it has decayed long before) applied by
    FFT convolution."""
    import numpy as np

    if rate not in _RESPONSES:
        response = [1.0] + [0.0] * (rate // 2 - 1)
        for b, a in k_weighting(rate):
            response = biquad(response, b, a)
        _RESPONSES[rate] = np.array(response)
    h = _RESPONSES[rate]
    size = 1 << (len(x) + len(h) - 2).bit_length()
    return np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(h, size), size)[: len(x)]


def loudness(mono, rate: int) -> float | None:
    """BS.1770-4 integrated loudness (LUFS) of a mono signal: 400 ms blocks every 100 ms, the -70 LUFS absolute and
    the -10 LU relative gate. None when nothing passes the gates (silence, or shorter than one block)."""
    import numpy as np

    x = np.asarray(mono, dtype=np.float64)
    block, hop = round(0.4 * rate), round(0.1 * rate)
    if len(x) < block:
        return None
    y = k_weighted(x, rate)
    energy = np.concatenate([[0.0], np.cumsum(y * y)])
    starts = np.arange(0, len(x) - block + 1, hop)
    power = (energy[starts + block] - energy[starts]) / block
    gated = power[power > 10 ** ((-70.0 + 0.691) / 10)]
    if not len(gated):
        return None
    kept = gated[gated > gated.mean() * 0.1]
    return -0.691 + 10 * math.log10(float(kept.mean()))


def levelling_gain_db(
    measured: float | None, peak: float, lufs: float, peak_db: float
) -> float:
    """Static gain to `lufs` integrated, never lifting the sample peak above `peak_db` (none when unmeasurable)."""
    gain = 0.0 if measured is None else lufs - measured
    if peak > 0:
        gain = min(gain, peak_db - 20 * math.log10(peak))
    return round(gain, 2)


def _wav_chunks(path: Path):
    data = Path(path).read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError(f"Not a RIFF/WAVE file: {Path(path).name}")
    pos, fmt, raw = 12, None, None
    while pos + 8 <= len(data):
        cid = data[pos : pos + 4]
        size = struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        body = data[pos + 8 : pos + 8 + size]
        if cid == b"fmt ":
            tag, channels, rate, _, _, bits = struct.unpack("<HHIIHH", body[:16])
            if tag == 0xFFFE and len(body) >= 26:
                tag = struct.unpack("<H", body[24:26])[0]
            fmt = (tag, channels, rate, bits)
        elif cid == b"data":
            raw = body
        pos += 8 + size + (size & 1)
    if fmt is None or raw is None:
        raise ValueError(f"WAV without fmt/data: {Path(path).name}")
    return fmt, raw


def wav_info(path: Path) -> tuple[int, int, int]:
    """(channels, sample rate, frames) of a WAV."""
    (_tag, channels, rate, bits), raw = _wav_chunks(path)
    return channels, rate, len(raw) // (channels * bits // 8)


def read_wav(path: Path):
    """-> (float32 array [frames, channels], rate) from float32 or 16/24/32-bit PCM."""
    import numpy as np

    (tag, channels, rate, bits), raw = _wav_chunks(path)
    if tag == 3 and bits == 32:
        x = np.frombuffer(raw[: len(raw) // 4 * 4], "<f4").astype(np.float32)
    elif tag == 1 and bits == 16:
        x = np.frombuffer(raw[: len(raw) // 2 * 2], "<i2").astype(np.float32) / 32768.0
    elif tag == 1 and bits == 32:
        x = (
            np.frombuffer(raw[: len(raw) // 4 * 4], "<i4").astype(np.float32)
            / 2147483648.0
        )
    elif tag == 1 and bits == 24:
        b = (
            np.frombuffer(raw[: len(raw) // 3 * 3], np.uint8)
            .reshape(-1, 3)
            .astype(np.int32)
        )
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        x = np.where(v >= 1 << 23, v - (1 << 24), v).astype(np.float32) / 8388608.0
    else:
        raise ValueError(
            f"Unsupported WAV format (tag {tag}, {bits} bit): {Path(path).name}"
        )
    return x[: len(x) // channels * channels].reshape(-1, channels), rate


def write_pcm16(path: Path, pcm, rate: int) -> None:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(pcm.astype("<i2").tobytes())


def master(raw: Path, lufs: float | None = None, peak_db: float | None = None):
    """CLI float output -> (mono PCM16 samples, rate, stats): stereo averaged; levelled to `lufs` with the peak at most
    `peak_db` when given, else only a peak above full scale scaled to 0.98 (the research's guard)."""
    import numpy as np

    x, rate = read_wav(raw)
    mono = (x.mean(axis=1) if x.shape[1] > 1 else x[:, 0]).astype(np.float64)
    peak = float(np.abs(mono).max()) if len(mono) else 0.0
    stats = {"sample_rate": rate, "channels_native": int(x.shape[1])}
    if lufs is not None:
        measured = loudness(mono, rate)
        gain_db = levelling_gain_db(measured, peak, float(lufs), float(peak_db))
        stats["loudness_lufs"] = None if measured is None else round(measured, 2)
    else:
        gain_db = round(20 * math.log10(0.98 / peak), 2) if peak > 0.999 else 0.0
    mono = mono * 10 ** (gain_db / 20)
    pcm = np.clip(np.round(mono * 32767.0), -32768, 32767).astype("<i2")
    rms = float(np.sqrt(np.mean(mono**2))) if len(mono) else 0.0
    stats.update(
        gain_db=gain_db,
        seconds=round(len(mono) / rate, 3),
        peak_dbfs=round(20 * math.log10(max(peak * 10 ** (gain_db / 20), 1e-9)), 1),
        rms_dbfs=round(20 * math.log10(max(rms, 1e-9)), 1),
    )
    return pcm, rate, stats


# --- CLI sessions --------------------------------------------------------------------------------------------------


def run_cli(
    job: Job, args: list[str], log_path: Path, cwd: Path, limits: dict, on_request=None
) -> dict:
    """Run one CLI session, its output streamed to a log; kill it when no request finishes for `stall_seconds`
    (plus the load grace before the first) or when the job is cancelled."""
    lines: list[str] = []
    state = {"last": time.monotonic() + float(limits["load_grace_seconds"]), "done": 0}
    stall = float(limits["stall_seconds"])
    with open(log_path, "w", encoding="utf-8") as log:
        log.write(subprocess.list2cmdline(args) + "\n")
        log.flush()
        started = time.monotonic()
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(cwd),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if os.name == "nt"
            else 0,
        )

        def reader():
            for raw in process.stdout:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                lines.append(line)
                log.write(line + "\n")
                log.flush()
                if line.startswith("request_id="):
                    state["last"] = time.monotonic()
                    state["done"] += 1

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        killed = cancelled = False
        reported = 0
        while process.poll() is None:
            time.sleep(POLL_SECONDS)
            if job.cancelled():
                process.kill()
                cancelled = True
                break
            if time.monotonic() - state["last"] > stall:
                process.kill()
                killed = True
                break
            if on_request and state["done"] != reported:
                reported = state["done"]
                on_request(reported)
        process.wait()
        thread.join(timeout=10)
        wall = time.monotonic() - started
    if cancelled:
        raise Cancelled
    return {
        "rc": process.returncode,
        "killed": killed,
        "lines": lines,
        "metrics": parse_metrics(lines),
        "wall": wall,
    }


def pause(job: Job, seconds: float) -> None:
    """Wait, honouring cancellation."""
    end = time.monotonic() + float(seconds)
    while True:
        job.check()
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(POLL_SECONDS, left))


def run_sequence(
    job: Job,
    args_for,
    items: list,
    workdir: Path,
    finish,
    limits: dict,
    label: str,
    on_progress=None,
):
    """Run `items` [(rid, request, context)] through CLI sessions until each is written or failed for good.
    `args_for(sequence)` is the command for a request-sequence file; `finish(rid, context, raw, metrics)` publishes one
    output. Returns [(rid, context, error)] for the requests that failed."""
    workdir.mkdir(parents=True, exist_ok=True)
    # A stopped session's leftovers are not trusted.
    for rid, _, _ in items:
        (workdir / f"{rid}.wav").unlink(missing_ok=True)
    failed, pending, ooms, reseeds, attempt, done = [], list(items), {}, {}, 0, 0
    while pending:
        job.check()
        attempt += 1
        sequence = workdir / f"requests_{attempt}.json"
        sequence.write_text(
            json.dumps(
                {"requests": [r for _, r, _ in pending]}, indent=1, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        print(f"[{label}] {len(pending)} request(s), attempt {attempt}", flush=True)
        outcome = run_cli(
            job,
            args_for(sequence),
            workdir / f"cli_{attempt}.log",
            workdir,
            limits,
            (lambda n, base=done: on_progress(base + n)) if on_progress else None,
        )
        remaining = []
        for rid, request, context in pending:
            raw = workdir / f"{rid}.wav"
            if not raw.exists():
                remaining.append((rid, request, context))
                continue
            try:
                finish(rid, context, raw, outcome["metrics"].get(rid, {}))
            except Exception as exc:  # noqa: BLE001 - a broken output file fails only its own request
                failed.append((rid, context, f"finalize: {exc}"))
                print(f"  FAILED {rid}: finalize: {exc}", flush=True)
            raw.unlink(missing_ok=True)
            done += 1
        if on_progress:
            on_progress(done)
        if not remaining:
            break
        rid, request, context = remaining[0]
        action, detail = triage(
            outcome, ooms.get(rid, 0), reseeds.get(rid, 0), "seed" in request, limits
        )
        if action == "missing":
            failed += [(r, c, detail) for r, _, c in remaining]
            break
        if action == "oom":
            ooms[rid] = ooms.get(rid, 0) + 1
            print(
                f"  out of memory at {rid}; retry {ooms[rid]} in {limits['oom_wait_seconds']} s",
                flush=True,
            )
            pause(job, limits["oom_wait_seconds"])
            pending = remaining
            continue
        if action == "reseed":
            # A run that never stops (Higgs loops into max_tokens) is sampling bad luck: the next seed, recorded.
            reseeds[rid] = reseeds.get(rid, 0) + 1
            request["seed"] += 1
            info = context.setdefault("info", {})
            info.setdefault("params", {})["seed"] = request["seed"]
            info["seed_retries"] = reseeds[rid]
            print(
                f"  {rid} hit max_tokens; retry {reseeds[rid]} with seed {request['seed']}",
                flush=True,
            )
            pending = remaining
            continue
        failed.append((rid, context, f"{detail} (log cli/cli_{attempt}.log)"))
        print(f"  FAILED {rid}: {detail}", flush=True)
        pending = remaining[1:]
    return failed


# --- job helpers ---------------------------------------------------------------------------------------------------


def home_file(job: Job, relative: str) -> Path:
    if job.home is None or not job.home.is_dir():
        raise RuntimeError("The tts engine's home is not installed.")
    path = job.home / relative
    if not path.is_file():
        raise RuntimeError(f"{relative} is not installed in the tts home.")
    return path


def cli_program(job: Job) -> Path:
    cli = job.program("cli")
    if not cli.is_file():
        raise RuntimeError(
            f"The audio.cpp CLI is not installed ({cli.name} is missing)."
        )
    return cli


def write_json(job: Job, path: Path, document) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with job.writing(path) as temporary:
        temporary.write_text(
            json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )


def publish(job: Job, out: Path, sidecar: Path, document: dict, pcm, rate: int) -> None:
    """The sidecar first, then the audio: a take counts as finished only when both exist."""
    write_json(job, sidecar, document)
    with job.writing(out) as temporary:
        write_pcm16(temporary, pcm, rate)


def wall_seconds(metrics: dict):
    value = metrics.get("wall_ms")
    return round(float(value) / 1000.0, 3) if value else None


# --- tasks ---------------------------------------------------------------------------------------------------------


def generate(job: Job) -> None:
    params = job.params
    name = params.get("generator")
    generators = params.get("generators") or {}
    if name not in generators:
        raise ValueError(
            f"Unknown generator {name!r} (the spec has {', '.join(sorted(generators))})."
        )
    spec = generators[name]
    family = spec["family"]
    if family not in CONTROLS:
        raise ValueError(f"No category controls for the {family!r} family.")
    if params.get("references", "seed") != "seed":
        raise ValueError("References come only from the seed.")
    if params.get("seed", SEED_RULE) != SEED_RULE:
        raise ValueError(f"Unknown seed rule {params.get('seed')!r}.")
    clips = []
    if "seed" in job.inputs:
        document = json.loads(job.inputs["seed"].read_text(encoding="utf-8"))
        clips = seed_clips(document, job.inputs["seed"].parent, job.inputs)
    emotions = {
        key.split(":", 1)[1]: str(path)
        for key, path in job.inputs.items()
        if key.startswith("emotion:")
    }
    job.progress(0.02, f"Planning {name}")
    planned, skipped = plan_takes(params, clips, job.seed, emotions)
    if planned and family != "maya1" and not clips:
        raise RuntimeError(
            f"{name} clones a voice: the task needs the seed's clips (input `seed`)."
        )
    sidecars = job.work / "takes"
    failed, pending = [], []
    for n, take in enumerate(planned):
        stem = f"{safe_name(take['line'])}_{take['take']}"
        take["out"], take["sidecar"] = (
            job.target(f"{stem}.wav"),
            sidecars / f"{stem}.json",
        )
        if take["out"].exists() and take["sidecar"].exists():
            continue
        try:
            request, info = build_request(
                spec,
                take,
                f"r{n:04d}",
                params["higgs_max_tokens"],
                voice=params.get("voice") or MAYA_VOICE,
                clone_mode=params.get("clone_mode") or "auto",
            )
        except ValueError as exc:
            failed.append({"take": take["id"], "error": str(exc)})
            continue
        pending.append((f"r{n:04d}", request, {"take": take, "info": info}))
    total = max(1, len(planned))
    finished = len(planned) - len(pending) - len(failed)
    if pending:
        cli = cli_program(job)
        package = home_file(job, spec["package"])
        workdir = job.work / "cli"
        lufs, peak_db = float(params["lufs"]), float(params["peak_db"])

        def finish(rid, context, raw, metrics):
            take, info = context["take"], context["info"]
            pcm, rate, stats = master(raw, lufs, peak_db)
            document = {
                "generator": name,
                "family": family,
                "task": spec["task"],
                "package": spec["package"],
                "session_options": spec.get("session") or {},
                "line": take["line"],
                "take": take["take"],
                "category": take["category"],
                "line_kind": take["line_kind"],
                "text": take["text"],
                "seed": info["params"]["seed"],
                **info,
                **stats,
                "seconds_wall": wall_seconds(metrics),
            }
            publish(job, take["out"], take["sidecar"], document, pcm, rate)
            print(f"  {take['id']}: {stats['seconds']:.2f} s @ {rate} Hz", flush=True)

        def progress(done):
            job.progress(
                0.05 + 0.9 * (finished + done) / total,
                f"{name}: {finished + done} of {len(planned)} takes",
            )

        progress(0)
        errors = run_sequence(
            job,
            lambda sequence: cli_args(
                cli,
                spec["task"],
                family,
                package,
                sequence,
                workdir,
                spec.get("session"),
            ),
            session_order(family, pending),
            workdir,
            finish,
            session_limits(params),
            name,
            progress,
        )
        failed += [
            {"take": context["take"]["id"], "error": error}
            for _, context, error in errors
        ]
    made = []
    for take in planned:
        if take["out"].exists() and take["sidecar"].exists():
            info = json.loads(take["sidecar"].read_text(encoding="utf-8"))
            job.output(
                f"take:{take['line']}_{take['take']}", take["out"], "audio", **info
            )
            made.append(take["id"])
    report = {
        "generator": name,
        "family": family,
        "package": spec["package"],
        "takes_per_line": int(params["takes_per_line"]),
        "line_range": list(line_span(params.get("line_range"), len(params["lines"]))),
        "planned": len(planned),
        "made": made,
        "failed": failed,
        "skipped": skipped,
    }
    path = job.target("report.json")
    write_json(job, path, report)
    job.output(
        "report",
        path,
        "analysis_report",
        made=len(made),
        failed=len(failed),
        skipped=len(skipped),
    )
    if planned and not made:
        raise RuntimeError(
            f"{name} made no take: {failed[0]['error'] if failed else 'unknown'}"
        )
    job.progress(1.0, f"{name}: {len(made)} of {len(planned)} takes")


def superres(job: Job) -> None:
    params = job.params
    family, rate_out = params["family"], int(params["sample_rate"])
    clips = {key: path for key, path in job.inputs.items() if key.startswith("clip:")}
    if not clips and "audio" in job.inputs:
        clips = {"audio": job.inputs["audio"]}
    if not clips:
        raise RuntimeError("The superres task needs clip or audio inputs.")
    lifted_dir = job.work / "lifted"
    entries, kept, failed, items, stems = [], [], [], [], set()
    for n, (key, path) in enumerate(sorted(clips.items())):
        try:
            _, rate, _ = wav_info(path)
        except (OSError, ValueError) as exc:
            failed.append({"clip": key, "error": str(exc)})
            continue
        if rate >= int(params["below_hz"]):
            kept.append({"clip": key, "sample_rate": rate})
            continue
        stem = safe_name(key.split(":", 1)[-1])
        stem = stem if stem not in stems else f"{stem}_{n}"
        stems.add(stem)
        entry = {
            "key": key,
            "out": job.target(f"{stem}.wav"),
            "sidecar": lifted_dir / f"{stem}.json",
        }
        entries.append(entry)
        if entry["out"].exists() and entry["sidecar"].exists():
            continue
        options = dict(params.get("defaults") or {})
        if family == "universr":
            options["input_sample_rate"] = universr_input_rate(
                rate, params["input_rates"]
            )
        rid = f"s{n:04d}"
        request = {"id": rid, "audio": str(Path(path).resolve()), "options": options}
        items.append(
            (rid, request, {"entry": entry, "rate_in": rate, "options": options})
        )
    total = max(1, len(entries))
    finished = len(entries) - len(items)
    if items:
        cli = cli_program(job)
        package = home_file(job, params["package"])
        workdir = job.work / "cli"

        def finish(rid, context, raw, metrics):
            pcm, rate, stats = master(raw)
            if rate != rate_out:
                raise ValueError(
                    f"{params['model']} wrote {rate} Hz, not {rate_out} Hz."
                )
            document = {
                "model": params["model"],
                "family": family,
                "package": params["package"],
                "source_rate": context["rate_in"],
                "params": context["options"],
                **stats,
                "seconds_wall": wall_seconds(metrics),
            }
            entry = context["entry"]
            publish(job, entry["out"], entry["sidecar"], document, pcm, rate)

        def progress(done):
            job.progress(
                0.05 + 0.9 * (finished + done) / total,
                f"Lifted {finished + done} of {len(entries)}",
            )

        progress(0)
        errors = run_sequence(
            job,
            lambda sequence: cli_args(
                cli, SUPERRES_TASK, family, package, sequence, workdir
            ),
            items,
            workdir,
            finish,
            session_limits(params),
            params["model"],
            progress,
        )
        failed += [
            {"clip": context["entry"]["key"], "error": error}
            for _, context, error in errors
        ]
    lifted = []
    for entry in entries:
        if entry["out"].exists() and entry["sidecar"].exists():
            info = json.loads(entry["sidecar"].read_text(encoding="utf-8"))
            job.output(entry["key"], entry["out"], "audio", **info)
            lifted.append(entry["key"])
    path = job.target("report.json")
    write_json(
        job,
        path,
        {"model": params["model"], "lifted": lifted, "kept": kept, "failed": failed},
    )
    job.output(
        "report",
        path,
        "analysis_report",
        lifted=len(lifted),
        kept=len(kept),
        failed=len(failed),
    )
    if (entries or failed) and not lifted:
        raise RuntimeError(
            f"No clip was lifted: {failed[0]['error'] if failed else 'unknown'}"
        )
    job.progress(1.0, f"Lifted {len(lifted)} of {len(entries)}")


TASKS = {"generate": generate, "superres": superres}

if __name__ == "__main__":
    sys.exit(run(TASKS))
