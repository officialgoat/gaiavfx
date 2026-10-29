"""S7k: replay the local-e6 promise, wrong-cut and narrative-draft failures."""

from copy import deepcopy

import harness_support
import pytest
from harness_support import action
from test_harness_drafting import draft_context

from gaiavfx.application.harness.session import AgentSession
from gaiavfx.application.harness.tools.draft import DraftStoryline
from gaiavfx.application.harness.tools.plan import ProposePlan
from gaiavfx.application.harness.world import direction

kernel = harness_support.kernel


def post(kernel, *sequence, message="Survey the courtyard for the reverse cut."):
    kernel.model.replies = iter(sequence)
    kernel.harness.post_message(kernel.id, message, str(len(kernel.model.calls)))
    kernel.runner.run_once()
    return [t for t in kernel.harness.turns(kernel.id) if t["role"] == "agent"]


def test_k1_promise_then_plan(kernel):
    turns = post(
        kernel,
        action("read_movie"),
        action("say", text="I'll survey the reverse angle so you can review the views."),
        action("propose_plan", goal="Survey", steps=[{"kind": "prepare", "title": "Survey"}]),
    )
    assert [t["state"] for t in turns] == ["accepted", "rejected", "accepted"]
    assert "call propose_plan now" in turns[1]["result"]["discrepancy"]
    assert "I'll survey the reverse angle" in turns[1]["result"]["discrepancy"]
    assert kernel.harness.read(kernel.id)["document"]["plan"]["state"] == "proposed"


@pytest.mark.parametrize("second", ["I'll survey the reverse angle.", "The view is missing."])
def test_k1_only_first_say_is_checked_and_new_human_resets(kernel, second):
    for _ in range(2):
        turns = post(
            kernel,
            action("say", text="I'll survey the reverse angle."),
            action("say", text=second),
        )
        assert [t["state"] for t in turns[-2:]] == ["rejected", "accepted"]


def test_k1_plain_second_say_and_restart_use_durable_turns(kernel):
    from gaiavfx.application.harness.loop import Harness
    from gaiavfx.application.harness.tools.talk import Say

    post(
        kernel,
        action("say", text="I'll survey the reverse angle."),
        action("say", text="The view is missing."),
    )
    restarted = Harness(kernel.db, kernel.store, kernel.model, lambda: kernel.settings)
    row = restarted.read(kernel.id)
    ctx = restarted.context(
        row, AgentSession.model_validate(row["document"]), restarted.turns(kernel.id)
    )
    assert Say().run(ctx, Say.Input(text="I'll survey the reverse angle.")).ok


@pytest.mark.parametrize(
    "text",
    [
        "I'll render takes.",
        "I will survey the reverse angle.",
        "I’m going to prepare the cut.",
        "Let me draft the beats.",
        "Next I'll create the cast.",
        "Next, I'll add a cut.",
        "I'll edit the cut.",
        "I'll insert Arin.",
        "I'll split the segment.",
        "I'll propose the plan.",
        "I'll plan the shots.",
        "I'll pin the frame.",
        "I'll set up the cast.",
    ],
)
def test_k1_closed_promise_patterns(text):
    from gaiavfx.application.harness.tools.request_rules import promised_work

    assert promised_work(text)


@pytest.mark.parametrize(
    "text",
    [
        "Would you like me to render?",
        "Shall I draft the beats?",
        "I can render once you approve.",
        "I'll render if you approve.",
        "I'll render after approval.",
        "I'll not render yet.",
        "I've rendered the takes.",
        "I will explain the options.",
        "Let me know if you want to render.",
        "The phrase is: I'll render.",
        "I'll discuss this. We can render later.",
    ],
)
def test_k1_closed_patterns_allow_discussion(text):
    from gaiavfx.application.harness.tools.request_rules import promised_work

    assert not promised_work(text)


@pytest.mark.parametrize(
    ("text", "promised"),
    [
        ("Let me check the current state of the recast to see the details for shot 9.", True),
        ("I'll look at shot 9 first.", True),
        ("I will read the movie.", True),
        ("Let me pull up the cast.", True),
        ("Let me know if you want me to check shot 9.", False),
        ("Shall I check shot 9?", False),
        ("I checked shot 9: it has two versions.", False),
        ("Let me see if it has versions.", False),
        ("Let me quickly check shot 9.", True),
        ("I'll take a look at the recast.", True),
        # Astra's P5 review: a read verb later in the sentence, or "Let me see:", answers instead of promising.
        ("Let me explain what I see: three shots are ready.", False),
        ("Let me see: shot 9 has two versions.", False),
        ("I'll summarise what I found in the recast.", False),
    ],
)
def test_k1_promised_reads(text, promised):
    from gaiavfx.application.harness.tools.request_rules import promised_read

    assert bool(promised_read(text)) is promised


def test_k1_a_promised_read_is_refused_once_and_the_read_follows(kernel):
    turns = post(
        kernel,
        action("say", text="I can help with that. Let me check the movie first."),
        action("read_movie"),
        action("say", text="The movie has one segment."),
        message="What is in this movie?",
    )
    assert [t["state"] for t in turns] == ["rejected", "accepted", "accepted"]
    assert "call read_movie now" in turns[0]["result"]["discrepancy"]


def test_k1_history_reads_work_and_approval_boundary():
    from gaiavfx.application.harness.tools.request_rules import turn_actions

    def turn(name, state="accepted"):
        return {"role": "agent", "state": state, "action": {"tool": name}}

    turns = [{"role": "human", "state": "accepted", "action": {"message": "Survey"}}]
    for tool in (
        "describe_reference",
        "reference_notes",
        "inspect_cut",
        "read_movie",
        "read_recast",
    ):
        turns.append(turn(tool))
    assert turn_actions(turns) == (False, False)
    turns.append(turn("say", "rejected"))
    assert turn_actions(turns) == (False, True)
    turns.append(turn("propose_plan"))
    assert turn_actions(turns) == (True, True)
    turns.append({"role": "system", "state": "accepted", "action": {"message": "Approved"}})
    assert turn_actions(turns, len(turns) - 1) == (False, False)
    turns.append(turn("prepare_cut"))
    assert turn_actions(turns, len(turns) - 2) == (True, False)


def test_k1_direct_tool_and_no_available_tool(kernel):
    from gaiavfx.application.harness.tools.talk import Say

    ctx = kernel.harness.context(kernel.row, AgentSession(plan_mode=False), [])
    tool = Say()
    result = tool.run(ctx, tool.Input(text="I'll add a place."))
    assert not result.ok and "call edit_place now" in result.discrepancy
    ctx.say_attempted = False
    result = tool.run(ctx, tool.Input(text="I'll render takes."))
    assert not result.ok and "call the tool now" in result.discrepancy
    ctx.say_attempted, ctx.work_performed = False, True
    assert tool.run(ctx, tool.Input(text="I'll add a place.")).ok


def test_k2_inspect_other_segment_then_create_requested_cut(tmp_path):
    from harness_eval import run_task
    from harness_eval_scripts import GOOD
    from harness_eval_tasks import TASKS

    task = next(t for t in TASKS if t.id == "hard-cut-at-frame-36")
    evidence = []
    result = run_task(task, GOOD[task.id], tmp_path, evidence=evidence.append)
    assert result["passed"], result
    turns = [t for t in evidence[0]["turns"] if t["role"] == "agent"]
    assert [t["action"]["tool"] for t in turns] == ["inspect_cut", "edit_cut", "say"]
    assert (
        "This cut (segment 1, f36, gate -> reverse) already has an authored relation."
        in turns[0]["result"]["payload"]["message"]
    )
    assert "Offer the next step" not in turns[0]["result"]["payload"]["message"]


@pytest.mark.parametrize("text", ["Shall I render takes?", "I can render once you approve."])
def test_k1_questions_and_conditional_offers_pass(kernel, text):
    turns = post(kernel, action("say", text=text))
    assert len(turns) == 1 and turns[0]["state"] == "accepted"


def story(kernel, *, places=1, duplicate=False):
    ctx, subject = draft_context(kernel)
    with kernel.db.transaction() as session:
        place = direction.create_place(session, "Courtyard")
        angles = [direction.add_angle(session, place["id"], n) for n in ("gate", "reverse")]
        if duplicate:
            angles.append(direction.add_angle(session, place["id"], "REVERSE"))
        if places == 2:
            gallery = direction.create_place(session, "Gallery")
            for name in ("gate", "reverse"):
                direction.add_angle(session, gallery["id"], name)
    ctx = kernel.harness.context(kernel.row, ctx.document, [])
    beat = {
        "title": "Reverse",
        "place": {"noun": "courtyard", "description": "Stone paving"},
        "subject_ids": [subject["id"]],
        "continues": False,
        "phases": [
            {"start_frame": 0, "description": "Arin walks", "screen_direction": "left to right"}
        ],
        "from_angle": "GATE",
        "cuts": [
            {
                "frame": 36,
                "to_angle": "reverse",
                "relation": "reverse",
                "crosses_barrier": True,
                "shot": "Arin walks past the railing from the reverse side.",
                "screen_direction": "right to left",
            }
        ],
    }
    return ctx, beat, place, angles


def draft(ctx, beats):
    tool = DraftStoryline()
    return tool.run(ctx, tool.Input(expected_revision=ctx.state.movie["revision"], beats=beats))


def test_k3_single_place_and_case_insensitive_angle_names(kernel):
    ctx, beat, _, angles = story(kernel)
    result = draft(ctx, [beat])
    assert result.ok, result.discrepancy
    with kernel.db.transaction() as session:
        cut = direction.read_cut(session, result.payload["segments"][0]["cut_ids"][0])
    assert cut["from_angle_id"] == angles[0]["id"]
    assert cut["to_angle_id"] == angles[1]["id"]


def test_k1_names_the_work_target_before_supporting_objects(kernel):
    from gaiavfx.application.harness.tools.talk import Say

    ctx, beat, _, _ = story(kernel)
    assert draft(ctx, [beat]).ok
    ctx = kernel.harness.context(kernel.row, AgentSession(plan_mode=False), [])
    tool = Say()
    for text, expected in (
        ("I'll add a cut in the reverse angle.", "edit_cut"),
        ("I'll create two segments with cuts.", "edit_segment"),
        ("I'll add a phase to the segment.", "edit_phase"),
    ):
        ctx.say_attempted = False
        result = tool.run(ctx, tool.Input(text=text))
        assert not result.ok and f"call {expected} now" in result.discrepancy


def test_k3_missing_place_lists_named_choices(kernel):
    ctx, beat, _, _ = story(kernel, places=2)
    result = draft(ctx, [beat])
    assert not result.ok and "beat 1 has cuts: use angle ids" in result.discrepancy
    for place in ctx.state.places:
        assert f"{place['name']} ({place['id']})" in result.discrepancy
    assert "not in this session's scope" not in result.discrepancy


def test_k3_ambiguous_angle_names_list_candidates(kernel):
    ctx, beat, _, angles = story(kernel, duplicate=True)
    result = draft(ctx, [beat])
    assert not result.ok and "ambiguous" in result.discrepancy
    for angle in angles:
        assert f"{angle['name']} ({angle['id']})" in result.discrepancy


@pytest.mark.parametrize(
    "field,value", [("from_angle", None), ("from_angle", "unknown"), ("to_angle", "wrong")]
)
def test_k3_missing_or_unknown_angles_give_exact_choices(kernel, field, value):
    ctx, beat, _, angles = story(kernel)
    if field == "to_angle":
        beat["cuts"][0][field] = value
    else:
        beat[field] = value
    result = draft(ctx, [beat])
    assert not result.ok and field in result.discrepancy
    assert (
        "required here" in result.discrepancy if value is None else "unknown" in result.discrepancy
    )
    for angle in angles:
        assert f"{angle['name']} ({angle['id']})" in result.discrepancy


def test_k3_scoped_missing_is_not_out_of_scope():
    from gaiavfx.application.harness.tools.common import scoped
    from gaiavfx.shared.errors import ApplicationError

    with pytest.raises(ApplicationError, match=r"Place is required here: Courtyard \(p1\)"):
        scoped([{"id": "p1", "name": "Courtyard"}], None, "Place")


def test_k3_missing_source_can_continue_known_predecessor(kernel):
    ctx, beat, _, angles = story(kernel)
    second = deepcopy(beat)
    second.update(continues=True)
    second.pop("from_angle")
    second["cuts"][0]["to_angle"] = "gate"
    second["phases"][0]["screen_direction"] = "right to left"
    second["cuts"][0]["screen_direction"] = "left to right"
    result = draft(ctx, [beat, second])
    assert result.ok, result.discrepancy
    with kernel.db.transaction() as session:
        cut = direction.read_cut(session, result.payload["segments"][1]["cut_ids"][0])
    assert cut["from_angle_id"] == angles[1]["id"]


def test_k4_refuses_third_end_beat_without_writes(kernel):
    ctx, beat, _, _ = story(kernel)
    beat.update(cuts=[])
    ctx.latest_user_message = "Set up the cast, the place and these two segments."
    result = draft(ctx, [beat, beat, {**beat, "title": "End"}])
    assert not result.ok and "'these two segments'" in result.discrepancy
    assert not result.receipts and not ctx.board.decisions
    with kernel.db.transaction() as session:
        assert direction.read_movie(session, kernel.movie["id"])["segments"] == []
    assert draft(ctx, [beat, beat]).ok


def test_k4_without_count_allows_three_beats(kernel):
    ctx, beat, _, _ = story(kernel)
    beat.update(cuts=[])
    ctx.latest_user_message = "Draft a storyline."
    assert draft(ctx, [beat, beat, beat]).ok


def test_k4_plan_rejects_extra_creation_but_allows_existing_edits(kernel):
    ctx = kernel.harness.context(kernel.row, AgentSession(), [])
    ctx.latest_user_message = "Set up these two segments."
    tool = ProposePlan()
    result = tool.run(
        ctx, tool.Input(goal="Story", steps=[{"kind": "edit", "title": "Draft three beats"}])
    )
    assert not result.ok and "'these two segments'" in result.discrepancy
    assert ctx.document.plan is None
    assert tool.run(
        ctx,
        tool.Input(
            goal="Story",
            steps=[
                {"kind": "edit", "title": "Draft two beats"},
                {"kind": "edit", "title": "Edit three existing segments"},
            ],
        ),
    ).ok


@pytest.mark.parametrize(
    "word,n",
    [
        ("one", 1),
        ("two", 2),
        ("three", 3),
        ("four", 4),
        ("five", 5),
        ("six", 6),
        ("seven", 7),
        ("eight", 8),
        ("nine", 9),
        ("ten", 10),
        ("eleven", 11),
        ("twelve", 12),
        ("13", 13),
    ],
)
def test_k4_closed_count_words_and_digits(word, n):
    from gaiavfx.application.harness.tools.request_rules import described_count

    for unit in ("segments", "shots", "parts", "beats"):
        assert described_count(f"Draft {word} {unit}")[0] == n
    assert described_count(f"Draft a {word}-part storyline")[0] == n


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Shot 1: walk. Shot 2: reverse.", 2),
        ("3 shots", 3),
        ("three-part storyline", 3),
        ("two segments and three shots", None),
        ("two or three beats", None),
        ("maybe two beats", None),
        ("Draft shots", None),
        ("Add a cut at frame 36 in segment 2", None),
        ("Shot 2: reverse. Shot 3: end.", None),
        ("Edit two existing segments", None),
        ("Draft two to three segments", None),
        ("Draft 2-3 shots", None),
        ("Could two beats work?", None),
        ("Create 3 new segments", 3),
    ],
)
def test_k4_counts_only_when_clear(message, expected):
    from gaiavfx.application.harness.tools.request_rules import described_count

    found = described_count(message)
    assert (found[0] if found else None) == expected


def test_k4_plan_counts_multiple_new_segment_steps():
    from gaiavfx.application.harness.tools.plan import ProposedStep
    from gaiavfx.application.harness.tools.request_rules import plan_segment_count

    assert (
        plan_segment_count(
            [ProposedStep(kind="edit", title=f"Create segment {n}") for n in range(1, 4)]
        )
        == 3
    )
    assert plan_segment_count([ProposedStep(kind="edit", title="Draft the storyline")]) is None
    for title in ("Create shots 1 and 2", "Create segment 1 and 2"):
        assert plan_segment_count([ProposedStep(kind="edit", title=title)]) is None
    assert (
        plan_segment_count(
            [
                ProposedStep(kind="edit", title=f"Draft the {name} beat")
                for name in ("opening", "middle", "end")
            ]
        )
        == 3
    )
