"""ADR 0016 §2: the shared dialogue analysis through the durable runner (A6–A8).

Real FFmpeg for the extraction and the envelope, the fake analysis engine (`fixtures/dialogue_fake_worker.py`) for
the four engine tasks, a real Library and fake ComfyUI. No model, no GPU.
"""

import json
import shutil
import sys
import threading
import time
from collections import Counter
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
from fake_comfy import FakeComfy

from gaiavfx.application import dialogue_analysis as da
from gaiavfx.application.dialogue_steps import dialogue_audio, dialogue_envelope
from gaiavfx.application.engine_jobs import EngineCommand
from gaiavfx.application.operation_steps import LocalContext
from gaiavfx.application.operations import OperationRunner
from gaiavfx.infrastructure import engines
from gaiavfx.infrastructure.database import Database
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.application import LibraryService, MediaLibrary
from gaiavfx.shared.errors import ApplicationError

WORKER = Path(__file__).parent / "fixtures" / "dialogue_fake_worker.py"
TASKS = ("analysis.separate", "analysis.transcribe", "analysis.speakers", "analysis.tag")
CLICK = "if(between(t,0.5,0.501),0.9,0)"


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    root = tmp_path_factory.mktemp("dialogue-template") / "library"
    LibraryService(root, Path(__file__).parents[1] / "migrations").create("Dialogue")
    return root


@pytest.fixture
def work(tmp_path, monkeypatch, template):
    monkeypatch.setenv("GAIAVFX_GPU_LOCK", str(tmp_path / "gpu.lock"))
    monkeypatch.setattr(engines, "free_vram_gb", lambda: None)
    root = tmp_path / "library"
    shutil.copytree(template, root)
    db = Database(root)
    yield db, MediaLibrary(db, MediaTools())
    db.close()


def film(media, path, *, audio, itsoffset=None, channels=1):
    """A 2 s, 25 fps test picture with a lavfi audio track; `itsoffset` delays the "audio" or "video" input."""
    picture = ["-f", "lavfi", "-i", "testsrc=size=64x48:rate=25:duration=2"]
    expression = "|".join([audio] * channels)
    sound = ["-f", "lavfi", "-i", f"aevalsrc='{expression}':s=48000:d=2"]
    if itsoffset == "audio":
        sound = ["-itsoffset", "0.2", *sound]
    elif itsoffset == "video":
        picture = ["-itsoffset", "0.2", *picture]
    media.run(
        media.ffmpeg,
        [
            "-v",
            "error",
            "-y",
            *picture,
            *sound,
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "ffv1",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
    )
    return path


def context(library, staging, paths, snapshot):
    return LocalContext(library, staging, {}, paths, snapshot)


def samples(media, path, channels):
    raw = path.with_suffix(".check.f32")
    media.decode_audio(path, raw, mono=False)
    return np.fromfile(raw, dtype="<f4").reshape(-1, channels)


def stream(media, path):
    return json.loads(
        media.run(
            media.ffprobe,
            ["-v", "error", "-show_streams", "-select_streams", "a:0", "-of", "json", str(path)],
        )
    )["streams"][0]


# --- A6 extraction --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("itsoffset", "channels", "click"),
    [("audio", 1, 33_600), ("video", 2, 14_400), (None, 2, 24_000)],
)
def test_the_mix_starts_at_video_frame_zero(work, tmp_path, itsoffset, channels, click):
    _, library = work
    media = library.media
    source = film(media, tmp_path / "film.mkv", audio=CLICK, itsoffset=itsoffset, channels=channels)
    probe = media.probe(source)
    if itsoffset:
        assert Fraction(probe["audio_start_time"]) - Fraction(probe["video_start_time"]) == (
            Fraction(1, 5) if itsoffset == "audio" else Fraction(-1, 5)
        )
    staging = tmp_path / "staging"
    snapshot = {"step_spec": {"source_asset": "film", "sample_rate": 48000}}
    outputs = dialogue_audio(context(library, staging, {"film": source}, snapshot))
    [(key, path, kind, info)] = outputs
    assert (key, kind) == ("mix", "audio_stem")
    assert info["offset_samples"] == {"audio": 9600, "video": -9600, None: 0}[itsoffset]
    assert info["channels"] == channels and info["sample_rate"] == 48000
    described = stream(media, path)
    assert described["codec_name"] == "pcm_f32le"
    assert int(described["sample_rate"]) == 48000 and described["channels"] == channels
    values = samples(media, path, channels)
    assert info["samples"] == len(values)
    assert abs(int(np.argmax(np.abs(values[:, 0]))) - click) <= 48
    if channels == 2:
        assert abs(float(values[click, 1]) - 0.9) < 1e-3

    # Resumable: a finished mix is returned again without running FFmpeg.
    def no_ffmpeg(*args, **kwargs):
        raise AssertionError("the finished mix was extracted again")

    original = media.extract_audio
    media.extract_audio = no_ffmpeg
    try:
        again = dialogue_audio(context(library, staging, {"film": source}, snapshot))
    finally:
        media.extract_audio = original
    assert again[0][3] == info


def test_more_than_two_channels_are_refused(work, tmp_path):
    _, library = work
    source = film(library.media, tmp_path / "surround.mkv", audio="0", channels=6)
    snapshot = {"step_spec": {"source_asset": "film", "sample_rate": 48000}}
    with pytest.raises(ApplicationError) as refused:
        dialogue_audio(context(library, tmp_path / "staging", {"film": source}, snapshot))
    assert str(refused.value) == (
        "This film's audio has 6 channels; dialogue analysis supports mono and stereo for now."
    )
    assert da.gap_for({"has_audio": False}) == {
        "code": "no_audio",
        "message": "This film has no audio track.",
    }
    assert da.gap_for({"has_audio": True, "audio_channels": 2}) is None


# --- A7 envelope ----------------------------------------------------------------------------------------------------


def test_the_envelope_reads_ten_millisecond_levels(work, tmp_path):
    _, library = work
    media = library.media
    rng = np.random.default_rng(5)
    noise = rng.normal(0.0, 0.1, 48000).astype(np.float32)
    noise[24000:28800] = 0.0  # 100 ms of digital silence, frames 50-59
    # The same noise in both channels: the envelope is the channel average, which keeps its level.
    raw = tmp_path / "noise.f32"
    np.stack([noise, noise], axis=1).astype("<f4").tofile(raw)
    wav = tmp_path / "noise.wav"
    media.run(
        media.ffmpeg,
        ["-v", "error", "-y", "-f", "f32le", "-ar", "48000", "-ac", "2", "-i", str(raw)]
        + ["-c:a", "pcm_f32le", str(wav)],
    )
    snapshot = {"step_spec": {"sample_rate": 48000, "hop": 480}}
    [(key, path, kind, info)] = dialogue_envelope(
        context(library, tmp_path / "staging", {"dialogue": wav}, snapshot)
    )
    assert (key, kind) == ("envelope", "analysis_report") and info["frames"] == 100
    with np.load(path) as data:
        db, hop = data["db"], int(data["hop"])
        assert int(data["rate"]) == 48000 and float(data["peak_db"]) == pytest.approx(db.max())
    assert hop == 480 and db.shape == (100,) and db.dtype == np.float32
    assert (db[50:60] <= -100).all()
    rest = np.concatenate([db[:50], db[60:]])
    assert (np.abs(rest + 20) <= 1).all(), rest
    assert not (tmp_path / "staging" / "dialogue.f32").exists()


# --- A8 the operation -----------------------------------------------------------------------------------------------


def imported(work, tmp_path, name="film.mkv"):
    db, library = work
    source = film(library.media, tmp_path / name, audio="0.3*sin(2*PI*220*t)", channels=2)
    prepared = library.prepare(source, source.name)
    with db.transaction() as session:
        return library.register(session, prepared, {"type": "import"})["id"]


def runner(work, *flags_for):
    db, library = work
    flags = dict(flags_for)
    commands = {
        step: EngineCommand((sys.executable, str(WORKER), *flags.get(step, ()))) for step in TASKS
    }
    return OperationRunner(
        db, FakeComfy(), library, "http://fake", poll_seconds=0, engines=commands
    )


def start(work, asset_id, **kwargs):
    db, library = work
    with db.transaction() as session:
        return da.start_dialogue_analysis(session, library, asset_id, **kwargs)


def read(db, operation_id):
    with db.transaction() as session:
        return OperationService().operation(session, operation_id)


def drain(work, run, operation_id):
    db, _ = work
    stages = []
    for _ in range(60):
        view = da.dialogue_view(read(db, operation_id))
        if not stages or stages[-1] != view["stage"]:
            stages.append(view["stage"])
        if not run.run_once():
            break
    view = da.dialogue_view(read(db, operation_id))
    if stages[-1] != view["stage"]:
        stages.append(view["stage"])
    return stages


def test_the_analysis_runs_both_stages_and_is_shared(work, tmp_path):
    db, library = work
    asset_id = imported(work, tmp_path)
    operation = start(work, asset_id)
    assert operation["_created"] and operation["request"] == {
        "source_asset": asset_id,
        "version": 1,
        "attempt": 1,
    }
    assert start(work, asset_id)["id"] == operation["id"]
    assert not start(work, asset_id)["_created"]
    assert da.dialogue_view(read(db, operation["id"]))["state"] == "running"
    stages = drain(work, runner(work), operation["id"])
    assert stages == ["audio", "separate", "transcribe", "speakers", "done"]
    settled = read(db, operation["id"])
    assert settled["state"] == "succeeded", settled
    assert [j["step"] for j in settled["jobs"]] == list(da.STEPS)
    assert settled["budget_reserved"] == 0 and not settled["candidates"]
    with db.transaction() as session:
        kinds = Counter(
            library.artifact(session, o["artifact_id"])["kind"]
            for j in settled["jobs"]
            for o in j["outputs"]
        )
        assert kinds == {"audio_stem": 3, "analysis_report": 3, "speaker_embeddings": 1}
        report = da.dialogue_report(session, library, operation["id"])
        asset = library.get(session, asset_id)
        assert da.latest(session, asset) == (read(db, operation["id"]) | {}, 1)
    assert report["sample_rate"] == 48000 and report["channels"] == 2
    assert report["offset_samples"] == 0 and report["samples"] == 96000
    assert [s["key"] for s in report["segments"]] == ["00000-200", "00001-1000", "00002-1700"]
    assert report["segments"][0]["words"][1] == {"start": 0.5, "end": 0.8, "word": "there."}
    assert len(report["speakers"]["00000-200"]["ecapa"]) == 192
    assert report["speakers"]["00002-1700"] == {"ecapa": None, "median_hz": None}
    assert report["whisper_spans"] == [[1.0, 1.5]]
    assert report["envelope"]["hop"] == 480 and len(report["envelope"]["db"]) == 200
    view = da.dialogue_view(settled)
    assert view == {"state": "succeeded", "stage": "done", "done": 6, "total": 6, "error": None}
    # Stage 2 reads frozen artifacts of stage 1, never a job's live output.
    speakers = settled["jobs"][3]
    assert {v["source"] for v in speakers["inputs"].values()} == {"artifact"}
    # A succeeded analysis is never run again, even on a retry.
    assert start(work, asset_id, retry_failed=True)["id"] == operation["id"]


def test_a_failed_attempt_retries_as_the_next_attempt(work, tmp_path):
    db, library = work
    asset_id = imported(work, tmp_path)
    operation = start(work, asset_id)
    drain(work, runner(work, ("analysis.transcribe", ("--crash", "transcribe"))), operation["id"])
    failed = read(db, operation["id"])
    assert failed["state"] in {"failed", "partial"}
    view = da.dialogue_view(failed)
    assert view["state"] == "failed" and view["stage"] == "transcribe"
    assert view["error"].startswith("The dialogue analysis failed while transcribing.")
    assert "C:/" not in view["error"] and failed["jobs"][2]["id"] not in view["error"]
    assert start(work, asset_id)["id"] == operation["id"]  # no automatic retry
    retry = start(work, asset_id, retry_failed=True)
    assert retry["id"] != operation["id"] and retry["request"]["attempt"] == 2
    assert retry["request_key"] != failed["request_key"]
    with db.transaction() as session:
        assert da.latest(session, library.get(session, asset_id))[0]["id"] == retry["id"]
    drain(work, runner(work), retry["id"])
    assert read(db, retry["id"])["state"] == "succeeded"
    assert start(work, asset_id, retry_failed=True)["id"] == retry["id"]


def test_cancelling_during_separation_reads_as_a_failed_step(work, tmp_path):
    db, _ = work
    asset_id = imported(work, tmp_path)
    operation = start(work, asset_id)
    run = runner(work, ("analysis.separate", ("--slow", "separate")))
    worker = threading.Thread(target=drain, args=(work, run, operation["id"]))
    worker.start()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        jobs = read(db, operation["id"])["jobs"]
        if jobs[1]["state"] == "running":
            break
        time.sleep(0.05)
    with db.transaction() as session:
        OperationService().request_cancel(session, operation["id"])
    worker.join(90)
    assert not worker.is_alive()
    cancelled = read(db, operation["id"])
    assert cancelled["state"] == "cancelled", cancelled
    view = da.dialogue_view(cancelled)
    assert view["state"] == "failed" and view["stage"] == "separate"
    assert view["error"] == "The dialogue analysis was cancelled. Retry to run it again."
    assert all(j["id"] not in view["error"] for j in cancelled["jobs"])


def test_the_operation_refuses_films_it_cannot_analyse(work, tmp_path):
    db, library = work
    asset_id = imported(work, tmp_path)
    with db.transaction() as session:
        from gaiavfx.modules.library.assets import AssetRecord

        session.get(AssetRecord, asset_id).info = {
            **library.get(session, asset_id)["info"],
            "has_audio": False,
        }
    with pytest.raises(ApplicationError) as refused:
        start(work, asset_id)
    assert refused.value.status == 422 and str(refused.value) == "This film has no audio track."
    with db.transaction() as session:
        assert OperationService().recent(session, kind=da.NAME) == []
