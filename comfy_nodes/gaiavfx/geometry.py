"""GAIAVFX's set nodes (ADR 0019 §4): the geometry of a take's frames for a set seed, saved as one npz the app keeps as a
`set_geometry` artifact.

GaiaSaveStillGeometry (ADR 0020 §4) saves a fill probe's still geometry: MoGe-2's depth and mask and SAM 3's glass
(`still.py`).

GaiaSaveSetGeometry takes Depth Anything 3's multiview geometry (DA3_GEOMETRY, comfy_extras/nodes_depth_anything_3.py:
depth (B,H,W), confidence (B,H,W), extrinsics (1,B,3|4,4) world-to-camera, intrinsics (1,B,3,3) in pixels, the frames
as image (B,H,W,3)) and, optionally, the people SAM 3 tracked in the same frames (MASK, (B,H,W)). It writes
`geometry.npz`: `image` (uint8), `depth`, `confidence`, `extrinsics` (B,4,4), `intrinsics` (B,3,3), `people` (bool,
all False without a mask) and `processed` (the grid DA3 ran on, height and width).

DA3 returns depth and confidence on the frames' own grid but its intrinsics on the grid it ran on, which it chose
from `resolution` and `resize_method` without cropping. This node takes the same two settings and rescales the
intrinsics onto the frames' grid with DA3's own sizing rule, so every saved array shares one grid. No GAIAVFX
imports (comfy_nodes stay independent of the app).
"""

from pathlib import Path

import numpy as np


class GaiaSaveSetGeometry:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "da3_geometry": ("DA3_GEOMETRY",),
                "resolution": ("INT", {"default": 504, "min": 140, "max": 2520, "step": 14}),
                "resize_method": (["upper_bound_resize", "lower_bound_resize"],),
                "filename_prefix": ("STRING", {"default": "gaiavfx/set-geometry"}),
            },
            "optional": {"people": ("MASK",)},
        }

    RETURN_TYPES = ()
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "gaiavfx/sets"

    def save(self, da3_geometry, resolution, resize_method, filename_prefix, people=None):
        import folder_paths
        from comfy.ldm.depth_anything_3.preprocess import compute_target_size

        depth = da3_geometry["depth"].detach().float().cpu().numpy()
        count, height, width = depth.shape
        image = da3_geometry["image"].detach().float().cpu().numpy()
        if image.shape[:3] != (count, height, width):
            raise ValueError("The DA3 frames and depth differ in size.")
        extrinsics = da3_geometry["extrinsics"].detach().float().cpu().numpy().reshape((count, -1, 4))
        if extrinsics.shape[1] == 3:
            bottom = np.tile(np.array([[[0.0, 0.0, 0.0, 1.0]]], np.float32), (count, 1, 1))
            extrinsics = np.concatenate([extrinsics, bottom], 1)
        intrinsics = da3_geometry["intrinsics"].detach().float().cpu().numpy().reshape((count, 3, 3)).copy()
        processed = compute_target_size(height, width, resolution, resize_method)
        intrinsics[:, 0, :] *= width / processed[1]
        intrinsics[:, 1, :] *= height / processed[0]
        mask = np.zeros((count, height, width), bool)
        if people is not None:
            found = people.detach().float().cpu().numpy()
            if found.ndim == 2:
                found = found[None]
            if found.shape[0] == 0:  # SAM found nobody in these frames
                found = np.zeros((count, height, width), np.float32)
            if found.shape != (count, height, width):
                raise ValueError("The people masks and the frames differ in count or size.")
            mask = found > 0.5
        folder = Path(folder_paths.get_output_directory()) / filename_prefix
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "geometry.npz"
        np.savez_compressed(
            target,
            image=(np.clip(image, 0, 1) * 255).round().astype(np.uint8),
            depth=depth.astype(np.float32),
            confidence=da3_geometry["confidence"].detach().float().cpu().numpy().astype(np.float32),
            extrinsics=extrinsics.astype(np.float32),
            intrinsics=intrinsics.astype(np.float32),
            people=mask,
            processed=np.asarray(processed, np.int32),
        )
        subfolder = target.parent.relative_to(folder_paths.get_output_directory()).as_posix()
        return {"ui": {"geometry": [{"filename": target.name, "subfolder": subfolder, "type": "output"}]}}


class GaiaSaveStillGeometry:
    """A fill probe's still (ADR 0020 §4): MoGe's depth and mask for one picture and SAM 3's glass detections, saved
    as one `geometry.npz` (`still.still_arrays`)."""

    GLASS = 4  # optional detection masks, at most

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "moge_geometry": ("MOGE_GEOMETRY",),
                "filename_prefix": ("STRING", {"default": "gaiavfx/still"}),
            },
            "optional": {f"glass_{n}": ("MASK",) for n in range(cls.GLASS)},
        }

    RETURN_TYPES = ()
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = "gaiavfx/sets"

    def save(self, moge_geometry, filename_prefix, **glass):
        import folder_paths

        from .still import still_arrays

        def plain(value):
            return value.detach().float().cpu().numpy()

        arrays = still_arrays(
            plain(moge_geometry["depth"]),
            plain(moge_geometry["mask"]),
            [plain(glass[key]) for key in sorted(glass) if glass[key] is not None],
        )
        folder = Path(folder_paths.get_output_directory()) / filename_prefix
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "geometry.npz"
        np.savez_compressed(target, **arrays)
        subfolder = target.parent.relative_to(folder_paths.get_output_directory()).as_posix()
        return {"ui": {"geometry": [{"filename": target.name, "subfolder": subfolder, "type": "output"}]}}

