"""Register a tiny continuation artifact through the Library owner for the fake render fixture."""

import json
import struct
import sys
from pathlib import Path

from gaiavfx.application.workspace import Workspace

root = Path(sys.argv[1])
workspace = Workspace(root)
header = json.dumps(
    {
        "__metadata__": {"format": "h3_motion_context_av_v1"},
        "video": {"shape": [1, 1, 1, 1, 1]},
        "audio": {"shape": [1, 1, 1, 1]},
    }
).encode()
source = root / "fake-context.safetensors"
source.write_bytes(struct.pack("<Q", len(header)) + header)
prepared = workspace.library.prepare_artifact(source)
with workspace.db.transaction() as session:
    artifact = workspace.library.register_artifact(session, prepared)
print(json.dumps({"id": artifact["id"]}))
workspace.db.close()
