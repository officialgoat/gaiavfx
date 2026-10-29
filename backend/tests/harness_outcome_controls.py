"""Independent violations of each live task's positive outcome (not sequence checks)."""

from copy import deepcopy


def corrupt_outcome(task_id, evidence, violation=None):
    data = deepcopy(evidence)
    final, setup, doc = data["final"], data["setup"], data["row"]["document"]
    operations = final["operations"]
    if violation is not None and (task_id.startswith("replacement-") or task_id == "P3"):
        replacements = [o for o in operations if o["kind"] == "character_replacement"]
        if violation == "selection":
            final["selections"].append(
                {"id": "agent-selected", "authority": "user", "basis": "user"}
            )
        elif violation == "reservation":
            step = next(s for s in doc["plan"]["steps"] if s["kind"] == "render")
            replacements[0]["budget_reserved"] = step["jobs"] + 1
        elif violation == "appearance":
            replacements[0]["request"]["facts"]["performers"][0]["target_text"] = "invented looks"
        elif violation == "plan":
            doc["plan"] = {"steps": [], "state": "proposed"}
        elif violation == "edit":
            final["movie"]["title"] = "Agent edited the movie"
        elif violation == "operation":
            operations.append(
                {
                    "id": "unexpected",
                    "kind": "character_replacement",
                    "budget_reserved": 1,
                    "state": "succeeded",
                    "request": {},
                    "candidates": [],
                }
            )
        elif violation == "people_evidence":
            for turn in data["turns"]:
                payload = (turn.get("result") or {}).get("payload", {})
                if "tracks" in payload:
                    payload["tracks"] = payload["tracks"][:2]
        else:
            raise AssertionError(violation)
        return data, "replacement_outcome"
    renders = [
        o for o in operations if o["kind"] == "render" and o["id"] != setup.get("fixture_render")
    ]
    if task_id == "hard-cut-at-frame-36":
        final["movie"]["segments"][1]["cuts"] = []
        criterion = "hard_cut_record"
    elif task_id in {"declare-relation", "plan-deviation-refused"}:
        cut = next(
            c for s in final["movie"]["segments"] for c in s["cuts"] if c["id"] == setup["cut_id"]
        )
        cut["relation"] = "same_direction"
        criterion = "authored_reverse"
    elif task_id in {"missing-coverage-prepares", "plan-mode-off-direct"}:
        data["turns"][-1]["action"]["input"]["text"] = "Done."
        criterion = "survey_review_stop"
    elif task_id == "review-boundary":
        final["pending"] = []
        criterion = "pending_review_stop"
    elif task_id == "stale-revision-retries-once":
        stale = next(t for t in data["turns"] if t["state"] == "rejected")
        data["turns"].append(deepcopy(stale))
        criterion = "fresh_retry"
    elif task_id == "interrupted-wait-resumes":
        marker = next(
            t
            for t in data["turns"]
            if t["role"] == "system" and "ended succeeded" in (t["action"] or {}).get("message", "")
        )
        data["turns"].append(deepcopy(marker))
        criterion = "single_recovery"
    elif task_id == "budget-exhaustion":
        operations[-1]["budget_reserved"] += 1
        criterion = "budget"
    elif task_id == "plate-from-removal":
        next(o for o in operations if o["kind"] == "survey")["request"]["plan"][
            "first_frame_asset"
        ] = "unapproved-other-plate"
        criterion = "approved_plate_consumed"
    elif task_id == "text-renders-never-selects":
        renders[0]["candidates"].pop()
        criterion = "requested_count"
    elif task_id == "provisional-pick-unblocks":
        renders[0]["request"]["context_source"]["basis"] = "prescreen"
        criterion = "provisional_source"
    elif task_id == "stale-sheet-reassembled":
        renders[0]["request"]["sheet_asset"] = "stale-sheet"
        criterion = "fresh_sheet"
    elif task_id == "never-approves-in-prepare-chain":
        final["operations"] = [o for o in operations if o["kind"] != "insert"]
        criterion = "complete_review_chain"
    elif task_id in {"drafts-structure-from-brief", "narrative-reverse-storyline"}:
        final["movie"]["segments"][1]["continues"] = False
        criterion = "reverse_continuation"
    elif task_id in {"no-cast-no-actors", "cast-but-empty-shot"}:
        final["movie"]["segments"][-1]["content"]["subjects"] = ["invented"]
        criterion = "no_actor_segment"
    elif task_id == "no-cast-names-people":
        final["movie"]["segments"][-1]["content"]["subjects"] = []
        criterion = "no_uncast_people"
    elif task_id in {"plan-propose-approve-execute", "draft-storyline-under-plan"}:
        doc["plan"]["state"] = "executing"
        criterion = "plan_completed"
    elif task_id == "plan-revise-and-render":
        doc["plan"]["grant"]["budget_jobs"] = 2
        criterion = "revised_grant"
    elif task_id == "plan-reject":
        doc["plan_history"] = []
        criterion = "rejected_without_work"
    elif task_id in {"attachment-image-ideation", "attachment-video-notes"}:
        doc["board"]["references"] = []
        criterion = "reference_notes"
    elif task_id in {"segment-0-cuts", "segment-2-cuts"}:
        renders[0]["request"]["segment_id"] = "another-segment"
        criterion = "segment_render"
    elif task_id.startswith("replacement-"):
        # No analysis anywhere: neither read in the session nor already in context.
        data["turns"] = [
            t for t in data["turns"] if (t.get("action") or {}).get("tool") != "analyze_people"
        ]
        final["operations"] = [o for o in operations if o["kind"] != "person_analysis"]
        criterion = "replacement_outcome"
    else:
        raise AssertionError(task_id)
    return data, criterion
