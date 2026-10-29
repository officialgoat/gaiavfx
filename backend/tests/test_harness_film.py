"""A film's own conversation: its recast only, ADR 0012 §8's tools only, plan mode always on."""

from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import harness_support
import pytest
from harness_support import action

from gaiavfx.application.harness import plans, scope
from gaiavfx.application.harness.actions import ToolContext, ToolResult, parse_action
from gaiavfx.application.harness.board import Whiteboard
from gaiavfx.application.harness.knowledge import knowledge_head, load
from gaiavfx.application.harness.prompt import PROMPTS, system_prompt
from gaiavfx.application.harness.scope import (
    FILM_TOOLS,
    FilmState,
    refresh_scope,
    scope_view,
    state_label,
)
from gaiavfx.application.harness.session import (
    PLAN_JOBS_MAX,
    STEP_JOBS_MAX,
    AgentSession,
    Plan,
    PlanGrant,
    PlanStep,
)
from gaiavfx.application.harness.tools import DEFAULT_TOOLS, applicable_tools
from gaiavfx.application.harness.tools import recast as tools
from gaiavfx.application.harness.tools.common import tool_schema
from gaiavfx.application.harness.tools.plan import ProposePlan
from gaiavfx.application.recast_run import estimate_run
from gaiavfx.shared.errors import ApplicationError

kernel = harness_support.kernel
RECAST = "film-recast"
# C1.3: a film's conversation never offers these, in any state.
MOVIE_TOOLS = {
    "read_movie",
    "read_segment",
    "inspect_cut",
    "edit_cut",
    "edit_place",
    "edit_segment",
    "edit_phase",
    "edit_cast",
    "draft_storyline",
    "prepare_cut",
    "read_candidates",
    "render_candidates",
    "analyze_people",
    "replace_character",
    "pick_provisional",
    "analyze_reference_video",
}


def voice(**values):
    """A VoiceDTO as recast_state gives it; its ids must never reach the agent's view."""
    return {
        "samples": [{"asset_id": "hidden-recording", "name": "a.wav", "seconds": 30.0}] * 2,
        "seconds": 58.53,
        "state": "confirmed",
        "proposed_by": "user",
        "model_state": None,
        "stage": None,
        "done": 0,
        "total": 0,
        "error": None,
        "reason": None,
        "audition_asset_id": "hidden-reel",
        "library_changed": False,
        "blocked": False,
    } | values


def film_recast():
    """A recast reader's state as the harness sees it, with a conform seed it must never show."""
    return {
        "id": RECAST,
        "revision": 3,
        "title": "Harbor film",
        "film": {"frame_count": 200, "asset_id": "film"},
        "characters": [
            {
                "id": "c01",
                "label": "Navigator",
                "hidden": False,
                "screen_time_frames": 100,
                "target": {
                    "asset_id": "portrait",
                    "label": "Veyra",
                    "state": "confirmed",
                    "proposed_by": "user",
                },
                "lines_confirmed": 13,
                "lines_proposed": 2,
                "voice": voice(model_state="waiting", stage="audition", done=3, total=3),
            },
            {
                "id": "c02",
                "label": "Deckhand",
                "hidden": True,
                "screen_time_frames": 20,
                "target": None,
            },
            {
                "id": "c03",
                "label": "Lookout",
                "hidden": False,
                "screen_time_frames": 40,
                "target": None,
            },
        ],
        "shots": [
            {
                "id": f"s{i}",
                "index": i,
                "start": i * 50,
                "end": (i + 1) * 50,
                "included": True,
                "status": "planned",
                "choice": None,
                "versions": [],
                "plan": {
                    "performers": ["c01"],
                    "gaps": [],
                    "renders_per_window": 2,
                    "windows": [{"start": i * 50, "end": (i + 1) * 50, "length": 56}],
                },
            }
            for i in range(4)
        ],
        "run": {"state": "idle", "mode": "draft", "range": None, "renders_used": 0},
        "estimate": {"renders": 8, "shots": 4},
        "conforms": [{"seed": "PRIVATE CONFORM SEED"}],
    }


def plan(state="executing", kinds=("render", "review")):
    return Plan(
        id="plan",
        revision=1,
        goal="Recast the Navigator",
        proposed_turn=0,
        state=state,
        budget_jobs=2,
        steps=[
            PlanStep(kind="render", title="Recast the first shot", target=RECAST, jobs=2),
            PlanStep(kind="review", title="Review the recast"),
        ],
        grant=PlanGrant(
            budget_jobs=2, step_kinds=list(kinds), approved_turn=1, reserved_at_approval=0
        )
        if state == "executing"
        else None,
    )


@pytest.fixture
def film(monkeypatch):
    state = film_recast()
    monkeypatch.setattr(tools, "recast_state", lambda *args, **kwargs: deepcopy(state))
    world = FilmState(
        movie={"id": "", "title": "Harbor film", "revision": 3},
        recast_id=RECAST,
        recasts=[tools.compact(state)],
        assets=[{"id": "portrait", "kind": "image", "name": "Veyra"}],
    )
    document = AgentSession(scope_kind="film", scope_id=RECAST)

    def context(document=document, jobs_limit=0):
        return ToolContext(
            db=SimpleNamespace(transaction=nullcontext),
            session_id="session",
            movie_id="",
            scope_kind="film",
            scope_id=RECAST,
            board=Whiteboard(),
            view=scope_view(world, "off", 0, jobs_limit, document=document),
            turn=1,
            state=world,
            document=document,
            jobs_limit=jobs_limit,
            # A video in the conversation would offer people analysis in a movie.
            attachments=[{"id": "clip", "kind": "video", "name": "Harbor clip"}],
            latest_user_message="Recast the Navigator with Veyra in this clip.",
            library=SimpleNamespace(get=lambda *args: {"name": "Veyra", "kind": "image"}),
        )

    return SimpleNamespace(state=state, world=world, context=context)


def executing(film):
    document = AgentSession(scope_kind="film", scope_id=RECAST, plan=plan())
    return film.context(document, jobs_limit=2)


def test_film_document_always_plans():
    assert AgentSession(scope_kind="film", plan_mode=False).plan_mode is True
    stored = AgentSession(scope_kind="film", scope_id=RECAST).model_dump() | {"plan_mode": False}
    assert AgentSession.model_validate(stored).plan_mode is True
    assert AgentSession(scope_kind="movie", plan_mode=False).plan_mode is False


@pytest.mark.parametrize("phase", ["no plan", "proposed", "executing"])
def test_film_catalog_is_a_subset_of_the_film_tools(film, phase):
    document = AgentSession(
        scope_kind="film",
        scope_id=RECAST,
        plan=None
        if phase == "no plan"
        else plan("proposed" if phase == "proposed" else "executing"),
    )
    context = film.context(document, jobs_limit=2)
    names = {tool.name for tool, _ in applicable_tools(context)}
    assert names <= FILM_TOOLS and not names & MOVIE_TOOLS, names
    assert {"read_recast", "propose_plan", "propose_cast"} <= names
    # start_recast_run only with an executing plan granting render on a step targeting this recast.
    assert ("start_recast_run" in names) is (phase == "executing")
    # The gate, not the tools' own narrowing, keeps a movie's agent out: here it would apply.
    movie_only = next(tool for tool in DEFAULT_TOOLS if tool.name == "read_movie")
    assert movie_only.narrow(context) is not None and tool_schema(context, movie_only) is None


def test_movie_tools_are_refused_in_a_film(film):
    context = executing(film)
    applicable = applicable_tools(context)
    for tool, value in (
        ("edit_cut", {"op": "create"}),
        ("render_candidates", {"segment_id": "segment"}),
        ("analyze_people", {"asset_id": "clip"}),
    ):
        with pytest.raises(ApplicationError, match="not available in this state"):
            parse_action(action(tool, **value), applicable, context)
    with pytest.raises(ApplicationError):
        parse_action(action("read_recast", recast_id="other"), applicable, context)
    result = tools.ReadRecast().run(context, tools.ReadRecast.Input(recast_id="other"))
    assert not result.ok and "not in this session's scope" in result.discrepancy
    # The scoped refusal does not advertise a movie tool the film cannot use.
    assert "read_movie" not in result.discrepancy


@pytest.mark.parametrize(
    ("values", "words"),
    [
        ({}, "2 recordings, 58.5 s; not built"),
        (
            {"reason": "Build the voice to hear an audition."},
            "Build the voice to hear an audition.",
        ),
        ({"state": "proposed"}, "proposed recordings the user has not confirmed"),
        (
            {"model_state": "making", "stage": "train", "done": 30, "total": 40},
            "building (training the voice, 30 of 40)",
        ),
        (
            {"model_state": "making", "stage": "audition", "done": 1, "total": 3},
            "making the audition (converting the audition, 1 of 3)",
        ),
        (
            {
                "model_state": "making",
                "reason": "Confirm at least 3 of Mara's lines to hear an audition.",
            },
            "Confirm at least 3 of Mara's lines",
        ),
        ({"model_state": "approved"}, "approved by the user"),
        ({"model_state": "rejected"}, "the user rejected the audition"),
        (
            {"model_state": "failed", "error": "The voice build was cancelled."},
            "failed: The voice build was cancelled.",
        ),
    ],
)
def test_the_view_says_each_voice_state_in_words(values, words):
    said = tools.voice_words(voice(**values))
    assert words in said and "hidden" not in said
    assert tools.voice_words(None) is None


def test_the_film_catalog_sends_version_choices_to_the_review_bin(film):
    # The first film campaign, 23 Oct: say and ask_human still pointed a film's user at the Review workspace.
    context = film.context()
    lines = {tool.name: tool.describe(context) for tool, _ in applicable_tools(context)}
    for name in ("say", "ask_human"):
        assert "Review bin" in lines[name] and "Review workspace" not in lines[name]
    movie = next(tool for tool in DEFAULT_TOOLS if tool.name == "say")
    assert "Review workspace" in movie.describe(SimpleNamespace(scope_kind="movie"))


def test_a_promised_read_in_a_film_names_the_film_read(film):
    from gaiavfx.application.harness.tools.talk import Say

    # The held-out film campaign, 23 Oct: the agent promised a read and ended the turn.
    text = "I can help you with that. Let me check the current state of the recast to see the details for shot 9."
    result = Say().run(film.context(), Say.Input(text=text))
    assert not result.ok and "call read_recast now" in result.discrepancy
    assert "read_movie" not in result.discrepancy


def test_start_recast_run_needs_an_approved_render_step_on_this_recast(film):
    value = tools.StartRecastRun.Input(recast_id=RECAST, mode="draft", render_budget=2)
    for document in (
        AgentSession(scope_kind="film", scope_id=RECAST),
        AgentSession(scope_kind="film", scope_id=RECAST, plan=plan("proposed")),
        AgentSession(scope_kind="film", scope_id=RECAST, plan=plan(kinds=("review",))),
    ):
        context = film.context(document, jobs_limit=2)
        assert "start_recast_run" not in {t.name for t, _ in applicable_tools(context)}
        assert not tools.StartRecastRun().run(context, value).ok


def test_film_view_prompt_knowledge_and_labels(film):
    context = film.context()
    view = context.view
    assert view.startswith(f"Film {RECAST} r3: Harbor film\n")
    assert "Character c01: Navigator; target confirmed (proposed by user)" in view
    assert "Character c02: Deckhand; no target; hidden True" in view
    # The in-app trial (23 Oct): the voice and the lines, in words, never an id.
    assert (
        "100 frames on screen; lines confirmed 13, proposed 2; voice 2 recordings, 58.5 s; "
        "the audition waits for the user's review" in view
    )
    assert "hidden-reel" not in view and "hidden-recording" not in view
    assert "Lookout; no target; hidden False; 40 frames on screen\n" in view + "\n"
    assert "Range estimates" in view
    assert (
        "Estimate for the planned range (the whole film, draft mode): 8 renders for 4 shots" in view
    )
    assert "more than the" not in view
    assert scope.FILM_NO_PLAN in view.splitlines() and "Shots: planned 4" in view
    for absent in ("PRIVATE CONFORM SEED", "Movie ", "draft_storyline", "read_movie", "Recasts:"):
        assert absent not in view, absent
    # An executing film plan renders its steps; the render diagnostics ignore a recast target.
    running = executing(film)
    assert "step 0 [render] pending" in running.view
    assert state_label(context, []) == "film/plan/planning"
    assert state_label(running, []) == "film/plan/executing"
    assert knowledge_head(context) == load()["cinematography"]["replacement"]["recast"]
    prompt = system_prompt(context, applicable_tools(context))
    assert prompt.startswith(PROMPTS["system"]["film_loop"])
    # The film's own places and the user's actions, and a plan mode that cannot be turned off (the trial, 22 Oct).
    assert PROMPTS["system"]["film_conversation"] in prompt
    assert PROMPTS["system"]["film_plan_on"] in prompt
    for movie_line in ("conversation", "plan_on", "plan_off"):
        assert PROMPTS["system"][movie_line] not in prompt
    assert prompt.endswith(PROMPTS["examples"]["heading"] + "\n" + PROMPTS["examples"]["film_plan"])
    for movie_example in ("story_plan", "render_plan", "techniques", "replacement", "story_cut"):
        assert PROMPTS["examples"][movie_example] not in prompt
    # A film session sees exactly its recast: refreshing adds nothing, even with a video attached.
    refresh_scope(None, film.world, context.attachments, object(), [])
    assert [r["id"] for r in film.world.recasts] == [RECAST] and not film.world.people_analyses


def test_the_estimate_is_the_one_start_recast_run_computes_for_the_planned_range():
    """The trial (22 Oct, F1): a plan reserves what the start will compute, not a stale stored-plan figure, and the
    view says when one plan step cannot hold it."""
    state = film_recast()
    state["run"]["range"] = {"start": 0, "end": 100}
    state["estimate"] = {
        "renders": 1,
        "shots": 2,
    }  # the recast's own figure, from older stored plans
    payload = tools.compact(state)
    _shots, expected = estimate_run(state, mode="draft", frame_range={"start": 0, "end": 100})
    assert payload["estimate"]["renders"] == expected["renders"] != 1
    assert payload["estimate"]["range"] == {"start": 0, "end": 100}
    assert payload["estimate"]["mode"] == "draft"
    line = next(text for text in scope.recast_lines(payload) if text.startswith("Estimate"))
    assert line.startswith("Estimate for the planned range (frames 0-100, draft mode)")
    assert "more than the" not in line
    for shot in state["shots"]:
        shot["plan"]["windows"] = shot["plan"]["windows"] * 14
    payload = tools.compact(state)
    assert payload["estimate"]["renders"] > PLAN_JOBS_MAX
    line = next(text for text in scope.recast_lines(payload) if text.startswith("Estimate"))
    assert f"more than the {PLAN_JOBS_MAX} renders one plan may reserve" in line


@pytest.mark.parametrize("status", ["needs_review", "rendering", "approved", "failed"])
def test_a_shot_already_rendered_is_not_estimated(status):
    """The in-app trial (23 Oct): a run over a shot in review reserved 14 renders and the feeder rendered none,
    because it starts only shots without replacement windows."""
    state = film_recast()
    _shots, before = estimate_run(state, mode="draft")
    state["shots"][1]["status"] = status
    shots, after = estimate_run(state, mode="draft")
    assert [s["id"] for s in shots] == ["s0", "s2", "s3"]
    assert after["renders"] == before["renders"] * 3 // 4 and after["shots"] == 3


def test_a_recast_render_step_may_reserve_more_than_a_movie_step(film):
    """The user (22 Oct, after the trial's F1): a recast's render step may reserve up to the plan's limit."""
    state = film_recast()
    for shot in state["shots"]:
        shot["plan"]["windows"] = shot["plan"]["windows"] * 2
    film.world.recasts = [tools.compact(state)]
    jobs = film.world.recasts[0]["estimate"]["renders"]
    assert STEP_JOBS_MAX < jobs <= PLAN_JOBS_MAX
    tool = ProposePlan()
    steps = [
        {"kind": "render", "title": "Recast the film", "target": RECAST, "jobs": jobs},
        {"kind": "review", "title": "Review the recast"},
    ]
    result = tool.run(film.context(), tool.Input(goal="Recast the Navigator", steps=steps))
    assert result.ok, result.discrepancy


def test_film_scope_needs_a_library_and_reads_only_its_recast(kernel, monkeypatch):
    reads = []
    monkeypatch.setattr(
        scope, "recast_state", lambda session, library, recast_id, **kw: reads.append(recast_id)
    )
    with pytest.raises(ApplicationError, match="Open a Library") as missing:
        scope.scope_state(None, "", "film", RECAST, "off")
    assert missing.value.status == 409 and not reads
    monkeypatch.setattr(scope, "recast_state", lambda *args, **kw: film_recast())
    with kernel.db.transaction() as session:
        state = scope.scope_state(session, "", "film", RECAST, "off", kernel.harness.library)
    assert isinstance(state, FilmState) and state.recast_id == RECAST
    assert state.movie == {"id": "", "title": "Harbor film", "revision": 3}
    assert [r["id"] for r in state.recasts] == [RECAST] and "conforms" not in state.recasts[0]


@pytest.fixture
def film_session(kernel, monkeypatch):
    k = kernel
    monkeypatch.setattr(scope, "recast_state", lambda *args, **kw: film_recast())
    row = k.harness.open("ignored", "film", RECAST)
    assert row["_created"] and row["movie_id"] == "" and row["scope_id"] == RECAST
    clock = SimpleNamespace(now=1000.0, reads=0, state=film_recast())

    def reader(session, library, recast_id, *, private_plans=False):
        assert recast_id == RECAST and private_plans
        clock.reads += 1
        return deepcopy(clock.state)

    monkeypatch.setattr(plans, "recast_state", reader)
    monkeypatch.setattr(plans.time, "monotonic", lambda: clock.now)
    return SimpleNamespace(k=k, id=row["id"], clock=clock)


def started(f, frame_range=None):
    """The agent's start_recast_run result, linked to the executing plan as the loop does."""
    k = f.k
    document = AgentSession.model_validate(k.harness.read(f.id)["document"])
    document.plan = plan()
    receipt = {
        "kind": "recast_run",
        "id": f"{RECAST}:4",
        "recast_id": RECAST,
        "budget_reserved": 2,
    }
    value = tools.StartRecastRun.Input(
        recast_id=RECAST, mode="draft", render_budget=2, range=frame_range
    )
    k.harness._link_step(
        document, ToolResult(changed=True, receipts=[receipt]), tools.StartRecastRun(), value
    )
    return document, receipt


def test_a_recast_run_puts_the_next_review_in_the_users_hands(film_session):
    document, receipt = started(film_session)
    render, review = document.plan.steps
    assert render.status == "pending" and render.receipts == [receipt]
    assert review.status == "awaiting_user" and review.review_of == f"recast:{RECAST}"
    # A ranged run records its own range with the step.
    document, _ = started(film_session, tools.FrameRange(start=0, end=150))
    assert document.plan.steps[1].review_of == f"recast:{RECAST}@0-150"


def save(f, document):
    k = f.k
    with k.db.transaction() as session:
        row = k.store.read_session(session, f.id)
        k.store.update_session(
            session, f.id, row["revision"], state="waiting_human", document=document.model_dump()
        )


def shots(*statuses):
    return [
        {"id": f"s{i}", "start": i * 50, "end": (i + 1) * 50, "status": status}
        for i, status in enumerate(statuses)
    ]


def test_recast_review_completes_from_the_recast_with_a_throttled_driver(film_session):
    f = film_session
    k, clock = f.k, f.clock
    document, _ = started(f, tools.FrameRange(start=0, end=150))
    document.plan.steps[0].status = "done"  # the agent marked its render step done
    save(f, document)
    clock.state["run"] = {"state": "running", "range": None}
    assert not k.harness.resume_reviewed(f.id) and clock.reads == 1
    # Within 30 s the driver's poll does not read the recast again, even if it settled.
    clock.state["run"] = {"state": "finished", "range": {"start": 0, "end": 150}}
    clock.state["shots"] = shots("approved", "needs_review", "failed", "planned")
    clock.now += 29
    assert not k.harness.resume_reviewed(f.id) and clock.reads == 1
    clock.now += 2
    assert not k.harness.resume_reviewed(f.id) and clock.reads == 2
    for paused in ({"state": "paused", "range": None}, {"state": "paused", "reason": "Budget"}):
        clock.state["run"] = paused
        clock.state["shots"] = shots("approved")
        clock.now += 31
        assert not k.harness.resume_reviewed(f.id)
    step = AgentSession.model_validate(k.harness.read(f.id)["document"]).plan.steps[1]
    assert step.status == "awaiting_user"
    # Shot 3 lies outside the run's range; its pending review does not hold this step.
    clock.state["run"] = {"state": "finished", "range": {"start": 0, "end": 150}}
    clock.state["shots"] = shots("approved", "kept_original", "failed", "needs_review")
    clock.now += 31
    assert k.harness.resume_reviewed(f.id)
    row = k.harness.read(f.id)
    saved = AgentSession.model_validate(row["document"])
    assert row["state"] == "queued" and saved.plan.state == "done"
    assert saved.plan.steps[1].status == "done"
    turn = k.harness.turns(f.id)[-1]
    assert turn["role"] == "system" and turn["action"] == {
        "message": f"The user reviewed recast {RECAST}: 1 approved, 1 kept original, "
        "1 failed; continuing the plan.",
        "review_recast_id": RECAST,
    }


def test_a_later_run_over_other_shots_does_not_complete_the_review(film_session):
    """Astra R-C: the step waits on the shots of the run that created it, not the latest run's range."""
    f = film_session
    k, clock = f.k, f.clock
    document, _ = started(f, tools.FrameRange(start=0, end=100))
    save(f, document)
    # The user then ran and reviewed shots 3-4; shot 1 of the agent's run still waits.
    clock.state["run"] = {"state": "finished", "range": {"start": 100, "end": 200}}
    clock.state["shots"] = shots("needs_review", "approved", "approved", "approved")
    k.harness.post_message(f.id, "Shots 3 and 4 are done.", "film-later-run")
    saved = AgentSession.model_validate(k.harness.read(f.id)["document"])
    assert saved.plan.steps[1].status == "awaiting_user"
    clock.state["shots"] = shots("approved", "approved", "approved", "approved")
    k.harness.post_message(f.id, "Shot 1 too.", "film-later-run-2")
    saved = AgentSession.model_validate(k.harness.read(f.id)["document"])
    assert saved.plan.steps[1].status == "done"


def test_a_user_message_checks_the_recast_review_at_once(film_session):
    f = film_session
    k, clock = f.k, f.clock
    document, _ = started(f)
    save(f, document)
    clock.state["run"] = {"state": "running", "range": None}
    assert not k.harness.resume_reviewed(f.id) and clock.reads == 1
    clock.state["run"] = {"state": "idle", "range": None}
    clock.state["shots"] = shots("approved", "approved")
    k.harness.post_message(f.id, "I chose the versions.", "film-message")
    assert clock.reads == 2
    saved = AgentSession.model_validate(k.harness.read(f.id)["document"])
    assert saved.plan.steps[1].status == "done" and saved.plan.state == "executing"


def test_no_library_skips_the_recast_review_without_error(film_session):
    f = film_session
    document, _ = started(f)
    save(f, document)
    f.k.harness.library = None
    assert not f.k.harness.resume_reviewed(f.id) and f.clock.reads == 0
