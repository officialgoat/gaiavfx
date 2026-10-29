import hashlib
import json
import wave
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
from fake_comfy import ReplacementComfy
from PIL import Image
from test_replacement_contract import mug_facts
from test_replacement_runner import drain, read

from gaiavfx.application.operation_steps import LocalContext
from gaiavfx.application.operations import OperationRunner
from gaiavfx.application.replace_character import (
    people_for_asset,
    replacement_plan,
    start_people_analysis,
    start_replacement,
)
from gaiavfx.application.replacement_steps import (
    _pipe,
    analysis_extract,
    decode_rgb,
    lossless_video,
)
from gaiavfx.infrastructure.database import Database
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.integrations.minimax_h3.replacement import compile_replacement
from gaiavfx.modules.direction.application import default_replacement_policy
from gaiavfx.modules.direction.replacement import (
    Arm,
    Box,
    ReplacementFacts,
    from_record,
    to_record,
)
from gaiavfx.modules.library.application import LibraryService, MediaLibrary


def make_work(tmp_path, fps=Fraction(24), count=190):
    LibraryService(tmp_path, Path(__file__).parents[1] / "migrations").create("Windows")
    db = Database(tmp_path)
    library = MediaLibrary(db, MediaTools())
    image = np.full((128, 192, 3), (30, 80, 110), np.uint8)
    frames = np.repeat(image[None], count, axis=0)
    audio = tmp_path / "audio.wav"
    library.media.run(
        library.media.ffmpeg,
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=10",
            "-c:a",
            "pcm_f32le",
            str(audio),
        ],
    )
    source = lossless_video(library.media, frames, tmp_path / "source.mkv", fps, audio)
    target = tmp_path / "target.png"
    Image.new("RGB", (32, 48), (200, 40, 60)).save(target)
    assets = []
    for path in (source, target):
        prepared = library.prepare(path, path.name)
        with db.transaction() as session:
            assets.append(library.register(session, prepared, {"type": "import"})["id"])
    runner = OperationRunner(
        db,
        ReplacementComfy(tmp_path, library.media, people=1),
        library,
        "http://fake",
        poll_seconds=0,
    )
    return db, library, runner, assets, frames


@pytest.mark.parametrize("fps", [Fraction(24), Fraction(25), Fraction(24000, 1001)])
def test_seek_decode_is_bit_equal_to_sequential_decode(tmp_path, fps):
    media = MediaTools()
    frames = np.random.default_rng(3).integers(0, 256, (190, 32, 48, 3), dtype=np.uint8)
    source = lossless_video(media, frames, tmp_path / "source.mkv", fps)
    # Also cover keyframe seeking and YUV conversion in a lossy source.
    encoded = tmp_path / "source.mp4"
    media.run(
        media.ffmpeg,
        [
            "-y",
            "-i",
            str(source),
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-g",
            "48",
            "-pix_fmt",
            "yuv420p",
            str(encoded),
        ],
    )
    offset = tmp_path / "offset.mkv"
    media.run(
        media.ffmpeg,
        ["-y", "-i", str(source), "-c", "copy", "-output_ts_offset", "10", str(offset)],
    )
    for path in (source, encoded, offset):
        sequential = decode_rgb(media, path)
        for start, end in ((0, 5), (71, 144), (126, 180)):
            assert np.array_equal(decode_rgb(media, path, start, end), sequential[start:end])
            assert media.constant_frame_rate(path, start, end, fps)


@pytest.mark.parametrize("fps", [Fraction(24), Fraction(25), Fraction(24000, 1001)])
@pytest.mark.parametrize("container", ["mkv", "mp4"])
def test_audio_leading_video_and_b_frame_delay_keep_frame_and_audio_origin(
    tmp_path, fps, container
):
    media = MediaTools()
    frames = np.random.default_rng(8).integers(0, 256, (190, 32, 48, 3), dtype=np.uint8)
    video = lossless_video(media, frames, tmp_path / "video.mkv", fps)
    source = tmp_path / f"delayed.{container}"
    codecs = (
        ["-c:v", "copy", "-c:a", "pcm_s16le"]
        if container == "mkv"
        else [
            "-c:v",
            "libx264",
            "-bf",
            "2",
            "-g",
            "48",
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-c:a",
            "aac",
            "-avoid_negative_ts",
            "make_zero",
        ]
    )
    media.run(
        media.ffmpeg,
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=10",
            "-itsoffset",
            "0.03",
            "-i",
            str(video),
            "-map",
            "1:v:0",
            "-map",
            "0:a:0",
            *codecs,
            str(source),
        ],
    )
    info = media.probe(source)
    origin = Fraction(info["video_start_time"]) - Fraction(info["format_start_time"])
    assert origin > 0
    raw = _pipe(
        media,
        [
            "-i",
            str(source),
            *(
                ["-vf", "scale=in_color_matrix=bt709:in_range=limited:out_range=full"]
                if container == "mp4"
                else []
            ),
            "-an",
            "-fps_mode",
            "passthrough",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
    )
    sequential = np.frombuffer(raw, np.uint8).reshape(190, 32, 48, 3)
    for start, end in ((0, 1), (0, 73), (71, 144), (189, 190)):
        assert np.array_equal(decode_rgb(media, source, start, end), sequential[start:end])
        assert not media.probe(source, frame_range=(start, end))["variable_frame_rate"]
    composite = lossless_video(
        media, sequential[71:144], tmp_path / "composite.mkv", fps, source, 71, 73
    )
    expected = _pipe(
        media,
        [
            "-i",
            str(source),
            "-vn",
            "-af",
            f"atrim=start={float(origin + 71 / fps):.12f}:end={float(origin + 144 / fps):.12f},asetpts=PTS-STARTPTS",
            "-f",
            "f32le",
            "pipe:1",
        ],
    )
    actual = _pipe(media, ["-i", str(composite), "-vn", "-f", "f32le", "pipe:1"])
    assert actual == expected
    # Previews use the same picture-relative audio origin and explicit BT.709.
    from types import SimpleNamespace

    from gaiavfx.application.shot_assembly import shot_assembly

    staging = tmp_path / "assembly"
    context = SimpleNamespace(
        library=SimpleNamespace(media=media),
        staging=staging,
        paths={"source": source, "window": composite},
        snapshot={
            "step_spec": {
                "source_asset": "source",
                "start": 71,
                "end": 144,
                "fps": str(fps),
                "windows": [{"start": 71, "end": 144, "asset": "window"}],
            }
        },
    )
    shot_assembly(context)
    stream = json.loads(
        media.run(
            media.ffprobe,
            ["-v", "error", "-show_streams", "-of", "json", str(staging / "preview.mp4")],
        )
    )["streams"][0]
    assert (
        stream["color_space"],
        stream["color_primaries"],
        stream["color_transfer"],
        stream["pix_fmt"],
    ) == ("bt709", "bt709", "bt709", "yuv420p")
    audio_reference = tmp_path / "expected.m4a"
    media.run(
        media.ffmpeg,
        [
            "-y",
            "-i",
            str(source),
            "-vn",
            "-af",
            f"atrim=start={float(origin + 71 / fps):.12f}:end={float(origin + 144 / fps):.12f},asetpts=PTS-STARTPTS",
            "-c:a",
            "aac",
            str(audio_reference),
        ],
    )
    expected_preview_audio = _pipe(
        media, ["-i", str(audio_reference), "-vn", "-f", "f32le", "pipe:1"]
    )
    assert (
        _pipe(media, ["-i", str(staging / "preview.mp4"), "-vn", "-f", "f32le", "pipe:1"])
        == expected_preview_audio
    )


@pytest.mark.parametrize("fps", [Fraction(25), Fraction(24000, 1001)])
def test_fractional_source_analysis_plan_render_and_audio(tmp_path, fps):
    db, library, runner, assets, frames = make_work(tmp_path, fps)
    try:
        with db.transaction() as session:
            analysis = start_people_analysis(session, assets[0], 71, 124, "analysis")
        drain(runner)
        assert read(db, analysis["id"])["state"] == "succeeded"
        with db.transaction() as session:
            report = people_for_asset(session, assets[0], library=library)[0]["report"]
            request = {
                "source_asset": assets[0],
                "analysis_operation": analysis["id"],
                "performers": [{"track_id": report["tracks"][0]["id"], "target_asset": assets[1]}],
            }
            plan = replacement_plan(session, request, library=library, renders=1)
            assert not plan["stop"]
            assert from_record(ReplacementFacts, plan["facts"]).fps == fps
            operation = start_replacement(session, request, library=library, renders=1)
            source = library.path(session, assets[0])
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        drain(runner)
        operation = read(db, operation["id"])
        assert operation["state"] == "succeeded", operation
        assert len(operation["candidates"]) == 1
        with db.transaction() as session:
            composite = library.path(session, operation["candidates"][0]["asset_id"])
            guide_job = operation["jobs"][0]
            guide = library.path(session, guide_job["outputs"][0]["asset_id"])
        assert Fraction(library.media.probe(guide)["frame_rate"]) == 24
        info = library.media.probe(composite)
        assert Fraction(info["frame_rate"]) == fps and info["has_audio"]
        assert decode_rgb(library.media, composite).shape == frames[71:124].shape

        def audio(path, start=0, end=None):
            filters = f"atrim=start={float(start / fps):.12f}" + (
                f":end={float(end / fps):.12f}" if end else ""
            )
            return _pipe(
                library.media, ["-i", str(path), "-vn", "-af", filters, "-f", "f32le", "pipe:1"]
            )

        assert audio(composite) == audio(source, 71, 124)
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    finally:
        db.close()


def test_rate_policy_and_frame_interval_lowering():
    policy = default_replacement_policy()
    for fps, status in [
        (24, "tested"),
        (25, "developing"),
        (Fraction(24000, 1001), "developing"),
        (30, "experimental"),
        (Fraction(30000, 1001), "experimental"),
        (48, "unsupported"),
    ]:
        assert policy.fps_status(fps) == status
    assert policy.fps_status(24, variable=True) == "unsupported"
    facts = replace(mug_facts(), fps=Fraction(24000, 1001), silent_intervals=((24, 48),))
    assert from_record(ReplacementFacts, to_record(facts)) == facts
    assert (
        "From 1 to 2 seconds"
        in compile_replacement(facts, Arm("rgb", "single", "active", None, 1, 0)).text
    )


def test_rational_rate_records_preserve_numeric_geometry():
    facts = replace(
        mug_facts(), fps=Fraction(24000, 1001), required_context=(Box(Fraction(1, 2), 0, 1, 1),)
    )
    record = to_record(facts)
    assert record["fps"] == "24000/1001"
    assert record["required_context"][0]["x0"] == 0.5
    assert from_record(ReplacementFacts, record) == facts


@pytest.mark.parametrize("fps", [Fraction(25), Fraction(24000, 1001)])
def test_guide_audio_event_remains_at_the_same_frame(tmp_path, fps):
    media = MediaTools()
    sample_rate, start, event, count = 48000, 11, 50, 73
    samples = np.zeros(round(5 * sample_rate), np.int16)
    event_sample = round(float(event / fps) * sample_rate)
    # A short tone burst survives atempo's overlap-add processing.
    tone = (np.sin(np.arange(2400) * 2 * np.pi * 440 / sample_rate) * 25000).astype(np.int16)
    samples[event_sample : event_sample + len(tone)] = tone
    source = tmp_path / "event.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(samples.tobytes())
    frames = np.zeros((count, 16, 16, 3), np.uint8)
    guide = lossless_video(
        media, frames, tmp_path / "guide.mkv", 24, source, start, count, source_fps=fps
    )
    pcm = np.frombuffer(_pipe(media, ["-i", str(guide), "-vn", "-f", "s16le", "pipe:1"]), np.int16)
    active = np.flatnonzero(abs(pcm.astype(np.int32)) > 5000)
    expected = (event - start) / 24
    assert abs(active[0] / sample_rate - expected) < 1 / 24
    assert abs(len(pcm) / sample_rate - count / 24) < 1 / 24


def test_variable_frame_rate_is_rejected_before_analysis(tmp_path):
    from types import SimpleNamespace

    media = MediaTools()
    source = tmp_path / "variable.mkv"
    media.run(
        media.ffmpeg,
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=48x32:rate=24:duration=4",
            "-vf",
            "setpts=PTS+if(gte(N\\,20)\\,0.125/TB\\,0)",
            "-fps_mode",
            "vfr",
            "-c:v",
            "ffv1",
            str(source),
        ],
    )
    assert not media.constant_frame_rate(source, 0, 73, Fraction(24))
    context = LocalContext(
        SimpleNamespace(media=media),
        tmp_path / "stage",
        {},
        {"source": source},
        {"step_spec": {"source_asset": "source", "start": 0, "end": 73, "fps": "24"}},
    )
    from gaiavfx.application.replace_character import ReplacementPlanningError

    assert media.probe(source, frame_range=(0, 73))["variable_frame_rate"]
    with pytest.raises(ReplacementPlanningError) as error:
        analysis_extract(context)
    assert error.value.gaps[0].code == "unsupported_fps"


def test_http_timing_contract_uses_rational_strings_and_frame_pairs():
    from pydantic import ValidationError

    from gaiavfx.api.production import ReplacementFactsDTO, ReplacementPlanInput

    facts = to_record(replace(mug_facts(), silent_intervals=((24, 48),)))
    for fps in ("24", "25", "24000/1001"):
        dto = ReplacementFactsDTO.model_validate({**facts, "fps": fps})
        assert json.loads(dto.model_dump_json())["fps"] == fps
        assert dto.silent_intervals == [(24, 48)]
    for fps in (24, "23.976", "-24", "24/1/1"):
        with pytest.raises(ValidationError):
            ReplacementFactsDTO.model_validate({**facts, "fps": fps})
    request = {
        "source_asset": "source",
        "analysis_operation": "analysis",
        "performers": [{"track_id": "one", "target_asset": "target"}],
    }
    for interval in ((1.5, 2.4), (True, 2)):
        with pytest.raises(ValidationError):
            ReplacementPlanInput.model_validate({**request, "silent_intervals": [interval]})
