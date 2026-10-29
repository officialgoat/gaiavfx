"""The worldbuilder engine's worker (ADR 0015, ADR 0018 §4). Runs in the worldbuilder home's interpreter (the splat
research environment: torch, gsplat, plyfile).

A set's atlas is a folder: an atlas.json and the keyframe files its records name (relative to it). The splat tasks
read `inputs["atlas"]`, or, with `atlas_state`, the atlas that `atlas_prepare` left in the operation's scratch folder
(`scratch/atlas/<name>`).

- `atlas_prepare`        (CPU) lay out a version's atlas from Library files, retract keyframes and recompute the
                         eye-level map, as an import's bundle computes it. inputs: `atlas` (the version's atlas, its
                         current keyframes' files named by sha256) and each current keyframe's still and depth.
                         params: `name`, `files` ({record id: {"image": input key, "data": input key}}), `retract`
                         ([{"record", "reason"}], whole keyframes), `keep` ([{"record", "glass", "reason"}]: probe
                         stills consolidation set aside, kept by the user, with the input key of the still geometry
                         that judged them, ADR 0020 §8), `voxel`, `room`, `height` and `grid` (the base map's, so the
                         two maps are cut alike and B3 names one place in both). Outputs: `atlas` (`set_atlas`, files
                         named by sha256 again), `map` (`set_map`), `summary` (`analysis_report`: the map's metadata
                         and the labelled current keyframes) and, for each kept still, `data:<id>` (`set_keyframe`).
- `atlas_from_frames`    (CPU) a set seeded from a take (ADR 0019 §4, ADR 0020 §3): the reconstruction gate on the
                         take's DA3 geometry (`worldbuilder_splat/frames.py`) and the layout it allows (`moving` when
                         it holds; `centre`, one viewpoint and its headings, when the camera only stood still or
                         turned and `fill` is on), its frames as pinhole keyframes in a levelled world, people
                         retracted, with their map. inputs: `geometry` (`set_geometry`, the npz GaiaSaveSetGeometry
                         saves). params: `name`, `place` (the atlas's place), `gate` (the gate's settings), `fill`
                         (the unseen space will be filled). Outputs: `summary` (`analysis_report`: the gate's verdict
                         and numbers, the `layout` or null, and with a layout the map's metadata, the labelled
                         keyframes and the levelling) and, with a layout, `atlas`, `map`, and for each keyframe
                         `still:<id>` (`set_still`) and `data:<id>` (`set_keyframe`).
- `splat_init_atlas`     rebuild the set's splat from every current keyframe of the atlas. params: `name` (the state's
                         folder), `steps` (default 3000), `seam_mask_deg` (default 0), `atlas_state`. An atlas
                         without a panorama (a seed's) is fitted by the frames path (`frames.fit_frames`).
- `splat_extend`         extend the state `from` into `name` with the atlas's new keyframes. params: `from`, `name`,
                         `steps` (default 1500), `atlas_state`. A take's state is extended by the frames path
                         (`frames.extend_frames`).
- `splat_atlas_metrics`  held-out metrics of the state `name` against the atlas's keyframes. params: `name`,
                         `atlas_state`.
- `render_views`         pictures of a set's cameras (ADR 0019 §3): inputs `splat` (a version's `set_splat` PLY);
                         params `views` ([{"key", "viewmat", "fov", "width", "height"}]: OpenCV world-to-camera poses,
                         the horizontal field of view in degrees) and `hide` ([{"shape": "sphere" | "box", "params"}],
                         the version's hidden regions, hidden as the workbench hides them: every gaussian whose centre
                         lies inside). Outputs: one `image` per view, named by its key.

Filling a stage's unseen space (ADR 0020 §4), one round in four tasks around the H3 probes and the stills' geometry
(both ComfyUI). A round is a folder, `scratch/fill/<name>`, holding its plan and a folder per probe:
- `fill_explore`         (CPU) plan the round on the atlas (`worldbuilder_splat/fill.py`: holds, turns, then steps) and
                         write each probe's move: its cameras, its memory along the move and its regenerate mask (lossless
                         videos) and its first frame. params: `name`, `atlas_state`, `rules` (the `[fill]` rules), `seed`,
                         `voxel` and `room` (the first round's), `frames` (73), `workers`. Outputs: `summary` (the plan
                         and the coverage before it) and per probe `first:<n>`, `memory:<n>`, `regen:<n>` (`set_probe`).
- `fill_paths`           (GPU) the splat's depth along each probe's move, as R5's Fun ControlNet control. params: `name`,
                         `from` (the splat state). Outputs: per probe `depth:<n>` (`set_probe`).
- `fill_stills`          (CPU) each rendered probe's last frame, where it was really seen from, its memory restored there
                         exactly. inputs: each rendered probe's video, named in params `videos` ({n: input key}); a
                         probe without one failed: rejected. params: `name`, `atlas_state`, `videos`. Outputs: per still `still:<n>` (`set_probe`).
- `fill_consolidate`     (CPU) the round's stills into the next atlas, one at a time (R5's geometric acceptance; the rest
                         enter as appearance only), with their trajectory frames, and its map. inputs: `geometry:<n>`
                         (MoGe's depth, its mask and SAM 3's glass) and the videos `videos` names. params: `name`,
                         `atlas_state` (the base), `atlas` (the new atlas's name), `rules`, `voxel`, `room`, `height`,
                         `grid` (the base
                         map's). Outputs as `atlas_from_frames`'s, the summary adding the round (accepted, coverage,
                         gain).

A splat's state is a folder (`splat.ply`, the training state, its views) kept in the operation's scratch folder,
`scratch/splat/<name>`, so the next round extends it; a relaunch whose state already holds a summary for the same
atlas and settings skips the fit. Outputs: `splat` (`set_splat`, the PLY) and `summary` (`analysis_report`, the fit's
or the metrics' numbers). The fitting and map code is the research's own (`worldbuilder_splat/`, vendored unchanged
but for its imports), so its numbers can be compared with the research runs.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path

from protocol import Job, run


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _plain(value):
    return value.tolist() if hasattr(value, "tolist") else str(value)


def _folder(job: Job, kind: str, name: str) -> Path:
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("A state's name is a plain folder name.")
    root = (job.scratch or job.work) / kind
    root.mkdir(parents=True, exist_ok=True)
    return root / name


def _state(job: Job, name: str) -> Path:
    return _folder(job, "splat", name)


def _atlas(job: Job) -> Path:
    name = job.params.get("atlas_state")
    if not name:
        return job.inputs["atlas"]
    path = _folder(job, "atlas", name) / "atlas.json"
    if not path.is_file():
        raise FileNotFoundError(f"No prepared atlas {name}; an atlas task runs first.")
    return path


def _identity(job: Job, extra: dict) -> dict:
    return {"task": job.task, "atlas": _sha(_atlas(job)), **extra}


def _write_json(job: Job, name: str, document) -> Path:
    target = job.target(name)
    with job.writing(target) as partial:
        partial.write_text(json.dumps(document, default=_plain), encoding="utf-8")
    return target


# ---------------------------------------------------------------- atlas


def _heading(viewmat) -> float:
    ahead = [float(v) for v in viewmat[2][:3]]
    return round(math.degrees(math.atan2(ahead[0], ahead[2])) % 360, 1)


def _labelled(cameras: list[dict], current: list[dict]) -> list[tuple[str, dict]]:
    """An import's labels (the maintained engine implementation): the geometry cameras keep their K numbers, the other
    current records are A1, A2, ... in atlas order; K labels first."""
    label_of = {c["id"]: c["label"] for c in cameras}
    out, extra = [], 0
    for record in current:
        label = label_of.get(record["id"])
        if label is None:
            extra += 1
            label = f"A{extra}"
        out.append((label, record))
    return sorted(out, key=lambda item: (item[0][0] != "K", int(item[0][1:])))


def _lay_out(job: Job, folder: Path) -> dict:
    """The base atlas with its current keyframes' files copied into `folder/keyframes` and named relative to it."""
    from worldbuilder_splat import memory as mem

    atlas = json.loads(job.inputs["atlas"].read_text(encoding="utf-8"))
    files = job.params.get("files") or {}
    (folder / "keyframes").mkdir(parents=True)
    current = {r["id"] for r in mem.current(atlas)}
    for record in atlas["keyframes"]:
        if record["id"] not in current:
            continue
        keys = files.get(record["id"])
        if not keys:
            raise ValueError(f"The job lacks the files of keyframe {record['id']}.")
        name = re.sub(r"[^A-Za-z0-9_.-]", "-", record["id"])
        for field, suffix in (("image", ".png"), ("data", ".npz")):
            relative = f"keyframes/{name}{suffix}"
            shutil.copyfile(job.inputs[keys[field]], folder / relative)
            record[field] = relative
    return atlas


def _by_content(atlas: dict, folder: Path) -> dict:
    """The atlas as Library keeps it: every file this folder holds named by its sha256, as an import names them."""
    out = copy.deepcopy(atlas)
    for record in out["keyframes"]:
        if isinstance(record.get("image"), str) and (folder / record["image"]).is_file():
            record["image"] = {"asset_sha256": _sha(folder / record["image"])}
            record["data"] = {"artifact_sha256": _sha(folder / record["data"])}
    return out


@contextmanager
def _lattice(ex, params: dict):
    """While carving, the free-space grid's lower corner moves down onto the base map's voxel lattice (its grid's
    `lower` and voxel), so every voxel, and with it every grid cell, lies where it lay in the base map. Without a base
    grid at the same voxel, the research's own grid."""
    import numpy as np

    base = params.get("grid")
    if not base or params.get("voxel") is None:
        yield
        return
    original = ex.FreeSpace.around.__func__
    origin = np.asarray(base["lower"], float)

    def around(cls, *args, **kwargs):
        space = original(cls, *args, **kwargs)
        voxel = space.voxel
        lower = origin + np.floor((space.lower - origin) / voxel) * voxel
        shape = np.maximum(np.ceil((space.upper - lower) / voxel - 1e-9).astype(int), 1)
        return cls(lower, voxel, np.zeros(tuple(shape), dtype=np.int8))

    ex.FreeSpace.around = classmethod(around)
    try:
        yield
    finally:
        ex.FreeSpace.around = classmethod(original)


def _carve(atlas_path: Path, params: dict, band_rooms: float = 0.15) -> dict:
    """The roomba map as `plausibility.place_map` carves it, with the base map's voxel, room and eye height when the
    params hold them, so a retraction changes what the map shows and not the plane it is cut at. An atlas without a
    panorama (a seed's) takes its room from the depth of what its frames saw (`frames.frames_room`)."""
    from worldbuilder_splat import explore as ex
    from worldbuilder_splat import frames as fr
    from worldbuilder_splat import memory as mem

    import numpy as np

    keyframes = mem.load_keyframes(atlas_path)
    with _lattice(ex, params):
        space = ex.observed_space(keyframes, voxel=params.get("voxel"))
    pano = next((k for k in keyframes if k.kind == "erp"), None)
    room = params.get("room") or (
        ex.clearance(space, pano.centre, horizontal=True, quantile=0.35)
        if pano is not None
        else fr.frames_room(keyframes)
    )
    height = params.get("height")
    height = float(np.mean([k.centre[1] for k in keyframes])) if height is None else float(height)
    plane = ex.eye_plane(space, height, max(2 * space.voxel, band_rooms * room))
    return {
        "space": space,
        "plane": plane,
        "frontier": ex.frontier_cells(plane),
        "room": float(room),
        "height": height,
    }


def _grid(plane, space, base: dict | None):
    """The base map's lettered grid, moved onto this map's cells (one voxel, the same world origin), so B3 names the
    same place in both versions; a new grid only without a base or at another voxel size."""
    from worldbuilder_splat import plausibility as pl

    if not base or abs(float(base["voxel"]) - float(space.voxel)) > 1e-9:
        return pl.grid_for(plane, space.voxel, space.lower)
    voxel = float(space.voxel)
    i0 = round((base["lower"][0] + base["i0"] * voxel - float(space.lower[0])) / voxel)
    k0 = round((base["lower"][2] + base["k0"] * voxel - float(space.lower[2])) / voxel)
    return pl.Grid(
        i0, k0, base["size"], base["cols"], base["rows"], voxel, tuple(float(v) for v in space.lower)
    )


def atlas_prepare(job: Job) -> None:
    from worldbuilder_splat import memory as mem

    folder = _folder(job, "atlas", job.params["name"])
    keep = job.params.get("keep") or []
    identity = {
        "task": job.task,
        "atlas": _sha(job.inputs["atlas"]),
        **{k: job.params.get(k) for k in ("files", "retract", "voxel", "room", "height", "grid")},
        **({"keep": keep, "glass": [_sha(job.inputs[i["glass"]]) for i in keep]} if keep else {}),
    }
    done = folder / "worker-summary.json"
    if done.is_file() and json.loads(done.read_text(encoding="utf-8")).get("identity") == identity:
        job.progress(1.0, "Already prepared")
    else:
        if folder.exists():
            shutil.rmtree(folder)
        job.progress(0.05, "Laying out the atlas")
        atlas = _lay_out(job, folder)
        for item in job.params.get("retract") or []:
            record = next((r for r in mem.current(atlas) if r["id"] == item["record"]), None)
            if record is None:
                raise ValueError(f"Keyframe {item['record']} is not current in this atlas.")
            if record.get("kind") == "erp":
                raise ValueError("The panorama is the set's ground truth; it cannot be retracted.")
            atlas = mem.retract(
                atlas, folder, folder / "keyframes", record, reason=item.get("reason", "")
            )
        for item in keep:
            atlas = _keep(job, folder, atlas, item)
        atlas_path = folder / "atlas.json"
        atlas_path.write_text(json.dumps(atlas, indent=1), encoding="utf-8")
        job.check()
        summary = _describe(job, folder, atlas, atlas_path, "worldbuilder engine, atlas_prepare")
        done.write_text(
            json.dumps({"identity": identity, "summary": summary}, default=_plain),
            encoding="utf-8",
        )
        job.progress(0.95, "Prepared")
    summary = json.loads(done.read_text(encoding="utf-8"))["summary"]
    _publish_atlas(job, folder)
    atlas = json.loads((folder / "atlas.json").read_text(encoding="utf-8"))
    for record in atlas["keyframes"]:
        if record.get("kept"):
            # A kept still's new arrays (its picture is unchanged): the version's depth artifact for it.
            name = f"{record['id']}.npz"
            _output_file(job, name, folder / record["data"], f"data:{record['id']}", "set_keyframe")
    job.output("summary", _write_json(job, "summary.json", summary), "analysis_report")


def _keep(job: Job, folder: Path, atlas: dict, item: dict) -> dict:
    """Keep a probe still that consolidation set aside (ADR 0020 §8): a record that supersedes it with role
    `generated`, its trusted and solid pixels restored as consolidation would have kept them, from its own aligned
    depth and SAM 3's glass in the still geometry that judged it (`memory.view_keyframe`, as `fill.consolidate`
    calls it). Its picture and pose are unchanged; a rejected still has no trajectory frames to add."""
    import numpy as np
    from PIL import Image
    from worldbuilder_splat import explore as ex
    from worldbuilder_splat import memory as mem

    record = next((r for r in mem.current(atlas) if r["id"] == item["record"]), None)
    if record is None:
        raise ValueError(f"Keyframe {item['record']} is not current in this atlas.")
    if record.get("role") != mem.APPEARANCE_ONLY or "consolidation" not in record:
        raise ValueError(f"Keyframe {record['id']} is not a still that consolidation set aside.")
    key = mem.load_keyframe(record, folder)
    h, w = key.depth.shape
    with np.load(job.inputs[item["glass"]]) as data:
        glass = np.squeeze(data["glass"]) > 0.5 if "glass" in data.files else np.zeros((h, w), bool)
    if glass.shape != (h, w):
        glass = np.asarray(Image.fromarray(glass).resize((w, h), Image.NEAREST))
    glass = glass if glass.mean() < 0.5 else np.zeros_like(glass)
    root = record["id"].split("~")[0]
    new_id = f"{root}~{1 + sum(1 for r in atlas['keyframes'] if r['id'].startswith(root + '~'))}"
    # The pixels MoGe measured, before consolidation fitted planes into the glass (it stores that fit's valid).
    measured = key.valid & np.isfinite(key.depth) & (key.depth > 0)
    kept = mem.view_keyframe(
        new_id, key.image, key.viewmat, key.k, key.depth, measured, glass=mem.grow(glass, 4)
    )
    names = ("id", "image", "data", "sha256", "viewmat", "position", "K", "role")
    extra = {k: v for k, v in record.items() if k not in names}
    extra |= {"role": "generated", "supersedes": record["id"], "kept": {"reason": item.get("reason", "")}}
    return ex.add_keyframe(atlas, mem.keyframe_record(kept, folder / "keyframes", folder, **extra), "kept")


def _describe(job: Job, folder: Path, atlas: dict, atlas_path: Path, producer: str) -> dict:
    """Carve the atlas's map into `folder/map.npz`, name its files by content (`atlas-by-content.json`), and say
    what the version holds: the map's metadata and its current keyframes, labelled."""
    from worldbuilder_splat import explore as ex
    from worldbuilder_splat import memory as mem
    from worldbuilder_splat import plausibility as pl

    import numpy as np

    job.progress(0.3, "Carving the map")
    m = _carve(atlas_path, job.params)
    space, plane = m["space"], m["plane"]
    grid = _grid(plane, space, job.params.get("grid"))
    cameras = pl.cameras_of(atlas)
    hints = pl.map_hints(plane, space.voxel, m["room"], grid, cameras)
    np.savez_compressed(
        folder / "map.npz",
        plane=plane,
        frontier=m["frontier"],
        lower=space.lower,
        voxel=np.float32(space.voxel),
    )

    def strip(items):
        return [{k: v for k, v in item.items() if k != "region"} for item in items]

    meta = {
        "producer": producer,
        "voxel": float(space.voxel),
        "lower": [float(v) for v in space.lower],
        "shape": list(plane.shape),
        "room": round(float(m["room"]), 4),
        "height": round(float(m["height"]), 5),
        "grid": grid.record(),
        "coverage": ex.coverage(space, plane, m["room"]),
        "cameras": [{**c, "cell": grid.name_of(c["position"])} for c in cameras],
        "walls": strip(hints["walls"]),
        "wall_angles": hints["angles"],
        "hints": strip(hints["hints"]),
        "rectilinear": hints["rectilinear"],
    }
    keyframes = [
        {
            "record": record["id"],
            "key": record["id"],
            "label": label,
            "kind": record["kind"],
            "role": record.get("role", "generated"),
            "supersedes": record.get("supersedes"),
            "viewmat": record["viewmat"],
            "intrinsics": record.get("K"),
            "heading": None if record["kind"] == "erp" else _heading(record["viewmat"]),
            "image_sha256": _sha(folder / record["image"]),
            "data_sha256": _sha(folder / record["data"]),
            **_size(folder / record["image"]),
            "info": {
                k: record[k]
                for k in (
                    "consolidation",
                    "retraction",
                    "kept",
                    "supersedes",
                    "position",
                    "frame",
                    "heldout",
                    "people",
                )
                if k in record
            },
        }
        for label, record in _labelled(cameras, mem.current(atlas))
    ]
    (folder / "atlas-by-content.json").write_text(
        json.dumps(_by_content(atlas, folder), indent=1), encoding="utf-8"
    )
    return {"atlas_version": atlas["version"], "map_meta": meta, "keyframes": keyframes}


def _size(path: Path) -> dict:
    from PIL import Image

    with Image.open(path) as image:  # closed at once: Windows refuses to remove a folder with an open file
        return {"width": image.width, "height": image.height}


def _output_file(job: Job, name: str, source: Path, key: str, kind: str) -> None:
    target = job.target(name)
    with job.writing(target) as partial:
        shutil.copyfile(source, partial)
    job.output(key, target, kind)


def _publish_atlas(job: Job, folder: Path) -> None:
    for key, name, kind in (("atlas", "atlas-by-content.json", "set_atlas"), ("map", "map.npz", "set_map")):
        _output_file(job, name, folder / name, key, kind)


def atlas_from_frames(job: Job) -> None:
    from worldbuilder_splat import frames as fr
    from worldbuilder_splat import memory as mem

    folder = _folder(job, "atlas", job.params["name"])
    identity = {
        "task": job.task,
        "geometry": _sha(job.inputs["geometry"]),
        **{k: job.params.get(k) for k in ("place", "gate")},
        "fill": bool(job.params.get("fill")),
    }
    done = folder / "worker-summary.json"
    if done.is_file() and json.loads(done.read_text(encoding="utf-8")).get("identity") == identity:
        job.progress(1.0, "Already prepared")
    else:
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        job.progress(0.05, "Reading the take's geometry")
        frames = fr.load(job.inputs["geometry"])
        gate = fr.Gate.of(job.params.get("gate"))
        job.progress(0.15, "Testing whether the take holds one place")
        record, _ = fr.verdict(frames, gate)
        layout = fr.layout(record, bool(job.params.get("fill")))
        summary = {"gate": record, "layout": layout}
        if layout:
            job.check()
            job.progress(0.6, "Laying out the keyframes")
            place = job.params.get("place") or "Set"
            atlas = fr.build_atlas(frames, folder, place, gate, record, layout)
            atlas_path = folder / "atlas.json"
            atlas_path.write_text(json.dumps(atlas, indent=1), encoding="utf-8")
            summary |= _describe(job, folder, atlas, atlas_path, "worldbuilder engine, atlas_from_frames")
            summary["levelling"] = atlas["levelling"]
        done.write_text(json.dumps({"identity": identity, "summary": summary}, default=_plain), encoding="utf-8")
        job.progress(0.95, "Prepared" if layout else "Refused")
    summary = json.loads(done.read_text(encoding="utf-8"))["summary"]
    if summary["layout"]:
        _publish_atlas(job, folder)
        atlas = json.loads((folder / "atlas.json").read_text(encoding="utf-8"))
        for record in mem.current(atlas):
            _output_file(job, f"{record['id']}.png", folder / record["image"], f"still:{record['id']}", "set_still")
            _output_file(job, f"{record['id']}.npz", folder / record["data"], f"data:{record['id']}", "set_keyframe")
    job.output("summary", _write_json(job, "summary.json", summary), "analysis_report")


# ---------------------------------------------------------------- splat


def _splat2(argv: list[str]) -> dict:
    from worldbuilder_splat import splat2

    return splat2.run(splat2.parser().parse_args(argv))


def _publish(job: Job, state: Path, summary: dict) -> None:
    job.output("summary", _write_json(job, "summary.json", summary), "analysis_report")
    splat = state / "splat.ply"
    if splat.is_file():
        copy_path = job.target("splat.ply")
        if not copy_path.exists() or copy_path.stat().st_size != splat.stat().st_size:
            with job.writing(copy_path) as partial:
                shutil.copyfile(splat, partial)
        job.output(
            "splat",
            copy_path,
            "set_splat",
            gaussians=summary.get("gaussians"),
            sh_degree=_sh_degree(copy_path),
            state=state.name,
        )


def _sh_degree(path: Path) -> int:
    """The spherical-harmonics degree a 3DGS PLY header declares (its f_rest_ properties), as an import reads it."""
    with path.open("rb") as stream:
        head = stream.read(1 << 16).split(b"end_header", 1)[0].decode("ascii", "replace")
    rest = sum(line.split()[-1].startswith("f_rest_") for line in head.splitlines() if line.startswith("property"))
    return math.isqrt(rest // 3 + 1) - 1


def _fit(job: Job, state: Path, identity: dict, fit) -> None:
    done = state / "worker-summary.json"
    if done.is_file():
        previous = json.loads(done.read_text(encoding="utf-8"))
        if previous.get("identity") == identity:
            job.progress(1.0, "Already fitted")
            _publish(job, state, previous["summary"])
            return
    if state.exists():
        shutil.rmtree(state)
    job.check()
    job.progress(0.02, "Fitting the splat")
    summary = fit()
    job.check()
    with job.writing(done) as partial:
        partial.write_text(
            json.dumps({"identity": identity, "summary": summary}, default=str),
            encoding="utf-8",
        )
    job.progress(1.0, "Fitted")
    _publish(job, state, summary)


def splat_init_atlas(job: Job) -> None:
    steps = int(job.params.get("steps", 3000))
    seam = float(job.params.get("seam_mask_deg", 0.0))
    state = _state(job, job.params["name"])
    atlas = _atlas(job)
    identity = _identity(job, {"steps": steps, "seam": seam})
    if not _has_panorama(atlas):
        from worldbuilder_splat import frames as fr

        _fit(job, state, identity, lambda: fr.fit_frames(state, atlas, steps))
        return
    argv = ["init-atlas", "--out", str(state), "--atlas", str(atlas)]
    argv += ["--steps", str(steps), "--seam-mask-deg", str(seam)]
    _fit(job, state, identity, lambda: _splat2(argv))


def _has_panorama(atlas_path: Path) -> bool:
    from worldbuilder_splat import memory as mem

    atlas = json.loads(atlas_path.read_text(encoding="utf-8"))
    return any(r.get("kind") == "erp" for r in mem.current(atlas))


def splat_extend(job: Job) -> None:
    steps = int(job.params.get("steps", 1500))
    source = _state(job, job.params["from"])
    if not (source / "splat.ply").is_file():
        raise FileNotFoundError(f"No splat state {source.name} to extend.")
    state = _state(job, job.params["name"])
    atlas = _atlas(job)
    identity = _identity(job, {"steps": steps, "from": source.name})
    if not _has_panorama(atlas):
        from worldbuilder_splat import frames as fr

        _fit(job, state, identity, lambda: fr.extend_frames(state, source, atlas, steps))
        return
    argv = ["extend", "--out", str(state), "--state", str(source), "--atlas", str(atlas)]
    argv += ["--steps", str(steps)]
    _fit(job, state, identity, lambda: _splat2(argv))


def splat_atlas_metrics(job: Job) -> None:
    state = _state(job, job.params["name"])
    if not (state / "splat.ply").is_file():
        raise FileNotFoundError(f"No splat state {state.name} to measure.")
    out = job.work / "metrics"
    job.progress(0.05, "Rendering the held-out views")
    summary = _splat2(
        ["atlas-metrics", "--out", str(out), "--state", str(state), "--atlas", str(_atlas(job))]
    )
    job.output("summary", _write_json(job, "summary.json", summary), "analysis_report")


def _read_ply(path: Path, device):
    """A 3DGS PLY (SH degree 1, `splat2.save_ply`'s layout) as the renderer's parameters, read without pickle."""
    import numpy as np
    import torch
    from plyfile import PlyData

    vertex = PlyData.read(str(path))["vertex"].data
    rest = sorted(
        (n for n in vertex.dtype.names if n.startswith("f_rest_")), key=lambda n: int(n.rsplit("_", 1)[1])
    )
    if len(rest) != 9:
        raise ValueError("render_views draws splats of SH degree 1 (nine f_rest values).")

    def columns(*names):
        return np.stack([np.asarray(vertex[n], dtype=np.float32) for n in names], 1)

    count = len(vertex)
    values = {
        "means": columns("x", "y", "z"),
        "sh0": columns("f_dc_0", "f_dc_1", "f_dc_2")[:, None, :],
        "shN": columns(*rest).reshape(count, 3, 3).transpose(0, 2, 1),
        "opacities": np.asarray(vertex["opacity"], dtype=np.float32),
        "scales": columns("scale_0", "scale_1", "scale_2"),
        "quats": columns("rot_0", "rot_1", "rot_2", "rot_3"),
    }
    return {k: torch.as_tensor(np.ascontiguousarray(v), device=device) for k, v in values.items()}


def hidden(means, hide):
    """Which gaussians the hidden regions cover: a centre inside a sphere, or inside an axis-aligned box."""
    import numpy as np

    mask = np.zeros(len(means), dtype=bool)
    for region in hide:
        centre = np.asarray(region["params"]["centre"], dtype=np.float32)
        if region["shape"] == "sphere":
            radius = float(region["params"]["radius"])
            mask |= ((means - centre) ** 2).sum(1) <= radius**2
        elif region["shape"] == "box":
            half = np.asarray(region["params"]["size"], dtype=np.float32) / 2
            mask |= (np.abs(means - centre) <= half).all(1)
        else:
            raise ValueError(f"Unknown hidden region shape {region['shape']!r}.")
    return mask


def render_views(job: Job) -> None:
    import numpy as np
    import torch
    from PIL import Image

    from worldbuilder_splat import geometry as geo
    from worldbuilder_splat import splat2

    views = job.params.get("views") or []
    if not views:
        raise ValueError("render_views needs at least one view.")
    keys = [str(v["key"]) for v in views]
    if len(set(keys)) != len(keys) or not all(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", k) for k in keys):
        raise ValueError("Each view needs its own key of letters, digits, '-' or '_'.")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    job.progress(0.02, "Loading the splat")
    params = _read_ply(job.inputs["splat"], device)
    covered = hidden(params["means"].cpu().numpy(), job.params.get("hide") or [])
    if covered.any():
        params["opacities"][torch.as_tensor(covered, device=device)] = -1e4
    for index, (key, view) in enumerate(zip(keys, views, strict=True)):
        job.check()
        width, height = int(view["width"]), int(view["height"])
        k = geo.intrinsics(float(view["fov"]), width, height)
        rgb, _, _ = splat2.render_numpy(params, np.asarray(view["viewmat"], dtype=float), k, width, height)
        target = job.target(f"{key}.png")
        with job.writing(target) as partial:
            Image.fromarray((rgb * 255).round().astype(np.uint8)).save(partial, format="PNG")
        job.output(key, target, "image", width=width, height=height, hidden=int(covered.sum()))
        job.progress((index + 1) / len(views), f"Rendered {index + 1} of {len(views)}")


# ---------------------------------------------------------------- filling unseen space (ADR 0020 §4)


def _ffmpeg(job: Job) -> str:
    """Use the engine's program, configured media, bundled tools, then PATH."""
    from worldbuilder_splat import splat2

    found = str(job.programs["ffmpeg"]) if "ffmpeg" in job.programs else splat2._ffmpeg()
    if not Path(found).is_file() and not shutil.which(found):
        raise RuntimeError("Filling a stage needs ffmpeg (register it or install the media tools).")
    return found


def _round(job: Job) -> Path:
    return _folder(job, "fill", job.params["name"])


def _plan(folder: Path) -> dict:
    path = folder / "probes.json"
    if not path.is_file():
        raise FileNotFoundError(f"No planned round {folder.name}; fill_explore runs first.")
    return json.loads(path.read_text(encoding="utf-8"))


def _videos(job: Job, plan: dict) -> dict:
    """Each probe's rendered video, or None where its render failed: `params["videos"]` names the input that holds
    probe n's (the app keys an asset input by the asset's id)."""
    named = job.params.get("videos") or {}
    return {n: job.inputs.get(named[str(n)]) if str(n) in named else None for n in range(len(plan["probes"]))}


def _done(folder: Path, identity: dict) -> dict | None:
    done = folder / "worker-summary.json"
    if done.is_file():
        previous = json.loads(done.read_text(encoding="utf-8"))
        if previous.get("identity") == identity:
            return previous["summary"]
    return None


def _finish(folder: Path, identity: dict, summary: dict) -> None:
    (folder / "worker-summary.json").write_text(
        json.dumps({"identity": identity, "summary": summary}, default=_plain), encoding="utf-8"
    )


def _image(path: Path, array) -> Path:
    from PIL import Image

    import numpy as np

    Image.fromarray(np.asarray(array, dtype=np.uint8)).save(path)
    return path


def fill_explore(job: Job) -> None:
    from worldbuilder_splat import fill
    from worldbuilder_splat import frames as fr
    from worldbuilder_splat import memory as mem
    from worldbuilder_splat import splat2

    import numpy as np

    atlas = _atlas(job)
    folder = _round(job)
    keys = ("rules", "seed", "voxel", "room", "frames", "toward")  # not `workers`: it changes no result
    identity = {"task": job.task, "atlas": _sha(atlas), **{k: job.params.get(k) for k in keys}}
    summary = _done(folder, identity)
    if summary is None:
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        ffmpeg = _ffmpeg(job)
        rules = fill.Rules.of(job.params.get("rules"))
        job.progress(0.05, "Carving the map")
        keyframes = mem.load_keyframes(atlas)
        room = float(job.params.get("room") or fr.frames_room(keyframes))
        space = fill.observed(keyframes, room, rules.margin, voxel=job.params.get("voxel"))
        job.progress(0.15, "Planning the probes")
        probes = fill.plan(
            space,
            keyframes,
            room,
            rules,
            seed=int(job.params.get("seed") or 0),
            toward=job.params.get("toward"),
        )
        by_id = {k.id: k for k in keyframes}
        planned = []
        for n, probe in enumerate(probes):
            job.check()
            job.progress(0.2 + 0.75 * n / max(len(probes), 1), f"Remembering probe {n + 1} of {len(probes)}")
            path = fill.memory_path(
                atlas,
                keyframes,
                by_id[probe.start],
                probe,
                int(job.params.get("frames", 73)),
                workers=int(job.params.get("workers", 6)),
            )
            here = folder / f"p{n}"
            here.mkdir()
            _image(here / "first.png", path["frames"][0])
            splat2.encode_video(here / "memory.mp4", path["frames"], ffmpeg, lossless=True)
            splat2.encode_video(here / "regen.mp4", path["regions"].astype(np.uint8) * 255, ffmpeg, lossless=True)
            (here / "poses.json").write_text(
                json.dumps([{"frame": i, "viewmat": v.tolist(), "K": path["k"].tolist()} for i, v in enumerate(path["views"])]),
                encoding="utf-8",
            )
            width, height = path["size"]
            planned.append(
                {
                    **probe.record(),
                    "folder": here.name,
                    "width": width,
                    "height": height,
                    "hfov": round(fr.hfov(path["k"], width), 4),
                    **path["report"],
                }
            )
        summary = {
            "round": job.params["name"],
            "atlas_version": json.loads(atlas.read_text(encoding="utf-8"))["version"],
            "voxel": float(space.voxel),
            "room": room,
            "coverage": fill.coverage(space, keyframes, room),
            "probes": planned,
        }
        (folder / "probes.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
        _finish(folder, identity, summary)
    for n, probe in enumerate(summary["probes"]):
        here = folder / probe["folder"]
        _output_file(job, f"p{n}-first.png", here / "first.png", f"first:{n}", "set_probe")
        _output_file(job, f"p{n}-memory.mp4", here / "memory.mp4", f"memory:{n}", "set_probe")
        _output_file(job, f"p{n}-regen.mp4", here / "regen.mp4", f"regen:{n}", "set_probe")
    job.output("summary", _write_json(job, "summary.json", summary), "analysis_report")


def fill_paths(job: Job) -> None:
    from worldbuilder_splat import geometry as geo
    from worldbuilder_splat import splat2

    import numpy as np

    folder = _round(job)
    plan = _plan(folder)
    state = _state(job, job.params["from"])
    if not (state / "splat.ply").is_file():
        raise FileNotFoundError(f"No splat state {state.name} to render the probes' depth from.")
    identity = {"task": job.task, "plan": _sha(folder / "probes.json"), "state": _sha(state / "splat.ply")}
    if _done(folder / "paths", identity) is None:
        ffmpeg = _ffmpeg(job)
        (folder / "paths").mkdir(exist_ok=True)
        params, _ = splat2.load_state(state)
        for n, probe in enumerate(plan["probes"]):
            job.check()
            job.progress(0.05 + 0.9 * n / max(len(plan["probes"]), 1), f"Rendering probe {n + 1}'s depth")
            here = folder / probe["folder"]
            poses = json.loads((here / "poses.json").read_text(encoding="utf-8"))
            rendered = [
                splat2.render_numpy(params, p["viewmat"], p["K"], probe["width"], probe["height"]) for p in poses
            ]
            depth = np.stack([r[1] for r in rendered])
            alpha = np.stack([r[2] for r in rendered])
            splat2.encode_video(here / "depthvis.mp4", geo.depth_visualisation(depth, alpha), ffmpeg, lossless=True)
        _finish(folder / "paths", identity, {"probes": len(plan["probes"])})
    for n, probe in enumerate(plan["probes"]):
        here = folder / probe["folder"]
        _output_file(job, f"p{n}-depth.mp4", here / "depthvis.mp4", f"depth:{n}", "set_probe")
    job.output("summary", _write_json(job, "summary.json", {"probes": len(plan["probes"])}), "analysis_report")


def fill_stills(job: Job) -> None:
    from worldbuilder_splat import fill
    from worldbuilder_splat import memory as mem
    from worldbuilder_splat import splat2

    import numpy as np

    folder = _round(job)
    plan = _plan(folder)
    atlas = _atlas(job)
    videos = _videos(job, plan)
    identity = {
        "task": job.task,
        "plan": _sha(folder / "probes.json"),
        "atlas": _sha(atlas),
        "videos": {n: _sha(v) if v else None for n, v in videos.items()},
    }
    summary = _done(folder / "stills", identity)
    if summary is None:
        ffmpeg = _ffmpeg(job)
        (folder / "stills").mkdir(exist_ok=True)
        keyframes = mem.load_keyframes(atlas)
        stills = []
        for n, probe in enumerate(plan["probes"]):
            job.check()
            here = folder / probe["folder"]
            if videos[n] is None:  # its render failed: a rejected probe (ADR 0020 §5)
                stills.append({"probe": n, "still": False, "reason": "no render"})
                continue
            job.progress(0.05 + 0.9 * n / len(plan["probes"]), f"Placing probe {n + 1}'s still")
            frames = list(splat2.decode_video(videos[n], probe["width"], probe["height"], ffmpeg))
            poses = json.loads((here / "poses.json").read_text(encoding="utf-8"))
            end, k = np.asarray(poses[-1]["viewmat"]), np.asarray(poses[-1]["K"])
            step = max(float(probe["step"]), 0.1) * float(plan["room"])
            found = fill.probe_still(keyframes, frames[-1], end, k, step)
            _image(here / "merged.png", found["merged"])
            _image(here / "memory.png", found["memory"])
            for name in ("protected", "region"):
                _image(here / f"{name}.png", np.repeat(found[name][..., None].astype(np.uint8) * 255, 3, -1))
            (here / "view.json").write_text(
                json.dumps(
                    {"viewmat": found["view"].tolist(), "K": k.tolist(), "width": probe["width"], "height": probe["height"]}
                ),
                encoding="utf-8",
            )
            (here / "pose.json").write_text(json.dumps({**found["pose"], "probe": n, "plan": probe}), encoding="utf-8")
            (here / "merge.json").write_text(json.dumps(found["merge"]), encoding="utf-8")
            stills.append({"probe": n, "still": True, **found["merge"], "pose": found["pose"]})
        summary = {"stills": stills}
        _finish(folder / "stills", identity, summary)
    for row in summary["stills"]:
        if row["still"]:
            here = folder / plan["probes"][row["probe"]]["folder"]
            _output_file(job, f"p{row['probe']}-still.png", here / "merged.png", f"still:{row['probe']}", "set_probe")
    job.output("summary", _write_json(job, "summary.json", summary), "analysis_report")


def fill_consolidate(job: Job) -> None:
    from worldbuilder_splat import explore as ex
    from worldbuilder_splat import fill
    from worldbuilder_splat import memory as mem
    from worldbuilder_splat import splat2

    folder = _round(job)
    plan = _plan(folder)
    base = _atlas(job)
    out = _folder(job, "atlas", job.params["atlas"])
    videos = _videos(job, plan)
    inputs = {n: (job.inputs.get(f"geometry:{n}"), videos[n]) for n in range(len(plan["probes"]))}
    identity = {
        "task": job.task,
        "atlas": _sha(base),
        "plan": _sha(folder / "probes.json"),
        "rules": job.params.get("rules"),
        "inputs": {n: [_sha(p) if p else None for p in pair] for n, pair in inputs.items()},
        **{k: job.params.get(k) for k in ("voxel", "room", "height", "grid")},
    }
    summary = _done(out, identity)
    if summary is None:
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        ffmpeg = _ffmpeg(job)
        rules = {**(job.params.get("rules") or {}), "voxel": job.params.get("voxel")}
        stills = [
            {
                "key": f"{plan['round']}p{n}",
                "probe": f"{plan['round']}/p{n}",
                "move": plan["probes"][n]["kind"],
                "base": folder / plan["probes"][n]["folder"],
                "geometry": geometry,
                "video": video,
                "poses": folder / plan["probes"][n]["folder"] / "poses.json",
            }
            for n, (geometry, video) in inputs.items()
            if geometry is not None and video is not None
        ]
        job.progress(0.1, "Consolidating the stills")
        atlas, report = fill.consolidate(
            base, out, stills, rules, lambda video, w, h: list(splat2.decode_video(video, w, h, ffmpeg))
        )
        atlas_path = out / "atlas.json"
        ex.save_atlas(atlas, atlas_path)
        keyframes = mem.load_keyframes(atlas_path)
        room = float(plan["room"])
        rules_of = fill.Rules.of(job.params.get("rules"))
        after = fill.coverage(fill.observed(keyframes, room, rules_of.margin, voxel=plan["voxel"]), keyframes, room)
        report |= {"coverage": after, "gain": fill.gain(plan["coverage"], after)}
        summary = {"round": report} | _describe(job, out, atlas, atlas_path, "worldbuilder engine, fill_consolidate")
        _finish(out, identity, summary)
    _publish_atlas(job, out)
    atlas = json.loads((out / "atlas.json").read_text(encoding="utf-8"))
    for record in mem.current(atlas):
        _output_file(job, f"{record['id']}.png", out / record["image"], f"still:{record['id']}", "set_still")
        _output_file(job, f"{record['id']}.npz", out / record["data"], f"data:{record['id']}", "set_keyframe")
    job.output("summary", _write_json(job, "summary.json", summary), "analysis_report")


TASKS = {
    "atlas_from_frames": atlas_from_frames,
    "atlas_prepare": atlas_prepare,
    "render_views": render_views,
    "splat_init_atlas": splat_init_atlas,
    "splat_extend": splat_extend,
    "splat_atlas_metrics": splat_atlas_metrics,
    "fill_explore": fill_explore,
    "fill_paths": fill_paths,
    "fill_stills": fill_stills,
    "fill_consolidate": fill_consolidate,
}


def probe() -> dict:
    try:
        import gsplat
        import torch

        return {"gsplat": gsplat.__version__, "torch": torch.__version__, "cuda": torch.cuda.is_available()}
    except ImportError as error:  # the probe reports, it never fails the environment check
        return {"missing": str(error)}


if __name__ == "__main__":
    sys.exit(run(TASKS, probe=probe))
