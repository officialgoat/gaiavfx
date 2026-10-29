"""A finished set build for the browser suite (ADR 0018 §4): Run queued fixes as if the worldbuilder engine had run.
No engine runs: in one transaction the set's K1 is excluded for its first Judge issue, the real build is started, its
two jobs end with the outputs the worker writes (the base's own atlas, map and splat, and the prepared keyframes with
K1 as a colour-only record), and the real advancer writes the proposed version. Prints the new version's id.

    python tests/browser-set-build.py <library> <set name>
"""

import json
import sys
import tempfile
from pathlib import Path

from gaiavfx.application import set_build, sets
from gaiavfx.application.workspace import Workspace
from gaiavfx.modules.generation.operations.service import OperationService

root, name = Path(sys.argv[1]), sys.argv[2]
workspace = Workspace(root)
library = workspace.library
service = OperationService()

with workspace.db.transaction() as session:
    set_id = next(
        row["id"] for row in sets.list_sets(session, library) if row["name"] == name
    )
    version = sets.set_state(session, library, set_id)["version"]
    members = sets.STORE.keyframes(session, version["id"])
    base = next(
        v
        for v in sets.STORE.get(session, set_id)["versions"]
        if v["id"] == version["id"]
    )
    shas = {
        k["id"]: (
            library.get(session, k["image_asset_id"])["sha256"],
            library.artifact(session, k["depth_artifact_id"])["sha256"],
        )
        for k in members
    }

# The worker's labels: cameras keep their K numbers; the other current records are A1, A2, ... in atlas order, where
# an excluded keyframe's superseding record comes last.
cameras = [k for k in members if k["label"].startswith("K") and k["label"] != "K1"]
others = [k for k in members if not k["label"].startswith("K")]
k1 = next(k for k in members if k["label"] == "K1")
labels = {k["id"]: k["label"] for k in cameras}
labels.update({k["id"]: f"A{n}" for n, k in enumerate([*others, k1], 1)})
entries = []
for k in [*cameras, *others, k1]:
    excluded = k is k1
    entries.append(
        {
            "record": f"{k['key']}~1" if excluded else k["key"],
            "label": labels[k["id"]],
            "kind": k["kind"],
            "role": "appearance-only" if excluded else k["role"],
            "supersedes": k["key"] if excluded else None,
            "viewmat": k["viewmat"],
            "intrinsics": k["intrinsics"],
            "heading": k["heading"],
            "image_sha256": shas[k["id"]][0],
            "data_sha256": shas[k["id"]][1],
            "info": {"retraction": {"whole": True, "reason": "browser suite"}}
            if excluded
            else {},
        }
    )
with tempfile.TemporaryDirectory() as scratch:
    prepared = Path(scratch) / "prepared.json"
    prepared.write_text(
        json.dumps(
            {"atlas_version": 1, "map_meta": base["map_meta"], "keyframes": entries}
        ),
        encoding="utf-8",
    )
    fitted = Path(scratch) / "fit.json"
    fitted.write_text(
        json.dumps({"gaussians": 4, "heldout_psnr_refined": 19.5, "anchor_psnr": 30.0}),
        encoding="utf-8",
    )
    blobs = [
        library.prepare_blob(path, "analysis_report", {}) for path in (prepared, fitted)
    ]

with workspace.db.transaction() as session:
    issue = next(
        i for i in sets.STORE.issues(session, set_id) if i["author"] == "judge"
    )
    sets.queue_fix(
        session,
        library,
        set_id,
        version_id=version["id"],
        action="retract",
        target={"keyframe": k1["id"], "label": "K1"},
        shows="",
        issue_id=issue["id"],
    )
    operation = set_build.start_set_build(
        session, library, set_id, base_version_id=version["id"]
    )
    summary, fit = (library.register_artifact(session, blob) for blob in blobs)

    def output(key, artifact_id):
        artifact = library.artifact(session, artifact_id)
        return {
            "output_key": key,
            "artifact_id": artifact["id"],
            "sha256": artifact["sha256"],
        }

    outputs = [
        [
            output("atlas", base["atlas_artifact_id"]),
            output("map", base["map_artifact_id"]),
            output("summary", summary["id"]),
        ],
        [output("summary", fit["id"]), output("splat", base["splat_artifact_id"])],
    ]
    for job, produced in zip(
        service.operation(session, operation["id"])["jobs"], outputs, strict=True
    ):
        for state in ("claimed", "running"):
            service.transition(session, job["id"], state)
        service.transition(session, job["id"], "collecting", outputs=produced)
        service.transition(session, job["id"], "succeeded")
    service.settle_operation(session, operation["id"])
    done = set_build.advance_set_build(
        session, service.operation(session, operation["id"]), library=library
    )
    print(done["review"][-1]["version_id"])
workspace.db.close()
