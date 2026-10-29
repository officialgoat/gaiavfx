"""Lower a film face scan to the maintained CPU node."""

import json


def face_scan_graph(video, frames, models_root="", det_size=640):
    return {
        "faces": {
            "class_type": "GaiaFaceScan",
            "inputs": {
                "video": video,
                "frames_json": json.dumps(sorted(set(frames))),
                "models_root": models_root,
                "det_size": det_size,
            },
        }
    }
