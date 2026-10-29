"""Blind reference reviews; human rankings unlock only bounded provenance."""

from gaiavfx.application.harness.actions import ToolResult, pin_enum
from gaiavfx.application.harness.tools.common import TransactionTool, scoped
from gaiavfx.application.harness.world import (
    FINISHED,
    LIST,
    fields,
    operations,
    plate_approved,
    plate_survey_cuts,
    ranked,
    review_pending,
    selections,
)
from gaiavfx.application.prepare_references import (
    open_insertion_review,
    open_plate_review,
    open_views_review,
)
from gaiavfx.application.render_cut import open_takes_review
from gaiavfx.application.replace_character import open_replacement_review
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError

OPENERS = {
    "removal": open_plate_review,
    "survey": open_views_review,
    "insert": open_insertion_review,
    "render": open_takes_review,
    "character_replacement": open_replacement_review,
}


def prescreen_summary(document):
    if document is None:
        return None
    checks = document["checks"]
    return {
        "pass": [row["check"] for row in checks if row["verdict"] == "pass"],
        "fail": [row["check"] for row in checks if row["verdict"] == "fail"],
        "unsupported": sum(row["verdict"] == "unsupported" for row in checks),
    }


class ReadCandidates(TransactionTool):
    name = "read_candidates"
    description = "Read candidates blind; human ranking unlocks provenance."

    class Input(StrictModel):
        operation_id: str

    def narrow(self, context):
        ids = [
            o["id"]
            for o in context.state.operations
            if o["state"] in FINISHED and o["kind"] in OPENERS
        ]
        return pin_enum(self.Input.model_json_schema(), "operation_id", ids) if ids else None

    def execute(self, session, context, value):
        scoped(context.state.operations, value.operation_id, "Operation")
        operation = operations.operation(session, value.operation_id)
        if operation["state"] not in FINISHED:
            raise ApplicationError(f"Operation is {operation['state']}; wait for completion.", 422)
        if operation["kind"] not in OPENERS:
            raise ApplicationError("This operation has no reference review.", 422)
        review = OPENERS[operation["kind"]](session, value.operation_id)
        payload = {
            "review_set_id": review["id"],
            "purpose": review["purpose"],
            "members": [
                {
                    **fields(m, "letter", "asset_id", "kind"),
                    "prescreen": prescreen_summary(m["prescreen"]),
                }
                for m in review["members"][:LIST]
            ],
            "ranking": None,
            "selection": None,
            "selection_revision": 0,
        }
        if review_pending(session, operation):
            payload["message"] = (
                f"Pending {review['purpose']} review. End with say naming these candidates "
                "and the Review workspace. The user reviews there; do not ask for ranks in chat."
            )
        elif plate_approved(session, operation):
            payload["message"] = "Plate review complete; the plate is approved."
            if any(
                c["place_id"] == operation["request"]["place_id"]
                for c in plate_survey_cuts(context.state)
            ):
                payload["message"] += " Continue with the survey using prepare_cut."
        if review["purpose"] == "segment_takes":
            segment_id = review["context"]["segment_id"]
            selection = selections.current(session, context.movie_id, segment_id)
            payload["selection"] = (
                fields(selection, "authority", "basis", "revision") if selection else None
            )
            payload["selection_revision"] = selections.revision(
                session, context.movie_id, segment_id
            )
        if ranked(review):
            letters = {m["candidate_id"]: m["letter"] for m in review["members"]}
            ranking = review["ranking"]
            payload["ranking"] = {
                "tiers": [[letters[c] for c in tier] for tier in ranking["tiers"]],
                "usable_tiers": ranking["usable_tiers"],
                "general": ranking["general"][:1000],
                "notes": {
                    letters[key]: text[:500] for key, text in list(ranking["notes"].items())[:LIST]
                },
                "tags": {
                    letters[key]: tags[:LIST] for key, tags in list(ranking["tags"].items())[:LIST]
                },
            }
            jobs = {job["id"]: job for job in operation["jobs"]}
            by_ordinal = {job["ordinal"]: job for job in operation["jobs"]}
            candidates = {c["id"]: c for c in operation["candidates"]}
            revealed = []
            for member in review["members"][:LIST]:
                job = jobs[candidates[member["candidate_id"]]["job_id"]]
                # Local fitted outputs inherit the seed of their producer.
                while job["seed"] is None and job.get("depends_on") in by_ordinal:
                    job = by_ordinal[job["depends_on"]]
                revealed.append(
                    {
                        "letter": member["letter"],
                        "seed": job["seed"],
                        "technique": operation["request"].get("technique", operation["kind"]),
                    }
                )
            payload["revealed"] = revealed
        return ToolResult(
            payload=payload, progress=True, receipts=[{"kind": "review_set", "id": review["id"]}]
        )
