"""What Gaia sees in a turn: the one place a call's context is assembled.

A turn reads its scope's world (a movie, a segment, a cut, or a film's recast), keeps the operations it still owes a
report on in view, refreshes what a request brought in (people analyses, Recast runs, stages), reads the conversation
for what the user said and attached, and writes the view the model reads. Plan review and the view's step check read
the same world through the narrower readings here, never a hand-built stand-in.
"""

from gaiavfx.application.harness import history
from gaiavfx.application.harness.actions import ToolContext
from gaiavfx.application.harness.budget import Budget
from gaiavfx.application.harness.scope import FilmState, scope_state, scope_view
from gaiavfx.application.harness.tools.panoramas import refresh_panoramas
from gaiavfx.application.harness.tools.render import reported_reviews
from gaiavfx.application.harness.tools.replacement import refresh_analyses
from gaiavfx.application.harness.tools.sets import refresh_sets
from gaiavfx.application.harness.world import FINISHED, LIST, operation_view, operations


def assemble(db, library, settings, row, document, turns, *, autonomy, cancelled, turn_id=""):
    """The context of one call in the session `row`, with `turns` its conversation so far."""
    budget = Budget.of(document, turns, settings)
    read = reported_reviews(turns)
    started = history.message_renders(turns)
    with db.transaction() as session:
        state = scope_state(session, row["movie_id"], row["scope_kind"], row["scope_id"], library)
        # Keep unread operations reachable even after their segment is removed.
        visible = {o["id"]: o for o in state.operations}
        owed = [
            visible[op_id]
            if op_id in visible
            else operation_view(session, operations.operation(session, op_id))
            for op_id in sorted(started - read)
        ]
        owed.sort(key=lambda o: o["state"] not in FINISHED)
        owed_ids = {o["id"] for o in owed}
        state.operations = [*owed, *(o for o in state.operations if o["id"] not in owed_ids)][:LIST]
        rejections = history.plate_rejections(session, state)
        refresh(session, state, history.attachments(turns), library, turns)
    state.reported_reviews = read
    return ToolContext(
        db=db,
        session_id=row["id"],
        movie_id=row["movie_id"],
        scope_kind=row["scope_kind"],
        scope_id=row["scope_id"],
        board=document.board,
        view=scope_view(state, autonomy, budget, document=document),
        turn=len(turns),
        turn_id=turn_id,
        state=state,
        settings={**document.settings, "agent_budget_jobs": settings.get("agent_budget_jobs", 6)},
        autonomy=autonomy,
        budget=budget,
        cancelled=cancelled,
        document=document,
        library=library,
        latest_user_message=history.latest_user_message(turns),
        reference_notes=history.references_read(turns),
        recent_user_words=history.recent_user_words(turns),
        plate_rejections=rejections,
        report_turns=turns,
        unread_attachments=history.unread_attachments(turns),
        unread_renders=[o["id"] for o in owed if o["state"] in FINISHED],
        attachments=history.attachments(turns),
    )


def refresh(session, state, attachments, library, turns):
    """Bring in what the request brought: people analyses of attached videos, Recast runs and stages. A film's
    recast is already in its state, and no other recast may enter it."""
    if not isinstance(state, FilmState):
        refresh_analyses(session, state, attachments, library, turns)
        refresh_sets(session, state, library, turns, history.latest_user_message(turns))
        refresh_panoramas(session, state, library, turns, history.latest_user_message(turns))


def for_plan_review(session, row, document, turns, library):
    """What plan review reads to judge a finished step: the scope's world in the review's own transaction, and what
    the conversation said, with nothing refreshed and no view written."""
    state = scope_state(session, row["movie_id"], row["scope_kind"], row["scope_id"], library)
    return ToolContext(
        db=None,
        session_id=row["id"],
        movie_id=row["movie_id"],
        scope_kind=row["scope_kind"],
        scope_id=row["scope_id"],
        board=document.board,
        view="",
        turn=len(turns),
        state=state,
        document=document,
        library=library,
        attachments=history.attachments(turns),
        latest_user_message=history.latest_user_message(turns),
        reference_notes=history.references_read(turns),
        recent_user_words=history.recent_user_words(turns),
        plate_rejections=history.plate_rejections(session, state),
        report_turns=turns,
    )


def for_view(state, document, budget):
    """What the view's step check reads while the view is written: the world, the plan and the budget."""
    return ToolContext(
        db=None,
        session_id="",
        movie_id=state.movie["id"],
        scope_kind="",
        scope_id="",
        board=document.board,
        view="",
        turn=0,
        state=state,
        document=document,
        budget=budget,
    )
