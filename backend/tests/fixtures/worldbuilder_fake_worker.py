"""The worldbuilder worker for the set build tests: the real worker and its real map, memory and fill code (numpy,
PIL and ffmpeg run in the app's environment), with fakes only where gsplat would run: the splat fits write a PLY with
a 3DGS header (SH degree 1) and return the numbers a fit reports, and a fill round's depth paths are flat. ffmpeg is
the app's own (GAIAVFX_MEDIA_DIR). `--crash <task>` makes that task fail."""

import json
import os
import struct
import sys
import types
from pathlib import Path

WORKERS = Path(__file__).resolve().parents[3] / "engine_workers"
sys.path.insert(0, str(WORKERS))

PROPERTIES = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2"]
PROPERTIES += [f"f_rest_{n}" for n in range(9)]
PROPERTIES += ["opacity", "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]


def _write_ply(path: Path, count=4):
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {count}\n"
        + "".join(f"property float {name}\n" for name in PROPERTIES)
        + "end_header\n"
    ).encode()
    path.write_bytes(
        header + struct.pack(f"<{count * len(PROPERTIES)}f", *([0.5] * count * len(PROPERTIES)))
    )


def _run(args):
    args.out.mkdir(parents=True, exist_ok=True)
    _write_ply(args.out / "splat.ply")
    atlas = Path(args.atlas).read_text(encoding="utf-8")
    return {
        "command": args.command,
        "atlas_bytes": len(atlas),
        "gaussians": 4,
        "heldout_psnr": 20.5,
        "heldout_psnr_refined": 21.25,
        "anchor_psnr": 37.0,
    }


def _parse(argv):
    values = {"command": argv[0]}
    for key, value in zip(argv[1::2], argv[2::2], strict=True):
        values[key.lstrip("-").replace("-", "_")] = (
            Path(value) if key in ("--out", "--state", "--atlas") else value
        )
    return types.SimpleNamespace(**values)


# The research module itself, with only its GPU entry points faked: its video and scene helpers run for real.
from worldbuilder_splat import splat2

splat2.run = _run
splat2.parser = lambda: types.SimpleNamespace(parse_args=_parse)

from worldbuilder_splat import frames

frames.fit_frames = lambda out, atlas, steps: _run(
    types.SimpleNamespace(command="init-frames", out=Path(out), atlas=Path(atlas))
)
frames.extend_frames = lambda out, source, atlas, steps: _run(
    types.SimpleNamespace(command="extend-frames", out=Path(out), atlas=Path(atlas))
)

import worldbuilder as worker


def _fake_render(job):
    """render_views without gsplat: a flat picture per camera, named by its key, of the camera's size."""
    from PIL import Image

    for view in job.params["views"]:
        target = job.target(f"{view['key']}.png")
        Image.new("RGB", (int(view["width"]), int(view["height"])), (90, 120, 150)).save(target)
        job.output(view["key"], target, "image", hidden=len(job.params.get("hide") or []))


worker.TASKS["render_views"] = _fake_render

media = Path(os.environ.get("GAIAVFX_MEDIA_DIR") or ".") / (
    "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
)
if media.is_file():
    worker._ffmpeg = lambda job: str(media)


def _fake_paths(job):
    """fill_paths without gsplat: every probe's depth control a flat video of its move's length."""
    import numpy as np

    folder = worker._round(job)
    plan = worker._plan(folder)
    for n, probe in enumerate(plan["probes"]):
        here = folder / probe["folder"]
        count = len(json.loads((here / "poses.json").read_text(encoding="utf-8")))
        depth = np.full((count, probe["height"], probe["width"]), 128, np.uint8)
        splat2.encode_video(here / "depthvis.mp4", depth, worker._ffmpeg(job), lossless=True)
        worker._output_file(
            job, f"p{n}-depth.mp4", here / "depthvis.mp4", f"depth:{n}", "set_probe"
        )
    job.output(
        "summary",
        worker._write_json(job, "summary.json", {"probes": len(plan["probes"])}),
        "analysis_report",
    )


worker.TASKS["fill_paths"] = _fake_paths

crash = sys.argv[sys.argv.index("--crash") + 1] if "--crash" in sys.argv else None
if crash:
    index = sys.argv.index("--crash")
    del sys.argv[index : index + 2]

    def _crash(job):
        raise RuntimeError(f"{crash} crashed on purpose")

    worker.TASKS[crash] = _crash

# A fill round's memory runs in worker processes, which import this script again (Windows spawns them): only the
# process that was started runs the job.
if __name__ == "__main__":
    sys.exit(worker.run(worker.TASKS, probe=worker.probe))
