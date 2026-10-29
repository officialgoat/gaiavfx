"""ADR 0016 §4, P5c: the shared voice build through the durable runner (A17–A28), and its audition operation.

Real FFmpeg for the local steps (the recordings, the seed cut, the expressive library), the fake voice engine
(`fixtures/voice_fake_worker.py`) for every engine task, a real Library and fake ComfyUI. No model, no GPU.
"""

import functools
import json
import math
import shutil
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from fake_comfy import FakeComfy

from gaiavfx.application import voice_build as vb
from gaiavfx.application.engine_jobs import EngineCommand
from gaiavfx.application.operations import OperationRunner
from gaiavfx.infrastructure import engines
from gaiavfx.infrastructure.database import Database
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.integrations.voice import expressive
from gaiavfx.integrations.voice import spec as voice_spec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.application import LibraryService, MediaLibrary
from gaiavfx.modules.library.assets import AssetRecord
from gaiavfx.shared.errors import ApplicationError

WORKER = Path(__file__).parent / "fixtures" / "voice_fake_worker.py"
ENGINE_STEPS = (
    "analysis.transcribe",
    "analysis.speakers",
    "analysis.filter",
    "tts.generate",
    "tts.superres",
    "seedvc.finetune",
    "seedvc.convert",
    "rvc.prepare",
    "rvc.train",
    "rvc.index",
    "rvc.convert",
)
COMMANDS = {step: EngineCommand((sys.executable, str(WORKER))) for step in ENGINE_STEPS}
# Expressive recordings by relative path and tone: screams without a speaker folder (the speaker "library"), alex's sobs.
LIBRARY_TONES = {
    "screams/a.wav": 220,
    "screams/b.wav": 220,
    "sobs/alex/c.wav": 150,
    "sobs/alex/d.wav": 150,
}


def synthesize(media, path, expression, seconds):
    media.run(
        media.ffmpeg,
        ["-v", "error", "-y", "-f", "lavfi", "-i", f"aevalsrc='{expression}':s=48000:d={seconds}"]
        + ["-ac", "1", "-c:a", "pcm_s16le", str(path)],
    )
    return path


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    """A library with three recordings (20 s, 20 s, 10 s) and a picture, made once."""
    folder = tmp_path_factory.mktemp("voice-template")
    root = folder / "library"
    LibraryService(root, Path(__file__).parents[1] / "migrations").create("Voice")
    db = Database(root)
    library = MediaLibrary(db, MediaTools())
    media = library.media
    ids = {}
    for name, hz, seconds in (("first", 180, 20), ("second", 200, 20), ("short", 190, 10)):
        path = synthesize(
            media,
            folder / f"{name}.wav",
            f"0.3*sin(2*PI*{hz}*t)*(0.6+0.4*sin(2*PI*0.5*t))",
            seconds,
        )
        prepared = library.prepare(path, path.name)
        with db.transaction() as session:
            ids[name] = library.register(session, prepared, {"type": "import"})["id"]
    from PIL import Image

    picture = folder / "still.png"
    Image.new("RGB", (8, 8), (10, 20, 30)).save(picture)
    with db.transaction() as session:
        ids["picture"] = library.register(
            session, library.prepare(picture, picture.name), {"type": "import"}
        )["id"]
    db.close()
    return root, ids


@pytest.fixture(scope="module")
def expressive_folder(tmp_path_factory):
    """Four recordings: two screams without a speaker (one 25 s long, quiet at 9 s and 16 s) and two of alex's sobs."""
    root = tmp_path_factory.mktemp("expressive") / "recordings"
    media = MediaTools()
    for relative, hz in LIBRARY_TONES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative == "screams/b.wav":
            expression = f"0.3*sin(2*PI*{hz}*t)*not(between(t,9.0,9.2))*not(between(t,16.0,16.2))"
            synthesize(media, path, expression, 25)
        else:
            synthesize(media, path, f"0.3*sin(2*PI*{hz}*t)", 2.5)
    return root


@pytest.fixture
def work(tmp_path, monkeypatch, template):
    monkeypatch.setenv("GAIAVFX_GPU_LOCK", str(tmp_path / "gpu.lock"))
    monkeypatch.setattr(engines, "free_vram_gb", lambda: None)
    # A build runs some fifty fake workers: notice each one's exit sooner than the executor's 0.2 s poll.
    monkeypatch.setattr(engines, "run", functools.partial(engines.run, poll=0.02))
    for name in ("VOICE_FAKE_CRASH", "VOICE_FAKE_HANG", "VOICE_FAKE_DROP", "VOICE_FAKE_EVERY"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "library"
    shutil.copytree(template[0], root)
    db = Database(root)
    yield db, MediaLibrary(db, MediaTools()), template[1]
    db.close()


def runner(work, stop=None):
    db, library, _ = work
    return OperationRunner(
        db, FakeComfy(), library, "http://fake", poll_seconds=0, engines=COMMANDS, stop=stop
    )


def read(db, operation_id):
    with db.transaction() as session:
        return OperationService().operation(session, operation_id)


def start(work, names=("first", "second"), folder="", attempt=1):
    db, library, ids = work
    with db.transaction() as session:
        return vb.start_voice_build(
            session,
            library,
            [ids[name] for name in names],
            library_scan=expressive.scan(str(folder)),
            attempt=attempt,
        )


def drain(work, run, operation_id, limit=3000):
    """Run until the runner has nothing to do; the build's views along the way."""
    db = work[0]
    views = [vb.build_view(read(db, operation_id))]
    for _ in range(limit):
        if not run.run_once():
            break
        views.append(vb.build_view(read(db, operation_id)))
    return views


def stages(views):
    seen = []
    for view in views:
        if view["stage"] and (not seen or seen[-1] != view["stage"]):
            seen.append(view["stage"])
    return seen


def by_stage(operation):
    found = {}
    for job in operation["jobs"]:
        found.setdefault(job["snapshot"]["stage"]["name"], []).append(job)
    return found


def units(jobs):
    return [job["snapshot"]["stage"]["unit"] for job in jobs]


def blob(work, artifact_id):
    db, library, _ = work
    with db.transaction() as session:
        return library.read_blob(session, artifact_id)


def output(job, key):
    [found] = [o for o in job["outputs"] if o.get("output_key") == key]
    return found


def strings(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, str):
        yield value


def wav_samples(path):
    with wave.open(str(path), "rb") as stream:
        return stream.getnframes(), stream.getframerate()


# --- A27 pure rules ------------------------------------------------------------------------------------------------


def test_the_seed_cut_follows_the_research_rule():
    # Research-like whisper segments: 1.5-3 s each, in two recordings of 40 s and 35 s.
    first = [(0.4 + 3.1 * k, 0.4 + 3.1 * k + 1.5 + (k % 3) * 0.5, f"s{k}") for k in range(12)]
    second = [(41.0 + 2.6 * k, 41.0 + 2.6 * k + 2.0, f"t{k}") for k in range(13)]
    cuts = vb.seed_cuts(
        first + second,
        min_s=4.0,
        max_s=10.0,
        pad_before=0.1,
        pad_after=0.15,
        total_s=60.0,
        bounds=[(0.0, 40.0), (40.3, 75.3)],
    )
    lengths = [b - a for a, b, _ in cuts]
    assert all(4.0 + 0.25 - 1e-9 <= length <= 10.0 + 0.25 for length in lengths), lengths
    assert cuts[0][0] == pytest.approx(0.3) and cuts[0][2] == "s0 s1"
    # No clip crosses a recording; the pads stay inside it.
    assert all(b <= 40.0 or a >= 40.3 for a, b, _ in cuts)
    assert sum(lengths) <= 60.0 + 1
    # Stops once the seed holds 60 s: a longer recording set gives no more than 61 s.
    many = [(0.2 + 2.5 * k, 2.2 + 2.5 * k, "w") for k in range(80)]
    long_cut = vb.seed_cuts(
        many, min_s=4.0, max_s=10.0, pad_before=0.1, pad_after=0.15, total_s=60.0
    )
    total = sum(b - a for a, b, _ in long_cut)
    assert 60.0 - 5 <= total <= 61.0 and long_cut[-1][1] < 200
    # A recording's unfinished accumulation is dropped.
    assert (
        vb.seed_cuts([(0.0, 3.0, "x")], min_s=4, max_s=10, pad_before=0, pad_after=0, total_s=60)
        == []
    )


def test_a_recording_of_four_to_ten_seconds_is_kept_whole():
    """The user (23 Oct, after the P5 trial): a recording that is already a 4-10 s clip is not cut again (the trial's
    seven p011 clips lost their tails: 48.7 s of seed from 58.5 s). Longer recordings keep the research cut."""
    seconds = [7.67, 6.31, 19.39, 6.25, 4.59, 6.01, 8.31]
    bounds, segments, at = [], [], 0.0
    for n, length in enumerate(seconds):
        bounds.append((at, at + length))
        # Two transcript segments per recording, a little inside its edges, as Whisper returns them.
        middle = at + length / 2
        segments += [
            (at + 0.2, middle - 0.1, f"r{n}a"),
            (middle + 0.1, at + length - 0.3, f"r{n}b"),
        ]
        at += length + 0.3
    cuts = vb.seed_cuts(
        segments, min_s=4.0, max_s=10.0, pad_before=0.1, pad_after=0.15, total_s=60.0, bounds=bounds
    )
    whole = [(a, b, text) for a, b, text in cuts if (a, b) in bounds]
    assert [text for _a, _b, text in whole] == [f"r{n}a r{n}b" for n in (0, 1, 3, 4, 5, 6)]
    # The 19.39 s recording is still cut by the research rule, inside its own bounds.
    long = [(a, b) for a, b, _ in cuts if bounds[2][0] <= a and b <= bounds[2][1]]
    assert long and all((a, b) != bounds[2] for a, b in long)
    assert sum(b - a for a, b, _ in whole) == pytest.approx(sum(seconds) - 19.39)
    # A recording shorter than 4 s is still not a seed clip on its own.
    assert (
        vb.seed_cuts(
            [(0.1, 2.9, "x")],
            min_s=4,
            max_s=10,
            pad_before=0,
            pad_after=0,
            total_s=60,
            bounds=[(0.0, 3.0)],
        )
        == []
    )


def test_maya_voices_follow_the_median_bands():
    texts = [vb.maya_voice(hz) for hz in (120, 165, 200, 300)]
    assert texts == [
        "Realistic adult male voice with a natural accent. Clear, low-pitched voice.",
        "Realistic adult voice with a natural accent. Clear, mid-pitched voice.",
        "Realistic adult female voice with a natural accent. Clear, mid-pitched voice.",
        "Realistic adult female voice with a natural accent. Clear, high-pitched voice.",
    ]


def test_library_speakers_prefer_speech_like_categories():
    clips = [
        {"id": "lib0001", "category": "scream", "speaker": "alex"},
        {"id": "lib0002", "category": "neutral", "speaker": "alex"},
        {"id": "lib0003", "category": "scream", "speaker": None},
        {"id": "lib0004", "category": "sob", "speaker": None},
        {"id": "lib0005", "category": "sob", "speaker": "mute"},
    ]
    items = [
        {"key": "lib0001", "median_hz": 400.0},
        {"key": "lib0002", "median_hz": 140.0},
        {"key": "lib0003", "median_hz": 300.0},
        {"key": "lib0004", "median_hz": 200.0},
        {"key": "lib0005", "median_hz": None},
    ]
    assert vb.library_speakers(items, clips) == {"alex": 140.0, "library": 250.0}


def test_chunks_ranges_and_training_ends_tile_exactly():
    items = list(range(129))
    pieces = vb.chunks(items, 60)
    assert [len(p) for p in pieces] == [60, 60, 9]
    assert [item for piece in pieces for item in piece] == items
    assert vb.chunks([], 5) == []
    assert vb.line_ranges(129, 60) == [(0, 60), (60, 120), (120, 129)]
    assert vb.train_ends(200, 25) == [25, 50, 75, 100, 125, 150, 175, 200]
    assert vb.train_ends(60, 25) == [25, 50, 60]
    with pytest.raises(ValueError):
        vb.chunks(items, 0)


def test_long_library_clips_are_cut_at_quiet_frames_into_short_pieces():
    rate, hop = 48000, 480
    samples = 25 * rate
    db = np.full(samples // hop, -20.0)
    db[900:920] = -120.0  # quiet at 9.0 s
    db[1600:1620] = -120.0  # quiet at 16.0 s
    spans = vb.piece_spans(db, samples, rate=rate, hop=hop, piece_seconds=12.0, window_seconds=2.0)
    # Three pieces (25 s needs three of at most 12 s), each cut inside a quiet stretch, at its frame nearest the split.
    assert spans == [(0, 900 * hop), (900 * hop, 1619 * hop), (1619 * hop, samples)]
    # Without quiet frames the pieces still tile the clip and none exceeds the limit.
    flat = vb.piece_spans(
        np.zeros(70 * rate // hop),
        70 * rate,
        rate=rate,
        hop=hop,
        piece_seconds=12.0,
        window_seconds=2.0,
    )
    assert flat[0][0] == 0 and flat[-1][1] == 70 * rate
    assert all(b - a <= 12 * rate for a, b in flat) and len(flat) == 6
    # Equally quiet frames: the cut nearest the even split wins.
    even = vb.piece_spans(
        np.zeros(18 * rate // hop),
        18 * rate,
        rate=rate,
        hop=hop,
        piece_seconds=12.0,
        window_seconds=2.0,
    )
    assert even == [(0, 9 * rate), (9 * rate, 18 * rate)]
    assert vb.piece_spans(
        db, 11 * rate, rate=rate, hop=hop, piece_seconds=12.0, window_seconds=2.0
    ) == [(0, 11 * rate)]


def test_candidate_rows_carry_ids_and_keep_designed_takes_apart_from_converted_recordings():
    lines = {
        "neutral00": {"id": "neutral00", "category": "neutral", "kind": "speech", "text": "Hello."},
        "scream00": {
            "id": "scream00",
            "category": "scream",
            "kind": "nonverbal",
            "text": "(screams)",
        },
    }
    rows = vb.candidate_rows(
        {
            "takes": [
                {"id": "fish_audio__neutral00_0", "generator": "fish_audio", "line": "neutral00"}
            ],
            "designed": [
                {"id": "maya1_vc__scream00_0", "generator": "maya1", "line": "scream00"}
                | {"take_id": "maya1__scream00_0"}
            ],
            "bootstrap": [
                {
                    "id": "bootstrap__lib0001",
                    "source_id": "lib0001",
                    "category": "sob",
                    "speaker": None,
                }
            ],
        },
        lines,
        ["scream", "sob"],
    )
    assert rows == [
        {
            "id": "fish_audio__neutral00_0",
            "category": "neutral",
            "kind": "speech",
            "generator": "fish_audio",
            "text": "Hello.",
        },
        {
            "id": "maya1_vc__scream00_0",
            "category": "scream",
            "kind": "nonverbal",
            "generator": "maya1",
            "text": "",
            "take_id": "maya1__scream00_0",
        },
        {
            "id": "bootstrap__lib0001",
            "category": "sob",
            "kind": "nonverbal",
            "generator": "bootstrap",
            "speaker": "library",
            "text": "",
            "source_id": "lib0001",
        },
    ]


def test_the_sentences_name_no_job_path_or_worker_line():
    job = {
        "id": "job-123",
        "ordinal": 0,
        "step": "rvc.train",
        "state": "failed",
        "error": "Engine task rvc.train failed (exit 1):\nTraceback ... C:/engines/rvc/model.bin",
        "snapshot": {"stage": {"name": "train", "unit": "train:75"}},
        "outputs": [],
    }
    operation = {
        "id": "op-1",
        "kind": vb.NAME,
        "state": "awaiting_review",
        "cancel_requested": False,
        "error": None,
        "request": {"library": {"clips": 0}},
        "jobs": [job],
    }
    view = vb.build_view(operation)
    assert view["state"] == "failed" and view["stage"] == "train"
    assert view["error"] == (
        "The voice build failed while training the voice. Retry; if it fails again, the app log has the details."
    )
    for error, sentence in (
        ("CUDA out of memory. Tried to allocate", vb.MEMORY),
        ("Engine analysis is not installed (C:/x/python.exe is missing).", vb.ENGINES),
    ):
        view = vb.build_view(operation | {"jobs": [job | {"error": error}]})
        assert view["error"] == sentence
    cancelled = vb.build_view(operation | {"state": "cancelled"})
    assert cancelled["error"] == vb.CANCELLED
    # An advance that keeps failing (a broken report) reads failed, not running forever.
    finished = job | {"state": "succeeded", "error": None}
    assert vb.build_view(operation | {"jobs": [finished]})["state"] == "running"
    stuck = vb.build_view(
        operation
        | {"jobs": [finished], "review": [{"decided_by": "advancer_error", "error": "C:/x.json"}]}
    )
    assert stuck["state"] == "failed" and stuck["error"].startswith(
        "The voice build failed while training the voice."
    )
    for sentence in (view["error"], cancelled["error"], vb.UNREADABLE, vb.LIBRARY_CHANGED):
        assert "job-123" not in sentence and "C:/" not in sentence and "Traceback" not in sentence


# --- A17-A20, A25 the whole build ----------------------------------------------------------------------------------


def test_the_build_runs_every_stage_with_hidden_material_and_id_manifests(
    work, expressive_folder, monkeypatch
):
    db, library, ids = work
    operation = start(work, folder=expressive_folder)
    assert operation["_created"]
    scan = expressive.scan(str(expressive_folder))
    assert operation["request"] == {
        "samples": [ids["first"], ids["second"]],
        "version": vb.BUILD_VERSION,
        "attempt": 1,
        "library": {
            "state": "ready",
            "digest": scan["digest"],
            "clips": 4,
            "categories": {"scream": 2, "sob": 2},
        },
    }
    # Every second line keeps the build's shape (two filter chunks, two designed conversions) at half the volume.
    monkeypatch.setenv("VOICE_FAKE_EVERY", "2")
    monkeypatch.setenv("VOICE_FAKE_DROP", "fish_audio__neutral0")
    views = drain(work, runner(work), operation["id"])
    settled = read(db, operation["id"])
    assert settled["state"] == "succeeded", [
        (j["step"], j["state"], j["error"]) for j in settled["jobs"]
    ]
    # A20: the stages in order, progress never ahead of the total.
    assert stages(views) == list(vb.STAGES)
    assert all(v["total"] >= v["done"] for v in views)
    assert [v["done"] for v in views] == sorted(v["done"] for v in views)
    assert views[-1] == {
        "state": "succeeded",
        "stage": None,
        "done": len(settled["jobs"]),
        "total": len(settled["jobs"]),
        "error": None,
    }
    # A17: the jobs, stage by stage.
    stage = by_stage(settled)
    assert list(stage) == list(vb.STAGES)
    assert [j["step"] for j in stage["samples"]] == [
        "voice_samples",
        "analysis.transcribe",
        "voice_library",
    ]
    assert [j["step"] for j in stage["seed"]] == ["analysis.speakers", "voice_seed"]
    material = stage["material"]
    assert [j["step"] for j in material] == ["tts.generate"] * 7 + [
        "seedvc.finetune",
        "analysis.speakers",
    ]
    tts = material[:7]
    generators = [j["snapshot"]["task_spec"]["generator"] for j in tts]
    assert generators == ["higgs_audio_tts", "voxcpm2", "omnivoice", "fish_audio"] + ["maya1"] * 3
    assert [j["snapshot"]["task_spec"].get("line_range") for j in tts[4:]] == [
        [0, 60],
        [60, 120],
        [120, 129],
    ]
    assert all(j["snapshot"]["engine"]["artifact_kind"] == "voice_material" for j in tts)
    assert {j["snapshot"]["task_spec"]["voice"] for j in tts} == {vb.maya_voice(190.0)}
    convert = stage["convert"]
    superres = [j for j in convert if j["step"] == "tts.superres"]
    lifted = {k[5:] for j in superres for k in j["inputs"]}
    assert lifted and {name.split("__")[0] for name in lifted} == {"higgs_audio_tts", "omnivoice"}
    assert all(len(j["inputs"]) <= 120 for j in superres)
    [bootstrap] = [j for j in convert if j["snapshot"]["stage"]["unit"].startswith("bootstrap:")]
    params = bootstrap["snapshot"]["task_spec"]
    assert set(params["clip_speakers"].values()) == {"alex", "library"}
    assert params["target_median_hz"] == 190.0
    assert params["source_median_hz"]["alex"] == pytest.approx(150, abs=2)
    assert params["source_median_hz"]["library"] == pytest.approx(220, abs=2)
    designed = [j for j in convert if j["snapshot"]["stage"]["unit"].startswith("designed:")]
    assert designed and all(j["snapshot"]["task_spec"]["pitch_shift"] == 0 for j in designed)
    assert {k.split("__")[0] for j in designed for k in j["inputs"] if k.startswith("clip:")} == {
        "clip:maya1"
    }
    # The first seed clip alone is the reference, as in the research.
    assert all(
        {"model", "reference:00"} <= set(j["inputs"])
        and [k for k in j["inputs"] if k.startswith("reference:")] == ["reference:00"]
        for j in [*designed, bootstrap]
    )
    assert all(len([k for k in j["inputs"] if k.startswith("clip:")]) <= 30 for j in designed)
    filters = stage["filter"]
    candidates = json.loads(blob(work, output(filters[0], "candidates:0")["artifact_id"]))
    rows = [
        row
        for n in range(len(filters) - 1)
        for row in json.loads(blob(work, output(filters[0], f"candidates:{n}")["artifact_id"]))
    ]
    assert [j["step"] for j in filters] == ["voice_candidates"] + ["analysis.filter"] * math.ceil(
        len(rows) / 200
    )
    assert len(candidates) == 200 and len(rows) > 200
    train = stage["train"]
    # Four chunks of 50 epochs (the user, 23 Oct), each resuming the one before.
    assert [j["step"] for j in train] == ["rvc.prepare"] + ["rvc.train"] * 4 + ["rvc.index"]
    assert [j["snapshot"]["task_spec"]["epochs"] for j in train[1:5]] == [50, 100, 150, 200]
    assert [j["depends_on"] for j in train[1:5]] == [j["ordinal"] for j in train[0:4]]
    assert train[5]["depends_on"] == train[0]["ordinal"]
    assert [j["step"] for j in stage["package"]] == ["voice_package"]

    # Hidden material: every take, lifted clip, converted clip, seed clip and library clip is a voice_material
    # artifact; the build has no candidate and the Library lists only what was imported.
    with db.transaction() as session:
        kinds = {
            library.artifact(session, o["artifact_id"])["kind"]
            for j in settled["jobs"]
            for o in j["outputs"]
            if str(o.get("output_key")).startswith(("take:", "clip:"))
        }
        assert kinds == {"voice_material"}
        assert not settled["candidates"]
        listed = library.browse(session, limit=100)
        assert listed["total"] == 4 and {item["id"] for item in listed["items"]} == set(
            ids.values()
        )
        assert session.query(AssetRecord).count() == 4
    library_job = stage["samples"][2]
    pieces = [
        o["output_key"] for o in library_job["outputs"] if o["output_key"].startswith("clip:")
    ]
    assert pieces == [
        "clip:lib0001",
        "clip:lib0002a",
        "clip:lib0002b",
        "clip:lib0002c",
        "clip:lib0003",
        "clip:lib0004",
    ]
    # The 25 s scream is cut in its two quiet stretches (9.0-9.2 s and 16.0-16.2 s) into pieces under 12 s.
    lengths = [output(library_job, f"clip:lib0002{x}")["info"]["seconds"] for x in "abc"]
    assert sum(lengths) == pytest.approx(25.0) and max(lengths) < 12
    assert 9.0 <= lengths[0] < 9.2 and 16.0 <= lengths[0] + lengths[1] < 16.2
    assert library_job["snapshot"]["step_spec"]["folder"] == str(expressive_folder)

    # A18: manifests carry ids, never paths; filter and prepare inputs name every clip and every source.
    roots = (str(expressive_folder), str(db.root), str(db.root).replace("\\", "/"))
    seed_job = stage["seed"][1]
    documents = [json.loads(blob(work, output(seed_job, "seed")["artifact_id"]))]
    documents += [
        json.loads(blob(work, output(filters[0], f"candidates:{n}")["artifact_id"]))
        for n in range(len(filters) - 1)
    ]
    documents += [json.loads(blob(work, output(j, "kept")["artifact_id"])) for j in filters[1:]]
    documents.append(json.loads(blob(work, output(library_job, "library")["artifact_id"])))
    for document in documents:
        for item in document["clips"] if isinstance(document, dict) else document:
            assert "path" not in item
        assert not any(root in text for text in strings(document) for root in roots)
    by_id = {row["id"]: row for row in rows}
    for job in filters[1:]:
        chunk = json.loads(
            blob(work, output(filters[0], job["inputs"]["candidates"]["output_key"])["artifact_id"])
        )
        for row in chunk:
            assert f"clip:{row['id']}" in job["inputs"]
            if row.get("source_id"):
                assert f"clip:{row['source_id']}" in job["inputs"]
        assert {"seed", *(f"clip:seed{n:02d}" for n in range(len(documents[0]["clips"])))} <= set(
            job["inputs"]
        )
    designed_rows = [row for row in rows if row["generator"] == "maya1"]
    assert designed_rows and all(
        "source_id" not in r and r["take_id"].startswith("maya1__") for r in designed_rows
    )
    assert {r["id"] for r in rows if r.get("source_id")} == {
        f"bootstrap__{name}"
        for name in ("lib0001", "lib0002a", "lib0002b", "lib0002c", "lib0003", "lib0004")
    }
    assert by_id["bootstrap__lib0003"]["speaker"] == "alex"
    assert by_id["bootstrap__lib0001"]["speaker"] == "library"
    assert by_id["fish_audio__neutral00_0"]["text"] and not by_id["bootstrap__lib0001"]["text"]
    prepare = train[0]
    kept = [
        row
        for job in filters[1:]
        for row in json.loads(blob(work, output(job, "kept")["artifact_id"]))
    ]
    dropped = {row["id"] for row in rows if "fish_audio__neutral0" in row["id"]}
    assert dropped and len(kept) == len(rows) - len(dropped)
    assert {f"clip:{row['id']}" for row in kept} <= set(prepare["inputs"])
    assert not {f"clip:{name}" for name in dropped} & set(prepare["inputs"])
    assert sorted(k for k in prepare["inputs"] if k.startswith("kept:")) == [
        f"kept:{n}" for n in range(len(filters) - 1)
    ]
    dataset = json.loads(blob(work, output(prepare, "dataset")["artifact_id"]))
    levelled = {f["generator"]: f["levelled"] for f in dataset["files"] if f.get("generator")}
    assert (
        levelled["maya1"] is True
        and levelled["bootstrap"] is False
        and levelled["fish_audio"] is True
    )

    # A19: the voice model manifest.
    found = vb.manifest_artifact(settled)
    package = stage["package"][0]
    assert found == {
        "artifact_id": output(package, "model")["artifact_id"],
        "sha256": output(package, "model")["sha256"],
    }
    calls = []
    original = library.read_blob

    def counting(session, artifact_id):
        calls.append(artifact_id)
        return original(session, artifact_id)

    library.read_blob = counting
    with db.transaction() as session:
        manifest = vb.voice_manifest(session, library, operation["id"])
        assert vb.voice_manifest(session, library, operation["id"]) is manifest
        assert calls == [found["artifact_id"]]
        weights = library.artifact(session, manifest["weights"]["artifact_id"])
        index = library.artifact(session, manifest["index"]["artifact_id"])
    library.read_blob = original
    assert (weights["kind"], weights["sha256"]) == ("voice_weights", manifest["weights"]["sha256"])
    assert (index["kind"], index["sha256"]) == ("voice_index", manifest["index"]["sha256"])
    assert weights["id"] == output(train[4], "model")["artifact_id"]  # the last of the four chunks
    assert manifest["target_median_hz"] == 190.0 and manifest["operation_id"] == operation["id"]
    assert manifest["recipe_fingerprint"] == voice_spec.fingerprint()
    assert manifest["library"]["digest"] == scan["digest"] and "folder" not in manifest["library"]
    assert manifest["samples"] == [
        {"id": ids[name], "sha256": settled["jobs"][0]["inputs"][ids[name]]["sha256"]}
        for name in ("first", "second")
    ]
    assert (manifest["filter"]["kept"], manifest["filter"]["of"]) == (
        len(rows) - len(dropped),
        len(rows),
    )
    assert manifest["filter"]["by_category"]["neutral"]["of"] - manifest["filter"]["by_category"][
        "neutral"
    ]["kept"] == len(dropped)
    assert manifest["epochs"] == 200 and manifest["dataset"]["clips"] == len(dataset["files"])
    assert not any(root in text for text in strings(manifest) for root in roots)
    with db.transaction() as session:
        latest, attempt = vb.latest(session, [s["sha256"] for s in manifest["samples"]])
    assert (latest["id"], attempt) == (operation["id"], 1)


# --- A21-A24, A26 retry, attempts, shutdown, cancel, no library ----------------------------------------------------


def test_a_stopped_training_chunk_is_retried_in_place(work, monkeypatch):
    db, library, _ = work
    monkeypatch.setenv("VOICE_FAKE_EVERY", "10")
    operation = start(work)
    crash = db.root / "staging" / "scratch" / operation["id"] / "train" / "crash"
    crash.parent.mkdir(parents=True)
    crash.write_text("150", encoding="utf-8")
    run = runner(work)
    drain(work, run, operation["id"])
    stopped = read(db, operation["id"])
    assert stopped["state"] == "awaiting_review"
    view = vb.build_view(stopped)
    assert view["state"] == "failed" and view["stage"] == "train"
    assert view["error"].startswith("The voice build failed while training the voice.")
    chunks = vb.stopped_jobs(stopped)
    assert [j["snapshot"]["task_spec"]["epochs"] for j in chunks] == [150, 200]
    assert "epoch 150 failed" in chunks[0]["error"]
    assert all(j["error"].startswith("Input job ") for j in chunks[1:])
    # The advancer leaves a stopped stage alone: nothing more happens without the human retry.
    assert run.run_once() is False and len(read(db, operation["id"])["jobs"]) == len(
        stopped["jobs"]
    )

    with db.transaction() as session:
        retried = vb.retry_stopped(session, stopped)
    replacements = retried["jobs"][len(stopped["jobs"]) :]
    assert [j["snapshot"]["task_spec"]["epochs"] for j in replacements] == [150, 200]
    assert replacements[0]["depends_on"] == chunks[0]["depends_on"]  # the finished chunk 100
    assert [j["depends_on"] for j in replacements[1:]] == [j["ordinal"] for j in replacements[:-1]]
    assert [j["snapshot"]["retry_of"] for j in replacements] == [j["id"] for j in chunks]
    assert retried["review"][-1] == {"decided_by": vb.NAME, "stage": "train", "retry": True}
    assert (
        vb.build_view(retried)["state"] == "running"
        and vb.build_view(retried)["total"] == view["total"]
    )
    drain(work, runner(work), operation["id"])
    settled = read(db, operation["id"])
    # The superseded chunks stay failed but no longer decide the state: the build settles succeeded.
    assert settled["state"] == "succeeded" and vb.succeeded(settled)
    assert vb.build_view(settled)["state"] == "succeeded" and vb.manifest_artifact(settled)
    assert not vb.stopped_jobs(settled)
    state = json.loads((crash.parent / "state.json").read_text(encoding="utf-8"))
    assert state["chunks"] == [50, 100, 150, 200]  # 50 and 100 committed once: the scratch held
    with db.transaction() as session:
        assert vb.voice_manifest(session, library, operation["id"])["epochs"] == 200
        with pytest.raises(ApplicationError) as refused:
            vb.retry_stopped(session, settled)
    assert refused.value.status == 409


def test_a_build_that_fails_its_first_stage_settles_failed_and_attempts_again(work):
    db, library, ids = work
    with db.transaction() as session:
        library.path(session, ids["first"]).unlink()  # the recording can no longer be read
    operation = start(work)
    drain(work, runner(work), operation["id"])
    failed = read(db, operation["id"])
    assert failed["state"] == "failed"
    assert [j["state"] for j in failed["jobs"]] == ["failed"] * 3
    view = vb.build_view(failed)
    assert (view["state"], view["stage"], view["error"]) == ("failed", "samples", vb.UNREADABLE)
    with db.transaction() as session:
        shas = [library.get(session, ids[name])["sha256"] for name in ("first", "second")]
        assert vb.latest(session, shas)[0]["id"] == operation["id"]
        assert vb.latest(session, shas)[1] == 1
        with pytest.raises(ApplicationError):
            vb.retry_stopped(session, failed)  # a settled build is not retried in place
    again = start(work, attempt=2)
    assert again["_created"] and again["request_key"] != failed["request_key"]
    assert again["request_key"] == vb.key(shas, 2)
    with db.transaction() as session:
        latest, attempt = vb.latest(session, shas)
    assert (latest["id"], attempt) == (again["id"], 2)


def test_a_stopped_runner_relaunches_the_job_in_its_work_directory(work, monkeypatch):
    db = work[0]
    monkeypatch.setenv("VOICE_FAKE_EVERY", "10")
    operations_dir = db.root / "staging" / "operations"

    def stop_when_hanging(stop):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and not stop.is_set():
            if operations_dir.is_dir() and any(operations_dir.glob("*/hanging")):
                for marker in operations_dir.glob("*/hanging"):
                    marker.unlink()
                stop.set()
                return
            time.sleep(0.05)

    def stopped_run(step):
        stop = threading.Event()
        watcher = threading.Thread(target=stop_when_hanging, args=(stop,))
        watcher.start()
        drain(work, runner(work, stop=stop), operation["id"])
        watcher.join()
        assert stop.is_set()
        [job] = [j for j in read(db, operation["id"])["jobs"] if j["state"] == "running"]
        assert job["step"] == step
        return job

    operation = start(work)
    monkeypatch.setenv("VOICE_FAKE_HANG", "tts.generate:fish_audio")
    fish = stopped_run("tts.generate")
    assert fish["snapshot"]["task_spec"]["generator"] == "fish_audio"
    monkeypatch.setenv("VOICE_FAKE_HANG", "seedvc.finetune")
    finetune = stopped_run("seedvc.finetune")
    monkeypatch.delenv("VOICE_FAKE_HANG")
    drain(work, runner(work), operation["id"])
    settled = read(db, operation["id"])
    assert settled["state"] == "succeeded"
    jobs = {j["id"]: j for j in settled["jobs"]}
    log = (db.root / "logs" / "engines" / f"{fish['id']}.log").read_text(encoding="utf-8")
    assert log.count("--- launch") == 2 and "present fish_audio__neutral00_0" in log
    assert jobs[fish["id"]]["state"] == "succeeded"
    assert output(jobs[finetune["id"]], "model")["info"]["reused"] is True
    assert (db.root / "logs" / "engines" / f"{finetune['id']}.log").read_text(
        encoding="utf-8"
    ).count("--- launch") == 2


def test_cancelling_during_the_material_stage_settles_cancelled(work, monkeypatch):
    db, library, _ = work
    monkeypatch.setenv("VOICE_FAKE_EVERY", "10")
    monkeypatch.setenv("VOICE_FAKE_HANG", "tts.generate:higgs_audio_tts")
    operation = start(work)
    operations_dir = db.root / "staging" / "operations"

    def cancel_when_hanging():
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if operations_dir.is_dir() and any(operations_dir.glob("*/hanging")):
                with db.transaction() as session:
                    vb.cancel_build(session, shas)
                return
            time.sleep(0.05)

    with db.transaction() as session:
        shas = [library.get(session, i)["sha256"] for i in operation["request"]["samples"]]
    watcher = threading.Thread(target=cancel_when_hanging)
    watcher.start()
    drain(work, runner(work), operation["id"])
    watcher.join()
    cancelled = read(db, operation["id"])
    assert cancelled["state"] == "cancelled", [(j["step"], j["state"]) for j in cancelled["jobs"]]
    view = vb.build_view(cancelled)
    assert (view["state"], view["stage"], view["error"]) == ("failed", "material", vb.CANCELLED)
    with db.transaction() as session:
        latest, attempt = vb.latest(session, shas)
        assert (latest["id"], attempt) == (operation["id"], 1)
        vb.cancel_build(session, shas)  # a settled build stays as it is
    assert read(db, operation["id"])["state"] == "cancelled"
    again = start(work, attempt=2)
    assert again["_created"] and again["request"]["attempt"] == 2
    with db.transaction() as session:
        assert vb.latest(session, shas)[0]["id"] == again["id"]


def test_without_an_expressive_folder_the_build_uses_the_generators_only(work, monkeypatch):
    db, library, _ = work
    monkeypatch.setenv("VOICE_FAKE_EVERY", "10")
    operation = start(work, folder="")
    assert operation["request"]["library"] == {
        "state": "not_set",
        "digest": "",
        "clips": 0,
        "categories": {},
    }
    drain(work, runner(work), operation["id"])
    settled = read(db, operation["id"])
    assert settled["state"] == "succeeded"
    stage = by_stage(settled)
    library_job = stage["samples"][2]
    assert json.loads(blob(work, output(library_job, "library")["artifact_id"])) == {
        "clips": [],
        "skipped": [],
    }
    assert units(stage["material"])[-1] == "finetune"
    assert not any(u.startswith("bootstrap:") for u in units(stage["convert"]))
    assert any(u.startswith("designed:") for u in units(stage["convert"]))
    rows = json.loads(blob(work, output(stage["filter"][0], "candidates:0")["artifact_id"]))
    assert {row["generator"] for row in rows} == {
        "higgs_audio_tts",
        "voxcpm2",
        "omnivoice",
        "fish_audio",
        "maya1",
    }
    with db.transaction() as session:
        assert vb.voice_manifest(session, library, operation["id"])["library"]["digest"] == ""


def test_the_build_is_shared_by_content_and_refuses_other_recordings(work, expressive_folder):
    first = start(work, folder=expressive_folder)
    again = start(work, folder=expressive_folder)
    assert first["_created"] and not again["_created"] and again["id"] == first["id"]
    # The same build is found whatever the library holds now: the key is the samples, the version and the attempt.
    assert start(work, folder="")["id"] == first["id"]
    reordered = start(work, names=("second", "first"), folder=expressive_folder)
    assert reordered["_created"] and reordered["request_key"] != first["request_key"]
    text = json.dumps(first["request"])
    assert str(expressive_folder) not in text and "screams" not in text and "a.wav" not in text
    for names, message in (
        (("short",), "Choose between 15 and 60 seconds of recordings; these are 10 s."),
        (
            ("first", "second", "short", "first"),
            "Choose between 15 and 60 seconds of recordings; these are 70 s.",
        ),
        (("first", "picture"), "Choose Library audio recordings for the voice."),
    ):
        with pytest.raises(ApplicationError) as refused:
            start(work, names=names)
        assert refused.value.status == 422 and str(refused.value) == message


# --- the audition --------------------------------------------------------------------------------------------------


def test_the_audition_cuts_converts_and_joins_a_reel(work, tmp_path):
    db, library, _ = work
    rate = 48000
    stem_path = tmp_path / "dialogue.wav"
    values = np.zeros(10 * rate, dtype=np.float32)
    tones = [
        (48_000, 96_000, 300),
        (150_000, 200_000, 500),
        (260_000, 300_000, 700),
        (400_000, 470_000, 900),
    ]
    for a, b, hz in tones:
        values[a:b] = 0.3 * np.sin(2 * np.pi * hz * np.arange(b - a) / rate)
    with wave.open(str(stem_path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes((values * 32767).astype("<i2").tobytes())
    weights_path, index_path = tmp_path / "voice.pth", tmp_path / "voice.index"
    weights_path.write_bytes(b"weights")
    index_path.write_bytes(b"index")
    with db.transaction() as session:
        refs = {
            name: library.register_artifact(session, library.prepare_blob(path, kind, {}))
            for name, path, kind in (
                ("stem", stem_path, "audio_stem"),
                ("weights", weights_path, "voice_weights"),
                ("index", index_path, "voice_index"),
            )
        }
        lines = [
            {
                "key": f"line{n}",
                "start_sample": a - 4800,
                "end_sample": b + 4800,
                "whisper_spans": [[0.1, 0.2]] if n == 2 else [],
            }
            for n, (a, b, _) in enumerate(tones)
        ]
        operation = vb.start_voice_audition(
            session,
            library,
            request_key="audition-test-1",
            stem={"artifact_id": refs["stem"]["id"], "sha256": refs["stem"]["sha256"]},
            lines=lines,
            weights={"artifact_id": refs["weights"]["id"], "sha256": refs["weights"]["sha256"]},
            index={"artifact_id": refs["index"]["id"], "sha256": refs["index"]["sha256"]},
            source_median_hz=120.0,
            target_median_hz=190.0,
            sample_rate=rate,
            voice_model="manifest-artifact",
            attempt=1,
        )
    assert operation["_created"] and operation["request"]["lines"] == [
        "line0",
        "line1",
        "line2",
        "line3",
    ]
    assert vb.audition_view(read(db, operation["id"]))["state"] == "running"
    drain(work, runner(work), operation["id"])
    settled = read(db, operation["id"])
    assert settled["state"] == "succeeded", [
        (j["step"], j["state"], j["error"]) for j in settled["jobs"]
    ]
    cut, convert, reel_job = settled["jobs"]
    assert convert["snapshot"]["task_spec"]["source_median_hz"] == 120.0
    assert convert["snapshot"]["task_spec"]["target_median_hz"] == 190.0
    assert convert["inputs"]["model"]["artifact_id"] == refs["weights"]["id"]
    assert output(convert, "converted:line2")["info"]["spans"] == [[0.1, 0.2]]
    with db.transaction() as session:
        for line in lines:
            clip = output(cut, f"clip:{line['key']}")
            artifact = library.artifact(session, clip["artifact_id"])
            assert artifact["kind"] == "voice_material"
            frames, found_rate = wav_samples(library.artifact_path(session, clip["artifact_id"]))
            assert (frames, found_rate) == (line["end_sample"] - line["start_sample"], rate)
            converted = output(convert, f"converted:{line['key']}")
            assert library.artifact(session, converted["artifact_id"])["kind"] == "voice_material"
        [candidate] = settled["candidates"]
        assert candidate["output_key"] == "reel" and candidate["kind"] == "audio"
        assert vb.audition_reel(settled) == candidate
        reel = library.get(session, candidate["asset_id"])
        assert reel["name"] == "audition.wav"
        frames, _ = wav_samples(library.path(session, candidate["asset_id"]))
        report = json.loads(
            library.read_blob(session, output(reel_job, "reel_report")["artifact_id"])
        )
    expected = sum(2 * (line["end_sample"] - line["start_sample"]) / rate + 0.9 for line in lines)
    assert abs(frames / rate - expected) <= 0.001
    assert [row["key"] for row in report["lines"]] == ["line0", "line1", "line2", "line3"]
    assert all(
        row["gain_db"] == pytest.approx(20 * math.log10(1 / 0.9), abs=0.05)
        for row in report["lines"]
    )
    assert vb.audition_view(settled) == {
        "state": "succeeded",
        "stage": "audition",
        "done": 3,
        "total": 3,
        "error": None,
    }
    with db.transaction() as session:
        repeat = vb.start_voice_audition(
            session,
            library,
            request_key="audition-test-1",
            stem={"artifact_id": refs["stem"]["id"], "sha256": refs["stem"]["sha256"]},
            lines=lines,
            weights={"artifact_id": refs["weights"]["id"], "sha256": refs["weights"]["sha256"]},
            index={"artifact_id": refs["index"]["id"], "sha256": refs["index"]["sha256"]},
            source_median_hz=120.0,
            target_median_hz=190.0,
            sample_rate=rate,
        )
    assert repeat["id"] == operation["id"] and not repeat["_created"]
