"""One seam for a session's scope: a movie's world, or a film's recast (ADR 0017 amendment 11).

Movie, segment and cut sessions read Direction through world_state and render_view
unchanged. A film session reads exactly its own recast: its state holds that one
recast, refresh adds nothing, and its view names the film instead of a movie.
"""

from dataclasses import dataclass

from gaiavfx.application.harness.context import VIEW_CEILING, estimate
from gaiavfx.application.harness.history import latest_user_message
from gaiavfx.application.harness.session import PLAN_JOBS_MAX
from gaiavfx.application.harness.tools.common import FILM_TOOLS
from gaiavfx.application.harness.tools.recast import compact
from gaiavfx.application.harness.tools.replacement import refresh_analyses
from gaiavfx.application.harness.tools.sets import refresh_sets
from gaiavfx.application.harness.view import render_view
from gaiavfx.application.harness.world import LIST, WorldState, clip, world_state
from gaiavfx.application.recast import recast_state
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.errors import ApplicationError

__all__ = [
    "FILM_TOOLS",
    "FilmState",
    "refresh_scope",
    "scope_state",
    "scope_view",
    "state_label",
]

FILM_NO_PLAN = (
    "Plan mode on; no plan yet. To start a recast run, call propose_plan: one render step on "
    "the recast id with jobs equal to an estimate above, then a review step. read_recast, "
    "propose_cast and propose_appearance need no plan; a human confirms every proposal. "
    "Use say only for questions, ideas the user asked to discuss, and reports."
)


@dataclass
class FilmState(WorldState):
    recast_id: str = ""


def scope_state(session, movie_id, scope_kind, scope_id, autonomy, library=None):
    if scope_kind != "film":
        return world_state(session, movie_id, scope_kind, scope_id, autonomy)
    if library is None:
        raise ApplicationError("Open a Library before talking to a film.", 409)
    # A missing recast is a 404 from its own reader; the agent never sees another recast.
    state = recast_state(session, library, scope_id, private_plans=True)
    return FilmState(
        movie={"id": "", "title": clip(state["title"], 120), "revision": state["revision"]},
        recast_id=scope_id,
        recasts=[compact(state)],
        assets=[a for a in MediaLibrary.imported_sources(session, LIST) if a["kind"] == "image"],
    )


def refresh_scope(session, state, attachments, library, turns):
    # The film's recast is already in state.recasts, and no other recast may enter it.
    if not isinstance(state, FilmState):
        refresh_analyses(session, state, attachments, library, turns)
        refresh_sets(session, state, library, turns, latest_user_message(turns))


def target_text(target):
    if not target:
        return "no target"
    return f"target {target.get('state')} (proposed by {target.get('proposed_by')})"


def voice_text(character):
    """Dialogue lines and the voice, when the character has them (ADR 0016)."""
    parts = []
    if character.get("lines_confirmed") or character.get("lines_proposed"):
        parts.append(
            f"lines confirmed {character.get('lines_confirmed', 0)}, proposed {character.get('lines_proposed', 0)}"
        )
    if character.get("voice"):
        parts.append(f"voice {character['voice']}")
    return "".join(f"; {part}" for part in parts)


def recast_lines(recast):
    characters = recast["characters"]
    lines = [
        f"  Character {c['id']}: {clip(c['label'], 80)}; {target_text(c['target'])}; "
        f"hidden {c['hidden']}; {c['screen_time_frames']} frames on screen{voice_text(c)}"
        for c in characters[:LIST]
    ]
    if len(characters) > LIST:
        lines.append(f"  [{len(characters) - LIST} more characters; read_recast reads them]")
    counts = ", ".join(f"{status} {count}" for status, count in recast["shot_counts"].items())
    lines.append(f"Shots: {counts or 'none'}")
    run = recast["run"] or {}
    lines.append(
        f"Run {run.get('state')}"
        + (f" ({run['reason']})" if run.get("reason") else "")
        + f"; mode {run.get('mode')}; range {run.get('range')}; "
        f"renders used {run.get('renders_used', 0)} of budget {run.get('render_budget')} over all runs; "
        f"shots done {run.get('shots_done', 0)}/{run.get('shots_total', 0)}"
    )
    plan = recast["estimate"]
    if plan:
        span = plan.get("range")
        where = f"frames {span['start']}-{span['end']}" if span else "the whole film"
        line = (
            f"Estimate for the planned range ({where}, {plan.get('mode')} mode): {plan['renders']} renders "
            f"for {plan['shots']} shots; start_recast_run with this range and mode reserves exactly this"
        )
        if plan["renders"] > PLAN_JOBS_MAX:
            # The trial (22 Oct, F1): a recast's render step may reserve up to the plan's limit (the user).
            line += (
                f". That is more than the {PLAN_JOBS_MAX} renders one plan may reserve: offer a range "
                "estimate below, or say the user can run it with Render on the trackbar"
            )
        lines.append(line)
    else:
        lines.append("Estimate: none; nothing is planned to render")
    options = [
        f"{o['mode']} {o['range']['start']}-{o['range']['end']}: {o['renders']}"
        for o in recast["range_estimates"]
    ]
    if options:
        lines.append("Range estimates (mode start-end: renders): " + "; ".join(options))
    return lines


def scope_view(state, autonomy, jobs_reserved, jobs_limit, *, document=None):
    view = render_view(state, autonomy, jobs_reserved, jobs_limit, document=document)
    if not isinstance(state, FilmState):
        return view
    # Plan targets must appear in the view (view_ids), so the recast and character ids are printed.
    head = [
        f"Film {state.recast_id} r{state.movie['revision']}: {state.movie['title']}",
        *recast_lines(state.recasts[0]),
    ]
    rest = [line for line in view.splitlines()[1:] if not line.startswith("Recasts: ")]
    if rest and document and not document.plan:
        rest[0] = FILM_NO_PLAN
    result = ""
    lines = [*head, *rest]
    for index, line in enumerate(lines):
        if line.startswith("[") and "use read_movie" in line:
            line = line.split(";")[0] + "; use read_recast for the recast]"
        if estimate(result + line) > VIEW_CEILING - 30:
            result += f"\n[{len(lines) - index} more lines; use read_recast for the recast]"
            break
        result += line + "\n"
    return result


def state_label(context, applicable):
    if context.document and context.document.plan_mode:
        plan = context.document.plan
        return f"{context.scope_kind}/plan/" + (
            "executing" if plan and plan.state == "executing" else "planning"
        )
    names = {tool.name for tool, _ in applicable}
    phase = (
        "reviewing"
        if any(o["review_pending"] for o in context.state.operations)
        else "rendering"
        if "render_candidates" in names
        else "picking"
        if "pick_provisional" in names
        else "preparing"
        if "prepare_cut" in names
        else "drafting"
        if not context.state.segments
        else "idle"
    )
    return f"{context.scope_kind}/{context.autonomy}/{phase}"
