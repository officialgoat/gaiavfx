"""Frame and fit steps through the durable runner."""

from pathlib import Path

import pytest
from fake_comfy import FakeComfy
from PIL import Image

from gaiavfx.application.operations import OperationRunner
from gaiavfx.infrastructure.database import Database
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.integrations.minimax_h3.graph import (
    context_descriptor,
    parse_render,
    render_presets,
)
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.application import LibraryService, MediaLibrary


def test_extract_frames_resolves_prior_video(tmp_path):
    LibraryService(tmp_path, Path(__file__).parents[1] / "migrations").create("Frames")
    db = Database(tmp_path)
    library = MediaLibrary(db, MediaTools())
    clip = tmp_path / "survey.mp4"
    library.media.run(
        library.media.ffmpeg,
        [
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=24:duration=5.167",
            "-c:v",
            "libx264",
            str(clip),
        ],
    )
    jobs = [
        JobSpec(
            "video", "comfy", "fake", snapshot={"knowledge": {"pack": "text"}}, graph={"seed": 1}
        ),
        JobSpec(
            "image",
            "local",
            "minimax_h3",
            depends_on=0,
            step="extract_frames",
            inputs={"source_video": {"source": "job_output"}},
            snapshot={
                "step_spec": {
                    "source": "source_video",
                    "frames": [
                        ["opening", 12],
                        ["angle-2", 36],
                        ["overhead", 110],
                    ],
                }
            },
        ),
    ]
    with db.transaction() as session:
        operation = OperationService().create(
            session, kind="survey", request={}, jobs=jobs, budget_limit=None
        )
    fake = FakeComfy(
        lambda _: (
            {"save": {"videos": [{"filename": "survey.mp4", "type": "output"}]}},
            {"save:videos:0": clip.read_bytes()},
        )
    )
    runner = OperationRunner(db, fake, library, "http://fake", poll_seconds=0)
    assert runner.run_once()
    assert runner.run_once()
    with db.transaction() as session:
        saved = OperationService().operation(session, operation["id"])
        assert saved["state"] == "succeeded"
        assert {item["output_key"] for item in saved["candidates"]} == {
            "save:videos:0",
            "opening",
            "angle-2",
            "overhead",
        }
        video = next(item for item in saved["candidates"] if item["kind"] == "video")
        frames = [item for item in saved["candidates"] if item["kind"] == "image"]
        for frame in frames:
            asset = library.get(session, frame["asset_id"])
            assert asset["provenance"][0]["inputs"] == {
                video["asset_id"]: library.get(session, video["asset_id"])["sha256"]
            }
            assert library.path(session, frame["asset_id"]).suffix == ".png"
        assert frames[0]["asset_id"] != frames[1]["asset_id"]
    db.close()


def test_fit_frame_and_delivery_presets(tmp_path):
    LibraryService(tmp_path, Path(__file__).parents[1] / "migrations").create("Fit")
    db = Database(tmp_path)
    library = MediaLibrary(db, MediaTools())
    source = tmp_path / "source.png"
    Image.new("RGB", (1344, 784), (20, 40, 60)).save(source)
    prepared = library.prepare(source, source.name)
    with db.transaction() as session:
        asset = library.register(session, prepared, {"type": "fixture"})
        op = OperationService().create(
            session,
            kind="fit",
            request={},
            jobs=[
                JobSpec(
                    "image",
                    "local",
                    "minimax_h3",
                    step="fit_frame",
                    inputs={asset["id"]: {"sha256": asset["sha256"]}},
                    snapshot={"step_spec": {"source": asset["id"], "width": 768, "height": 448}},
                )
            ],
            budget_limit=None,
        )
    OperationRunner(db, FakeComfy(), library, "http://fake").run_once()
    with db.transaction() as session:
        candidate = OperationService().operation(session, op["id"])["candidates"][0]
        with Image.open(library.path(session, candidate["asset_id"])) as image:
            assert image.size == (768, 448)
        missing = OperationService().create(
            session,
            kind="fit",
            request={},
            jobs=[
                JobSpec(
                    "image",
                    "local",
                    "minimax_h3",
                    step="fit_frame",
                    inputs={asset["id"]: {"sha256": asset["sha256"]}},
                    snapshot={"step_spec": {"source": asset["id"], "height": 448}},
                )
            ],
            budget_limit=None,
        )
    OperationRunner(db, FakeComfy(), library, "http://fake").run_once()
    with db.transaction() as session:
        assert OperationService().operation(session, missing["id"])["state"] == "failed"
    assert render_presets().delivery == {"width": 768, "height": 448}
    assert context_descriptor("gaiavfx/t") == {
        "output_key": "context",
        "filename": "context_00001.safetensors",
        "subfolder": "gaiavfx/t",
        "type": "output",
    }
    text = (
        Path(__file__).parents[1] / "src/gaiavfx/integrations/minimax_h3/knowledge/render.toml"
    ).read_text()
    with pytest.raises(ValueError):
        parse_render(text.split("[delivery]")[0])
    with pytest.raises(ValueError):
        parse_render(text.replace("[replacement]", "[replacement]\nextra = 1"))
    db.close()
