"""S7k follow-up: reproduce e2e run 4 without a live model or GPU."""

import json
from copy import deepcopy

import harness_support
import pytest
import test_harness_reference_text
from harness_support import action, run
from test_harness_follow_through import draft, story
from test_harness_reference_text import ANGLE, prepare_grant
from test_harness_tools import context_for

from gaiavfx.application.harness.actions import action_schema, parse_action
from gaiavfx.application.harness.context import SCHEMA_CEILING, estimate
from gaiavfx.application.harness.session import AgentSession, Plan, PlanGrant, PlanStep
from gaiavfx.application.harness.tools import applicable_tools
from gaiavfx.application.harness.tools.common import available
from gaiavfx.application.harness.tools.draft import DraftStoryline
from gaiavfx.application.harness.tools.edits import EditPhase
from gaiavfx.application.harness.tools.insertion import insertion_facts, resolve_insertion
from gaiavfx.application.harness.tools.places import EditPlace
from gaiavfx.application.harness.tools.plan import edit_title
from gaiavfx.application.harness.tools.prepare import PrepareCut
from gaiavfx.application.harness.tools.render import RenderCandidates
from gaiavfx.application.harness.world import direction, operations
from gaiavfx.shared.errors import ApplicationError

kernel = harness_support.kernel
world = harness_support.world
insertion_ready = test_harness_reference_text.insertion_ready


def plan(context, kind, target="", *, edit=False):
    steps = [PlanStep(kind="edit", title="Author place", status="done")] if edit else []
    steps.append(
        PlanStep(
            kind=kind,
            title="Prepare cut" if kind == "prepare" else "Render two takes of segment 2",
            target=target,
            jobs=2,
        )
    )
    context.document.plan_mode = True
    context.document.plan = Plan(
        id="run4",
        revision=1,
        state="executing",
        goal="Continue the requested work",
        proposed_turn=0,
        budget_jobs=6,
        steps=steps,
        grant=PlanGrant(
            budget_jobs=6,
            step_kinds=[s.kind for s in steps],
            approved_turn=1,
            reserved_at_approval=0,
        ),
    )
    context.jobs_limit = 6
    return context


def test_k3_source_inside_cut_infers_place_and_schema_requires_it(kernel):
    context, beat, place, angles = story(kernel)
    beat.pop("from_angle")
    beat["continues"] = True
    beat["cuts"][0].update(from_angle=angles[0]["id"], to_angle=angles[1]["id"])
    props = DraftStoryline().narrow(context)["properties"]["beats"]["items"]["properties"]
    assert "place_id" not in props and "from_angle" not in props
    assert "from_angle" in props["cuts"]["items"]["required"]
    opening = {**deepcopy(beat), "title": "Opening", "continues": False, "cuts": []}
    result = draft(context, [opening, beat])
    assert result.ok, result.discrepancy
    with kernel.db.transaction() as session:
        cut = direction.read_cut(session, result.payload["segments"][1]["cut_ids"][0])
        assert direction.read_angle(session, cut["from_angle_id"])["place_id"] == place["id"]


@pytest.mark.parametrize("cross_place", [False, True])
def test_k3_chains_and_cross_place_angles_refuse_atomically(kernel, cross_place):
    context, beat, _, angles = story(kernel)
    beat["cuts"][0]["from_angle"] = angles[0]["id"]
    beat["cuts"][0]["to_angle"] = angles[1]["id"]
    if cross_place:
        with kernel.db.transaction() as session:
            other = direction.create_place(session, "Gallery")
            source = direction.add_angle(session, other["id"], "Gallery door")
        context = kernel.harness.context(kernel.row, context.document, [])
        beat["cuts"][0]["from_angle"] = source["id"]
    else:
        beat["cuts"].append({**beat["cuts"][0], "frame": 60})
    result = draft(context, [beat])
    assert not result.ok and not result.receipts and not context.board.decisions
    assert (
        ("Gallery" in result.discrepancy and "Courtyard" in result.discrepancy)
        if cross_place
        else ("gate" in result.discrepancy and "reverse" in result.discrepancy)
    )
    with kernel.db.transaction() as session:
        assert direction.read_movie(session, kernel.movie["id"])["segments"] == []


@pytest.mark.parametrize("edit", [False, True])
def test_k5_k6_place_repair_uses_actual_available_tool_and_lists_all_missing(world, edit):
    work, setup = world
    with work.db.transaction() as session:
        place = direction.read_place(session, setup["place_id"])
        direction.update_place(
            session, place["id"], place["revision"], description="", sound="", overhead=""
        )
        angle = direction.read_angle(session, setup["angles"][2])
        direction.update_angle(session, angle["id"], angle["revision"], description="")
    context = plan(context_for(work, setup), "prepare", setup["cut_id"], edit=edit)
    offered = {tool.name for tool, _ in applicable_tools(context)}
    assert available(context, "edit_place") is edit
    assert ("edit_place" in offered) is edit
    result = PrepareCut().run(context, PrepareCut.Input(cut_id=setup["cut_id"]))
    assert not result.ok
    for field in ("description", "sound", "overhead", "reverse", "straight down", "footsteps"):
        assert field in result.discrepancy
    assert ("edit_place" in result.discrepancy) is edit
    if not edit:
        assert "propose_plan" in result.discrepancy and "user approves" in result.discrepancy
        assert "Properties" not in result.discrepancy
    with work.db.transaction() as session:
        assert not operations.recent(session)


@pytest.mark.parametrize("word", ["footsteps", "voices", "breathing", "fabric"])
def test_k6_sound_disallows_person_noises(world, word):
    work, setup = world
    context = context_for(work, setup)
    place = context.state.places[0]
    result = EditPlace().run(
        context,
        EditPlace.Input(
            op="set",
            place_id=place["id"],
            expected_revision=place["revision"],
            sound=f"Soft {word} on cobblestones",
        ),
    )
    assert not result.ok and "ambient sound of the empty place" in result.discrepancy


def test_k6_creation_requires_all_survey_text_in_grammar_and_execution(kernel):
    context = kernel.harness.context(kernel.row, AgentSession(plan_mode=False), [])
    tool = EditPlace()
    shape = tool.narrow(context)
    assert {"description", "sound", "overhead"} <= set(shape["required"])
    assert "description" in shape["properties"]["angles"]["items"]["required"]
    result = tool.run(
        context, tool.Input(op="add_place", name="Courtyard", angles=[{"name": "gate"}])
    )
    assert not result.ok and all(
        name in result.discrepancy for name in ("sound", "overhead", "description")
    )
    with kernel.db.transaction() as session:
        assert direction.places(session) == []


def test_k7_executing_plan_can_be_revised_but_only_human_approval_grants_new_scope(kernel):
    row = run(
        kernel,
        action(
            "propose_plan",
            goal="Prepare",
            steps=[{"kind": "prepare", "title": "Prepare references", "jobs": 2}],
        ),
    )
    approved = kernel.harness.approve_plan(kernel.id, row["revision"])
    grant = deepcopy(approved["document"]["plan"]["grant"])
    kernel.harness.request_cancel(kernel.id)
    kernel.runner.run_once()
    kernel.model.replies = iter(
        [
            action(
                "propose_plan",
                goal="Repair and prepare",
                steps=[
                    {"kind": "edit", "title": "Set place sound text"},
                    {"kind": "prepare", "title": "Prepare references", "jobs": 2},
                ],
            )
        ]
    )
    kernel.harness.post_message(kernel.id, "You may edit the place text yourself", "new-scope")
    kernel.runner.run_once()
    row = kernel.harness.read(kernel.id)
    document = AgentSession.model_validate(row["document"])
    assert document.plan.state == "proposed" and document.plan.grant is None
    assert document.plan_history[-1].grant.model_dump() == grant
    assert document.plan.revision == 2
    context = kernel.harness.context(row, document, kernel.harness.turns(kernel.id))
    assert not available(context, "edit_place")
    approved = kernel.harness.approve_plan(kernel.id, row["revision"])
    assert approved["document"]["plan"]["grant"]["step_kinds"] == ["edit", "prepare"]


@pytest.mark.parametrize(
    "title",
    [
        "Set Arcade Courtyard sound text",
        "Add courtyard overhead",
        "Write the gallery description",
        "Describe the reverse angle",
        "Set angle text",
    ],
)
def test_k8_text_authoring_is_an_edit(title):
    assert edit_title(title)


@pytest.mark.parametrize(
    "title",
    ["Survey the reverse angle", "Insert Arin into the reviewed view", "Prepare a place view"],
)
def test_k8_production_is_not_text_authoring(title):
    assert not edit_title(title)


@pytest.mark.parametrize("identity", [False, True])
def test_k9_run4_ids_and_orientation_use_records_only_size_needs_repair(insertion_ready, identity):
    work, setup = insertion_ready
    context = prepare_grant(context_for(work, setup))
    with work.db.transaction() as session:
        segment = direction.read_segment(session, setup["segment_id"])
        subject = direction.read_subject(session, segment["content"]["subjects"][0])
        subject = direction.update_subject(
            session,
            subject["id"],
            subject["revision"],
            name="Arin",
            description="red plaid shirt, dark jeans and brown boots",
        )
    wording = {
        "who": subject["identity_asset_id"] if identity else subject["id"],
        "looks": "toward the camera, back partially turned",
        "pronouns": "he",
        "size": "full body visible",
        "distance_description": "mid-distance",
    }
    tool = PrepareCut()
    result = tool.run(context, tool.Input(cut_id=setup["cut_id"], insertion=wording))
    assert not result.ok and result.discrepancy.startswith("Invalid insertion fields: size:")
    assert ";" not in result.discrepancy
    with work.db.transaction() as session:
        cut = direction.read_cut(session, setup["cut_id"])
        resolved = resolve_insertion(
            session,
            context,
            cut,
            {**wording, "size": "one fifth", "looks": subject["identity_asset_id"]},
        )
        assert resolved["looks"] == subject["description"]
    wording["size"] = "one fifth"
    result = tool.run(context, tool.Input(cut_id=setup["cut_id"], insertion=wording))
    assert result.ok, result.discrepancy
    with work.db.transaction() as session:
        recorded = direction.read_cut(session, setup["cut_id"])["insertion"]
    assert recorded["who"] == "Arin" and recorded["looks"] == subject["description"]
    assert recorded["facing"] == "screen_left" and recorded["plate"] == ANGLE
    for field in ("keep", "where", "along", "barrier", "feet_placement", "occlude"):
        assert "railing" in recorded[field] or "cobblestone" in recorded[field]


def test_k9_refusal_collects_invalid_fields_and_explains_unknown_ids(insertion_ready):
    work, setup = insertion_ready
    context = context_for(work, setup)
    with work.db.transaction() as session:
        cut = direction.read_cut(session, setup["cut_id"])
        with pytest.raises(ApplicationError) as error:
            resolve_insertion(
                session,
                context,
                cut,
                {
                    "who": "00000000-0000-0000-0000-000000000000",
                    "pronouns": "he",
                    "size": "full body",
                    "keep": "plaid shirt",
                },
            )
    assert all(field in str(error.value) for field in ("who:", "size:", "keep:"))
    assert "person's name" in str(error.value) and "Arin" in str(error.value)


def test_k10_moving_phases_require_direction_and_refuse_without_writes(kernel):
    context, beat, _, _ = story(kernel)
    context.latest_user_message = "Draft Arin walking across the courtyard."
    beat["cuts"] = []
    beat.pop("from_angle")
    blank = deepcopy(beat)
    blank["phases"][0]["screen_direction"] = ""
    with pytest.raises(ApplicationError, match="screen_direction must not be empty"):
        parse_action(
            action(
                "draft_storyline", expected_revision=context.state.movie["revision"], beats=[blank]
            ),
            [(DraftStoryline(), DraftStoryline().narrow(context))],
        )
    beat["phases"][0].pop("screen_direction")
    result = draft(context, [beat])
    assert not result.ok and "state the walker's screen direction" in result.discrepancy
    beat["phases"][0]["screen_direction"] = "left to right"
    result = draft(context, [beat])
    assert result.ok
    context = kernel.harness.context(kernel.row, context.document, [])
    phase = result.payload["segments"][0]["phase_ids"][0]
    tool = EditPhase()
    result = tool.run(
        context, tool.Input(op="set", phase_id=phase, expected_revision=1, screen_direction="")
    )
    assert not result.ok and "state the walker's screen direction" in result.discrepancy


def test_k10_continuing_cut_uses_predecessor_when_no_phase_precedes_it(monkeypatch):
    from gaiavfx.application.harness.tools import insertion

    segment = {
        "movie_id": "movie",
        "ordinal": 1,
        "continues": True,
        "phases": [{"start_frame": 36, "content": {}}],
    }
    previous = {"phases": [{"start_frame": 0, "content": {"screen_direction": "left to right"}}]}
    monkeypatch.setattr(insertion.direction, "read_segment", lambda *_: segment)
    monkeypatch.setattr(
        insertion.direction, "read_movie", lambda *_: {"segments": [previous, segment]}
    )
    monkeypatch.setattr(insertion.direction, "read_angle", lambda *_: {})
    assert (
        insertion_facts(
            None,
            {
                "segment_id": "s",
                "frame": 36,
                "relation": "reverse",
                "crosses_barrier": True,
                "to_angle_id": "a",
            },
        )[2]
        == "screen_left"
    )


def test_k10_unknown_facing_is_required_in_prepare_grammar(insertion_ready):
    work, setup = insertion_ready
    with work.db.transaction() as session:
        segment = direction.read_segment(session, setup["segment_id"])
        phase = segment["phases"][0]
        direction.update_phase(
            session,
            phase["id"],
            phase["revision"],
            content={**phase["content"], "screen_direction": ""},
        )
    context = prepare_grant(context_for(work, setup))
    shape = PrepareCut().narrow(context)
    insertion = shape["properties"]["insertion"]
    assert "insertion" in shape["required"] and "facing" in insertion["required"]
    assert insertion["properties"]["facing"]["enum"] == ["screen_left", "screen_right"]


def test_k10_phase_requirements_follow_the_target_and_prepare_edit_budget(world):
    work, setup = world
    with work.db.transaction() as session:
        segment = direction.read_segment(session, setup["segment_id"])
        moving = segment["phases"][0]
        moving = direction.update_phase(
            session,
            moving["id"],
            moving["revision"],
            content={**moving["content"], "description": "Arin walks", "screen_direction": ""},
        )
        segment = direction.read_segment(session, segment["id"])
        still = direction.add_phase(
            session,
            segment["id"],
            segment["revision"],
            start_frame=12,
            content={"description": "Arin stands still", "screen_direction": "", "sound": "Wind"},
        )
    context = plan(context_for(work, setup), "prepare", setup["cut_id"], edit=True)
    tool = EditPhase()
    for target in (moving, still):
        raw = action(
            "edit_phase",
            op="set",
            phase_id=target["id"],
            expected_revision=target["revision"],
            sound="Rain",
        )
        if target is moving:
            with pytest.raises(ApplicationError, match="screen_direction is required"):
                parse_action(raw, [(tool, tool.narrow(context))])
            raw["input"]["screen_direction"] = ""
            with pytest.raises(ApplicationError, match="must not be empty"):
                parse_action(raw, [(tool, tool.narrow(context))])
        else:
            parse_action(raw, [(tool, tool.narrow(context))])
    schema = action_schema(applicable_tools(context))
    assert estimate(json.dumps(schema, separators=(",", ":"))) <= SCHEMA_CEILING


@pytest.mark.parametrize("video", [False, True])
def test_k10_plate_removal_requires_its_prose_and_video_frame(video):
    from gaiavfx.application.harness.actions import inlined
    from gaiavfx.application.harness.tools.preparation_schema import plate_shape

    model = inlined(PrepareCut.Input.model_json_schema())
    shape = plate_shape(
        model["properties"]["plate"],
        [{"id": "source", "kind": "video" if video else "image"}],
        "angle",
    )
    branch = next(b for b in shape["oneOf"] if "whom" in b["properties"])
    assert "empty" not in branch["properties"]
    assert {"whom", "object_pronoun", "behind"} <= set(branch["required"])
    assert ("frame" in branch["required"]) is video
    if not video:
        empty = next(b for b in shape["oneOf"] if "empty" in b["properties"])
        assert empty["properties"]["empty"]["const"] is True
        assert "empty" in empty["required"]
        assert not {"frame", "whom", "object_pronoun", "behind"} & empty["properties"].keys()


@pytest.mark.parametrize("stale", [False, True])
def test_k11_wrong_segment_never_renders_even_when_both_are_ready(world, stale):
    work, setup = world
    context = context_for(work, setup)
    first = context.state.segments[0]
    second = {
        **deepcopy(first),
        "id": "second-segment",
        "ordinal": 1,
        "predecessor": {
            "id": first["id"],
            "segment_number": 1,
            "selection": {
                "asset_id": setup["take"],
                "stale": stale,
                "stale_reasons": ["phases changed"] if stale else [],
            },
        },
    }
    context.state.segments.append(second)
    for segment in (first, second):
        context.state.segment_plans[segment["id"]] = {"ready": True, "refusals": []}
    plan(context, "render", second["id"])
    offered = applicable_tools(context)
    tool = RenderCandidates()
    assert tool.narrow(context)["properties"]["segment_id"]["enum"] == [second["id"]]
    for direct in (False, True):
        if direct:
            result = tool.run(context, tool.Input(segment_id=first["id"]))
            message = result.discrepancy
            assert not result.ok
        else:
            with pytest.raises(ApplicationError) as error:
                parse_action(action("render_candidates", segment_id=first["id"]), offered, context)
            message = str(error.value)
        assert (
            "step_target_mismatch" in message
            and "segment 2 (second-segment)" in message
            and first["id"] in message
        )
    if stale:
        result = tool.run(context, tool.Input(segment_id=second["id"]))
        assert (
            not result.ok
            and "stale (phases changed)" in result.discrepancy
            and setup["take"] in result.discrepancy
        )
    assert context.document.plan.steps[0].status == "pending"
    with work.db.transaction() as session:
        assert not operations.recent(session)


def test_k11_prepare_call_must_match_step_target(world):
    work, setup = world
    context = plan(context_for(work, setup), "prepare", setup["angles"][1])
    result = PrepareCut().run(context, PrepareCut.Input(cut_id=setup["cut_id"]))
    assert not result.ok and "step_target_mismatch" in result.discrepancy


def test_k11_real_stale_selection_keeps_requested_target_and_explains_it(tmp_path):
    from harness_eval_tasks import close, dz_library

    from gaiavfx.application.candidate_review import open_review, select_take
    from gaiavfx.application.harness.view import render_view
    from gaiavfx.modules.generation.operations.lifecycle import JobSpec

    work, setup = dz_library(tmp_path, second_segment=True, autonomy="render")
    try:
        with work.db.transaction() as session:
            first = direction.read_segment(session, setup["segment_id"])
            subject_ids = first["content"]["subjects"]
            operation = operations.create(
                session,
                kind="render",
                request={
                    "segment_revision": first["revision"],
                    "phase_revisions": {p["id"]: p["revision"] for p in first["phases"]},
                    "subject_revisions": {
                        sid: direction.read_subject(session, sid)["revision"] for sid in subject_ids
                    },
                },
                jobs=[JobSpec("video", "local", "fake", step="fixture")],
                budget_limit=None,
            )
            candidate = operations.add_candidate(
                session, operation["jobs"][0]["id"], setup["take"], "fixture", "video"
            )
            review = open_review(
                session,
                purpose="segment_takes",
                operation_ids=[operation["id"]],
                context={"movie_id": setup["movie_id"], "segment_id": first["id"]},
            )
            select_take(
                session,
                review_set_id=review["id"],
                candidate_id=candidate["id"],
                authority="user",
                expected_revision=0,
            )
            phase = first["phases"][0]
            direction.update_phase(
                session,
                phase["id"],
                phase["revision"],
                content={**phase["content"], "description": "Arin stops"},
            )
        context = plan(context_for(work, setup), "render", setup["second_segment_id"])
        tool = RenderCandidates()
        assert tool.narrow(context)["properties"]["segment_id"]["enum"] == [
            setup["second_segment_id"]
        ]
        view = render_view(context.state, "render", 0, 6, document=context.document)
        assert "Phases changed since this take" in view and setup["take"] in view
        for target in (setup["segment_id"], setup["second_segment_id"]):
            result = tool.run(context, tool.Input(segment_id=target))
            assert not result.ok and not result.receipts
            assert "segment 2" in result.discrepancy
            assert (
                "step_target_mismatch" if target == setup["segment_id"] else "stale"
            ) in result.discrepancy
        with work.db.transaction() as session:
            assert [o["id"] for o in operations.recent(session)] == [operation["id"]]
    finally:
        close(work)
