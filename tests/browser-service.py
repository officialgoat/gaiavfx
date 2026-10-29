"""Browser-only composition: real HTTP/application stack with the backend's fake Comfy gateway.

No route or runtime switch for this fixture ships in the application.
"""

import io
import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

from gaiavfx import bootstrap
from gaiavfx.infrastructure.media import MediaTools
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "tests"))
import synthetic_room
from fake_comfy import FakeComfy
from fill_answers import FillAnswers

real_create_app = bootstrap.create_app


def create_test_app(*args, **kwargs):
    # This process uses only FakeComfy and scripted models. Keep its simulated
    # lease independent of other worktrees and the workstation's real GPU lane.
    os.environ["GAIAVFX_GPU_LOCK"] = str(Path(args[0]) / "fake-gpu.lock")
    app = real_create_app(*args, **kwargs)
    root = Path(args[0])
    root.mkdir(parents=True, exist_ok=True)
    videos = []
    for index in range(4):
        video = root / f"fake-comfy-{index}.mp4"
        subprocess.run(
            [
                str(MediaTools().ffmpeg),
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=24:duration=6",
                "-vf",
                f"drawbox=x={index * 25}:y=150:w=20:h=20:color=white:t=fill",
                "-c:v",
                "libx264",
                str(video),
            ],
            check=True,
        )
        videos.append(video.read_bytes())
    output_index = 0
    header = json.dumps(
        {
            "__metadata__": {"format": "h3_motion_context_av_v1"},
            "video": {"shape": [1, 1, 1, 1, 1]},
            "audio": {"shape": [1, 1, 1, 1]},
        }
    ).encode()
    context = struct.pack("<Q", len(header)) + header

    # A stage's fill rounds (ADR 0020): its probes render their memory and its stills' geometry is the room's own,
    # except the first round's first still, whose bent depth consolidation sets aside (Keep this still, §8).
    fill = FillAnswers(lambda: app.state.operations.db.root)
    fill.bent = {"r1p0"}

    class ServiceComfy(FakeComfy):
        def upload(self, url, source, filename, directory_type="input"):
            super().upload(url, source, filename, directory_type)
            fill.keep(filename, source)

    def outputs(graph):
        nonlocal output_index
        filled = fill.answer(graph)
        if filled is not None:
            return filled
        # A set from a take (ADR 0019 §4): the synthetic room's exact geometry, its camera moving through the room
        # for a segment's first shot and standing still for a later shot, so the gate passes once and refuses once
        # without filling.
        if any(node["class_type"] == "GaiaSaveSetGeometry" for node in graph.values()):
            moving = graph["shot"]["inputs"]["batch_index"] == 0
            shots = synthetic_room.truck() if moving else synthetic_room.still()
            return {
                "save": {"geometry": [{"filename": "geometry.npz", "type": "output"}]}
            }, {"save:geometry:0": synthetic_room.geometry_npz(shots)}
        if any(
            node["class_type"] == "TextEncodeQwenImageEditPlus"
            for node in graph.values()
        ):
            stream = io.BytesIO()
            Image.new(
                "RGB", (320, 180), (graph["sample"]["inputs"]["seed"] % 256, 90, 120)
            ).save(stream, "PNG")
            return {"save": {"images": [{"filename": "edit.png", "type": "output"}]}}, {
                "save:images:0": stream.getvalue()
            }
        # Distinct fake media exercises per-candidate loading and selection identity.
        media = videos[output_index % len(videos)]
        output_index += 1
        return {
            "save_video": {
                "images": [{"filename": "take.mp4", "type": "output"}],
                "animated": [True],
            }
        }, {
            "save_video:images:0": media,
            "context": context,
        }

    app.state.operations.comfy = ServiceComfy(outputs, polls_to_complete=1)
    app.state.operations.poll_seconds = 0.01
    # Set renders run the worldbuilder worker with its fake splat renderer (ADR 0019 §3), and seeds from a take
    # its real gate and atlas with a fake splat fit (§4); repairs have no atlas_prepare engine here, so they stop
    # while preparing, as a library without engines does.
    from gaiavfx.application import set_fill, set_seed, set_views
    from gaiavfx.application.engine_jobs import EngineCommand
    from gaiavfx.infrastructure import engines

    # The fake renderer needs no graphics memory; never wait on the workstation's real GPU.
    engines.free_vram_gb = lambda: None
    fake = Path(__file__).resolve().parents[1] / "backend" / "tests" / "fixtures"
    worker = EngineCommand((sys.executable, str(fake / "worldbuilder_fake_worker.py")))
    app.state.operations.engines = {
        set_views.STEP: worker,
        set_seed.ATLAS: worker,
        set_seed.SPLAT: worker,
        **{
            step: worker
            for step in (
                set_fill.EXPLORE,
                set_fill.PATHS,
                set_fill.STILLS,
                set_fill.CONSOLIDATE,
                set_fill.EXTEND,
            )
        },
    }
    # A test-only switch lets one spec own the fake repair engines, then restore the missing-engine fixture that
    # sets.spec.ts deliberately exercises. No route or switch ships in the product.
    from gaiavfx.application import set_build

    repair_defaults = {
        step: app.state.operations.engines.get(step) for step in set_build.STEPS
    }

    @app.post("/api/test/stage-repair-engines")
    def stage_repair_engines(enabled: bool):
        for step in set_build.STEPS:
            if enabled:
                app.state.operations.engines[step] = worker
            elif repair_defaults[step] is not None:
                app.state.operations.engines[step] = repair_defaults[step]
            else:
                app.state.operations.engines.pop(step, None)
        return {"enabled": enabled}

    # A tiny export can finish before a browser click on Cancel lands under load. Hold each
    # export briefly while honouring cancellation, so the cancel test is deterministic.
    real_export = MediaTools.export_sequence

    def held_export(self, items, output, width, height, cancelled, progress):
        for _ in range(30):
            if cancelled():
                break
            time.sleep(0.1)
        return real_export(self, items, output, width, height, cancelled, progress)

    MediaTools.export_sequence = held_export
    return app


bootstrap.create_app = create_test_app
bootstrap.main()
