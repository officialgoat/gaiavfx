"""S7o regressions from e2e-library-6 turns 10, 20, 26 and the pin loop."""

from copy import deepcopy

import harness_support
import pytest
from harness_support import action
from test_harness_drafting import draft_context
from test_harness_e2e_context import pack
from test_harness_follow_through import post

from gaiavfx.application.harness.actions import parse_action
from gaiavfx.application.harness.context import turn_messages
from gaiavfx.application.harness.tools.draft import DraftStoryline
from gaiavfx.application.harness.tools.edits import EditCut, EditPhase
from gaiavfx.application.harness.tools.insertion import resolve_insertion
from gaiavfx.application.harness.tools.places import EditPlace
from gaiavfx.application.harness.world import direction
from gaiavfx.shared.errors import ApplicationError

kernel = harness_support.kernel


def courtyard(kernel):
    ctx, subject = draft_context(kernel)
    with kernel.db.transaction() as session:
        place = direction.create_place(session, "Arcade courtyard")
        angles = [direction.add_angle(session, place["id"], n) for n in ("Side-on", "Reverse")]
        direction.update_angle(
            session,
            angles[1]["id"],
            1,
            description="Wide view of open paving beyond the railing, olive trees and columns.",
        )
    ctx = kernel.harness.context(kernel.row, ctx.document, [])
    beat = {
        "title": "Walk",
        "place": {"noun": "courtyard", "description": "Open paving"},
        "subject_ids": [subject["id"]],
        "continues": False,
        "delivered_frames": 102,
        "phases": [
            {
                "start_frame": 0,
                "description": "Arin walks along the railing",
                "screen_direction": "left to right",
            }
        ],
        "cuts": [],
    }
    reverse = deepcopy(beat)
    reverse.update(continues=True, title="Reverse")
    reverse["cuts"] = [
        {
            "frame": 36,
            "from_angle": angles[0]["id"],
            "to_angle": angles[1]["id"],
            "relation": "reverse",
            "crosses_barrier": True,
            "shot": "Arin walks beyond the railing",
            "screen_direction": "right to left",
        }
    ]
    return ctx, [beat, reverse]


def drafted(kernel):
    ctx, beats = courtyard(kernel)
    tool = DraftStoryline()
    result = tool.run(ctx, tool.Input(expected_revision=2, beats=beats))
    assert result.ok, result.discrepancy
    with kernel.db.transaction() as session:
        segment = direction.read_movie(session, kernel.movie["id"])["segments"][1]
    return kernel.harness.context(kernel.row, ctx.document, []), segment


def test_o1_empty_angles_give_an_executable_example(kernel):
    ctx, _ = draft_context(kernel)
    tool = EditPlace()
    with pytest.raises(ApplicationError) as error:
        parse_action(
            action(
                "edit_place",
                op="add_place",
                name="Arcade courtyard",
                description="Empty courtyard",
                sound="Wind",
                overhead="Looking straight down",
                angles=[],
            ),
            [(tool, tool.narrow(ctx))],
        )
    assert "add_place needs at least one angle" in str(error.value)
    assert "name" in str(error.value) and "description" in str(error.value)


@pytest.mark.parametrize(
    "tool_name,values,field",
    [
        ("draft_storyline", {"expected_revision": 1, "beats": []}, "beats"),
        ("edit_segment", {"op": "add", "expected_revision": 1, "phases": []}, "phases"),
        ("propose_plan", {"goal": "Film", "steps": []}, "steps"),
        ("render_candidates", {"segment_id": "segment", "seeds": []}, "seeds"),
    ],
)
def test_o1_other_authored_list_minimums_name_count_and_content(tool_name, values, field):
    from gaiavfx.application.harness.tools import DEFAULT_TOOLS

    tool = next(t for t in DEFAULT_TOOLS if t.name == tool_name)
    with pytest.raises(ApplicationError) as error:
        parse_action(action(tool_name, **values), [(tool, tool.Input.model_json_schema())])
    assert f"{field} needs at least 1 items:" in str(error.value)


def test_o1_reading_list_minimum_has_sample_frame_guidance():
    from gaiavfx.application.harness.reading import validate_notes

    with pytest.raises(ApplicationError) as error:
        validate_notes(
            {"shots": [], "continuity": "", "lighting": "", "suggested_techniques": []},
            "video",
            6,
        )
    assert "shots needs at least 1 items" in str(error.value)
    assert "from_frame, to_frame sample indices" in str(error.value)


@pytest.mark.parametrize("frame", [96, 12, 21])
def test_o2_draft_cut_timing_refuses_atomically(kernel, frame):
    ctx, beats = courtyard(kernel)
    beats[1]["cuts"][0]["frame"] = frame
    tool = DraftStoryline()
    result = tool.run(ctx, tool.Input(expected_revision=2, beats=beats))
    assert not result.ok and "belongs at frame 36" in result.discrepancy
    with kernel.db.transaction() as session:
        assert not direction.read_movie(session, kernel.movie["id"])["segments"]


@pytest.mark.parametrize(
    "shot",
    [
        "Arin's back to camera, courtyard stretching before him",
        "Arin walks toward the camera beyond the railing",
    ],
)
def test_o3_after_cut_shot_keeps_lateral_walk(kernel, shot):
    ctx, beats = courtyard(kernel)
    beats[1]["cuts"][0]["shot"] = shot
    tool = DraftStoryline()
    result = tool.run(ctx, tool.Input(expected_revision=2, beats=beats))
    assert not result.ok and "lateral" in result.discrepancy
    ctx.latest_user_message = shot
    assert tool.run(ctx, tool.Input(expected_revision=2, beats=beats)).ok


def test_o4_move_remove_are_available_and_duplicate_create_names_them(kernel):
    ctx, segment = drafted(kernel)
    tool = EditCut()
    schema = tool.narrow(ctx)
    ops = {b["properties"]["op"]["const"] for b in schema["oneOf"]}
    assert {"move", "remove"} <= ops
    cut = segment["cuts"][0]
    result = tool.run(
        ctx,
        tool.Input(
            op="create",
            expected_revision=segment["revision"],
            segment_id=segment["id"],
            frame=36,
            from_angle_id=cut["from_angle_id"],
            to_angle_id=cut["to_angle_id"],
            phase={"description": "Arin beyond the railing"},
        ),
    )
    assert not result.ok and "op=move" in result.discrepancy and "op=remove" in result.discrepancy


def test_o4_second_side_to_reverse_cut_is_refused(kernel):
    ctx, segment = drafted(kernel)
    cut = segment["cuts"][0]
    tool = EditCut()
    result = tool.run(
        ctx,
        tool.Input(
            op="create",
            expected_revision=segment["revision"],
            segment_id=segment["id"],
            frame=72,
            from_angle_id=cut["from_angle_id"],
            to_angle_id=cut["to_angle_id"],
            phase={"description": "Arin beyond the railing"},
        ),
    )
    assert not result.ok and "chain" in result.discrepancy.lower()


def test_o4_agent_moves_then_removes_cut_with_independent_undo_receipts(kernel):
    ctx, segment = drafted(kernel)
    cut = segment["cuts"][0]
    tool = EditCut()
    _, value = parse_action(
        action(
            "edit_cut", op="move", cut_id=cut["id"], expected_revision=cut["revision"], frame=60
        ),
        [(tool, tool.narrow(ctx))],
    )
    moved = tool.run(ctx, value)
    assert moved.ok and moved.receipts[0]["command"] == "move_cut"
    with kernel.db.transaction() as session:
        segment = direction.read_segment(session, segment["id"])
        assert [p["start_frame"] for p in segment["phases"]] == [0, 60]
    ctx = kernel.harness.context(kernel.row, ctx.document, [])
    _, value = parse_action(
        action("edit_cut", op="remove", cut_id=cut["id"], expected_revision=segment["revision"]),
        [(tool, tool.narrow(ctx))],
    )
    removed = tool.run(ctx, value)
    assert removed.ok and removed.payload["removed"]
    with kernel.db.transaction() as session:
        assert not direction.cuts(session, segment["id"])
        direction.undo(session, removed.receipts[0]["id"])
        direction.undo(session, moved.receipts[0]["id"])
        assert direction.read_cut(session, cut["id"])["frame"] == 36
        assert [p["start_frame"] for p in direction.phases(session, segment["id"])] == [0, 36]


def test_o5_precut_phase_continues_predecessor(kernel):
    ctx, segment = drafted(kernel)
    phase = segment["phases"][0]
    tool = EditPhase()
    result = tool.run(
        ctx,
        tool.Input(
            op="set",
            phase_id=phase["id"],
            expected_revision=phase["revision"],
            description="Arin walks behind the railing",
            screen_direction="right to left",
        ),
    )
    assert not result.ok and "continue segment 1: left to right" in result.discrepancy


@pytest.mark.parametrize("same_action", [False, True])
def test_o7_different_refusals_are_progress(kernel, monkeypatch, same_action):
    from gaiavfx.application.harness.tools import plan

    # The four distinct run-6 corrections, followed by the corrected proposal.
    refusals = iter(
        [
            "prepare_jobs: pin needs 2 jobs",
            "prepare_jobs: survey place needs 0 jobs",
            "render_prerequisites: plate and sheet missing",
            "render_prerequisites: plate missing",
            "",
        ]
    )
    monkeypatch.setattr(plan, "check_plan_costs", lambda *args: next(refusals))
    sequence = [
        action(
            "propose_plan",
            goal="Film",
            steps=[{"kind": "prepare", "title": f"Prepare {0 if same_action else n}"}],
        )
        for n in range(5)
    ]
    turns = post(kernel, *sequence)
    assert [t["state"] for t in turns] == ["rejected"] * 4 + ["accepted"]


def test_o8_proposal_reports_all_costs_and_prerequisites(kernel):
    from gaiavfx.application.harness.tools.plan import ProposePlan

    ctx, segment = drafted(kernel)
    ctx.document.plan = None
    cut_id = segment["cuts"][0]["id"]
    tool = ProposePlan()
    result = tool.run(
        ctx,
        tool.Input(
            goal="Prepare and render",
            steps=[
                {"kind": "prepare", "title": "Insert pin", "target": cut_id, "jobs": 0},
                {"kind": "prepare", "title": "Survey reverse", "target": cut_id, "jobs": 4},
                {"kind": "render", "title": "Render", "target": segment["id"], "jobs": 2},
                {"kind": "review", "title": "Review takes"},
            ],
        ),
    )
    assert not result.ok and ctx.document.plan is None
    for problem in ("pin 2", "survey 1", "render_prerequisites", "plate", "sheet"):
        assert problem in result.discrepancy


def test_o8_all_plan_problems_survive_the_old_discrepancy_limit(kernel):
    from gaiavfx.application.harness.tools.plan import ProposePlan

    ctx, segment = drafted(kernel)
    ctx.document.plan = None
    cut_id = segment["cuts"][0]["id"]
    steps = [
        {"kind": "prepare", "title": "Survey " + str(n) + " x" * 80, "target": cut_id, "jobs": 4}
        for n in range(10)
    ]
    steps += [
        {"kind": "render", "title": "Render", "target": segment["id"], "jobs": 2},
        {"kind": "review", "title": "Review"},
    ]
    tool = ProposePlan()
    result = tool.run(ctx, tool.Input(goal="Prepare then render", steps=steps))
    assert not result.ok and len(result.discrepancy) > 2000
    assert all(f"Survey {n} " in result.discrepancy for n in range(10))
    assert "render_prerequisites" in result.discrepancy and "sheet" in result.discrepancy


@pytest.mark.parametrize(
    "field,value",
    [
        ("size", "about two thirds of the image height"),
        ("where", "Arin walks right to left beyond the railing, mid-stride, bag swaying"),
        ("distance_description", "a short step from it"),
        ("distance_description", "close to the railing"),
    ],
)
def test_o9_o10_pin_wording_refuses_run6_failures(kernel, field, value):
    ctx, segment = drafted(kernel)
    wording = {
        "who": "Arin",
        "looks": "dark coat",
        "pronouns": "he",
        "facing": "screen_left",
        "plate": "Empty paving beyond the railing",
        "keep": "railing and paving",
        "where": "on the open paving beyond the railing",
        "size": "one fifth",
        "distance_description": "distant, small",
        "along": "along the paving",
        "barrier": "the railing",
        "feet_placement": "feet on paving",
        "occlude": "railing hides feet",
    }
    wording[field] = value
    with kernel.db.transaction() as session, pytest.raises(ApplicationError) as error:
        resolve_insertion(session, ctx, segment["cuts"][0], wording)
    assert field in str(error.value)
    if field == "distance_description":
        assert "8" in str(error.value)


def test_o11_sixty_turns_in_one_message_keep_request_recent_turns_and_plan():
    turns = [{"role": "human", "state": "accepted", "action": {"message": "Fix the reverse pin."}}]
    for n in range(60):
        turns.append(
            {
                "role": "agent",
                "state": "rejected",
                "action": action("prepare_cut", attempt=n),
                "result": {"discrepancy": f"Problem {n}: " + "pin wording " * 180},
            }
        )
    history = turn_messages(turns)
    result = pack(turns)
    assert result.report["total_reserved"] <= 32768
    assert history[0] in result.messages
    assert result.messages[-13:-1] == history[-12:]
    assert any("Earlier turns of this message" in m["content"] for m in result.messages)
    assert "Active plan" in result.messages[-1]["content"]


def test_o11_runtime_tokenizer_windows_current_message_without_inference_retry():
    from types import SimpleNamespace

    from gaiavfx.application.harness.context import call_with_window

    turns = [{"role": "human", "state": "accepted", "action": {"message": "Fix the pin."}}]
    for n in range(60):
        turns.append(
            {
                "role": "agent",
                "state": "accepted",
                "action": action("read_movie", n=n),
                "result": {"ok": True, "payload": "read"},
            }
        )
    assembly = pack(turns)
    reports, sent = [], []

    def call(settings, messages, schema, cancelled, log_path):
        if any('"n": 20}' in m["content"] for m in messages):
            raise ApplicationError("Request budget 33649 exceeds context 32768")
        sent.append(deepcopy(messages))
        return "reply"

    result = call_with_window(
        SimpleNamespace(call=call),
        {},
        assembly,
        {},
        lambda: False,
        None,
        lambda r: reports.append(deepcopy(r)),
    )
    assert result == "reply" and len(sent) == 1
    assert reports[-1]["summarized_current_turns"] >= 21
    assert sent[0][-13:-1] == turn_messages(turns)[-12:]
    assert any(m["content"] == "Fix the pin." for m in sent[0])


def test_o9_o10_record_defaults_match_research_position(kernel):
    ctx, segment = drafted(kernel)
    cut = segment["cuts"][0]
    with kernel.db.transaction() as session:
        view = direction.record_evidence(session, cut["to_angle_id"], "imported", "reviewed-view")
        direction.approve_evidence(session, view["id"])
        result = resolve_insertion(session, ctx, cut, {"pronouns": "he"})
    assert result["where"] == "on the open paving beyond the railing"
    assert result["size"] == "one fifth"
    assert result["distance_description"] == "distant, small"
    assert result["facing"] == "screen_left"


@pytest.mark.parametrize(
    "size,accepted",
    [
        ("one fifth", True),
        ("one third", True),
        ("one half", True),
        ("50%", True),
        ("2/3", False),
        ("three quarters", False),
        ("0.75", False),
        ("60 percent", False),
        ("more than half", False),
        ("over one half of image height", False),
    ],
)
def test_o9_barrier_scale_boundary_and_nonbarrier_control(size, accepted):
    from gaiavfx.application.harness.tools.pin_wording import placement_problems

    wording = {"where": "on paving beyond the railing", "barrier": "the railing", "size": size}
    assert ("size" not in placement_problems(wording, {}, {})) is accepted
    assert "size" not in placement_problems({**wording, "barrier": ""}, {}, {})
