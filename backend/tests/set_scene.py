"""A scene whose selected take can build a stage, with fakes for every model and GPU stage (ADR 0019 §4, 0020): a fake
ComfyUI answers the take-geometry graph with a synthetic room's exact geometry (`synthetic_room.py`) and a fill
round's graphs as `fill_answers.py` does, the real worldbuilder worker judges the take and lays out the atlas, and a
fake splat fit stands in for gsplat. Shared by the seed tests and the stage eval (`scripts/harness_eval_stage.py`).

The caller points `GAIAVFX_GPU_LOCK` and `engines.free_vram_gb` where its run needs them; nothing here does."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import synthetic_room as room
from fake_comfy import FakeComfy
from fill_answers import FillAnswers
from PIL import Image

from gaiavfx.application import set_build, set_fill, set_seed
from gaiavfx.application.direction_reading import DIRECTION
from gaiavfx.application.engine_jobs import EngineCommand
from gaiavfx.application.operations import OperationRunner
from gaiavfx.application.workspace import Workspace
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.modules.editing.selections.service import SelectionService
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.application import LibraryService

MIGRATIONS = Path(__file__).parents[1] / "migrations"
FAKE = Path(__file__).parent / "fixtures" / "worldbuilder_fake_worker.py"
OPERATIONS = OperationService()


def take_video(path: Path, shots) -> Path:
    """The shots' frames as an H.264 take, as a render would deliver one."""
    frames = np.stack([image for image, *_ in shots])
    subprocess.run(
        [
            str(MediaTools().ffmpeg),
            "-y",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{room.W}x{room.H}",
            "-r",
            "24",
            "-i",
            "-",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        input=frames.tobytes(),
        check=True,
    )
    return path


class TakeComfy(FakeComfy):
    """Answers a take-geometry graph with the synthetic room's geometry: a moving camera for the first shot, a still
    one for any later shot (its ImageFromBatch starts past frame 0); a fill round's graphs as `fill_answers` does."""

    def __init__(self):
        super().__init__(self.answer, polls_to_complete=1)
        self.graphs = []
        self.fill = FillAnswers(None)  # its root is set once the library exists

    def upload(self, url, source, filename, directory_type="input"):
        super().upload(url, source, filename, directory_type)
        self.fill.keep(filename, source)

    def answer(self, graph):
        self.graphs.append(graph)
        filled = self.fill.answer(graph)
        if filled is not None:
            return filled
        assert any(node["class_type"] == "GaiaSaveSetGeometry" for node in graph.values())
        moving = graph["shot"]["inputs"]["batch_index"] == 0
        npz = room.geometry_npz(room.truck() if moving else room.still())
        return {
            "save": {"geometry": [{"filename": "geometry.npz", "subfolder": "x", "type": "output"}]}
        }, {"save:geometry:0": npz}


def build_scene(folder: Path, *, second_scene: bool = False) -> SimpleNamespace:
    """A movie with one segment (a cut at f12) whose current take is a video, opening in a place not built as a set;
    with `second_scene`, a second segment opens in the same place, so a stage could keep it for both. Its runner
    drives every seed and fill stage through the fakes."""
    root = folder / "library"
    LibraryService(root, MIGRATIONS).create("Seeds")
    workspace = Workspace(root)
    video = take_video(folder / "take.mp4", room.truck(24))
    picture = folder / "ivo.png"
    Image.new("RGB", (64, 96), "brown").save(picture)
    with workspace.db.transaction() as session:
        asset = workspace.library.register(
            session, workspace.library.prepare(video, "take.mp4"), {"type": "import"}
        )
        identity = workspace.library.register(
            session, workspace.library.prepare(picture, "ivo.png"), {"type": "import"}
        )
        place = DIRECTION.create_place(session, "Box room")
        door = DIRECTION.add_angle(session, place["id"], "Door")
        corner = DIRECTION.add_angle(session, place["id"], "Corner")
        movie = DIRECTION.create_movie(session, "Box scenes")
        subject = DIRECTION.add_subject(
            session,
            movie["id"],
            movie["revision"],
            name="Ivo",
            description="A fisher",
            keeps="",
            identity_asset_id=identity["id"],
        )
        content = {
            "subjects": [subject["id"]],
            "place": {"noun": "room", "description": "A box room.", "landmarks": ""},
        }
        segment = DIRECTION.add_segment(
            session,
            movie["id"],
            subject["receipt"]["after"]["revision"],
            content=content,
            delivered_frames=24,
            continues=False,
            phases=[(0, {"description": "Ivo walks across.", "screen_direction": "", "sound": ""})],
        )
        DIRECTION.update_segment(
            session, segment["id"], segment["revision"], opening_angle_id=door["id"]
        )
        segment = DIRECTION.read_segment(session, segment["id"])
        DIRECTION.create_cut(
            session,
            segment["id"],
            segment["revision"],
            frame=12,
            from_angle_id=door["id"],
            to_angle_id=corner["id"],
        )
        segment = DIRECTION.read_segment(session, segment["id"])
        render = OPERATIONS.create(
            session,
            kind="render",
            # What a render freezes about the scene, so the take reads current until the scene changes.
            request={
                "segment_id": segment["id"],
                "segment_revision": segment["revision"],
                "phase_revisions": {p["id"]: p["revision"] for p in segment["phases"]},
                "subject_revisions": {
                    sid: DIRECTION.subjects(session, movie["id"])[0]["revision"]
                    for sid in segment["content"]["subjects"]
                },
            },
            jobs=[JobSpec("video", "local", "fake", step="fixture")],
            budget_limit=None,
        )
        candidate = OPERATIONS.add_candidate(
            session, render["jobs"][0]["id"], asset["id"], "take", "video"
        )
        SelectionService().select(
            session,
            movie_id=movie["id"],
            segment_id=segment["id"],
            candidate_id=candidate["id"],
            asset_id=asset["id"],
            authority="user",
            expected_revision=0,
        )
        second = None
        if second_scene:
            movie = DIRECTION.read_movie(session, movie["id"])
            second = DIRECTION.add_segment(
                session,
                movie["id"],
                movie["revision"],
                content=content,
                delivered_frames=24,
                continues=False,
                phases=[(0, {"description": "Ivo sits.", "screen_direction": "", "sound": ""})],
            )
            second = DIRECTION.update_segment(
                session, second["id"], second["revision"], opening_angle_id=corner["id"]
            )
    comfy = TakeComfy()
    comfy.fill.root = root
    worker = EngineCommand((sys.executable, str(FAKE)))
    run = OperationRunner(
        workspace.db,
        comfy,
        workspace.library,
        "http://fake",
        poll_seconds=0,
        sweep_seconds=0,
        engines={
            step: worker
            for step in (
                set_seed.ATLAS,
                set_seed.SPLAT,
                set_build.STEPS[0],
                set_fill.EXPLORE,
                set_fill.PATHS,
                set_fill.STILLS,
                set_fill.CONSOLIDATE,
                set_fill.EXTEND,
            )
        },
    )
    return SimpleNamespace(
        workspace=workspace,
        run=run,
        comfy=comfy,
        movie=movie,
        segment=segment,
        second=second,
        place=place,
        angles={"door": door, "corner": corner},
        candidate=candidate,
        asset=asset,
    )
