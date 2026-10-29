"""Synthetic replacement evidence, published through public services; no inference."""

import json
import sys
from pathlib import Path

from gaiavfx.application.replace_character import open_replacement_review
from gaiavfx.application.workspace import Workspace
from gaiavfx.modules.direction.application import replacement_policy_text
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.service import OperationService

root = Path(sys.argv[1])
workspace = Workspace(root)
library = workspace.library
service = OperationService()


def media(index, source=False):
    target = root / f"synthetic-replacement-{index}.mp4"
    library.media.run(
        library.media.ffmpeg,
        [
            "-y",
            "-i",
            str(root / f"fake-comfy-{index}.mp4"),
            "-vf",
            "scale=512:320",
            "-frames:v",
            "144" if source else "51",
            "-c:v",
            "libx264",
            str(target),
        ],
    )
    prepared = library.prepare(
        target,
        "Synthetic replacement source" if source else f"Synthetic candidate {index}",
    )
    with workspace.db.transaction() as session:
        return library.register(session, prepared, {"type": "import"})["id"]


def finish(session, job, outputs=None):
    for state in ("claimed", "running", "collecting", "succeeded"):
        service.transition(
            session,
            job["id"],
            state,
            **({"outputs": outputs} if state == "succeeded" and outputs else {}),
        )


source = media(0, True)
candidates = [media(1), media(2)]
report = {
    "source_asset": source,
    "shot_start": 12,
    "shot_end": 63,
    "native": [512, 320],
    "fps": 24,
    "letterbox": None,
    "concepts": ["person", "yellow mug"],
    "contacts": {},
    "tracks": [
        {
            "id": "person-left",
            "concept": "person",
            "box": {"x0": 40, "y0": 30, "x1": 150, "y1": 300},
            "presence": ["present"] * 51,
        },
        {
            "id": "person-right",
            "concept": "person",
            "box": {"x0": 370, "y0": 30, "x1": 470, "y1": 300},
            "presence": ["present"] * 49 + ["unresolved"] * 2,
        },
        {
            "id": "mug",
            "concept": "yellow mug",
            "box": {"x0": 155, "y0": 160, "x1": 170, "y1": 180},
            "presence": ["present"] * 51,
        },
    ],
}
report_path = root / "synthetic-people.json"
# Agent numbering includes object tracks; person labels must keep these gaps.
report["tracks"] = [report["tracks"][0], report["tracks"][2], report["tracks"][1]]
report_path.write_text(json.dumps(report), "utf-8")
prepared = library.prepare_blob(report_path, "analysis_report", {})
arm = {
    "treatment": "rgb",
    "framing": "crop",
    "grouping": "single",
    "round": 0,
    "seed_count": 1,
    "seed_offset": 0,
    "crop": None,
}
check_artifacts = []
for index in range(2):
    checks_path = root / f"synthetic-checks-{index}.json"
    checks_path.write_text(
        json.dumps(
            {
                "checks": [
                    {
                        "check": "arcface_target",
                        "verdict": "fail" if index == 0 else "pass",
                        "measured": 0.2 if index == 0 else 0.8,
                        "advice": "none",
                        "reason": "Synthetic calibrated likeness observation.",
                    },
                    {
                        "check": "background_geometry",
                        "verdict": "unsupported",
                        "measured": None,
                        "advice": "none",
                        "reason": "geometry unmeasured",
                    },
                    {
                        "check": "letterbox_difference",
                        "verdict": "pass",
                        "measured": 0.0,
                        "advice": "none",
                        "reason": "unchanged",
                    },
                    {
                        "check": "mouth_aperture",
                        "verdict": "unsupported",
                        "measured": 0.73,
                        "advice": "none",
                        "reason": "Not calibrated: threshold is not configured.",
                    },
                ]
            }
        ),
        "utf-8",
    )
    blob = library.prepare_blob(checks_path, "analysis_report", {})
    with workspace.db.transaction() as session:
        check_artifacts.append(library.register_artifact(session, blob)["id"])
with workspace.db.transaction() as session:
    artifact = library.register_artifact(session, prepared)
    analysis = service.create(
        session,
        kind="person_analysis",
        request={
            "source_asset": source,
            "start": 12,
            "end": 63,
            "concepts": ["person", "yellow mug"],
        },
        jobs=[JobSpec("analysis", "local", "synthetic", step="people_report")],
        budget_limit=None,
    )
    finish(
        session,
        analysis["jobs"][0],
        [{"output_key": "people", "artifact_id": artifact["id"]}],
    )
    operation = service.create(
        session,
        kind="character_replacement",
        request={
            "source_asset": source,
            "facts": {
                "source_asset": source,
                "shot_start": 12,
                "shot_end": 63,
                "fps": 24,
            },
            "policy_limits": {"max_rounds": 3},
            "policy": replacement_policy_text(),
        },
        jobs=[
            JobSpec(
                "video",
                "local",
                "synthetic",
                step="replacement_composite",
                seed=987650,
                snapshot={"step_spec": {"arm": arm}},
            )
        ],
        budget_limit=None,
        budget=4,
        awaits_review=True,
    )
    finish(
        session,
        operation["jobs"][0],
        [{"output_key": "checks", "artifact_id": check_artifacts[0]}],
    )
    service.add_candidate(
        session, operation["jobs"][0]["id"], candidates[0], "composite", "video"
    )
    next_arm = {**arm, "round": 1, "treatment": "inverted", "seed_offset": 1}
    operation = service.resume(
        session,
        operation["id"],
        {
            "decided_by": "advancer",
            "round": 1,
            "arm": next_arm,
            "gaps": [],
            "reasons": ["Likeness check failed."],
            "seeds": [987651],
        },
        jobs=[
            JobSpec(
                "video",
                "comfy",
                "synthetic",
                seed=987651,
                graph={"synthetic": {"class_type": "FixtureOnly", "inputs": {}}},
                snapshot={"knowledge": {"fixture": "synthetic"}},
            ),
            JobSpec(
                "video",
                "local",
                "synthetic",
                step="replacement_composite",
                seed=987651,
                snapshot={"step_spec": {"arm": next_arm}},
            ),
        ],
        awaits_review=True,
    )
    for job in operation["jobs"][1:]:
        finish(
            session,
            job,
            [{"output_key": "checks", "artifact_id": check_artifacts[1]}]
            if job["step"] == "replacement_composite"
            else None,
        )
    service.add_candidate(
        session, operation["jobs"][-1]["id"], candidates[1], "composite", "video"
    )
    service.resume(
        session,
        operation["id"],
        {
            "decided_by": "advancer",
            "round": 2,
            "arm": None,
            "gaps": [],
            "reasons": ["No further retry advised."],
            "seeds": [],
        },
    )
    review = open_replacement_review(session, operation["id"])
print(
    json.dumps(
        {
            "source": source,
            "analysis": analysis["id"],
            "operation": operation["id"],
            "review": review,
            "candidate_assets": candidates,
        }
    )
)
workspace.db.close()
