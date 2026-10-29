"""Pixel and diagnostic invariants independent of inference."""

import numpy as np
import pytest

from gaiavfx.application.replacement_steps import (
    alpha_map,
    background_geometry,
    crop,
    cut_check,
    decode_rgb,
    detect_letterbox,
    invert_performer,
    lossless_video,
    paste,
    uncrop,
)
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.modules.direction.replacement import Box, CropPlan
from gaiavfx.shared.errors import ApplicationError


def test_pixel_centres_inverse_feather_and_inversion():
    rng = np.random.default_rng(3)
    source = rng.integers(0, 256, (48, 64, 3), dtype=np.uint8)
    plan = CropPlan(
        (32, 32),
        Box(8, 8, 40, 40),
        1.0,
        ("left", "right", "top", "bottom"),
        4.0,
        4.0,
        ((1.0, 0.0, -8.0), (0.0, 1.0, -8.0)),
    )
    cropped = crop(source, plan)
    assert np.array_equal(cropped, source[8:40, 8:40])
    recovered = uncrop(cropped, plan, (64, 48))
    alpha = alpha_map(plan, (64, 48))
    assert np.array_equal(paste(source, recovered, alpha), source)
    generated = np.full_like(source, 210)
    composed = paste(source, generated, alpha)
    assert np.array_equal(composed[alpha == 0], source[alpha == 0])
    performer = np.zeros((48, 64), bool)
    performer[10:30, 12:32] = True
    protected = np.zeros_like(performer)
    protected[20:25, 20:40] = True
    inverse = invert_performer(source, performer, protected)
    assert np.array_equal(inverse[performer & ~protected], 255 - source[performer & ~protected])
    assert np.array_equal(inverse[~performer | protected], source[~performer | protected])


@pytest.mark.parametrize("size", [(1792, 1024), (1024, 1792), (1800, 800), (128, 64)])
def test_measurement_resize_bounds_aspect_and_nearest_masks(size):
    from gaiavfx.application.replacement_steps import measurement_pixels

    width, height = size
    masks = np.zeros((2, height, width), np.uint8)
    masks[:, : height // 2, : width // 2] = 255
    resized = measurement_pixels(masks, mask=True)
    h, w = resized.shape[1:]
    assert w <= 896 and h <= 512
    assert abs(w - width * h / height) <= 1
    assert set(np.unique(resized)) == {0, 255}
    frames = np.repeat(masks[..., None], 3, axis=3)
    assert measurement_pixels(frames).shape == (*resized.shape, 3)
    if size == (128, 64):
        assert measurement_pixels(frames) is frames


def test_composite_keeps_native_delivery_and_legacy_geometry_but_bounds_evidence(
    tmp_path, monkeypatch
):
    import json
    from types import SimpleNamespace

    import gaiavfx.application.replacement_steps as steps
    from gaiavfx.application.operation_steps import LocalContext
    from gaiavfx.modules.direction.replacement import (
        Arm,
        Performer,
        ReplacementFacts,
        Track,
        to_record,
    )

    width, height = 1792, 1024
    frames = np.full((1, height, width, 3), 80, np.uint8)
    masks = np.zeros(frames.shape[:3], bool)
    masks[:, : height // 2, : width // 2] = True
    facts = ReplacementFacts(
        "source",
        0,
        1,
        (width, height),
        24,
        None,
        (Performer("actor", "picture", None, None, "performer", "centre", None),),
        (),
        (),
        (),
        False,
        (),
        (Track("actor", Box(0, 0, width // 2, height // 2), ("present",), None, "user"),),
    )
    plan = CropPlan(
        (width, height),
        Box(0, 0, width, height),
        1.0,
        (),
        0.0,
        None,
        ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    )
    spec = {
        "facts": to_record(facts),
        "crop": to_record(plan),
        "arm": to_record(Arm("inverted", "single", "crop", plan, 1, 0)),
        "selected_tracks": ["actor"],
        "tracks": [{"id": "actor", "mask_artifact": "mask"}],
        "window_frames": 1,
        "face_measurement": True,
    }
    # Deliberately no frozen policy: old snapshots still retain valid observations.
    captured = {}
    monkeypatch.setattr(steps, "decode_rgb", lambda *args: frames)
    monkeypatch.setattr(steps, "_mask_data", lambda *args: masks)
    monkeypatch.setattr(steps, "crop", lambda frame, *args: frame)
    monkeypatch.setattr(steps, "uncrop", lambda frame, *args: frame)
    monkeypatch.setattr(
        steps, "background_geometry", lambda *args: {"scale": 1.0, "dx": 3.0, "dy": 0.0}
    )
    monkeypatch.setattr(steps, "background_textured", lambda *args: True)
    monkeypatch.setattr(
        steps,
        "lossless_video",
        lambda media, pixels, target, *args, **kwargs: captured.update({target.name: pixels}),
    )
    context = LocalContext(
        SimpleNamespace(media=None),
        tmp_path,
        {},
        {"source": tmp_path / "source", "render": tmp_path / "render"},
        {"step_spec": spec},
    )
    outputs = steps.replacement_composite(context)
    assert captured["composite.mkv"].shape == frames.shape
    assert np.array_equal(captured["composite.mkv"], frames)
    for name in ("candidate-evidence.mkv", "source-shot.mkv", "performer-0.mkv"):
        assert captured[name].shape == (1, 512, 896, 3)
    assert set(np.unique(captured["performer-0.mkv"])) == {0, 255}
    assert (
        next(path for key, path, _ in outputs if key == "candidate_pixels").name != "composite.mkv"
    )
    report = json.loads((tmp_path / "checks.json").read_text())
    geometry = next(c for c in report["checks"] if c["check"] == "background_geometry")
    assert geometry["measured"] == 3 and geometry["verdict"] == "unsupported"
    assert "geometry unmeasured" not in geometry["reason"] and not report["reasons"]


def test_letterbox_cuts_and_background_shift():
    rng = np.random.default_rng(4)
    frame = rng.integers(30, 220, (256, 384, 3), dtype=np.uint8)
    frame[:8] = frame[-8:] = 0
    frames = np.repeat(frame[None], 8, axis=0)
    assert detect_letterbox(frames).offset == (0, 8)
    cut_check(frames)
    frames[4:] = 255 - frames[4:]
    with pytest.raises(ApplicationError, match="cut"):
        cut_check(frames)
    measure = background_geometry(frame, np.roll(frame, 6, axis=1), np.zeros(frame.shape[:2], bool))
    assert measure["scale"] == pytest.approx(1, abs=0.001)
    assert measure["dx"] == pytest.approx(6, abs=0.01)
    assert measure["dy"] == pytest.approx(0, abs=0.01)
    assert (
        background_geometry(
            np.zeros_like(frame), np.zeros_like(frame), np.zeros(frame.shape[:2], bool)
        )
        is None
    )


def test_lossless_ffmpeg_replay_is_exact_and_repeatable(tmp_path):
    frames = np.random.default_rng(2).integers(0, 256, (3, 40, 64, 3), dtype=np.uint8)
    media = MediaTools()
    target = tmp_path / "lossless.mkv"
    lossless_video(media, frames, target, 24)
    before = target.read_bytes()
    assert np.array_equal(decode_rgb(media, target), frames)
    lossless_video(media, frames, target, 24)
    assert target.read_bytes() == before


def test_inversion_is_binary_after_lanczos_crop_not_a_soft_edge():
    from gaiavfx.application.replacement_steps import prepare_crop

    source = np.random.default_rng(14).integers(0, 256, (48, 64, 3), dtype=np.uint8)
    plan = CropPlan(
        (64, 64),
        Box(8.25, 8.25, 40.25, 40.25),
        2.0,
        (),
        0.0,
        None,
        ((2.0, 0.0, -16.0), (0.0, 2.0, -16.0)),
    )
    performer = np.zeros((48, 64), bool)
    performer[10:30, 12:32] = True
    protected = np.zeros_like(performer)
    protected[20:25, 20:40] = True
    reference = crop(source, plan)
    selected = crop((performer & ~protected).astype(np.uint8) * 255, plan) > 127
    inverted = prepare_crop(source, plan, performer, protected, "inverted")
    assert np.array_equal(inverted[selected], 255 - reference[selected])
    assert np.array_equal(inverted[~selected], reference[~selected])


def test_original_integer_pcm_precision_is_preserved(tmp_path):
    from gaiavfx.application.replacement_steps import _pipe

    media = MediaTools()
    pcm = np.random.default_rng(20).integers(-(2**31), 2**31, 6000, dtype=np.int32).tobytes()
    audio = tmp_path / "integer.wav"
    _pipe(
        media,
        [
            "-y",
            "-f",
            "s32le",
            "-ar",
            "48000",
            "-ac",
            "1",
            "-i",
            "pipe:0",
            "-c:a",
            "pcm_s32le",
            str(audio),
        ],
        pcm,
    )
    frames = np.zeros((3, 32, 48, 3), np.uint8)
    target = tmp_path / "soundtrack.mkv"
    lossless_video(media, frames, target, 24, audio)
    delivered = _pipe(
        media, ["-i", str(target), "-vn", "-f", "s32le", "-c:a", "pcm_s32le", "pipe:1"]
    )
    assert delivered == pcm


def test_object_contacts_use_twenty_percent_and_leave_track_bounds_true(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    import gaiavfx.application.replacement_steps as steps
    from gaiavfx.application.operation_steps import LocalContext

    masks = {key: np.zeros((5, 20, 32), bool) for key in ("p1", "p2", "chair", "mug")}
    masks["p1"][:, 4:8, 4:8] = True
    masks["p2"][:, 4:8, 24:28] = True
    masks["chair"][:, 15:17, 10:12] = True
    masks["chair"][0, 4:6, 10:12] = True  # only dilation connects it; 1/5 present frames
    masks["mug"][:, 4:6, 26:28] = True
    tracks = [
        {"id": key, "concept": "person" if key.startswith("p") else key, "mask_artifact": key}
        for key in masks
    ]
    manifest = tmp_path / "analysis.json"
    manifest.write_text(json.dumps({"tracks": tracks}), "utf-8")
    monkeypatch.setattr(steps, "_mask_data", lambda library, artifact: masks[artifact])
    monkeypatch.setattr(steps, "decode_rgb", lambda *args: np.full((5, 20, 32, 3), 80, np.uint8))
    context = LocalContext(
        SimpleNamespace(media=None),
        tmp_path,
        {},
        {"analysis": manifest, "source": tmp_path / "unused"},
        {
            "step_spec": {
                "source_asset": "source",
                "start": 0,
                "end": 5,
                "fps": 24,
                "concepts": ["person", "chair", "mug"],
                "dilate": 3,
            }
        },
    )
    output = steps.people_report(context)
    report = json.loads(output[0][1].read_text())
    assert np.asarray(report["tracks"][0]["appearance"]).shape == (12, 6)
    assert np.asarray(report["tracks"][0]["appearance"]).sum() == pytest.approx(1)
    assert report["contacts"] == {"p1": ["chair"], "p2": ["mug"]}
    assert report["tracks"][0]["box"] == {"x0": 4.0, "y0": 4.0, "x1": 8.0, "y1": 8.0}
    assert len(report["candidates"]["p1"]["avoided_regions"]) == 2
    assert set(report["candidates"]["p1"]["protected_tracks"]) == {"p2", "chair", "mug"}
    masks["chair"][0, 4:6, 10:12] = False
    steps.people_report(context)
    assert json.loads(output[0][1].read_text())["contacts"]["p1"] == []


def test_prepare_holds_last_frame_and_keeps_source_unchanged(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import gaiavfx.application.replacement_steps as steps
    from gaiavfx.application.operation_steps import LocalContext
    from gaiavfx.modules.direction.replacement import (
        Arm,
        Performer,
        ReplacementFacts,
        Track,
        to_record,
    )

    frames = np.stack([np.full((48, 64, 3), value, np.uint8) for value in (50, 51, 52)])
    original = frames.copy()
    mask = np.zeros(frames.shape[:3], bool)
    mask[:, 12:24, 20:28] = True
    track = Track("person", Box(20, 12, 28, 24), ("present",) * 3, None, "user")
    facts = ReplacementFacts(
        "source",
        2,
        5,
        (64, 48),
        24,
        None,
        (Performer("person", "target", None, None, "performer", "centre", None),),
        (),
        (),
        (),
        False,
        (),
        (track,),
    )
    plan = CropPlan(
        (32, 32), Box(8, 8, 40, 40), 1.0, (), 0.0, None, ((1.0, 0.0, -8.0), (0.0, 1.0, -8.0))
    )
    arm = Arm("rgb", "single", "crop", plan, 1, 0)
    captured = []

    def capture(media, pixels, target, fps, audio_source, shot_start, shot_frames):
        captured.append(pixels)
        assert fps == 24 and shot_start == 2 and shot_frames == 3
        assert audio_source == tmp_path / "source.mkv"

    monkeypatch.setattr(steps, "decode_rgb", lambda *args: frames)
    monkeypatch.setattr(steps, "_mask_data", lambda *args: mask)
    monkeypatch.setattr(steps, "lossless_video", capture)
    context = LocalContext(
        SimpleNamespace(media=None),
        tmp_path,
        {},
        {"source": tmp_path / "source.mkv"},
        {
            "step_spec": {
                "facts": to_record(facts),
                "arm": to_record(arm),
                "crop": to_record(plan),
                "tracks": [{"id": "person", "mask_artifact": "mask"}],
                "selected_tracks": ["person"],
                "window_frames": 5,
            }
        },
    )
    steps.replacement_prepare(context)
    assert len(captured[0]) == 5
    assert [int(frame[0, 0, 0]) for frame in captured[0]] == [50, 51, 52, 52, 52]
    assert np.array_equal(frames, original)


@pytest.mark.parametrize("source_fps", [24, 25])
def test_locked_guide_pads_real_silence_and_unlocked_guide_keeps_legacy_bytes(
    tmp_path, monkeypatch, source_fps
):
    from dataclasses import replace
    from fractions import Fraction
    from types import SimpleNamespace

    import gaiavfx.application.replacement_steps as steps
    from gaiavfx.application.operation_steps import LocalContext
    from gaiavfx.modules.direction.replacement import (
        Arm,
        Performer,
        ReplacementFacts,
        Track,
        to_record,
    )

    media = MediaTools()
    sample_rate, delivered, window = 48000, 51, 56
    audio = tmp_path / "speech.wav"
    media.run(
        media.ffmpeg,
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate={sample_rate}:duration=3",
            "-c:a",
            "pcm_f32le",
            str(audio),
        ],
    )
    source = tmp_path / "source.mkv"
    frames = np.full((60, 48, 64, 3), 80, np.uint8)
    lossless_video(media, frames, source, source_fps, audio)
    before = source.read_bytes()
    facts = ReplacementFacts(
        "source",
        2,
        2 + delivered,
        (64, 48),
        source_fps,
        None,
        (Performer("person", "target", None, None, "performer", "centre", None),),
        (),
        (),
        (),
        False,
        (),
        (Track("person", Box(20, 12, 28, 24), ("present",) * delivered, None, "user"),),
        speaking=True,
    )
    crop_plan = CropPlan(
        (32, 32),
        Box(8, 8, 40, 40),
        1.0,
        (),
        0.0,
        None,
        ((1.0, 0.0, -8.0), (0.0, 1.0, -8.0)),
    )
    arm = Arm("rgb", "single", "crop", crop_plan, 1, 0)
    monkeypatch.setattr(
        steps,
        "_selected_masks",
        lambda _ctx, _spec, pixels, **kwargs: (
            np.zeros(pixels.shape[:3], bool),
            np.zeros(pixels.shape[:3], bool),
        ),
    )

    def prepare(locked):
        context = LocalContext(
            SimpleNamespace(media=media),
            tmp_path / str(locked),
            {},
            {"source": source},
            {
                "step_spec": {
                    "facts": to_record(facts),
                    "arm": to_record(replace(arm, audio_lock=locked)),
                    "crop": to_record(crop_plan),
                    "window_frames": window,
                }
            },
        )
        return steps.replacement_prepare(context)[0][1]

    unlocked, locked = prepare(False), prepare(True)
    guide_frames = np.full((window, 32, 32, 3), 80, np.uint8)
    legacy = tmp_path / "legacy.mkv"
    # The pre-lock path: repeated video frames, source audio trimmed only to delivery.
    lossless_video(
        media,
        guide_frames,
        legacy,
        Fraction(24),
        source,
        2,
        delivered,
        **({"source_fps": facts.fps} if source_fps != 24 else {}),
    )
    assert unlocked.read_bytes() == legacy.read_bytes()

    def pcm(path):
        return np.frombuffer(
            steps._pipe(
                media,
                [
                    "-i",
                    str(path),
                    "-vn",
                    "-f",
                    "f32le",
                    "-c:a",
                    "pcm_f32le",
                    "pipe:1",
                ],
            ),
            np.float32,
        )

    speech, padded = pcm(unlocked), pcm(locked)
    assert len(padded) / sample_rate == pytest.approx(window / 24, abs=1 / sample_rate)
    assert np.array_equal(padded[: len(speech)], speech)
    assert np.count_nonzero(speech) > 0
    assert np.count_nonzero(padded[delivered * sample_rate // 24 :]) == 0
    assert np.array_equal(decode_rgb(media, locked), guide_frames)
    assert source.read_bytes() == before


@pytest.mark.parametrize("scale,shift,advice", [(0.84, 77, True), (1.0, 2, False), (1.0, 0, False)])
def test_global_geometry_catches_research_warp_but_allows_small_jitter(scale, shift, advice):
    from gaiavfx.application.replacement_steps import resample

    frame = np.random.default_rng(27).integers(20, 235, (384, 640, 3), dtype=np.uint8)
    warped = resample(frame, (np.arange(640) - shift) / scale, np.arange(384) / scale)
    excluded = np.zeros(frame.shape[:2], bool)
    excluded[80:220, 160:240] = True
    warped[80:220, 160:240] = 90  # changed performer pixels cannot decide geometry
    measure = background_geometry(frame, warped, excluded)
    assert measure["scale"] == pytest.approx(scale, abs=0.005)
    assert measure["dx"] == pytest.approx(shift, abs=1)
    assert (
        abs(measure["scale"] - 1) > 0.02 or np.hypot(measure["dx"], measure["dy"]) > 4
    ) == advice


@pytest.mark.parametrize("textured", [False, True])
def test_unmeasured_geometry_retries_only_textured_background(tmp_path, monkeypatch, textured):
    import json
    from types import SimpleNamespace

    import gaiavfx.application.replacement_steps as steps
    from gaiavfx.application.operation_steps import LocalContext
    from gaiavfx.modules.direction.application import replacement_policy_text
    from gaiavfx.modules.direction.replacement import (
        Arm,
        Performer,
        ReplacementFacts,
        Track,
        to_record,
    )

    shape = (1, 128, 192, 3)
    source = (
        np.random.default_rng(8).integers(20, 235, shape, dtype=np.uint8)
        if textured
        else np.full(shape, 80, np.uint8)
    )
    generated = np.full(shape, 80, np.uint8)  # no matching background
    facts = ReplacementFacts(
        "source",
        0,
        1,
        (192, 128),
        24,
        None,
        (Performer("p", "target", None, None, "performer", "centre", None),),
        (),
        (),
        (),
        False,
        (),
        (Track("p", Box(80, 40, 90, 60), ("present",), None, "user"),),
    )
    plan = CropPlan(
        (192, 128), Box(0, 0, 192, 128), 1.0, (), 0.0, None, ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    )
    arm = Arm("rgb", "single", "crop", plan, 1, 0)
    monkeypatch.setattr(
        steps,
        "decode_rgb",
        lambda media, path, *args: source if path.name == "source" else generated,
    )
    monkeypatch.setattr(
        steps,
        "_selected_masks",
        lambda *args, **kwargs: (np.zeros(shape[:3], bool), np.zeros(shape[:3], bool)),
    )
    monkeypatch.setattr(steps, "lossless_video", lambda *args, **kwargs: None)
    context = LocalContext(
        SimpleNamespace(media=None),
        tmp_path,
        {},
        {"source": tmp_path / "source", "render": tmp_path / "render"},
        {
            "step_spec": {
                "facts": to_record(facts),
                "crop": to_record(plan),
                "arm": to_record(arm),
                "window_frames": 1,
                "geometry_advisory": True,
                "policy": replacement_policy_text(),
            }
        },
    )
    steps.replacement_composite(context)
    report = json.loads((tmp_path / "checks.json").read_text())
    check = next(c for c in report["checks"] if c["check"] == "background_geometry")
    assert {key: check[key] for key in ("check", "verdict", "advice", "measured")} == {
        "check": "background_geometry",
        "verdict": "unsupported",
        "advice": "retry" if textured else "none",
        "measured": None,
    }
    assert report["reasons"] == (["geometry unmeasured"] if textured else []) + [
        report["tone_anchor"]["reason"]
    ]


def test_bt709_limited_h264_aac_composite_rgb_pcm_and_click_sync(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import gaiavfx.application.replacement_steps as steps
    from gaiavfx.application.operation_steps import LocalContext
    from gaiavfx.modules.direction.replacement import (
        Arm,
        Performer,
        ReplacementFacts,
        Track,
        to_record,
    )

    media = MediaTools()
    sample_rate, fps, click_frame, start, end = 48000, 24, 20, 5, 35
    frames = np.repeat(
        np.random.default_rng(5).integers(20, 235, (1, 64, 96, 3), dtype=np.uint8), 48, axis=0
    )
    frames[click_frame, :8, :8] = 255
    samples = np.zeros(2 * sample_rate, np.float32)
    samples[click_frame * (sample_rate // fps)] = 0.9
    audio = tmp_path / "click.wav"
    steps._pipe(
        media,
        [
            "-y",
            "-f",
            "f32le",
            "-ar",
            str(sample_rate),
            "-ac",
            "1",
            "-i",
            "pipe:0",
            "-c:a",
            "pcm_f32le",
            str(audio),
        ],
        samples.tobytes(),
    )
    source = tmp_path / "source.mp4"
    steps._pipe(
        media,
        [
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            "96x64",
            "-r",
            str(fps),
            "-i",
            "pipe:0",
            "-i",
            str(audio),
            "-vf",
            "scale=in_range=full:out_color_matrix=bt709:out_range=limited",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_range",
            "tv",
            "-c:a",
            "aac",
            str(source),
        ],
        frames.tobytes(),
    )
    original = decode_rgb(media, source, start, end)
    reference = steps._pipe(
        media,
        [
            "-i",
            str(source),
            "-vf",
            f"trim=start_frame={start}:end_frame={end},scale=in_color_matrix=bt709:in_range=limited:out_range=full",
            "-frames:v",
            str(end - start),
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
    assert original.tobytes() == reference
    plan = CropPlan(
        (48, 32),
        Box(24, 16, 72, 48),
        1.0,
        ("left", "right", "top", "bottom"),
        4.0,
        4.0,
        ((1.0, 0.0, -24.0), (0.0, 1.0, -16.0)),
    )
    rendered = tmp_path / "render.mkv"
    lossless_video(media, np.full((end - start, 32, 48, 3), 110, np.uint8), rendered, fps)
    facts = ReplacementFacts(
        "source",
        start,
        end,
        (96, 64),
        fps,
        None,
        (Performer("p", "target", None, None, "performer", "centre", None),),
        (),
        (),
        (),
        False,
        (),
        (Track("p", Box(40, 20, 50, 40), ("present",) * (end - start), None, "user"),),
    )
    arm = Arm("rgb", "single", "crop", plan, 1, 0)
    monkeypatch.setattr(
        steps,
        "_selected_masks",
        lambda *args, **kwargs: (
            np.zeros(original.shape[:3], bool),
            np.zeros(original.shape[:3], bool),
        ),
    )
    context = LocalContext(
        SimpleNamespace(media=media),
        tmp_path / "composite",
        {},
        {"source": source, "render": rendered},
        {
            "step_spec": {
                "facts": to_record(facts),
                "crop": to_record(plan),
                "arm": to_record(arm),
                "window_frames": end - start,
                "geometry_advisory": False,
            }
        },
    )
    output = steps.replacement_composite(context)[0][1]
    delivered = decode_rgb(media, output)
    outside = alpha_map(plan, facts.native) == 0
    assert np.array_equal(delivered[:, outside], original[:, outside])

    def pcm(path, trim):
        return steps._pipe(
            media,
            ["-i", str(path), "-vn", "-af", trim, "-f", "f32le", "-c:a", "pcm_f32le", "pipe:1"],
        )

    source_pcm = pcm(
        source, f"atrim=start={start / fps:.12f}:end={end / fps:.12f},asetpts=PTS-STARTPTS"
    )
    delivered_pcm = pcm(output, "anull")
    assert delivered_pcm == source_pcm
    audio_frame = int(np.argmax(np.abs(np.frombuffer(delivered_pcm, np.float32)))) // (
        sample_rate // fps
    )
    visual_frame = int(np.argmax(delivered[:, :8, :8].mean(axis=(1, 2, 3))))
    assert audio_frame == visual_frame == click_frame - start
    assert len(delivered) == end - start


def test_window_mouths_choose_and_measure_faces_like_the_face_check():
    import importlib.util
    from pathlib import Path

    from gaiavfx.application.replacement_steps import window_mouths

    path = Path(__file__).parents[2] / "comfy_nodes/gaiavfx/metrics.py"
    spec = importlib.util.spec_from_file_location("people_metrics_for_mouths", path)
    metrics = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(metrics)
    rng = np.random.default_rng(9)
    frames, height, width = 6, 90, 120
    masks = {
        "a": np.zeros((frames, height, width), bool),
        "b": np.zeros((frames, height, width), bool),
    }
    masks["a"][:, 10:70, 5:55] = True
    masks["b"][:, 20:80, 60:115] = True
    rows = []
    for frame in range(frames):
        for _ in range(3):
            x0, y0 = rng.uniform(-10, 100), rng.uniform(-10, 70)
            box = [x0, y0, x0 + rng.uniform(8, 40), y0 + rng.uniform(8, 40)]
            rows.append((frame, box, rng.uniform(0, 50, (106, 2))))
    faces = {
        "frame": np.array([r[0] for r in rows], np.int32),
        "box": np.array([r[1] for r in rows], np.float32),
        "landmark_106": np.array([r[2] for r in rows], np.float32),
    }

    class Npz(dict):
        files = property(lambda self: list(self))

    result = window_mouths(Npz(faces), masks, frames)
    for track, mask in masks.items():
        for frame in range(frames):
            if any(result[t]["aperture"][frame] is None for t in masks):
                continue  # a face both masks want is checked below
            listed = [
                {
                    "bbox": faces["box"][i].astype(float),
                    "embedding": np.ones(512),
                    "landmark_2d_106": faces["landmark_106"][i].astype(float),
                }
                for i in range(len(rows))
                if faces["frame"][i] == frame
            ]
            chosen = metrics.choose_face(listed, mask[frame])
            expected = (
                None
                if chosen is None
                else metrics.mouth_aperture(chosen["landmark_2d_106"], chosen["bbox"])
            )
            got = result[track]["aperture"][frame]
            assert (got is None) == (expected is None)
            if expected is not None:
                assert got == pytest.approx(expected, abs=1e-6)
    assert (
        window_mouths(Npz({k: v for k, v in faces.items() if k != "landmark_106"}), masks, 6) == {}
    )


def test_window_mouths_give_a_shared_face_to_the_track_it_overlaps_most():
    from gaiavfx.application.replacement_steps import window_mouths

    class Npz(dict):
        files = property(lambda self: list(self))

    masks = {"near": np.zeros((1, 100, 100), bool), "far": np.zeros((1, 100, 100), bool)}
    masks["near"][0, 10:60, 10:60] = True
    masks["far"][0, 30:90, 30:90] = True
    points = np.zeros((1, 106, 2), np.float32)
    points[0, 62, 1] = 4.0
    faces = Npz(
        frame=np.array([0], np.int32),
        box=np.array([[12, 12, 52, 52]], np.float32),
        landmark_106=points,
    )
    result = window_mouths(faces, masks, 1)
    assert result["near"]["aperture"] == [0.1]
    assert result["near"]["box"] == faces["box"].tolist()
    assert result["far"] == {"aperture": [None], "face_height": [None], "box": [None]}


def test_head_alpha_has_finite_support_exact_core_and_parent_pixels():
    from gaiavfx.application.replacement_steps import head_alpha
    from gaiavfx.modules.direction.head import HeadPolicy
    from gaiavfx.modules.direction.person_crop import fit

    native = (256, 256)
    plan = CropPlan.from_geometry(fit((80, 80, 176, 176), native, native, 16, (0, 0, *native)), 16)
    mask = np.zeros((256, 256), bool)
    mask[90:166, 90:166] = True
    alpha = head_alpha(mask, (80, 80, 176, 176), plan, HeadPolicy())
    assert (alpha[:62] == 0).all() and (alpha[194:] == 0).all()
    assert (alpha[94:162, 94:162] == 1).all()
    assert np.any((alpha > 0) & (alpha < 1))
    parent = np.random.default_rng(27).integers(0, 256, (256, 256, 3), dtype=np.uint8)
    output = paste(parent, np.full_like(parent, 100), alpha)
    assert np.array_equal(output[alpha == 0], parent[alpha == 0])
    assert (output[alpha == 1] == 100).all()


def test_window_mouth_tie_keeps_aperture_and_height_but_withholds_both_boxes():
    from gaiavfx.application.replacement_steps import window_mouths

    class Npz(dict):
        files = property(lambda self: list(self))

    points = np.zeros((1, 106, 2), np.float32)
    points[0, 62, 1] = 4.0
    faces = Npz(frame=np.array([0]), box=np.array([[12, 12, 52, 52]]), landmark_106=points)
    masks = {name: np.ones((1, 64, 64), bool) for name in ("b", "a")}
    result = window_mouths(faces, masks, 1)
    assert result["a"] == result["b"] == {"aperture": [0.1], "face_height": [40.0], "box": [None]}


def test_negative_head_render_measures_inversion_on_head_canvas_without_body_calibration(
    tmp_path, monkeypatch
):
    import json
    from types import SimpleNamespace

    from gaiavfx.application import replacement_steps as steps
    from gaiavfx.application.operation_steps import LocalContext
    from gaiavfx.modules.direction.application import replacement_policy_text
    from gaiavfx.modules.direction.head import head_track
    from gaiavfx.modules.direction.person_crop import fit
    from gaiavfx.modules.direction.replacement import (
        Arm,
        Performer,
        ReplacementFacts,
        Track,
        parse_replacement_policy,
        to_record,
    )

    width, height = 640, 640
    film = np.random.default_rng(61).integers(0, 256, (1, height, width, 3), dtype=np.uint8)
    parent = np.full_like(film, 110)  # Correlation must use film, never the parent guide.
    mask = np.ones((1, height, width), bool)
    mask[:, :, 340:] = False
    boxes = [[270, 250, 330, 350]]
    policy = replacement_policy_text()
    head_policy = parse_replacement_policy(policy).head
    squares = head_track(boxes, ["present"], head_policy)
    plan = CropPlan.from_geometry(
        fit((180, 160, 420, 400), (512, 512), (width, height), 32, (0, 0, width, height)), 16
    )
    original = crop(film[0], plan)
    selected = crop(steps.head_mask(mask[0], squares[0]).astype(np.uint8) * 255, plan) > 127
    rendered = original.copy()
    rendered[selected] = 255 - original[selected]
    facts = ReplacementFacts(
        "source",
        0,
        1,
        (width, height),
        24,
        None,
        (Performer("actor", "picture", None, None, "performer", "centre", None),),
        (),
        (),
        (),
        False,
        (),
        (Track("actor", Box(0, 0, 340, 640), ("present",), None, "user"),),
    )
    spec = {
        "facts": to_record(facts),
        "crop": to_record(plan),
        "arm": to_record(Arm("rgb", "single", "head", plan, 1, 0)),
        "head": {"parent_asset": "parent"},
        "policy": policy,
        "selected_tracks": ["actor"],
        "tracks": [
            {
                "id": "actor",
                "mask_artifact": "mask",
                "mouth": {"box": boxes},
                "presence": ["present"],
            }
        ],
        "window_frames": 1,
        "face_measurement": True,
    }
    monkeypatch.setattr(
        steps,
        "decode_rgb",
        lambda media, path, *args: rendered[None] if path.name == "render" else parent,
    )

    def film_stream(*args):
        yield from film

    monkeypatch.setattr(steps, "iter_rgb", film_stream)
    monkeypatch.setattr(steps, "_mask_data", lambda *args: mask)
    captured = {}
    monkeypatch.setattr(
        steps,
        "lossless_video",
        lambda media, frames, path, *args, **kwargs: captured.update({path.name: frames}),
    )
    context = LocalContext(
        SimpleNamespace(media=None),
        tmp_path,
        {},
        {key: tmp_path / key for key in ("source", "render", "parent")},
        {"step_spec": spec},
    )
    steps.replacement_composite(context)
    report = json.loads((tmp_path / "checks.json").read_text())
    check = next(c for c in report["checks"] if c["check"] == "inverted_copy")
    assert check["measured"] < -0.99
    assert check["verdict"] == "unsupported" and check["advice"] == "none"
    assert "measured on the head canvas; uncalibrated for head passes" in check["reason"]
    assert captured["source-shot.mkv"].shape == (1, 512, 512, 3)
    assert np.array_equal(captured["source-shot.mkv"][0], original)


@pytest.mark.parametrize("framing", ["crop", "active"])
def test_prepare_context_is_lossless_masked_rgb_and_does_not_change_guide(
    tmp_path, monkeypatch, framing
):
    from dataclasses import replace
    from types import SimpleNamespace

    from gaiavfx.application import replacement_steps as steps
    from gaiavfx.application.operation_steps import LocalContext
    from gaiavfx.modules.direction.replacement import (
        Arm,
        Performer,
        ReplacementFacts,
        Track,
        to_record,
    )

    media = MediaTools()
    pixels = np.full((81, 48, 64, 3), 100, np.uint8)
    source = tmp_path / "source.mkv"
    steps.lossless_video(media, pixels, source, 24)
    predecessor = np.stack([np.full((48, 64, 3), 20 + i, np.uint8) for i in range(73)])
    delivery = tmp_path / "delivery.mkv"
    steps.lossless_video(media, predecessor, delivery, 24)
    track = Track("person", Box(20, 12, 28, 24), ("present",) * 30, None, "user")
    facts = ReplacementFacts(
        "source",
        51,
        81,
        (64, 48),
        24,
        None,
        (Performer("person", "target", None, None, "performer", "centre", None),),
        (),
        (),
        (),
        False,
        (),
        (track,),
    )
    plan = CropPlan(
        (80, 64), Box(-8, -8, 72, 56), 1.0, (), 0.0, None, ((1.0, 0.0, 8.0), (0.0, 1.0, 8.0))
    )
    arm = Arm("inverted", "single", framing, plan if framing == "crop" else None, 1, 0)
    mask = np.ones((30, 48, 64), bool)
    monkeypatch.setattr(steps, "_mask_data", lambda *args: mask)
    spec = {
        "facts": to_record(facts),
        "arm": to_record(arm),
        "crop": to_record(plan),
        "tracks": [{"id": "person", "mask_artifact": "mask"}],
        "selected_tracks": ["person"],
        "window_frames": 39,
    }
    paths = {"source": source, "delivery": delivery}
    library = SimpleNamespace(media=media)
    plain = LocalContext(library, tmp_path / "plain", {}, paths, {"step_spec": spec})
    original = steps.replacement_prepare(plain)
    chain = {"asset": "delivery", "predecessor_start": 0, "start": 51, "end": 73}
    chained = LocalContext(
        library,
        tmp_path / "chained",
        {},
        paths,
        {
            "step_spec": {
                **spec,
                "facts": to_record(replace(facts, context_frames=22)),
                "chain": chain,
            }
        },
    )
    outputs = steps.replacement_prepare(chained)
    assert [o[0] for o in outputs] == ["guide", "context"]
    assert outputs[0][1].read_bytes() == original[0][1].read_bytes()
    context = outputs[1][1]
    info = media.probe(context)
    assert info["frame_rate"] == "24/1" and not info["has_audio"]
    assert round(info["duration"] * 24) == 22
    decoded = steps.decode_rgb(media, context, 0, 22)
    assert len(decoded) == 22
    for i, frame in enumerate(decoded):
        expected = steps.crop(predecessor[51 + i], plan)
        if framing == "active":
            expected[:8] = expected[-8:] = 0
            expected[:, :8] = expected[:, -8:] = 0
        assert np.array_equal(frame, expected)


@pytest.mark.parametrize("anchored", [False, True])
@pytest.mark.parametrize("bridge", [False, True])
def test_composite_tone_report_and_legacy_pixels(tmp_path, monkeypatch, anchored, bridge):
    import json
    from dataclasses import replace
    from types import SimpleNamespace

    from test_replacement_contract import mug_facts

    from gaiavfx.application import replacement_steps as steps
    from gaiavfx.application import tone_anchor as tone
    from gaiavfx.application.operation_steps import LocalContext
    from gaiavfx.modules.direction.application import replacement_policy_text
    from gaiavfx.modules.direction.replacement import Arm, to_record

    film = np.full((1, 96, 128, 3), 100, np.uint8)
    raw = np.full((1, 64, 64, 3), 80, np.uint8)
    facts = mug_facts(1)
    facts = replace(
        facts,
        native=(128, 96),
        letterbox=None,
        required_context=(),
        avoided_regions=(),
        tracks=tuple(replace(t, box=Box(50, 30, 60, 50)) for t in facts.tracks),
    )
    plan = CropPlan(
        (64, 64), Box(32, 16, 96, 80), 1.0, (), 0.0, None, ((1.0, 0.0, -32.0), (0.0, 1.0, -16.0))
    )
    policy = replacement_policy_text()
    if not anchored:
        policy = (
            policy[: policy.index("[tone_anchor]")]
            + policy[policy.index("\n[", policy.index("[tone_anchor]") + 1) :]
        )
    spec = {
        "facts": to_record(facts),
        "crop": to_record(plan),
        "arm": to_record(Arm("rgb", "single", "crop", plan, 1, 0)),
        "selected_tracks": [facts.performers[0].track_id],
        "tracks": [],
        "window_frames": 1,
        "policy": policy,
    }
    mask_reads, agreement_calls = [], []
    if bridge:
        spec.update(
            face_measurement=True,
            bridge={
                "start": 0,
                "end": 1,
                "windows": [
                    {
                        "start": 0,
                        "end": 1,
                        "selected_tracks": ["actor"],
                        "tracks": [{"id": "actor", "mask_artifact": key}],
                    }
                    for key in ("left", "right")
                ],
            },
        )

        def mask_data(library, key):
            mask_reads.append(key)
            return np.zeros(film.shape[:3], bool)

        monkeypatch.setattr(steps, "_mask_data", mask_data)
    agreeing = tone.agreeing

    def counted_agreeing(*args):
        agreement_calls.append(True)
        return agreeing(*args)

    monkeypatch.setattr(tone, "agreeing", counted_agreeing)
    captured = {}
    monkeypatch.setattr(
        steps, "decode_rgb", lambda media, path, *args: raw if str(path) == "raw" else film
    )
    monkeypatch.setattr(steps, "background_geometry", lambda *args: None)
    monkeypatch.setattr(steps, "background_textured", lambda *args: False)
    monkeypatch.setattr(
        steps,
        "lossless_video",
        lambda media, pixels, target, *args, **kwargs: captured.update({target.name: pixels}),
    )
    context = LocalContext(
        SimpleNamespace(media=None),
        tmp_path,
        {},
        {"source": "film", "render": "raw"},
        {"step_spec": spec},
    )
    steps.replacement_composite(context)
    assert len(agreement_calls) == int(anchored)
    assert mask_reads == (["left", "right"] if bridge else [])
    report = json.loads((tmp_path / "checks.json").read_text())
    delivered = captured["composite.mkv"]
    assert np.array_equal(delivered[:, :16], film[:, :16])
    if anchored:
        assert report["tone_anchor"]["applied"] and report["tone_anchor"]["neutral_frames"] == 0
        assert report["tone_anchor"]["gain_luma"] == pytest.approx([1.25, 1.25])
        assert report["tone_anchor"]["decision"] == "fitted"
        assert report["tone_anchor"]["unfitted_frames"] == 0
        assert report["tone_anchor"]["final_gain"] == pytest.approx([1.25] * 3)
        assert np.array_equal(delivered, film)
    else:
        expected = paste(film[0], uncrop(raw[0], plan, facts.native), alpha_map(plan, facts.native))
        assert "tone_anchor" not in report and np.array_equal(delivered[0], expected)


def test_bridge_prepare_rebuilds_raw_neighbours_and_pads_short_tail(tmp_path, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace

    from test_replacement_contract import mug_facts

    from gaiavfx.application import replacement_steps as steps
    from gaiavfx.application.operation_steps import LocalContext
    from gaiavfx.modules.direction.replacement import Arm, to_record

    facts = mug_facts(48)
    facts = replace(
        facts,
        shot_start=26,
        shot_end=74,
        native=(128, 96),
        letterbox=None,
        handoff="bridge",
        required_context=(),
        avoided_regions=(),
        tracks=tuple(replace(t, box=Box(50, 30, 60, 50)) for t in facts.tracks),
    )
    plan = CropPlan(
        (64, 64), Box(32, 16, 96, 80), 1.0, (), 0.0, None, ((1.0, 0.0, -32.0), (0.0, 1.0, -16.0))
    )
    film = np.full((48, 96, 128, 3), 100, np.uint8)
    raw = {
        "left": np.full((73, 64, 64, 3), 60, np.uint8),
        "right": np.full((23, 64, 64, 3), 120, np.uint8),
    }
    masks = {k: np.zeros((len(v), 96, 128), bool) for k, v in raw.items()}
    masks["left"][:, 30:40, 50:60] = True
    masks["right"][:, 35:45, 50:60] = True
    windows = [
        {
            "start": start,
            "end": end,
            "render_asset": key,
            "crop": to_record(plan),
            "selected_tracks": ["actor"],
            "tracks": [{"id": "actor", "mask_artifact": key}],
            "arm": to_record(Arm("rgb", "single", "active", None, 1, 0)),
        }
        for key, start, end in [("left", 0, 73), ("right", 51, 74)]
    ]
    spec = {
        "facts": to_record(facts),
        "crop": to_record(plan),
        "arm": to_record(Arm("rgb", "single", "crop", plan, 1, 0)),
        "selected_tracks": ["actor"],
        "tracks": [],
        "window_frames": 73,
        "bridge": {"join": 1, "start": 26, "end": 74, "generated": [48, 74], "windows": windows},
    }
    captured, decoded = {}, []
    monkeypatch.setattr(steps, "decode_rgb", lambda *args: film)
    monkeypatch.setattr(steps, "_mask_data", lambda library, key: masks[key])

    def decode(media, path, start, end):
        decoded.append((path, start, end))
        yield from raw[path][start:end]

    monkeypatch.setattr(steps, "iter_rgb", decode)
    monkeypatch.setattr(
        steps,
        "lossless_video",
        lambda media, pixels, target, *args, **kwargs: captured.update({target.name: pixels}),
    )
    context = LocalContext(
        SimpleNamespace(media=None),
        tmp_path,
        {},
        {"source": "film", "left": "left", "right": "right"},
        {"step_spec": spec},
    )
    outputs = steps.replacement_prepare(context)
    assert [key for key, _, _ in outputs] == ["guide", "pixels"]
    assert captured["guide.mkv"].shape == captured["pixels.mkv"].shape == (73, 64, 64, 3)
    assert decoded == [("left", 26, 73), ("right", 0, 23)]
    expected = [60] * 25 + [round(60 + 60 * i / 21) for i in range(22)] + [120] * 26
    assert list(captured["pixels.mkv"][:, 32, 32, 0]) == expected
    assert np.array_equal(captured["pixels.mkv"][-1], captured["pixels.mkv"][47])


def test_appearance_histogram_uses_only_present_masked_colour():
    from gaiavfx.application.replacement_steps import appearance_histogram

    frames = np.zeros((3, 4, 4, 3), np.uint8)
    frames[:] = [0, 255, 0]
    frames[0, :2] = [255, 0, 0]
    masks = np.zeros((3, 4, 4), bool)
    masks[:, :2] = True
    histogram = np.asarray(appearance_histogram(frames, masks, ["present", "absent", "unresolved"]))
    assert histogram.shape == (12, 6)
    assert histogram.sum() == 1 and histogram[0, 5] == 1
    assert appearance_histogram(frames, masks, ["absent"] * 3) is None
    assert appearance_histogram(np.zeros_like(frames), masks, ["present"] * 3) is None
