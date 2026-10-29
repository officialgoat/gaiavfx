"""The recast voice recipe as data (ADR 0016): each engine task's static settings, the filter calibrations, the TTS
generators the user ranked usable per category, the voice build's and the audition's numbers, and the expansion script
the build voices (`knowledge/expansion.toml`). Workers receive these settings as job params; the spec's fingerprint
joins every engine job's snapshot so a run is traceable to the recipe it used."""

import copy
import hashlib
import tomllib
from importlib import resources

SCHEMA = 1
# A Maya1 voice band whose limit reaches this is open-ended: every median pitch finds a band.
OPEN_HZ = 1e9
EXPANSION_KINDS = frozenset({"speech", "nonverbal"})
BUILD_CHUNKS = (
    "tts_lines_per_job",
    "superres_clips_per_job",
    "convert_clips_per_job",
    "filter_rows_per_job",
)


def spec_text() -> str:
    return (
        resources.files(__package__).joinpath("knowledge", "voice.toml").read_text(encoding="utf-8")
    )


def expansion_text() -> str:
    return (
        resources.files(__package__)
        .joinpath("knowledge", "expansion.toml")
        .read_text(encoding="utf-8")
    )


def fingerprint(text: str | None = None) -> str:
    return hashlib.sha256((spec_text() if text is None else text).encode("utf-8")).hexdigest()


def _number(value) -> bool:
    return type(value) in (int, float)


def _check_build(document: dict) -> None:
    build = document.get("build")
    version = build.get("version") if isinstance(build, dict) else None
    if type(version) is not int or version < 1:
        raise ValueError(
            "The voice spec needs a [build] table with an integer version of at least 1."
        )
    bounds = build.get("sample_seconds") or {}
    if not (_number(bounds.get("min")) and _number(bounds.get("max"))) or not (
        0 < bounds["min"] < bounds["max"]
    ):
        raise ValueError("[build] sample_seconds needs 0 < min < max.")
    cut = build.get("seed_clip_seconds") or {}
    if not all(_number(cut.get(key)) for key in ("min", "max", "pad_before", "pad_after")) or not (
        0 < cut["min"] < cut["max"] and cut["pad_before"] >= 0 and cut["pad_after"] >= 0
    ):
        raise ValueError("[build] seed_clip_seconds needs 0 < min < max and pads of at least 0.")
    for key in BUILD_CHUNKS:
        if type(build.get(key)) is not int or build[key] < 1:
            raise ValueError(f"[build] {key} needs a whole number of at least 1.")
    longest = ((document.get("tasks") or {}).get("analysis", {}).get("filter") or {}).get(
        "max_seconds"
    )
    piece = build.get("library_piece_seconds")
    if not _number(piece) or piece <= 0 or (_number(longest) and piece > longest):
        raise ValueError(
            "[build] library_piece_seconds needs 0 < seconds <= analysis.filter max_seconds."
        )
    extensions = build.get("library_extensions")
    if (
        not isinstance(extensions, list)
        or not extensions
        or not all(isinstance(e, str) and e and e == e.lower().lstrip(".") for e in extensions)
    ):
        raise ValueError(
            "[build] library_extensions needs lower-case file extensions without dots."
        )
    bands = build.get("maya_voice")
    if (
        not isinstance(bands, list)
        or not bands
        or not all(
            isinstance(band, dict)
            and _number(band.get("below_hz"))
            and isinstance(band.get("text"), str)
            and band["text"].strip()
            for band in bands
        )
        or any(bands[n]["below_hz"] >= bands[n + 1]["below_hz"] for n in range(len(bands) - 1))
        or bands[-1]["below_hz"] < OPEN_HZ
    ):
        raise ValueError(
            "[build] maya_voice needs ascending bands with a text each, the last one open-ended "
            f"(below_hz = {OPEN_HZ:g})."
        )


def _check_audition(document: dict) -> None:
    gaps = (document.get("audition") or {}).get("reel_gap_seconds") or {}
    if not all(_number(gaps.get(key)) and gaps[key] >= 0 for key in ("between", "after")):
        raise ValueError("The voice spec needs [audition] reel_gap_seconds with between and after.")


def load(text: str | None = None) -> dict:
    """Parse and check the spec; anything malformed is a ValueError, never a silent default."""
    document = tomllib.loads(spec_text() if text is None else text)
    if document.get("schema") != SCHEMA:
        raise ValueError(f"The voice spec needs schema = {SCHEMA}.")
    tasks = document.get("tasks") or {}
    if not tasks or not all(isinstance(engine, dict) and engine for engine in tasks.values()):
        raise ValueError("The voice spec lists no engine tasks.")
    calibrations = document.get("calibrations") or {}
    if not calibrations:
        raise ValueError("The voice spec needs at least one filter calibration.")
    for name, calibration in calibrations.items():
        limit = calibration.get("max_seed_seconds")
        categories = calibration.get("categories") or {}
        if not isinstance(limit, (int, float)) or limit <= 0 or not categories:
            raise ValueError(f"Calibration {name} needs max_seed_seconds > 0 and categories.")
        for category, row in categories.items():
            if type(row.get("p10")) not in (int, float) or type(row.get("classes")) is not list:
                raise ValueError(f"Calibration {name}, {category}: needs p10 and classes.")
    generate = tasks.get("tts", {}).get("generate", {})
    generators = generate.get("generators") or {}
    for category, names in (generate.get("allow") or {}).items():
        unknown = [name for name in names if name not in generators]
        if unknown:
            raise ValueError(f"TTS category {category} allows unknown generators {unknown}.")
    _check_build(document)
    _check_audition(document)
    return document


def settings(step: str, document: dict | None = None) -> dict:
    """A copy of one engine task's static settings, for its job params; an unknown step is a ValueError."""
    engine, _, task = step.partition(".")
    table = ((document or load()).get("tasks") or {}).get(engine, {}).get(task)
    if table is None:
        raise ValueError(f"The voice spec has no settings for {step!r}.")
    return copy.deepcopy(table)


def build(document: dict | None = None) -> dict:
    """A copy of the voice build's numbers (`[build]`): its version, the sample bounds, the seed cut, the job chunk
    sizes, the expressive library's piece length and extensions, and Maya1's voice bands."""
    return copy.deepcopy((document or load())["build"])


def audition(document: dict | None = None) -> dict:
    """A copy of the audition reel's numbers (`[audition]`)."""
    return copy.deepcopy((document or load())["audition"])


def expansion(text: str | None = None, document: dict | None = None) -> list[dict]:
    """The expansion script's lines in order, each {"id", "category", "kind", "text"}. Every id is unique, every
    category is allowed for at least one TTS generator and every kind is speech or nonverbal; anything else is a
    ValueError. `text` replaces the packaged script (tests)."""
    rows = tomllib.loads(expansion_text() if text is None else text).get("lines")
    if not isinstance(rows, list) or not rows:
        raise ValueError("The expansion script lists no lines.")
    allow = settings("tts.generate", document)["allow"]
    lines, seen = [], set()
    for row in rows:
        line = {key: row.get(key) for key in ("id", "category", "kind", "text")}
        if not all(isinstance(value, str) and value for value in line.values()):
            raise ValueError(
                f"Expansion line {line['id']!r} needs an id, a category, a kind and a text."
            )
        if line["id"] in seen:
            raise ValueError(f"Expansion line id {line['id']} is not unique.")
        if not allow.get(line["category"]):
            raise ValueError(
                f"Expansion line {line['id']}: no TTS generator is allowed for {line['category']}."
            )
        if line["kind"] not in EXPANSION_KINDS:
            raise ValueError(f"Expansion line {line['id']}: kind must be speech or nonverbal.")
        seen.add(line["id"])
        lines.append(line)
    return lines


def library_category(name: str, document: dict | None = None) -> str | None:
    """The recipe category an expressive-library folder names: a category of the filter calibrations or one of their
    aliases, singular or plural, in any case ("Screams" -> "scream", "pants" -> "pant"); None for anything else."""
    document = document or load()
    known = {
        category
        for calibration in document["calibrations"].values()
        for category in calibration["categories"]
    } | set(settings("analysis.filter", document).get("aliases") or {})
    word = str(name).strip().lower()
    for candidate in (
        word,
        word[:-1] if word.endswith("s") else "",
        word[:-2] if word.endswith("es") else "",
    ):
        if candidate and candidate in known:
            return candidate
    return None


def calibration(seed_seconds: float, document: dict | None = None) -> tuple[str, dict]:
    """The filter calibration for a seed of this length: the tightest set whose limit admits it."""
    choices = sorted(
        (document or load())["calibrations"].items(), key=lambda item: item[1]["max_seed_seconds"]
    )
    for name, chosen in choices:
        if seed_seconds <= chosen["max_seed_seconds"]:
            return name, copy.deepcopy(chosen)
    name, chosen = choices[-1]
    return name, copy.deepcopy(chosen)


def identity_threshold(category: str, speech: bool, chosen: dict, document: dict | None = None):
    """ECAPA cosine a kept clip needs: the calibrated 10th percentile of the category, capped by its kind."""
    rules = settings("analysis.filter", document)
    cap = rules["identity_cap_speech"] if speech else rules["identity_cap_nonverbal"]
    real = chosen["categories"].get(rules["aliases"].get(category, category))
    return min(cap, real["p10"]) if real else cap


def allowed(generator: str, category: str, document: dict | None = None) -> bool:
    return generator in settings("tts.generate", document)["allow"].get(category, [])
