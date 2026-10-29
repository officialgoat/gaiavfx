"""S7j: regressions from run 3 (survey f749859b, insertion aee0d1b4).

The source DB was copied before inspection. These authored strings reproduce the
failures without depending on that workstation database or running inference.
"""

import json
from copy import deepcopy
from dataclasses import replace

import harness_support
import pytest
import test_harness_e2e_tools
from harness_eval_tasks import close, dz_library, run_idle
from pydantic import ValidationError
from test_harness_e2e_tools import call
from test_harness_tools import context_for

from gaiavfx.application.harness.actions import action_schema
from gaiavfx.application.harness.context import HEAD_CEILING, estimate
from gaiavfx.application.harness.history import latest_user_message, references_read
from gaiavfx.application.harness.knowledge import cinematography_head, load
from gaiavfx.application.harness.session import Plan, PlanGrant, PlanStep
from gaiavfx.application.harness.tools import applicable_tools
from gaiavfx.application.harness.tools.common import validate_text
from gaiavfx.application.harness.tools.insertion import (
    resolve_insertion,
    toward_camera_requested,
)
from gaiavfx.application.harness.tools.places import EditPlace
from gaiavfx.application.harness.tools.plan import ProposePlan, setup_only
from gaiavfx.application.harness.tools.prepare import PrepareCut
from gaiavfx.application.harness.view import render_view
from gaiavfx.application.harness.world import direction, operations
from gaiavfx.application.prepare_references import (
    approve_plate,
    open_plate_review,
    start_empty_plate,
)
from gaiavfx.modules.direction.insertion import continuity_facing
from gaiavfx.modules.direction.place_text import (
    EMPTY_PLACE_REFUSAL,
    PERSON_WORDS,
    validate_place_text,
)
from gaiavfx.shared.errors import ApplicationError

world = harness_support.world
unplated = test_harness_e2e_tools.unplated
ANGLE = (
    "From inside the gallery behind the black iron railing, looking out across the railing "
    "into the sunlit cobblestone courtyard with potted olive trees and the far arcade"
)
RUN3_ANGLES = (
    (
        "Side-on view along the black iron railing, looking down the length of the courtyard. "
        "Arin walks left to right along the railing. Stone colonnade arches recede into the background."
    ),
    (
        "Reverse view from behind Arin at the railing, looking back into the courtyard. "
        "The arched colonnade and potted olive trees are visible behind him."
    ),
)


@pytest.mark.parametrize("word", PERSON_WORDS)
def test_t1_closed_whole_word_rule(word):
    with pytest.raises(ValueError, match="Describe only the empty place"):
        validate_place_text(f"The {word.upper()} beside the railing", ["Arin"])
    assert validate_place_text(f"{word}stone", ["Arin"])


def test_t1_cast_names_case_punctuation_and_clean_scene():
    assert set(PERSON_WORDS) == {
        "man",
        "woman",
        "person",
        "people",
        "he",
        "she",
        "him",
        "her",
        "walks",
        "walking",
        "stands",
        "standing",
        "his",
        "figure",
        "subject",
    }
    for text in (*RUN3_ANGLES, "ARIN's gallery", "By Dr. Vale"):
        with pytest.raises(ValueError, match="Describe only"):
            validate_place_text(text, ["Arin", "Dr. Vale"])
    assert validate_place_text("Marina with shelving and standingstone sculpture", ["Arin"])
    assert validate_place_text(ANGLE, ["Arin"]) == ANGLE


def test_t1_all_place_operations_refuse_atomically(world):
    work, setup = world
    context, tool = context_for(work, setup), EditPlace()
    place = context.state.places[0]
    angle = place["angles"][0]
    values = [
        {
            "op": "add_place",
            "name": "Gallery",
            "description": RUN3_ANGLES[0],
            "sound": "Wind",
            "overhead": "Straight down on paving",
            "angles": [{"name": "outside", "description": ANGLE}],
        },
        {
            "op": "add_place",
            "name": "Gallery",
            "description": "Empty courtyard",
            "sound": "Wind",
            "overhead": "Straight down on paving",
            "angles": [{"name": "inside", "description": RUN3_ANGLES[1]}],
        },
        {
            "op": "add_angle",
            "place_id": place["id"],
            "name": "inside",
            "description": RUN3_ANGLES[1],
        },
        *[
            dict(
                op="set",
                place_id=place["id"],
                expected_revision=place["revision"],
                **{field: RUN3_ANGLES[0]},
            )
            for field in ("description", "overhead", "sound")
        ],
        {
            "op": "set",
            "place_id": place["id"],
            "expected_revision": place["revision"],
            "description": ANGLE,
            "angles": [
                {
                    "angle_id": angle["id"],
                    "expected_revision": angle["revision"],
                    "description": RUN3_ANGLES[1],
                }
            ],
        },
    ]
    with work.db.transaction() as session:
        before = (direction.places(session), direction.angles(session, place["id"]))
    for value in values:
        refused = call(tool, context, **value)
        assert not refused.ok and refused.discrepancy == EMPTY_PLACE_REFUSAL
        with work.db.transaction() as session:
            assert (direction.places(session), direction.angles(session, place["id"])) == before
    assert call(
        tool, context, op="add_angle", place_id=place["id"], name="inside", description=ANGLE
    ).ok


def test_t1_legacy_survey_text_refused_without_rewriting(world):
    work, setup = world
    with work.db.transaction() as session:
        old = direction.read_angle(session, setup["angles"][2])
        angle = direction.update_angle(
            session, old["id"], old["revision"], description=RUN3_ANGLES[1]
        )
    context, tool = context_for(work, setup), PrepareCut()
    result = call(tool, context, cut_id=setup["cut_id"])
    assert not result.ok and EMPTY_PLACE_REFUSAL in result.discrepancy
    with work.db.transaction() as session:
        assert direction.read_angle(session, angle["id"]) == angle
        assert not operations.recent(session)


@pytest.fixture
def insertion_ready(tmp_path):
    work, setup = dz_library(tmp_path, insertion=False)
    with work.db.transaction() as session:
        angle = direction.read_angle(session, setup["angles"][2])
        direction.update_angle(session, angle["id"], angle["revision"], description=ANGLE)
        view = direction.record_evidence(session, angle["id"], "imported", setup["plate"])
        direction.approve_evidence(session, view["id"])
        segment = direction.read_segment(session, setup["segment_id"])
        phase = segment["phases"][0]
        direction.update_phase(
            session,
            phase["id"],
            phase["revision"],
            content={**phase["content"], "screen_direction": "left to right"},
        )
    try:
        yield work, setup
    finally:
        close(work)


def prepare_grant(context):
    context.document.plan_mode = True
    context.document.plan = Plan(
        id="prepare-only",
        revision=1,
        state="executing",
        goal="Prepare the reverse",
        proposed_turn=0,
        budget_jobs=2,
        grant=PlanGrant(
            budget_jobs=2, step_kinds=["prepare"], approved_turn=1, reserved_at_approval=0
        ),
        steps=[PlanStep(kind="prepare", title="Prepare reverse pin", jobs=2)],
    )
    context.jobs_limit = 2
    return context


def inline_wording(setup):
    return {
        k: v
        for k, v in setup["wording"].items()
        if k not in {"plate", "facing", "distance_to_barrier_m"}
    }


@pytest.mark.parametrize(
    "value, expected",
    [
        ({"keep": "tan shoulder bag"}, "keep names parts"),
        ({"keep": "the railing and plaid shirt"}, "keep names parts"),
        ({"size": "full body"}, "fraction of the image height"),
        ({"plate": "the reviewed target view"}, "empty approved target view"),
        ({"facing": "toward_camera"}, "explicit user request"),
    ],
)
def test_t2_run3_insertion_refusals(insertion_ready, value, expected):
    work, setup = insertion_ready
    context = prepare_grant(context_for(work, setup))
    wording = inline_wording(setup) | value
    # Run 3's bag was a keeps item distinct from the clothing string.
    with work.db.transaction() as session:
        segment = direction.read_segment(session, setup["segment_id"])
        subject = direction.read_subject(session, segment["content"]["subjects"][0])
        direction.update_subject(
            session, subject["id"], subject["revision"], keeps=["tan shoulder bag"]
        )
    if "facing" in value:
        with pytest.raises(ApplicationError, match="facing must be one of"):
            call(PrepareCut(), context, cut_id=setup["cut_id"], insertion=wording)
    result = PrepareCut().run(context, PrepareCut.Input(cut_id=setup["cut_id"], insertion=wording))
    assert not result.ok and expected in result.discrepancy
    with work.db.transaction() as session:
        assert direction.read_cut(session, setup["cut_id"])["insertion"] is None
        assert not operations.recent(session)


def test_t2_defaults_and_t3_prepare_grant_receipt_transaction(insertion_ready, monkeypatch):
    work, setup = insertion_ready
    context = prepare_grant(context_for(work, setup))
    assert "edit_cut" not in {tool.name for tool, _ in applicable_tools(context)}
    tool = PrepareCut()
    with pytest.raises(ApplicationError, match="insertion is required"):
        call(tool, context, cut_id=setup["cut_id"])
    refused = tool.run(context, tool.Input(cut_id=setup["cut_id"]))
    assert (
        not refused.ok
        and "prepare_cut" in refused.discrepancy
        and "edit_cut" not in refused.discrepancy
    )
    example = json.loads(refused.discrepancy.split("Example input: ")[1])
    assert example["insertion"]["plate"] == ANGLE
    assert example["insertion"]["keep"] == "railing, cobblestone, olive trees, arcade"
    assert example["insertion"]["facing"] == "screen_left"
    from gaiavfx.application.harness.tools import prepare

    original = prepare.start_insertion

    def fail(*args, **kwargs):
        raise ApplicationError("Simulated producer failure", 422)

    monkeypatch.setattr(prepare, "start_insertion", fail)
    result = call(tool, context, cut_id=setup["cut_id"], insertion=inline_wording(setup))
    assert not result.ok
    with work.db.transaction() as session:
        assert direction.read_cut(session, setup["cut_id"])["insertion"] is None
    monkeypatch.setattr(prepare, "start_insertion", original)
    result = call(tool, context, cut_id=setup["cut_id"], insertion=inline_wording(setup))
    assert result.ok, result
    assert [r["kind"] for r in result.receipts] == ["direction_receipt", "operation"]
    with work.db.transaction() as session:
        cut = direction.read_cut(session, setup["cut_id"])
        operation = operations.operation(session, result.waits_on)
        assert cut["insertion"]["plate"] == ANGLE
        assert cut["insertion"]["facing"] == "screen_left"
        assert operation["request"]["wording"] == cut["insertion"]
        assert operation["budget_reserved"] == 2


def test_t2_keep_suggestion_placements_and_explicit_front(insertion_ready):
    work, setup = insertion_ready
    context = context_for(work, setup)
    with work.db.transaction() as session:
        cut = direction.read_cut(session, setup["cut_id"])
        wording = inline_wording(setup)
        resolved = resolve_insertion(
            session, context, cut, {k: v for k, v in wording.items() if k not in {"keep", "along"}}
        )
        assert resolved["plate"] == ANGLE
        assert resolved["keep"] == "railing, cobblestone, olive trees, arcade"
        assert resolved["along"] == "along the cobblestone"
        context.latest_user_message = "Make Arin walk toward the camera."
        with pytest.raises(ApplicationError, match="behind:"):
            resolve_insertion(session, context, cut, wording | {"facing": "toward_camera"})
        resolved = resolve_insertion(
            session, context, cut, wording | {"facing": "toward_camera", "behind": "the far arcade"}
        )
        assert resolved["facing"] == "toward_camera"


def test_t2_public_direction_and_producer_reject_personal_keeps(insertion_ready):
    from gaiavfx.application.prepare_references import start_insertion

    work, setup = insertion_ready
    with work.db.transaction() as session:
        cut = direction.read_cut(session, setup["cut_id"])
        segment = direction.read_segment(session, cut["segment_id"])
        subject = direction.read_subject(session, segment["content"]["subjects"][0])
        direction.update_subject(
            session, subject["id"], subject["revision"], keeps=["tan shoulder bag"]
        )
        wording = setup["wording"] | {"keep": "the railing and tan shoulder bag"}
        with pytest.raises(ApplicationError, match="keep names parts"):
            direction.update_cut(session, cut["id"], cut["revision"], insertion=wording)
        view = next(e for e in direction.evidence(session, cut["to_angle_id"]) if e["approved"])
        with pytest.raises(ApplicationError, match="keep names parts"):
            start_insertion(
                session,
                cut_id=cut["id"],
                evidence_id=view["id"],
                subject_asset=setup["identity"],
                wording=wording,
                seeds=[1],
            )
        assert direction.read_cut(session, cut["id"])["revision"] == cut["revision"]
        assert not operations.recent(session)


@pytest.mark.parametrize(
    "intent, expected",
    [
        ("toward_camera", True),
        ("Arin faces the camera", True),
        ("No music, Arin walks toward the camera", True),
        ("Do not walk toward the camera", False),
        ("Reverse at the railing", False),
    ],
)
def test_t2_explicit_front_intent(intent, expected):
    assert toward_camera_requested(intent) is expected


def test_inline_insertion_schema_stays_within_ceiling(insertion_ready):
    from gaiavfx.application.harness.context import SCHEMA_CEILING

    work, setup = insertion_ready
    context = context_for(work, setup)
    for missing_plates in (0, 1, 2, 4):
        mixed = replace(context, state=deepcopy(context.state))
        for index in range(missing_plates):
            place = deepcopy(context.state.places[0])
            place.update(id=f"unplated-place-{index}", plate=None)
            source = f"source-angle-{index}"
            place["angles"][0]["id"] = source
            mixed.state.places.append(place)
            cut = context.state.cuts[0] | {
                "id": f"survey-cut-{index}",
                "place_id": place["id"],
                "from_angle_id": source,
            }
            mixed.state.cuts.append(cut)
            plan = deepcopy(context.state.plans[setup["cut_id"]])
            plan["recommended"]["actions"] = [{"action": "view_angle"}]
            mixed.state.plans[cut["id"]] = plan
        applicable = applicable_tools(mixed)
        size = estimate(json.dumps(action_schema(applicable), separators=(",", ":")))
        assert size <= SCHEMA_CEILING, (missing_plates, size)


@pytest.mark.parametrize(
    "direction_text,relation,crosses,expected",
    [
        ("left to right", "reverse", True, "screen_left"),
        ("right to left", "reverse", True, "screen_right"),
        ("left-to-right", "same_direction", False, "screen_right"),
        ("screen_left", "side", False, "screen_left"),
        ("steady pace", "reverse", True, None),
    ],
)
def test_t2_continuity(direction_text, relation, crosses, expected):
    assert continuity_facing(direction_text, relation, crosses) == expected


def test_t4_scripted_agent_uses_reading_notes_then_fits_reviews_and_approves(unplated):
    work, setup = unplated
    context = prepare_grant(context_for(work, setup))
    context.jobs_reserved = context.jobs_limit  # fitting consumes no inference budget
    context.attachments = [{"id": setup["plate"], "kind": "image", "name": "courtyard"}]
    turns = [
        {
            "state": "accepted",
            "action": {"tool": "describe_reference"},
            "result": {"focus": {"asset_id": setup["plate"]}},
        },
        {
            "state": "accepted",
            "action": {
                "tool": "reference_notes",
                "input": {"subject": "No people visible", "place": "the arcade courtyard"},
            },
        },
    ]
    context.reference_notes = references_read(turns)
    tool = PrepareCut()
    with pytest.raises(ApplicationError, match="plate is required"):
        call(tool, context, cut_id=setup["cut_id"])
    refusal = tool.run(context, tool.Input(cut_id=setup["cut_id"]))
    assert "no people" in refusal.discrepancy
    # Scripted recovery chooses the input the model is shown, with no invented ids.
    value = json.loads(refusal.discrepancy.split("Example input: ")[1])
    assert value["plate"]["empty"] is True and "whom" not in value["plate"]
    result = call(tool, context, **value)
    assert result.ok, result
    with work.db.transaction() as session:
        operation = operations.operation(session, result.waits_on)
        assert operation["budget_reserved"] == 0
        assert len(operation["jobs"]) == 1
        job = operation["jobs"][0]
        assert job["step"] == "fit_frame" and job["executor"] == "local"
        assert operation["request"]["source_asset"] == setup["plate"]
        retry = start_empty_plate(
            session,
            place_id=setup["place_id"],
            angle_id=setup["angles"][0],
            source_asset=setup["plate"],
            request_key=f"agent:{context.turn_id}",
        )
        assert retry["id"] == operation["id"] and not retry["_created"]
    run_idle(work, setup["fake"])
    assert not setup["fake"].posts
    with work.db.transaction() as session:
        operation = operations.operation(session, result.waits_on)
        assert operation["state"] == "succeeded"
        assert direction.read_place(session, setup["place_id"])["plate_asset_id"] is None
        candidate = operation["candidates"][0]
        info = work.library.asset_info(session, candidate["asset_id"])
        assert (info["info"]["width"], info["info"]["height"]) == (768, 448)
        review = open_plate_review(session, operation["id"])
        assert review["purpose"] == "plate"
        from gaiavfx.application.candidate_review import blind

        blind_review = blind(session, review["id"])
        assert (
            "request" not in blind_review["members"][0] and "seed" not in blind_review["members"][0]
        )
        approved = approve_plate(session, candidate_id=candidate["id"])
        assert approved["plate_asset_id"] == candidate["asset_id"]
        assert approved["plate_angle_id"] == setup["angles"][0]
    context = context_for(work, setup)
    assert context.state.places[0]["plate"]["source"]["empty"] is True


@pytest.mark.parametrize("extra", [{"whom": "Arin"}, {"frame": 0}, {"behind": "wall"}])
def test_t4_empty_plate_refuses_mixed_inputs(unplated, extra):
    work, setup = unplated
    tool, context = PrepareCut(), context_for(work, setup)
    value = {
        "cut_id": setup["cut_id"],
        "plate": {"source_asset": setup["plate"], "empty": True, **extra},
    }
    with pytest.raises(ApplicationError):
        call(tool, context, **value)
    # The structural union now rejects mixed variants before tool execution too.
    with pytest.raises(ValidationError):
        tool.Input(**value)


def test_t5_whom_refusal_says_what_to_write():
    for record_id in (
        "518eeb2b-a35e-4e84-9123-86399e23f4f7",
        "5905c7ea-9551-4028-8acb-1bcf7697c302",
    ):
        with pytest.raises(ApplicationError) as exc:
            validate_text({"plate": {"whom": record_id}})
        assert (
            str(exc.value)
            == "Write who to remove in plain words, for example 'Arin' or 'the man in the plaid shirt'; use empty: true if nobody is in the image"
        )


def test_t6_no_preparation_for_zero_cut_ready_segment(world):
    work, setup = world
    with work.db.transaction() as session:
        movie = direction.read_movie(session, setup["movie_id"])
        existing = direction.read_segment(session, setup["segment_id"])
        segment = direction.add_segment(
            session,
            movie["id"],
            movie["revision"],
            content=existing["content"],
            delivered_frames=102,
            continues=False,
            phases=[(0, existing["phases"][0]["content"])],
        )
    context = context_for(work, setup)
    context.document.plan_mode = True
    context.view = render_view(context.state, "prepare", 0, 6, document=context.document)
    tool = ProposePlan()
    result = call(
        tool,
        context,
        goal="Prepare Segment 1",
        steps=[{"kind": "prepare", "title": "Prepare Segment 1", "target": segment["id"]}],
    )
    assert not result.ok and "zero cuts and nothing missing" in result.discrepancy


@pytest.mark.parametrize(
    "message, expected",
    [
        (
            "Let's make a short film in this arcade courtyard (second image). Arin is the man in the first image. Shot 1: Arin walks along the black iron railing in one continuous side-on tracking shot. Shot 2 continues the motion and reverses at the railing, so we see the courtyard behind him. Set up the cast, the place and these two segments.",
            True,
        ),
        ("Set up cast, place and two shots. Shot 2 continues and reverses at the railing.", True),
        ("Draft a three-part storyline", True),
        ("Create two segments", True),
        ("Change the cut to frame 36", True),
        ("Draft the shots and prepare references", False),
        ("Create the segments and render takes", False),
        ("Set up the shots before surveying the place", False),
        ("Create the shots; preparing comes next", False),
        ("Make it work", False),
        ("Discuss how to create a sculpture", False),
    ],
)
def test_t7_documented_keyword_rule(message, expected):
    assert setup_only(message) is expected


def test_t7_decision_point_quotes_latest_user_only(world):
    work, setup = world
    context = context_for(work, setup)
    context.document.plan_mode = True
    context.latest_user_message = latest_user_message(
        [
            {"role": "human", "action": {"message": "Render two takes"}},
            {
                "role": "human",
                "action": {"message": "Just set up the cast, place and two segments"},
            },
            {"role": "agent", "action": {"message": "Prepare and render"}},
        ]
    )
    tool = ProposePlan()
    for kind in ("prepare", "render"):
        result = call(
            tool,
            context,
            goal="Set up the film",
            steps=[{"kind": kind, "title": "Produce references"}],
        )
        assert not result.ok and f'"{context.latest_user_message}"' in result.discrepancy
    assert context.document.plan is None
    assert call(
        tool, context, goal="Set up the film", steps=[{"kind": "edit", "title": "Draft the shots"}]
    ).ok


def test_t8_reverse_is_in_continuing_segment_guidance_within_budget():
    pack = load()
    assert "preceding shot continuous" in " ".join(pack["cinematography"]["continuity"]["rules"])
    assert "first beat has no cuts" in pack["prompts"]["draft"]["description"]
    assert "first beat cuts=[]" in pack["prompts"]["examples"]["story_cut"]
    assert "continuing beat" in pack["prompts"]["examples"]["story_cut"]
    assert ANGLE in json.dumps(EditPlace.Input.model_json_schema())
    assert estimate(cinematography_head()) <= HEAD_CEILING


def test_insertion_example_placement_comes_from_the_target_view_only():
    from gaiavfx.application.harness.tools.input_hints import scene_placement

    courtyard = scene_placement(
        "From inside the gallery behind the black iron railing, looking out across the railing "
        "into the sunlit cobblestone courtyard",
        True,
    )
    assert courtyard["where"] == "on the cobblestone beyond the railing"
    assert courtyard["barrier"] == "the railing"
    beach = scene_placement("A wide empty beach at dusk, wet sand reflecting the sky", False)
    assert beach == {
        "where": "on the sand",
        "along": "along the sand",
        "feet_placement": "Both feet contact the sand.",
    }
    unknown = scene_placement("A dim, cluttered workshop", False)
    assert set(unknown) == {"where"} and "railing" not in unknown["where"]
