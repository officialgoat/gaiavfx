"""Set bundles (ADR 0018 §3): the human-only import of a bundle folder as a new set with one proposed version.

The import checks a whole bundle before anything is registered:
- every path resolves inside the bundle, with its sha256;
- npz files load without pickle, with the expected arrays, dtypes and shapes;
- poses are finite rigid 4×4 matrices;
- the splat is a binary 3DGS PLY.

Files are copied into Library outside the transaction. The set, its keyframes, its proposed version and its judge
issues are then registered in one transaction.
"""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from gaiavfx.modules.sets.cameras import rigid
from gaiavfx.modules.sets.service import ISSUE_KINDS, SetStore
from gaiavfx.shared.errors import ApplicationError

STORE = SetStore()
BUNDLE_SCHEMA = 1
SPLAT_PROPERTIES = (
    "x",
    "y",
    "z",
    "f_dc_0",
    "f_dc_1",
    "f_dc_2",
    "opacity",
    "scale_0",
    "scale_1",
    "scale_2",
    "rot_0",
    "rot_1",
    "rot_2",
    "rot_3",
)
PLY_SIZES = {"float": 4, "float32": 4, "uchar": 1, "uint8": 1, "double": 8, "int": 4, "uint": 4}


class BundleError(ApplicationError):
    def __init__(self, message: str):
        super().__init__(f"This stage bundle cannot be imported: {message}", 422)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _file(bundle: Path, entry, what: str) -> Path:
    if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
        raise BundleError(f"{what} names no file.")
    relative = Path(entry["path"])
    path = (bundle / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(bundle) or not path.is_file():
        raise BundleError(f"{what} ({entry['path']}) is not a file inside the bundle.")
    if _sha256(path) != entry.get("sha256"):
        raise BundleError(f"{what} ({entry['path']}) does not match its sha256.")
    return path


def _matrix(value, shape, what: str) -> np.ndarray:
    try:
        array = np.asarray(value, dtype=float)
    except (TypeError, ValueError) as error:
        raise BundleError(f"{what} is not a matrix.") from error
    if array.shape != shape or not np.isfinite(array).all():
        raise BundleError(f"{what} is not a finite {shape[0]}×{shape[1]} matrix.")
    return array


def _pose(value, what: str) -> np.ndarray:
    viewmat = _matrix(value, (4, 4), what)
    if rigid(viewmat) is None:
        raise BundleError(f"{what} is not a rigid camera pose.")
    return viewmat


def _arrays(path: Path, what: str) -> dict[str, np.ndarray]:
    try:
        with np.load(path, allow_pickle=False) as data:
            return {name: data[name] for name in data.files}
    except (OSError, ValueError) as error:
        raise BundleError(f"{what} is not a readable npz file.") from error


def _check_depth(path: Path, width: int, height: int, what: str):
    arrays = _arrays(path, what)
    if not {"depth", "valid", "trust"} <= set(arrays):
        raise BundleError(f"{what} lacks depth, valid or trust.")
    for name, array in arrays.items():
        if array.shape != (height, width):
            raise BundleError(f"{what}: {name} is not {width}×{height}.")
        expected = np.floating if name == "depth" else np.bool_
        if not np.issubdtype(array.dtype, expected):
            raise BundleError(f"{what}: {name} has the wrong type.")


def _check_image(path: Path, width: int, height: int, what: str):
    try:
        with Image.open(path) as image:
            size = image.size
    except OSError as error:
        raise BundleError(f"{what} is not a readable image.") from error
    if size != (width, height):
        raise BundleError(f"{what} is {size[0]}×{size[1]}, not {width}×{height}.")


def check_map(arrays: dict[str, np.ndarray], meta: dict) -> None:
    plane, frontier = arrays.get("plane"), arrays.get("frontier")
    if plane is None or frontier is None or plane.ndim != 2 or frontier.shape != plane.shape:
        raise BundleError("the map needs a plane and a frontier of one shape.")
    if not set(np.unique(plane).tolist()) <= {0, 1, 2}:
        raise BundleError("the map plane holds values other than unknown, free and occupied.")
    if list(plane.shape) != list(meta.get("shape", [])):
        raise BundleError("the map's shape disagrees with its metadata.")
    if not float(meta.get("voxel", 0)) > 0 or len(meta.get("lower", [])) != 3:
        raise BundleError("the map metadata lacks its voxel size or lower corner.")


def splat_header(path: Path) -> dict:
    """Read a binary 3DGS PLY header: the vertex count and the properties, checked against the file size."""
    with path.open("rb") as stream:
        head = stream.read(1 << 16)
    end = head.find(b"end_header\n")
    if not head.startswith(b"ply\n") or end < 0:
        raise BundleError("the splat is not a PLY file.")
    lines = head[:end].decode("ascii", "replace").splitlines()
    if "format binary_little_endian 1.0" not in lines:
        raise BundleError("the splat is not a binary little-endian PLY.")
    count, properties, stride, in_vertex = 0, [], 0, False
    for line in lines:
        parts = line.split()
        if parts[:2] == ["element", "vertex"]:
            count, in_vertex = int(parts[2]), True
        elif parts[:1] == ["element"]:
            in_vertex = False
        elif parts[:1] == ["property"] and in_vertex:
            if parts[1] not in PLY_SIZES:
                raise BundleError("the splat has a property type this reader does not know.")
            properties.append(parts[2])
            stride += PLY_SIZES[parts[1]]
    if count <= 0 or not set(SPLAT_PROPERTIES) <= set(properties):
        raise BundleError("the splat lacks gaussians or a 3DGS property.")
    if path.stat().st_size < end + len(b"end_header\n") + count * stride:
        raise BundleError("the splat file is shorter than its header says.")
    sh_rest = sum(name.startswith("f_rest_") for name in properties)
    return {"gaussians": count, "sh_degree": math.isqrt(sh_rest // 3 + 1) - 1, "stride": stride}


def read_bundle(folder: Path) -> dict:
    """Check a whole bundle and return its document with resolved paths, before anything is registered."""
    bundle = Path(folder).resolve()
    document_path = bundle / "set.json"
    if not document_path.is_file():
        raise BundleError(f"{bundle} has no set.json.")
    try:
        document = json.loads(document_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BundleError("set.json is not valid JSON.") from error
    if document.get("schema") != BUNDLE_SCHEMA or not str(document.get("name", "")).strip():
        raise BundleError("set.json is not schema 1 or has no name.")
    version = document.get("version") or {}
    keyframes = version.get("keyframes") or []
    if not keyframes:
        raise BundleError("the version has no keyframes.")
    labels = [k.get("label") for k in keyframes]
    if len(set(labels)) != len(labels) or not all(isinstance(x, str) and x for x in labels):
        raise BundleError("keyframe labels must be unique.")
    files = {"atlas": _file(bundle, version.get("atlas"), "The atlas")}
    if version.get("map"):
        files["map"] = _file(bundle, version["map"], "The map")
        check_map(_arrays(files["map"], "The map"), version.get("map_meta") or {})
    if version.get("splat"):
        files["splat"] = _file(bundle, version["splat"], "The splat")
        files["splat_header"] = splat_header(files["splat"])
    checked = []
    for keyframe in keyframes:
        what = f"Keyframe {keyframe.get('label')}"
        if keyframe.get("kind") not in ("erp", "pinhole"):
            raise BundleError(f"{what} has an unknown kind.")
        if keyframe.get("role") not in ("observed", "generated", "trajectory", "appearance-only"):
            raise BundleError(f"{what} has an unknown role.")
        width, height = int(keyframe.get("width", 0)), int(keyframe.get("height", 0))
        _pose(keyframe.get("viewmat"), f"{what}'s pose")
        if keyframe["kind"] == "pinhole":
            _matrix(keyframe.get("intrinsics"), (3, 3), f"{what}'s intrinsics")
        image = _file(bundle, keyframe.get("image"), f"{what}'s still")
        depth = _file(bundle, keyframe.get("depth"), f"{what}'s depth")
        _check_image(image, width, height, f"{what}'s still")
        _check_depth(depth, width, height, f"{what}'s depth")
        checked.append({**keyframe, "image_path": image, "depth_path": depth})
    if keyframes[0].get("kind") != "erp":
        raise BundleError("the first keyframe (K0) must be the panorama.")
    for issue in document.get("issues") or []:
        region = issue.get("region")
        if region and region.get("viewmat") is not None:
            _pose(region["viewmat"], "An issue's camera")
    return {"bundle": bundle, "document": document, "files": files, "keyframes": checked}


def _rewrite_atlas(atlas_path: Path, keyframes: list[dict]) -> bytes:
    """The atlas manifest with its keyframe files named by content (Library keys them by sha256), so it no longer
    depends on the bundle's folder."""
    atlas = json.loads(atlas_path.read_text(encoding="utf-8"))
    by_key = {k["key"]: k for k in keyframes}
    for record in atlas.get("keyframes", []):
        known = by_key.get(record.get("id"))
        if known is not None:
            record["image"] = {"asset_sha256": known["image"]["sha256"]}
            record["data"] = {"artifact_sha256": known["depth"]["sha256"]}
    return json.dumps(atlas, indent=1).encode("utf-8")


def register_new_set(
    session,
    library,
    *,
    name: str,
    place_text: str,
    provenance: dict,
    origin: str,
    still_note: dict,
    keyframes: list[dict],
    stills: list,
    depth_ids: list[str],
    atlas_id: str,
    map_id: str | None,
    splat_id: str | None,
    map_meta: dict,
    metrics: dict,
    note: str,
    seed: int | None = None,
) -> tuple[str, str]:
    """A new set with its first version, proposed: the one way a set comes into being, for an import and for a seed
    from a take (ADR 0019 §4). `keyframes` describe the version's keyframes in order (`key`, `label`, `kind`,
    `role`, `viewmat`, `intrinsics`, `width`, `height`, `heading`, `info`); `stills` are their prepared images and
    `depth_ids` their registered `set_keyframe` artifacts. Returns (set id, version id)."""
    set_id = STORE.create(
        session, name=name, place_text=place_text, seed=seed, provenance=provenance
    )
    keyframe_ids = []
    for keyframe, still, depth_id in zip(keyframes, stills, depth_ids, strict=True):
        asset = library.register(
            session, still, {**still_note, "role": "set_keyframe", "label": keyframe["label"]}
        )
        keyframe_ids.append(
            STORE.add_keyframe(
                session,
                set_id,
                key=str(keyframe["key"])[:80],
                kind=keyframe["kind"],
                role=keyframe["role"],
                viewmat=keyframe["viewmat"],
                intrinsics=keyframe.get("intrinsics"),
                width=int(keyframe["width"]),
                height=int(keyframe["height"]),
                heading=keyframe.get("heading"),
                image_asset_id=asset["id"],
                depth_artifact_id=depth_id,
                supersedes_id=None,
                info=keyframe.get("info") or {},
            )
        )
    version_id = STORE.add_version(
        session,
        set_id,
        origin=origin,
        atlas_artifact_id=atlas_id,
        keyframe_ids=keyframe_ids,
        labels=[k["label"] for k in keyframes],
        map_artifact_id=map_id,
        splat_artifact_id=splat_id,
        map_meta=map_meta,
        metrics=metrics,
        note=note,
    )
    return set_id, version_id


def import_bundle(db, library, folder: Path) -> str:
    """Register a checked bundle as a new set with one proposed version. Returns the set id."""
    checked = read_bundle(folder)
    document, files = checked["document"], checked["files"]
    version = document["version"]
    origin_note = {
        "type": "set_import",
        "source": document.get("provenance", {}).get("source", "set bundle"),
    }
    stills = [
        library.prepare(k["image_path"], f"{document['name']} {k['label']}.png")
        for k in checked["keyframes"]
    ]
    depths = [
        library.prepare_blob(k["depth_path"], "set_keyframe", {"label": k["label"]})
        for k in checked["keyframes"]
    ]
    with tempfile.TemporaryDirectory() as scratch:
        atlas_copy = Path(scratch) / "atlas.json"
        atlas_copy.write_bytes(_rewrite_atlas(files["atlas"], checked["keyframes"]))
        atlas = library.prepare_blob(atlas_copy, "set_atlas", {})
    map_blob = (
        library.prepare_blob(files["map"], "set_map", {"shape": version["map_meta"]["shape"]})
        if "map" in files
        else None
    )
    splat_blob = (
        library.prepare_blob(files["splat"], "set_splat", files["splat_header"])
        if "splat" in files
        else None
    )
    with db.transaction() as session:
        set_id, version_id = register_new_set(
            session,
            library,
            name=document["name"],
            place_text=document.get("place_text", ""),
            seed=document.get("seed"),
            provenance=document.get("provenance") or {},
            origin="import",
            still_note=origin_note,
            keyframes=checked["keyframes"],
            stills=stills,
            depth_ids=[library.register_artifact(session, depth)["id"] for depth in depths],
            atlas_id=library.register_artifact(session, atlas)["id"],
            map_id=library.register_artifact(session, map_blob)["id"] if map_blob else None,
            splat_id=library.register_artifact(session, splat_blob)["id"] if splat_blob else None,
            map_meta=version.get("map_meta") or {},
            metrics=version.get("metrics") or {},
            note=version.get("note", ""),
        )
        for issue in document.get("issues") or []:
            kind = issue.get("kind")
            STORE.add_issue(
                session,
                set_id,
                version_id=version_id,
                kind=kind if kind in ISSUE_KINDS else "other",
                author="judge",
                why=issue.get("why") or "No reason given.",
                cells=issue.get("cells") or [],
                cameras=issue.get("cameras") or [],
                region=issue.get("region"),
                note=issue.get("note", ""),
            )
        return set_id
