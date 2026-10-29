"""Provisional continuation picks; human selection remains authoritative."""

from pydantic import Field

from gaiavfx.application.candidate_review import select_take
from gaiavfx.application.harness.actions import ToolResult, pin_enum
from gaiavfx.application.harness.board import write_entry
from gaiavfx.application.harness.tools.common import TransactionTool, permitted, revision, scoped
from gaiavfx.application.harness.world import direction, ranked, reviews, selections
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError


class PickProvisional(TransactionTool):
    name = "pick_provisional"
    description = "Pick a provisional continuation source for a continuing successor, recording its basis and respecting the user's selection."

    class Input(StrictModel):
        review_set_id: str
        letter: str = Field(min_length=1, max_length=4)
        expected_revision: int = Field(ge=0)

    def narrow(self, context):
        if not permitted(context, "pick"):
            return None
        eligible = {
            s["id"]
            for s in context.state.segments
            if s["continuing_successor"]
            and (not s["selection"] or s["selection"]["authority"] != "user")
        }
        ids = [
            o["review_set_id"]
            for o in context.state.operations
            if o["kind"] == "render" and o["segment_id"] in eligible and o["review_set_id"]
        ]
        return pin_enum(self.Input.model_json_schema(), "review_set_id", ids) if ids else None

    def execute(self, session, context, value):
        if not permitted(context, "pick"):
            raise ApplicationError("Provisional picks need render autonomy.", 422)
        review = reviews.blind(session, value.review_set_id)
        if (
            review["purpose"] != "segment_takes"
            or review["context"].get("movie_id") != context.movie_id
        ):
            raise ApplicationError("A segment takes review of this movie is required.", 422)
        segment_id = review["context"]["segment_id"]
        scoped(context.state.segments, segment_id, "Segment")
        movie = direction.read_movie(session, context.movie_id)
        segment = scoped(movie["segments"], segment_id, "Segment")
        successor = next(
            (s for s in movie["segments"] if s["ordinal"] == segment["ordinal"] + 1), None
        )
        if not successor or not successor["continues"]:
            raise ApplicationError("This segment has no continuing successor.", 422)
        revision(
            {"revision": selections.revision(session, context.movie_id, segment_id)},
            value.expected_revision,
            "Selection",
            self.name,
        )
        member = next((m for m in review["members"] if m["letter"] == value.letter), None)
        if member is None:
            raise ApplicationError("Choose a letter from the blind review.", 422)
        basis = "unjudged"
        if ranked(review):
            ranking = review["ranking"]
            if not ranking["usable_tiers"] or member["candidate_id"] not in ranking["tiers"][0]:
                raise ApplicationError(
                    "The user ranked this take below the usable line; choose the top usable tier.",
                    422,
                )
            basis = "user_ranking"
        selection = select_take(
            session,
            review_set_id=value.review_set_id,
            candidate_id=member["candidate_id"],
            authority="provisional",
            basis=basis,
            expected_revision=value.expected_revision,
        )
        write_entry(
            context.board,
            "decisions",
            f"Segment {segment_id}: provisional {value.letter}, basis {basis} (selection {selection['id']})",
            context.turn,
        )
        return ToolResult(
            payload={
                "segment_id": segment_id,
                "letter": value.letter,
                "authority": selection["authority"],
                "basis": basis,
                "revision": selection["revision"],
            },
            receipts=[
                {
                    "kind": "selection",
                    "id": selection["id"],
                    "authority": "provisional",
                    "basis": basis,
                }
            ],
            changed=True,
        )
