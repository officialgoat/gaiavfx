"""S7m, copied e2e run 5: cut a62dbe9f, turns 15/25/44/57/61 and schema failure.

Strings are minimized from the copied DB; no workstation DB or GPU is required.
"""

from copy import deepcopy
from types import SimpleNamespace

import harness_support
import pytest
import test_harness_e2e_tools
from harness_support import action, run
from pydantic import ValidationError
from test_harness_e2e_tools import call
from test_harness_round5 import executing
from test_harness_tools import context_for

from gaiavfx.application.harness.actions import parse_action
from gaiavfx.application.harness.session import PlanStep
from gaiavfx.application.harness.tools.authoring import require_shot, validate_overhead
from gaiavfx.application.harness.tools.draft import DraftStoryline
from gaiavfx.application.harness.tools.edits import EditCut
from gaiavfx.application.harness.tools.places import EditPlace
from gaiavfx.application.harness.tools.plan import ProposePlan
from gaiavfx.application.harness.tools.prepare import PrepareCut
from gaiavfx.application.harness.world import direction, operations
from gaiavfx.shared.errors import ApplicationError

world = harness_support.world
kernel = harness_support.kernel
unplated = test_harness_e2e_tools.unplated


def test_d1_draft_same_camera_reverse_rolls_back(world):
    work, setup = world
    ctx = context_for(work, setup)
    with work.db.transaction() as session:
        before = direction.read_movie(session, setup["movie_id"])
    beat = {
        "title": "Reverse at the railing",
        "subject_ids": [ctx.state.cast[0]["id"]],
        "place": {"noun": "courtyard", "description": "Sunlit courtyard"},
        "continues": True,
        "phases": [
            {"start_frame": 0, "description": "Arin walks", "screen_direction": "left to right"}
        ],
        "cuts": [
            {
                "frame": 36,
                "from_angle": setup["angles"][0],
                "to_angle": setup["angles"][0],
                "relation": "reverse",
                "crosses_barrier": True,
                "shot": "Arin beyond the railing with the courtyard behind him",
                "screen_direction": "right to left",
            }
        ],
    }
    tool = DraftStoryline()
    result = tool.run(ctx, tool.Input(expected_revision=before["revision"], beats=[beat]))
    assert not result.ok and "the cut needs a different target angle" in result.discrepancy
    assert "edit_place add_angle (the empty place from that camera)" in result.discrepancy
    assert not result.receipts
    with work.db.transaction() as session:
        assert direction.read_movie(session, setup["movie_id"]) == before


def test_d2_retarget_names_clear_relation_and_echo_the_actual_cut(world):
    work, setup = world
    ctx, tool = context_for(work, setup), EditCut()
    cut = ctx.state.cuts[0]
    with work.db.transaction() as session:
        record = direction.read_cut(session, cut["id"])
    result = call(
        tool,
        ctx,
        op="angles",
        cut_id=cut["id"],
        expected_revision=record["revision"],
        from_angle_id="gate",
        to_angle_id="along",
    )
    assert result.ok, result
    assert "gate -> along, relation unknown" in result.payload["message"]
    assert "clears the relation" in result.payload["message"]
    ctx = context_for(work, setup)
    refused = PrepareCut().run(ctx, PrepareCut.Input(cut_id=cut["id"]))
    assert not refused.ok and "Declare the relation" in refused.discrepancy
    result = call(
        tool,
        ctx,
        op="relation",
        cut_id=cut["id"],
        expected_revision=result.payload["revision"],
        relation="reverse",
        crosses_barrier=True,
    )
    assert result.ok and "gate -> along, relation reverse" in result.payload["message"]


def test_d2_create_existing_frame_names_angles_and_refuses_equal_targets(world):
    work, setup = world
    ctx, tool = context_for(work, setup), EditCut()
    value = tool.Input(
        op="create",
        segment_id=setup["segment_id"],
        expected_revision=1,
        frame=36,
        from_angle_id="gate",
        to_angle_id="along",
        phase={"description": "Arin beyond the railing", "screen_direction": "right to left"},
    )
    refused = tool.run(ctx, value)
    assert "op=angles" in refused.discrepancy
    value.to_angle_id = "reverse"
    assert "op=relation" in tool.run(ctx, value).discrepancy
    value.to_angle_id = "gate"
    assert "edit_place add_angle" in tool.run(ctx, value).discrepancy


@pytest.mark.parametrize("evidence", ["none visible", "no people", "words", "rejection"])
def test_d3_known_empty_source_has_only_empty_branch_and_runtime_backstop(unplated, evidence):
    work, setup = unplated
    ctx, tool = context_for(work, setup), PrepareCut()
    if evidence == "words":
        ctx.recent_user_words = ["The Empty courtyard image has nobody in it."]
    elif evidence == "rejection":
        ctx.plate_rejections = {setup["plate"]: "Use empty: true; the removal erased the railing"}
    else:
        ctx.reference_notes = {setup["plate"]: {"subject": evidence}}
    removal = {
        "source_asset": setup["plate"],
        "whom": "Arin",
        "object_pronoun": "him",
        "behind": "the courtyard",
    }
    schema = tool.narrow(ctx)
    with pytest.raises(ApplicationError):
        parse_action(action(tool.name, cut_id=setup["cut_id"], plate=removal), [(tool, schema)])
    refused = tool.run(ctx, tool.Input(cut_id=setup["cut_id"], plate=removal))
    assert not refused.ok and "known empty" in refused.discrepancy
    result = call(
        tool, ctx, cut_id=setup["cut_id"], plate={"source_asset": setup["plate"], "empty": True}
    )
    assert result.ok, result
    with work.db.transaction() as session:
        op = operations.operation(session, result.waits_on)
        assert op["budget_reserved"] == 0 and op["request"]["empty"]


def test_d3_plate_requires_a_structural_choice():
    for plate in (
        {"source_asset": "image"},
        {"source_asset": "image", "empty": False},
        {"source_asset": "image", "empty": True, "whom": "Arin"},
    ):
        with pytest.raises(ValidationError):
            PrepareCut.Input(cut_id="cut", plate=plate)


def test_d3_run5_user_words_resolve_the_courtyard_image_without_affecting_other_images():
    from gaiavfx.application.harness.tools.input_hints import known_empty_image

    ctx = SimpleNamespace(
        state=SimpleNamespace(
            assets=[
                {"id": "courtyard", "name": "Arcade courtyard.png", "kind": "image"},
                {"id": "kitchen", "name": "Kitchen.png", "kind": "image"},
            ],
            cast=[],
        ),
        attachments=[],
        reference_notes={},
        plate_rejections={},
        recent_user_words=["The courtyard image (second image) has nobody in it."],
        latest_user_message="Prepare the reverse",
    )
    assert known_empty_image(ctx, "courtyard")
    assert not known_empty_image(ctx, "kitchen")
    ctx.recent_user_words = ["The courtyard image isn't empty."]
    assert not known_empty_image(ctx, "courtyard")


@pytest.mark.parametrize(
    "behind", ["the black iron railing", "the iron railings", "the hanging lantern"]
)
def test_d3_removal_preserves_barriers_and_authored_keeps(unplated, behind):
    work, setup = unplated
    with work.db.transaction() as session:
        place = direction.read_place(session, setup["place_id"])
        direction.update_place(
            session,
            place["id"],
            place["revision"],
            description="Black iron railing beside paving. Keep the hanging lantern.",
        )
    ctx, tool = context_for(work, setup), PrepareCut()
    result = tool.run(
        ctx,
        tool.Input(
            cut_id=setup["cut_id"],
            plate={
                "source_asset": setup["plate"],
                "whom": "Arin",
                "object_pronoun": "him",
                "behind": behind,
            },
        ),
    )
    assert not result.ok and "behind describes what is revealed" in result.discrepancy
    with work.db.transaction() as session:
        assert not operations.recent(session)


def propose(ctx, steps):
    ctx.document.plan_mode = True
    return ProposePlan().run(
        ctx, ProposePlan.Input(goal="Prepare the reverse before rendering", steps=steps)
    )


def test_d4_prepare_step_costs_and_render_prerequisites(unplated):
    work, setup = unplated
    ctx = context_for(work, setup)
    ctx.reference_notes = {setup["plate"]: {"subject": "none visible"}}
    ctx.latest_user_message = "Prepare the reverse (plate, survey, pin, sheet) before rendering."
    steps = [
        {"kind": "prepare", "title": name, "target": setup["cut_id"], "jobs": cost}
        for name, cost in [
            ("Fit plate", 0),
            ("Survey", 1),
            ("Insert pin", 2),
            ("Assemble sheet", 0),
        ]
    ] + [
        {"kind": "render", "title": "Render segment", "target": setup["segment_id"], "jobs": 2},
        {"kind": "review", "title": "Review takes"},
    ]
    refused = propose(ctx, steps[-2:])
    assert not refused.ok and "render_prerequisites" in refused.discrepancy
    assert all(x in refused.discrepancy for x in ("plate", "survey", "pin", "sheet"))
    for index, expected in ((1, "1 jobs"), (2, "2 jobs")):
        bad = deepcopy(steps)
        bad[index]["jobs"] = 0
        refused = propose(ctx, bad)
        assert not refused.ok and expected in refused.discrepancy
    accepted = propose(ctx, steps)
    assert accepted.ok and accepted.payload["plan"]["budget_jobs"] == 5
    combined = [
        steps[0],
        {"kind": "prepare", "title": "Prepare views and pin", "target": setup["cut_id"], "jobs": 3},
        *steps[3:],
    ]
    assert propose(ctx, combined).ok
    combined[1]["jobs"] = 2
    refused = propose(ctx, combined)
    assert not refused.ok and "needs 3 jobs" in refused.discrepancy


def test_d4_unknown_relation_explains_technique_before_render(world):
    work, setup = world
    with work.db.transaction() as session:
        cut = direction.read_cut(session, setup["cut_id"])
        direction.update_cut(
            session,
            cut["id"],
            cut["revision"],
            relation=None,
            crosses_barrier=None,
            relation_source="unknown",
        )
    ctx = context_for(work, setup)
    result = propose(
        ctx,
        [
            {"kind": "edit", "title": "Declare relation", "target": setup["cut_id"]},
            {"kind": "render", "title": "Render", "target": setup["segment_id"], "jobs": 2},
            {"kind": "review", "title": "Review"},
        ],
    )
    assert not result.ok and "declaring its relation decides the technique" in result.discrepancy
    assert "reverse across a barrier needs views and a pin" in result.discrepancy


def test_d4_producer_cannot_spend_other_steps_budget(unplated):
    work, setup = unplated
    ctx, tool = context_for(work, setup), PrepareCut()
    ctx.document.plan_mode = True
    ctx.document.plan = executing(
        PlanStep(kind="prepare", title="Plate", target=setup["cut_id"], jobs=0),
        PlanStep(kind="render", title="Later render", target=setup["segment_id"], jobs=5),
    )
    result = tool.run(
        ctx,
        tool.Input(
            cut_id=setup["cut_id"],
            plate={
                "source_asset": setup["plate"],
                "whom": "Arin",
                "object_pronoun": "him",
                "behind": "the courtyard",
            },
        ),
    )
    assert (
        not result.ok
        and "producer needs 2 jobs" in result.discrepancy
        and "0 jobs remaining" in result.discrepancy
    )
    with work.db.transaction() as session:
        assert not operations.recent(session)


def test_d4_receipt_reattachment_does_not_refund_step_jobs():
    from gaiavfx.application.harness.tools.plan_costs import afford_step

    step = PlanStep(
        kind="render",
        title="Render",
        jobs=2,
        receipts=[
            {"kind": "operation", "id": "render", "budget_reserved": 2},
            {"kind": "operation", "id": "render"},
        ],
    )
    ctx = SimpleNamespace(document=SimpleNamespace(plan_mode=True, plan=executing(step)))
    with pytest.raises(ApplicationError, match="producer needs 1 jobs.*0 jobs remaining"):
        afford_step(ctx, 1)


@pytest.mark.parametrize("text", ["full", "wide shot", "extreme close up"])
def test_d5_shot_requires_camera_description(text):
    with pytest.raises(ApplicationError, match="what the new camera shows"):
        require_shot(text)
    require_shot("Arin beyond the railing with the sunlit courtyard behind him")


def test_d5_overhead_looks_down_and_bad_edit_rolls_back(world):
    work, setup = world
    ctx, tool = context_for(work, setup), EditPlace()
    with work.db.transaction() as session:
        before = direction.read_place(session, setup["place_id"])
    result = tool.run(
        ctx,
        tool.Input(
            op="set",
            place_id=before["id"],
            expected_revision=before["revision"],
            overhead="open sky, tree branches overhead",
        ),
    )
    assert not result.ok and "looking straight down on the place" in result.discrepancy
    with work.db.transaction() as session:
        assert direction.read_place(session, before["id"]) == before
    validate_overhead("Looking straight down on the courtyard and tree canopy from above")


@pytest.mark.parametrize("text", ["Open sky", "The ceiling above the hall", "Branches overhead"])
def test_d5_overhead_refuses_views_that_only_look_up(text):
    with pytest.raises(ValueError, match="looking straight down on the place"):
        validate_overhead(text)


@pytest.mark.parametrize(
    "error", [ApplicationError("schema uses 4626 > 4500 tokens"), ValueError("broken context")]
)
def test_d7_context_failure_records_a_system_turn_and_message_retries(kernel, monkeypatch, error):
    from gaiavfx.application.harness import loop

    original = loop.assemble

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(loop, "assemble", fail)
    row = run(kernel, action("say", text="Ready"))
    assert row["state"] == "waiting_human"
    turn = kernel.harness.turns(kernel.id)[-1]
    assert turn["role"] == "system" and str(error) in turn["action"]["message"]
    assert "Post a message to retry" in turn["action"]["message"]
    assert not kernel.model.calls
    monkeypatch.setattr(loop, "assemble", original)
    kernel.harness.post_message(kernel.id, "Retry", "retry")
    kernel.runner.run_once()
    assert kernel.harness.read(kernel.id)["state"] == "idle"
    assert len(kernel.model.calls) == 1


@pytest.mark.parametrize(
    "message",
    ["Prepare the reverse, removing the walker first.", "Make the plate without Arin."],
)
def test_live11_explicit_removal_offers_only_the_removal_plate(unplated, message):
    """local-e11 plate-from-removal: the model chose empty: true although the user
    asked to remove the walker, so nothing was removed."""
    work, setup = unplated
    ctx, tool = context_for(work, setup), PrepareCut()
    ctx.latest_user_message = message
    empty = {"source_asset": setup["plate"], "empty": True}
    schema = tool.narrow(ctx)
    with pytest.raises(ApplicationError):
        parse_action(action(tool.name, cut_id=setup["cut_id"], plate=empty), [(tool, schema)])
    refused = tool.run(ctx, tool.Input(cut_id=setup["cut_id"], plate=empty))
    assert not refused.ok and "asked for a removal" in refused.discrepancy
    removal = {
        "source_asset": setup["plate"],
        "whom": "the walker",
        "object_pronoun": "him",
        "behind": "the courtyard",
    }
    assert call(tool, ctx, cut_id=setup["cut_id"], plate=removal).ok


def test_live11_removal_words_without_a_person_keep_the_empty_plate(unplated):
    work, setup = unplated
    ctx, tool = context_for(work, setup), PrepareCut()
    ctx.latest_user_message = "Remove the stale sheet and prepare the reverse."
    result = call(
        tool, ctx, cut_id=setup["cut_id"], plate={"source_asset": setup["plate"], "empty": True}
    )
    assert result.ok, result


def test_e2e7_one_prepare_step_covers_the_free_plate_fit_and_sheet(unplated):
    """e2e run 7: one prepare step titled for the survey, pin and place survey (3 jobs)
    was refused six times for "plate, sheet", although both cost nothing."""
    work, setup = unplated
    ctx = context_for(work, setup)
    ctx.reference_notes = {setup["plate"]: {"subject": "none visible"}}
    ctx.latest_user_message = "Prepare the reverse cut and render two takes of segment 2."
    steps = [
        {
            "kind": "prepare",
            "title": "Survey Reverse Courtyard angle, insert Arin, survey place for the cut",
            "target": setup["cut_id"],
            "jobs": 3,
        },
        {
            "kind": "render",
            "title": "Render segment 2, two takes",
            "target": setup["segment_id"],
            "jobs": 2,
        },
        {"kind": "review", "title": "Review the two takes"},
    ]
    accepted = propose(ctx, steps)
    assert accepted.ok, accepted.discrepancy


def test_e2e7_a_costed_removal_plate_still_needs_naming_or_a_generic_step(unplated):
    work, setup = unplated
    ctx = context_for(work, setup)
    ctx.latest_user_message = "Prepare the reverse, removing the walker first, then render."
    named = {
        "kind": "prepare",
        "title": "Survey the reverse angle and insert Arin",
        "target": setup["cut_id"],
        "jobs": 3,
    }
    render = {"kind": "render", "title": "Render", "target": setup["segment_id"], "jobs": 2}
    refused = propose(ctx, [named, render, {"kind": "review", "title": "Review"}])
    assert not refused.ok and "plate" in refused.discrepancy
    assert "covers only those" in refused.discrepancy


def test_live12_placeholder_removal_wording_is_refused_with_the_users_words(unplated):
    """local-e12 plate-from-removal: whom, object_pronoun and behind arrived as "none"."""
    work, setup = unplated
    ctx, tool = context_for(work, setup), PrepareCut()
    ctx.latest_user_message = "Prepare the reverse, removing the walker first."
    placeholder = {
        "source_asset": setup["plate"],
        "whom": "none",
        "object_pronoun": "none",
        "behind": "none",
    }
    refused = tool.run(ctx, tool.Input(cut_id=setup["cut_id"], plate=placeholder))
    assert not refused.ok
    assert (
        "cannot be a placeholder" in refused.discrepancy and "'the walker'" in refused.discrepancy
    )
    with work.db.transaction() as session:
        assert not operations.recent(session)


def test_live12_jobs_on_an_edit_step_names_the_value_to_set(unplated):
    """local-e12 draft-storyline: an edit step with jobs 1 was refused three times
    without saying what to set."""
    work, setup = unplated
    ctx = context_for(work, setup)
    refused = propose(
        ctx, [{"kind": "edit", "title": "Draft three-part storm storyline", "jobs": 1}]
    )
    assert not refused.ok
    assert "Set jobs: 0 on the edit step 'Draft three-part storm storyline'" in refused.discrepancy


@pytest.mark.parametrize(
    "text",
    [
        "I'll set it to reverse so the shot is declared.",
        "I'll declare the reverse across the railing.",
        "Let me assemble the views sheet.",
    ],
)
def test_live13_promise_verbs_include_set_declare_and_assemble(text):
    """local-e13 plan-deviation: "I'll set it to reverse" ended the turn unrefused."""
    from gaiavfx.application.harness.tools.request_rules import promised_work

    assert promised_work(text)


def test_live13_one_cut_fills_an_omitted_prepare_target(unplated):
    """local-e13 plan-reject: four proposals omitted the target with one cut in scope."""
    work, setup = unplated
    ctx = context_for(work, setup)
    ctx.latest_user_message = "Survey the courtyard for the reverse cut."
    accepted = propose(
        ctx, [{"kind": "prepare", "title": "Survey the reverse angle in the Courtyard", "jobs": 1}]
    )
    assert accepted.ok, accepted.discrepancy
    assert accepted.payload["plan"]["steps"][0]["target"] == setup["cut_id"]


def test_live13_several_cuts_name_the_target_candidates(world):
    work, setup = world
    ctx = context_for(work, setup)
    # Two cuts in scope: the default cannot choose, so the refusal must name both.
    first = ctx.state.cuts[0]
    segment = next(s for s in ctx.state.segments if s["id"] == first["segment_id"])
    segment["cuts"].append({**first, "id": "second-cut", "frame": 70})
    ctx.latest_user_message = "Survey the reverse angle."
    refused = propose(ctx, [{"kind": "prepare", "title": "Survey the reverse angle", "jobs": 1}])
    assert not refused.ok and "prepare_target_required" in refused.discrepancy
    assert "one of:" in refused.discrepancy and "cut " in refused.discrepancy


def test_e2e10_render_target_follows_the_segment_the_title_names(world):
    """e2e run 10: "Render segment 1, 2 takes" had no target; the default took the only
    segment with a cut (segment 2), and every render was refused for a missing segment 1
    take. The title's segment wins; without one, only a single-segment movie defaults."""
    from gaiavfx.application.harness.tools.plan import default_producer_targets

    work, setup = world
    ctx = context_for(work, setup)
    first = next(s for s in ctx.state.segments if s["ordinal"] == 0)
    if len(ctx.state.segments) == 1:
        # Run 10 had two segments: the opener without cuts and the continuing reverse.
        ctx.state.segments.append({**first, "id": "second-segment", "ordinal": 1, "cuts": []})
    steps = [
        SimpleNamespace(kind="render", title="Render segment 1, 2 takes", target=""),
        SimpleNamespace(kind="render", title="Render two takes", target=""),
    ]
    default_producer_targets(ctx, steps)
    assert steps[0].target == first["id"]
    assert steps[1].target == ""
