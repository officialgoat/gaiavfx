"""ADR 0016: the recast voice recipe as data, checked against the engine registry."""

import pytest

from gaiavfx.integrations.voice import engines, spec

SPEC = spec.load()
REGISTRY = engines.load()


def test_the_packaged_spec_loads_and_is_fingerprinted():
    assert spec.fingerprint() == spec.fingerprint(spec.spec_text())
    assert len(spec.fingerprint()) == 64


def test_every_spec_task_is_a_registered_engine_task():
    for engine, tasks in SPEC["tasks"].items():
        for task in tasks:
            engines.task(REGISTRY, f"{engine}.{task}")


def test_model_files_named_by_the_spec_are_registered():
    def registered(engine, path):
        return path in {model["path"] for model in REGISTRY[engine].models}

    for generator in spec.settings("tts.generate")["generators"].values():
        assert registered("tts", generator["package"]), generator["package"]
    assert registered("tts", spec.settings("tts.superres")["package"])
    assert registered("analysis", spec.settings("analysis.separate")["checkpoint"])
    whisper = spec.settings("analysis.transcribe")["model"]
    assert any(m["path"].startswith(whisper + "/") for m in REGISTRY["analysis"].models)
    finetune = spec.settings("seedvc.finetune")
    assert registered("seedvc", finetune["checkpoint"]) and registered("seedvc", finetune["config"])
    train = spec.settings("rvc.train")
    assert registered("rvc", train["pretrained_g"]) and registered("rvc", train["pretrained_d"])


@pytest.mark.parametrize(
    "seconds, name", [(14.0, "seed15"), (30.0, "seed15"), (30.5, "seed60"), (61.0, "seed60")]
)
def test_the_calibration_follows_the_seed_length(seconds, name):
    assert spec.calibration(seconds)[0] == name


@pytest.mark.parametrize(
    "category, speech, threshold",
    [
        ("scream", False, 0.055),  # calibrated below the nonverbal cap
        ("moan", False, 0.20),  # capped
        ("sigh", False, 0.20),  # an alias of moan
        ("neutral", True, 0.35),  # capped for speech
        ("whisper", True, 0.106),
        ("unknown", True, 0.35),  # no calibration: the cap
    ],
)
def test_identity_thresholds(category, speech, threshold):
    _, chosen = spec.calibration(14.0)
    assert spec.identity_threshold(category, speech, chosen) == pytest.approx(threshold)


def test_the_curated_generators():
    allow = spec.settings("tts.generate")["allow"]
    assert allow["scream"] == ["higgs_audio_tts"]
    assert not any("index_tts2" in names for names in allow.values())  # until P5 brings references
    assert spec.allowed("fish_audio", "neutral") and not spec.allowed("maya1", "scream")


def test_the_pitch_map_keeps_the_high_knee_only():
    convert = spec.settings("rvc.convert")
    assert (convert["f0_method"], convert["index_rate"], convert["protect"]) == ("fcpe", 0.3, 0.33)
    assert convert["pitch_map"] == {
        "knee_semitones": 5.0,
        "ceiling_hz": 870.0,
        "unvoiced_spans": True,
    }


def test_settings_are_copies():
    spec.settings("rvc.convert")["index_rate"] = 1.0
    assert spec.settings("rvc.convert")["index_rate"] == 0.3
    with pytest.raises(ValueError, match="no settings"):
        spec.settings("rvc.nothing")


@pytest.mark.parametrize(
    "change, message",
    [
        (("schema = 1", "schema = 2"), "schema = 1"),
        (('scream = ["higgs_audio_tts"]', 'scream = ["dramabox"]'), "unknown generators"),
        (("max_seed_seconds = 30.0", "max_seed_seconds = 0"), "max_seed_seconds"),
        (("scream = { p10 = 0.055,", "scream = { pten = 0.055,"), "p10 and classes"),
    ],
)
def test_a_malformed_spec_is_refused(change, message):
    old, new = change
    text = spec.spec_text()
    assert old in text
    with pytest.raises(ValueError, match=message):
        spec.load(text.replace(old, new, 1))


# --- the voice build (P5a) ------------------------------------------------------------------------------------------


def test_the_build_and_audition_numbers():
    build = spec.build()
    assert build["version"] == 2
    assert build["sample_seconds"] == {"min": 15.0, "max": 60.0}
    assert build["seed_clip_seconds"] == {
        "min": 4.0,
        "max": 10.0,
        "pad_before": 0.1,
        "pad_after": 0.15,
    }
    assert (
        build["tts_lines_per_job"],
        build["superres_clips_per_job"],
        build["convert_clips_per_job"],
        build["filter_rows_per_job"],
    ) == (60, 120, 30, 200)
    assert build["library_piece_seconds"] <= spec.settings("analysis.filter")["max_seconds"]
    assert {"wav", "flac", "mp3"} <= set(build["library_extensions"])
    assert [band["below_hz"] for band in build["maya_voice"]] == [150.0, 180.0, 250.0, 1e9]
    assert spec.audition() == {"reel_gap_seconds": {"between": 0.3, "after": 0.6}}
    spec.build()["version"] = 99
    assert spec.build()["version"] == 2, "build() hands out copies"


VERSION_LINE = "version = 2                      # joins the voice_build content key; 2 (23 Oct): 4-10 s recordings kept whole\n"
LAST_BAND = "{ below_hz = 1e9,   text"


@pytest.mark.parametrize(
    "change, message",
    [
        ((VERSION_LINE, ""), "integer version"),
        ((VERSION_LINE, 'version = "1"\n'), "integer version"),
        (("{ min = 15.0, max = 60.0 }", "{ min = 60.0, max = 60.0 }"), "sample_seconds"),
        (("{ min = 15.0, max = 60.0 }", "{ min = 70.0, max = 60.0 }"), "sample_seconds"),
        ((LAST_BAND, "{ below_hz = 400.0, text"), "open-ended"),
        (("{ below_hz = 180.0,", "{ below_hz = 140.0,"), "ascending"),
        (("convert_clips_per_job = 30", "convert_clips_per_job = 0"), "convert_clips_per_job"),
        (("library_piece_seconds = 12.0", "library_piece_seconds = 30.0"), "library_piece_seconds"),
        (('library_extensions = ["wav"', 'library_extensions = [".WAV"'), "library_extensions"),
        (("reel_gap_seconds = { between = 0.3,", "reel_gap_seconds = { between = -1,"), "audition"),
    ],
)
def test_a_malformed_build_is_refused(change, message):
    old, new = change
    text = spec.spec_text()
    assert old in text
    with pytest.raises(ValueError, match=message):
        spec.load(text.replace(old, new, 1))


def test_the_expansion_script_ports_the_research_script():
    lines = spec.expansion()
    assert len(lines) == 129
    assert (lines[0]["id"], lines[-1]["id"]) == ("neutral00", "laugh04")
    assert len({line["id"] for line in lines}) == len(lines)
    allow = spec.settings("tts.generate")["allow"]
    assert all(allow.get(line["category"]) for line in lines), "every category has a generator"
    assert {line["kind"] for line in lines} == {"speech", "nonverbal"}
    counts = {}
    for line in lines:
        counts[line["category"]] = counts.get(line["category"], 0) + 1
    assert counts == {
        "neutral": 30,
        "shout": 18,
        "whisper": 14,
        "sad": 14,
        "fear": 10,
        "happy": 8,
        "scream": 6,
        "sob": 5,
        "gasp": 4,
        "pain": 5,
        "moan": 4,
        "pant": 3,
        "sigh": 3,
        "laugh": 5,
    }
    assert all(line["kind"] == "speech" for line in lines[:94])
    assert lines[94] == {
        "id": "scream00",
        "category": "scream",
        "kind": "nonverbal",
        "text": "Aaaaah!",
    }


@pytest.mark.parametrize(
    "rows, message",
    [
        (
            '[[lines]]\nid = "a"\ncategory = "neutral"\nkind = "speech"\ntext = "Hi."\n' * 2,
            "not unique",
        ),
        ('[[lines]]\nid = "a"\ncategory = "music"\nkind = "speech"\ntext = "Hi."\n', "music"),
        ('[[lines]]\nid = "a"\ncategory = "neutral"\nkind = "sung"\ntext = "Hi."\n', "kind"),
        ('[[lines]]\nid = "a"\ncategory = "neutral"\nkind = "speech"\n', "a text"),
        ("", "no lines"),
    ],
)
def test_a_malformed_expansion_script_is_refused(rows, message):
    with pytest.raises(ValueError, match=message):
        spec.expansion(rows)


@pytest.mark.parametrize(
    "name, category",
    [
        ("screams", "scream"),
        ("Pants", "pant"),
        ("gasp", "gasp"),
        ("SOBS", "sob"),
        (" laughs ", "laugh"),
        ("sighs", "sigh"),
        ("whispers", "whisper"),
        ("highpitches", "highpitch"),
        ("music", None),
        ("s", None),
        ("", None),
    ],
)
def test_library_folders_name_recipe_categories(name, category):
    assert spec.library_category(name) == category
