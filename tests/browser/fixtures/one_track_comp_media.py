"""Prepare the media of the one-track comp-state fixture (ADR 0017 hero gate) from a Tears of Steel recast library.

Reads the library read-only and writes `.cache/one-track-comp/`: the film at 640 px (24 fps, a keyframe every
second, so the monitors can seek), each shot's analysis poster, and the two target pictures. With `--shots` it also
rewrites `tests/browser/fixtures/one-track-comp.json` from the analysis report: the film's shot boundaries.

    python tests/browser/fixtures/one_track_comp_media.py --library .cache/recast-trial/library [--shots]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / ".cache" / "one-track-comp"
SHOTS = Path(__file__).with_name("one-track-comp.json")
FFMPEG = ROOT / ".runtime" / "media" / "ffmpeg.exe"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--shots", action="store_true")
    # The comp's monitors show frame 6552 (04:33:00, Thom on the canal bridge): the playhead of the comp state.
    parser.add_argument("--playhead", type=int, default=6552)
    args = parser.parse_args()
    library = args.library.resolve()
    db = sqlite3.connect(f"file:{(library / 'library.sqlite3').as_posix()}?mode=ro", uri=True)
    recast = db.execute("select source_asset_id, analysis_operation_id from editing_recasts").fetchone()
    film = db.execute("select path from library_assets where id = ?", (recast[0],)).fetchone()
    report = None
    for (path,) in db.execute(
        "select path from library_artifacts where kind = 'analysis_report' and path like '%.json' order by created_at"
    ):
        data = json.loads((library / path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and len(data.get("shots", [])) > 100:
            report = data
            break
    assert report, "no film analysis report with shots"
    targets = db.execute(
        "select label, target_asset_id from editing_recast_characters where target_asset_id is not null order by ordinal"
    ).fetchall()

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "posters").mkdir(exist_ok=True)
    subprocess.run(
        [str(FFMPEG), "-nostdin", "-y", "-loglevel", "error", "-i", str(library / film[0]), "-an",
         "-vf", "scale=640:-2", "-r", "24", "-g", "24", "-c:v", "libx264", "-preset", "veryfast",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(OUT / "film.mp4")],
        check=True,
    )
    # A version preview of the shot under the playhead: the original shot's own frames, as a version file starts
    # at the shot.
    fps = report["fps"][0] / report["fps"][1]
    shot = next(s for s in report["shots"] if s["start"] <= args.playhead < s["end"])
    subprocess.run(
        [str(FFMPEG), "-nostdin", "-y", "-loglevel", "error", "-ss", f"{shot['start'] / fps:.4f}",
         "-i", str(OUT / "film.mp4"), "-frames:v", str(shot["end"] - shot["start"]), "-an", "-c:v", "libx264",
         "-preset", "veryfast", "-g", "24", "-pix_fmt", "yuv420p", str(OUT / "version.mp4")],
        check=True,
    )
    for each in report["shots"]:
        row = db.execute("select path from library_artifacts where id = ?", (each["poster_artifact"],)).fetchone()
        if row:
            shutil.copyfile(library / row[0], OUT / "posters" / f"{each['index']:03d}.png")
    # The current shot's poster is its frame under the playhead, as the comp shows it.
    subprocess.run(
        [str(FFMPEG), "-nostdin", "-y", "-loglevel", "error", "-ss", f"{args.playhead / fps:.4f}",
         "-i", str(library / film[0]), "-frames:v", "1", "-vf", "scale=480:-2",
         str(OUT / "posters" / f"{shot['index']:03d}.png")],
        check=True,
    )
    for label, asset_id in targets:
        row = db.execute("select path from library_assets where id = ?", (asset_id,)).fetchone()
        shutil.copyfile(library / row[0], OUT / f"{label.lower().replace(' ', '-')}.png")
    if args.shots:
        SHOTS.write_text(
            json.dumps(
                {
                    "source": "Tears of Steel (CC BY 3.0, Blender Foundation); boundaries from the 8 Oct trial analysis",
                    "fps": report["fps"],
                    "frame_count": report["frame_count"],
                    "width": report["width"],
                    "height": report["height"],
                    "playhead": args.playhead,
                    "shots": [[s["start"], s["end"]] for s in report["shots"]],
                }
            )
            + "\n",
            encoding="utf-8",
        )
    print(f"{len(report['shots'])} shots, {len(targets)} targets -> {OUT}")


if __name__ == "__main__":
    main()
