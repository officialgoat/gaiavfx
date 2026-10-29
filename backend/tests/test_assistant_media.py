from pathlib import Path

import pytest

from gaiavfx.infrastructure.assistant_media import overlapping_batches, sample_video
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.shared.errors import ApplicationError


def test_samples_include_actual_boundaries_and_deduplicate_pts(tmp_path):
    media = MediaTools()
    source = tmp_path / "source.mp4"
    media.run(
        media.ffmpeg,
        [
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=96x64:rate=30:duration=3",
            "-c:v",
            "libx264",
            str(source),
        ],
    )
    frames = sample_video(media, source, tmp_path / "frames", 510000, 2710000, lambda: False)
    assert [f["source_us"] for f in frames] == [533333, 1533333, 2533333, 2700000]
    assert all(Path(f["path"]).is_file() for f in frames)
    assert len({f["pts"] for f in frames}) == len(frames)
    batches = list(overlapping_batches(frames, 3))
    assert batches[0][-1] == batches[1][0]
    assert {f["pts"] for batch in batches for f in batch} == {f["pts"] for f in frames}
    short = sample_video(media, source, tmp_path / "short", 501000, 549000, lambda: False)
    assert len(short) == 1 and short[0]["source_us"] == 533333
    targeted = sample_video(
        media,
        source,
        tmp_path / "targeted",
        510000,
        2710000,
        lambda: False,
        target_us=[1200000, 1233333, 1233333],
    )
    assert [f["source_us"] for f in targeted] == [
        533333,
        1200000,
        1233333,
        1533333,
        2533333,
        2700000,
    ]
    ending = media.ending_frame(source, tmp_path / "ending.png", 501000, 2700000)
    assert ending["source_end_us"] == 2700000
    assert ending["frame_ordinal"] == 80  # 2.666667 s; exclude the frame exactly at out.
    with pytest.raises(ApplicationError, match="two ordered frames"):
        list(overlapping_batches(frames, 1))
    with pytest.raises(ApplicationError, match="stopped"):
        sample_video(media, source, tmp_path / "cancelled", 0, 1000000, lambda: True)
    media.stop()


def test_variable_frame_rate_sampling_uses_source_pts(tmp_path):
    media = MediaTools()
    source = tmp_path / "vfr.mkv"
    media.run(
        media.ffmpeg,
        [
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=96x64:rate=10:duration=3",
            "-vf",
            "select='not(eq(mod(n,3),1))'",
            "-fps_mode",
            "vfr",
            "-c:v",
            "ffv1",
            str(source),
        ],
    )
    frames = sample_video(media, source, tmp_path / "vfr", 500000, 2600000, lambda: False)
    assert [f["source_us"] for f in frames] == [500000, 1500000, 2400000]
    assert all(Path(f["path"]).is_file() for f in frames)
    media.stop()
