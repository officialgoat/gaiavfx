"""GAIAVFX's maintained ComfyUI nodes and dependency health endpoint."""

import importlib
import logging
from pathlib import Path

import folder_paths
from aiohttp import web
from server import PromptServer

from .audio import GaiaH3AudioVAE
from .continuity import GaiaH3ContinuityTrim
from .geometry import GaiaSaveSetGeometry, GaiaSaveStillGeometry
from .lora_loader import GaiaH3LoraLoader
from .masked_span import GaiaH3MaskedSpan
from .power_shift import GaiaH3PowerShift

VERSION = "0.1.0"
_LOG = logging.getLogger(__name__)
_MODEL_FILES = ("det_10g.onnx", "w600k_r50.onnx", "2d106det.onnx")


def _probe(module):
    try:
        importlib.import_module(module)
    except Exception as error:
        # Binary-extension load failures matter just as much as missing packages.
        return False, f"{type(error).__name__}: {error}"
    return True, None


_insightface, _insightface_error = _probe("insightface")
_onnxruntime, _onnxruntime_error = _probe("onnxruntime")
_nodes = [
    GaiaH3AudioVAE,
    GaiaH3PowerShift,
    GaiaH3LoraLoader,
    GaiaH3MaskedSpan,
    GaiaH3ContinuityTrim,
    GaiaSaveSetGeometry,
    GaiaSaveStillGeometry,
]
if _insightface and _onnxruntime:
    from .people import GaiaFaceCheck
    from .scan import GaiaFaceScan

    _nodes += [GaiaFaceScan, GaiaFaceCheck]
else:
    _LOG.warning(
        "GAIAVFX: face nodes are unavailable. Install optional insightface and "
        "onnxruntime in ComfyUI's Python environment and restart ComfyUI. "
        "insightface: %s; onnxruntime: %s. Other GAIAVFX nodes remain available.",
        _insightface_error or "available",
        _onnxruntime_error or "available",
    )

NODE_CLASS_MAPPINGS = {node.__name__: node for node in _nodes}
_DISPLAY_NAMES = {
    "GaiaH3AudioVAE": "H3 Audio VAE Guard",
    "GaiaH3PowerShift": "H3 Power Shift",
    "GaiaH3LoraLoader": "H3 LoRA Loader",
    "GaiaH3MaskedSpan": "H3 Masked Latent Spans",
    "GaiaH3ContinuityTrim": "H3 Continuity Trim",
    "GaiaFaceScan": "Face Scan",
    "GaiaFaceCheck": "Face Check",
    "GaiaSaveSetGeometry": "Save Set Geometry",
    "GaiaSaveStillGeometry": "Save Still Geometry",
}
NODE_DISPLAY_NAME_MAPPINGS = {
    name: f"GAIAVFX {_DISPLAY_NAMES[name]}" for name in NODE_CLASS_MAPPINGS
}


def health_status():
    models = Path(folder_paths.models_dir) / "insightface" / "models" / "buffalo_l"
    return {
        "pack": "gaiavfx",
        "version": VERSION,
        "nodes": sorted(NODE_CLASS_MAPPINGS),
        "insightface": {"importable": _insightface, "error": _insightface_error},
        "onnxruntime": {"importable": _onnxruntime},
        "buffalo_l": {
            "present": all((models / name).is_file() for name in _MODEL_FILES),
            "path": str(models),
        },
    }


@PromptServer.instance.routes.get("/gaiavfx/health")
async def health(request):
    return web.json_response(health_status())
