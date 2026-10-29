"""Remove a probe's geometry catalog entry from the disposable browser library.

The stale workbench can then exercise the real Keep refusal through HTTP. No product route is added.
"""

import sys
from pathlib import Path

from gaiavfx.application import sets
from gaiavfx.application.workspace import Workspace
from gaiavfx.modules.library.assets import ArtifactRecord

root, set_id, key = sys.argv[1:]
workspace = Workspace(Path(root))
try:
    with workspace.db.transaction() as session:
        version = sets.STORE.get(session, set_id)["versions"][0]
        keyframe = next(
            k for k in sets.STORE.keyframes(session, version["id"]) if k["key"] == key
        )
        pinned = sets.judged_geometry(session, workspace.library, set_id, keyframe)
        assert pinned is not None
        session.delete(session.get(ArtifactRecord, pinned["artifact_id"]))
finally:
    workspace.db.close()
