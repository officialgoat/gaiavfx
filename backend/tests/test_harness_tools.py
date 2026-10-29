"""World tools protect scope, revision, review, producer and budget boundaries."""

from dataclasses import replace

import harness_support
import pytest

from gaiavfx.application.harness.actions import parse_action
from gaiavfx.application.harness.loop import Harness
from gaiavfx.application.harness.session import AgentSession, Plan, PlanGrant, PlanStep
from gaiavfx.application.harness.shots import shots
from gaiavfx.application.harness.tools import applicable_tools
from gaiavfx.application.harness.tools.candidates import ReadCandidates, prescreen_summary
from gaiavfx.application.harness.tools.edits import EditCut, EditPlace
from gaiavfx.application.harness.tools.prepare import PrepareCut, needed_jobs
from gaiavfx.application.harness.tools.reads import InspectCut
from gaiavfx.application.prepare_references import approve_pin, approve_view, open_views_review
from gaiavfx.modules.agent.service import AgentStore
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.generation.operations.review import ReviewService
from gaiavfx.modules.generation.operations.service import OperationService

world = harness_support.world


def context_for(work, setup):
    harness = Harness(work.db, AgentStore(), None, work.settings)
    row = harness.open(setup["movie_id"])
    row = harness.set_plan_mode(row["id"], row["revision"], False)
    return harness.context(row, AgentSession.model_validate(row["document"]), [], "test-turn")


def test_message_attachments_identity_idempotency_and_text_only_history(world, monkeypatch):
    from harness_support import ScriptedClient, action

    from gaiavfx.application.harness.context import turn_messages
    from gaiavfx.application.harness.driver import AgentRunner
    from gaiavfx.shared.errors import ApplicationError

    work, setup = world
    model = ScriptedClient([action("say", text="An image and a continuous shot.")])
    harness = Harness(work.db, AgentStore(), model, work.settings, library=work.library)
    row = harness.open(setup["movie_id"])
    attached = [setup["identity"], setup["take"]]
    harness.post_message(row["id"], "Discuss these.", "attached", attached)
    harness.post_message(row["id"], "Discuss these.", "attached", attached)
    turns = harness.turns(row["id"])
    assert len(turns) == 1
    assert [a["kind"] for a in turns[0]["action"]["attachments"]] == ["image", "video"]
    rendered = turn_messages(turns)[0]["content"]
    assert f'[attached image {setup["identity"]} "Walker.jpg"]' in rendered
    assert f'[attached video {setup["take"]} "take.mp4"]' in rendered
    with pytest.raises(ApplicationError) as conflict:
        harness.post_message(row["id"], "Discuss these.", "attached", [setup["identity"]])
    assert conflict.value.status == 409
    with pytest.raises(ApplicationError, match="is not in the library") as unknown:
        harness.post_message(row["id"], "Unknown", "unknown", ["missing"])
    assert unknown.value.status == 422
    with pytest.raises(ApplicationError):
        harness.post_message(row["id"], "Too many", "many", attached * 4)
    original = harness.library.asset_info
    monkeypatch.setattr(
        harness.library,
        "asset_info",
        lambda session, asset_id: {**original(session, asset_id), "kind": "audio"},
    )
    with pytest.raises(ApplicationError, match="image or video") as audio:
        harness.post_message(row["id"], "Audio", "audio", [setup["take"]])
    assert audio.value.status == 422
    AgentRunner(work.db, harness).run_once()
    assert all(isinstance(m["content"], str) for m in model.calls[0][0])
    summary = harness.read(row["id"])["document"]["summary"]
    assert "Walker.jpg" in summary and "take.mp4" in summary


def execute_edit(work, setup, tool, **value):
    context = context_for(work, setup)
    schema = tool.narrow(context)
    _, parsed = parse_action({"tool": tool.name, "thought": "", "input": value}, [(tool, schema)])
    result = tool.run(context, parsed)
    if result.ok and value["op"] in {"add", "create", "move", "remove"}:
        assert result.changed and result.receipts
        assert all(
            any(r["id"] in entry.text for entry in context.board.decisions) for r in result.receipts
        )
    return result


def keys(value):
    if isinstance(value, dict):
        return set(value) | set().union(*(keys(v) for v in value.values()), set())
    if isinstance(value, list):
        return set().union(*(keys(v) for v in value), set())
    return set()


def test_structural_commands_receipts_revisions_and_schema_two_preserved(world):
    from gaiavfx.application.harness.tools.edits import EditCast, EditPhase, EditSegment
    from gaiavfx.application.harness.tools.reads import ReadSegment

    work, setup = world
    direction = DirectionService()

    def movie():
        with work.db.transaction() as session:
            return direction.read_movie(session, setup["movie_id"])

    def segment(sid):
        with work.db.transaction() as session:
            return direction.read_segment(session, sid)

    cast = EditCast()
    added = execute_edit(
        work,
        setup,
        cast,
        op="add",
        expected_revision=movie()["revision"],
        name="Bea",
        description="Another walker",
        keeps=["green coat"],
        identity_asset_id=setup["identity"],
    )
    assert added.ok
    subject_id = added.payload["id"]
    changed = execute_edit(
        work, setup, cast, op="set", subject_id=subject_id, expected_revision=1, keeps=["blue coat"]
    )
    assert changed.ok and changed.payload["revision"] == 2
    stale = execute_edit(
        work, setup, cast, op="set", subject_id=subject_id, expected_revision=1, name="Stale"
    )
    assert not stale.ok and "Subject revision is 2" in stale.discrepancy
    edit = EditSegment()
    added = execute_edit(
        work,
        setup,
        edit,
        op="add",
        expected_revision=movie()["revision"],
        subject_ids=[subject_id],
        place={"noun": "courtyard", "description": "An arcade", "landmarks": "railing"},
        style="Grainy",
        delivered_frames=102,
        continues=False,
        phases=[{"start_frame": 0, "description": "Walk.", "screen_direction": "left to right"}],
    )
    assert added.ok
    sid = added.payload["id"]
    # Landed schema-two fields are retained while editing only S7's prose keys.
    with work.db.transaction() as session:
        row = direction.read_segment(session, sid)
        extras = {"events": [{"at_frame": 12, "description": "Bell."}], "speech": [], "states": []}
        row = direction.update_segment(
            session, sid, row["revision"], content={**row["content"], **extras}
        )
        phase = row["phases"][0]
        phase = direction.update_phase(
            session, phase["id"], phase["revision"], content={**phase["content"], "cast": []}
        )
    changed = execute_edit(
        work,
        setup,
        edit,
        op="set",
        segment_id=sid,
        expected_revision=row["revision"],
        style="Handheld",
        continues=True,
        delivered_frames=108,
    )
    assert changed.ok
    assert {k: segment(sid)["content"][k] for k in extras} == extras
    phases = EditPhase()
    changed = execute_edit(
        work,
        setup,
        phases,
        op="set",
        phase_id=phase["id"],
        expected_revision=phase["revision"],
        description="Pause.",
        screen_direction="stationary",
    )
    assert changed.ok and segment(sid)["phases"][0]["content"]["cast"] == []
    added_phase = execute_edit(
        work,
        setup,
        phases,
        op="add",
        segment_id=sid,
        expected_revision=segment(sid)["revision"],
        start_frame=48,
        description="Look.",
        screen_direction="",
        sound="Wind.",
    )
    assert added_phase.ok
    pid = added_phase.payload["id"]
    stale = execute_edit(work, setup, phases, op="remove", phase_id=pid, expected_revision=1)
    assert not stale.ok and "Segment revision is" in stale.discrepancy
    assert execute_edit(
        work, setup, phases, op="remove", phase_id=pid, expected_revision=segment(sid)["revision"]
    ).ok
    moved = execute_edit(
        work,
        setup,
        edit,
        op="move",
        segment_id=sid,
        expected_revision=movie()["revision"],
        ordinal=0,
    )
    assert moved.ok and movie()["segments"][0]["id"] == sid
    stale = execute_edit(
        work, setup, edit, op="move", segment_id=sid, expected_revision=1, ordinal=1
    )
    assert not stale.ok and "Movie revision is" in stale.discrepancy
    blocked = execute_edit(
        work, setup, cast, op="remove", subject_id=subject_id, expected_revision=movie()["revision"]
    )
    assert not blocked.ok and "appears in segment" in blocked.discrepancy
    blocked = execute_edit(
        work,
        setup,
        edit,
        op="remove",
        segment_id=setup["segment_id"],
        expected_revision=movie()["revision"],
    )
    assert not blocked.ok and setup["cut_id"] in blocked.discrepancy
    read = ReadSegment().run(context_for(work, setup), ReadSegment.Input(segment_id=sid))
    assert read.ok and read.payload["assembled"] is True
    assert read.payload["content"]["style"] == "Handheld"
    assert read.payload["phases"][0]["content"]["description"] == "Pause."
    assert "selection_history" in read.payload and read.payload["renders"] == []
    assert execute_edit(
        work, setup, edit, op="remove", segment_id=sid, expected_revision=movie()["revision"]
    ).ok
    assert execute_edit(
        work, setup, cast, op="remove", subject_id=subject_id, expected_revision=movie()["revision"]
    ).ok
    empty = replace(
        context_for(work, setup), state=replace(context_for(work, setup).state, cast=[])
    )
    assert '"const": "add"' not in __import__("json").dumps(edit.narrow(empty))


def test_cut_add_atomic_two_receipts_and_existing_phase(world):
    from gaiavfx.application.harness.tools.edits import EditSegment
    from gaiavfx.application.harness.tools.reads import ReadSegment
    from gaiavfx.shared.errors import ApplicationError

    work, setup = world
    direction = DirectionService()
    with work.db.transaction() as session:
        movie = direction.read_movie(session, setup["movie_id"])
    result = execute_edit(
        work,
        setup,
        EditSegment(),
        op="add",
        expected_revision=movie["revision"],
        subject_ids=[movie["subjects"][0]["id"]],
        place={"noun": "courtyard", "description": "Arcade"},
        delivered_frames=102,
        continues=True,
        phases=[{"start_frame": 0, "description": "Walk.", "screen_direction": "left to right"}],
    )
    sid = result.payload["id"]
    tool = EditCut()
    values = {
        "op": "create",
        "segment_id": sid,
        "expected_revision": 1,
        "frame": 36,
        "from_angle_id": setup["angles"][0],
        "to_angle_id": setup["angles"][2],
        "relation": "reverse",
        "crosses_barrier": True,
        "phase": {"description": "Reverse."},
    }
    stale = execute_edit(work, setup, tool, **(values | {"expected_revision": 9}))
    assert not stale.ok and "Segment revision is 1" in stale.discrepancy
    with pytest.raises(ApplicationError, match="crosses_barrier: true when"):
        execute_edit(work, setup, tool, **(values | {"crosses_barrier": None}))
    with work.db.transaction() as session:
        unchanged = direction.read_segment(session, sid)
        assert unchanged["revision"] == 1 and len(unchanged["phases"]) == 1
    result = execute_edit(work, setup, tool, **values)
    assert result.ok and [r["command"] for r in result.receipts] == ["add_phase", "create_cut"]
    with work.db.transaction() as session:
        row = direction.read_segment(session, sid)
        assert row["revision"] == 3 and len(row["phases"]) == 2
        direction.undo(session, result.receipts[-1]["id"])
        row = direction.read_segment(session, sid)
        assert not row["cuts"] and row["phases"][1]["start_frame"] == 36
        new_revision = row["revision"]
    existing = execute_edit(work, setup, tool, **(values | {"expected_revision": new_revision}))
    assert existing.ok and [r["command"] for r in existing.receipts] == ["create_cut"]
    with work.db.transaction() as session:
        row = direction.read_segment(session, sid)
        phase = row["phases"][1]
        direction.update_phase(session, phase["id"], phase["revision"], start_frame=48)
    read = ReadSegment().run(context_for(work, setup), ReadSegment.Input(segment_id=sid))
    assert "Cut frames without phases" in read.payload["assembled"]["error"]


def test_render_gating_blind_reads_and_ranked_provisional_picks(tmp_path):
    from harness_eval_tasks import TASKS, close, run_idle

    from gaiavfx.application.candidate_review import select_take
    from gaiavfx.application.harness.tools.picks import PickProvisional
    from gaiavfx.application.harness.tools.render import RenderCandidates
    from gaiavfx.infrastructure.database import timestamp
    from gaiavfx.modules.editing.selections.service import SelectionService
    from gaiavfx.modules.generation.operations.prescreen import VERSION, evaluate

    work, setup = next(t for t in TASKS if t.id == "provisional-pick-unblocks").build(tmp_path)
    try:
        context = context_for(work, setup)
        render, pick, reader = RenderCandidates(), PickProvisional(), ReadCandidates()
        assert render.narrow(context)
        assert render.narrow(replace(context, autonomy="prepare")) is None
        assert render.narrow(replace(context, jobs_reserved=6)) is None
        poor = replace(context, jobs_reserved=5)
        schema = render.narrow(poor)
        assert schema["properties"]["count"]["maximum"] == 1
        assert "seeds" not in schema["properties"]
        with pytest.raises(Exception, match="seeds is not allowed"):
            parse_action(
                {
                    "tool": render.name,
                    "thought": "",
                    "input": {"cut_id": setup["cut_id"], "seeds": [1, 2]},
                },
                [(render, schema)],
            )
        assert not render.run(poor, render.Input(cut_id=setup["cut_id"], seeds=[1, 2])).ok
        duplicate = render.run(context, render.Input(cut_id=setup["cut_id"], seeds=[1, 1]))
        assert not duplicate.ok and "distinct" in duplicate.discrepancy
        missing = render.run(context, render.Input(cut_id=setup["cut_id"], seeds=[1]))
        assert not missing.ok and "pick_provisional" in missing.discrepancy
        blind = reader.run(context, reader.Input(operation_id=setup["fixture_render"]))
        assert blind.ok and blind.payload["selection_revision"] == 0
        assert not keys(blind.payload) & {"seed", "job_id", "graph", "request"}
        assert pick.narrow(context) and pick.narrow(replace(context, autonomy="prepare")) is None
        with work.db.transaction() as session:
            review = ReviewService().blind(session, setup["fixture_render"])
            ids = [m["candidate_id"] for m in review["members"]]
            OperationService().record_prescreen(
                session,
                ids[0],
                {
                    "version": VERSION,
                    "measured_at": timestamp(),
                    "checks": evaluate(
                        "video",
                        {"frames": 2, "fps": 24, "width": 32, "height": 32, "audio": True},
                        {
                            "frames_counted": 1,
                            "frames_declared": 1,
                            "duration": 1 / 24,
                            "width": 32,
                            "height": 32,
                            "has_audio": False,
                        },
                    ),
                },
            )
        measured = reader.run(context, reader.Input(operation_id=setup["fixture_render"]))
        assert measured.payload["members"][0]["prescreen"] == {
            "pass": ["decodes", "resolution"],
            "fail": ["frame_count", "duration", "audio_present"],
            "unsupported": 6,
        }
        assert prescreen_summary(None) is None
        assert not keys(measured.payload) & {
            "seed",
            "job_id",
            "operation_id",
            "integration",
            "technique",
            "graph",
            "request",
            "evidence",
            "measured_at",
        }
        for before, after in zip(
            blind.payload["members"], measured.payload["members"], strict=True
        ):
            assert set(after) == {"letter", "asset_id", "kind", "prescreen"}
            assert {k: v for k, v in before.items() if k != "prescreen"} == {
                k: v for k, v in after.items() if k != "prescreen"
            }
        assert measured.payload["ranking"] is None and "revealed" not in measured.payload
        with work.db.transaction() as session:
            ReviewService().save_ranking(
                session,
                review["id"],
                expected_revision=0,
                tiers=[[ids[1]], [ids[0]]],
                usable_tiers=1,
                notes={},
                tags={},
                general="B is usable",
            )
        rejected = pick.run(
            context, pick.Input(review_set_id=review["id"], letter="A", expected_revision=0)
        )
        assert not rejected.ok and "below the usable line" in rejected.discrepancy
        selected = pick.run(
            context, pick.Input(review_set_id=review["id"], letter="B", expected_revision=0)
        )
        assert selected.ok and selected.receipts[0]["basis"] == "user_ranking"
        revealed = reader.run(context, reader.Input(operation_id=review["id"]))
        assert {m["seed"] for m in revealed.payload["revealed"]} == {901, 902}
        assert {m["technique"] for m in revealed.payload["revealed"]} == {"text"}
        assert revealed.payload["selection"]["basis"] == "user_ranking"
        with work.db.transaction() as session:
            select_take(
                session,
                review_set_id=review["id"],
                candidate_id=ids[1],
                authority="user",
                expected_revision=1,
            )
        refused = pick.run(
            context, pick.Input(review_set_id=review["id"], letter="B", expected_revision=2)
        )
        assert not refused.ok and "cannot replace a user selection" in refused.discrepancy
        stale = pick.run(
            context, pick.Input(review_set_id=review["id"], letter="B", expected_revision=0)
        )
        assert not stale.ok and "Selection revision is 2" in stale.discrepancy
        context = context_for(work, setup)
        assert pick.narrow(context) is None
        rendered = render.run(context, render.Input(cut_id=setup["cut_id"], seeds=[1, 2]))
        assert rendered.ok and rendered.waits_on and rendered.receipts[0]["budget_reserved"] == 2
        assert render.narrow(context_for(work, setup)) is None
        run_idle(work, setup["fake"])
        context = context_for(work, setup)
        assert render.narrow(context) is None  # Unread by this session, even if opened elsewhere.
        assert pick.narrow(context) is None  # No continuing successor of the last segment.
        with work.db.transaction() as session:
            op = OperationService().operation(session, rendered.waits_on)
            assert op["request_key"] == "agent:test-turn"
            assert op["request"]["context_source"]["basis"] is None
            assert (
                SelectionService().current(session, setup["movie_id"], setup["segment_id"]) is None
            )
        read = reader.run(context, reader.Input(operation_id=rendered.waits_on))
        assert read.ok
        with work.db.transaction() as session:
            AgentStore().create_turn(
                session, context.session_id, 0, "agent", "accepted", receipts=read.receipts
            )
        harness = Harness(work.db, AgentStore(), None, work.settings)
        row = harness.read(context.session_id)
        fresh = harness.context(
            row, AgentSession.model_validate(row["document"]), harness.turns(row["id"])
        )
        assert render.narrow(fresh)
    finally:
        close(work)


def test_segment_render_alias_scope_and_unread_guard(tmp_path):
    from harness_eval_tasks import TASKS, close, run_idle

    from gaiavfx.application.harness.tools.render import RenderCandidates

    work, setup = next(t for t in TASKS if t.id == "segment-2-cuts").build(tmp_path)
    try:
        context = context_for(work, setup)
        tool = RenderCandidates()
        schema = tool.narrow(context)
        assert setup["segment_id"] in schema["properties"]["segment_id"]["enum"]
        segment = next(s for s in context.state.segments if s["id"] == setup["segment_id"])
        for value in (
            {"seeds": [1]},
            {"segment_id": segment["id"], "cut_id": setup["cut_id"], "seeds": [1]},
        ):
            with pytest.raises(ValueError, match="Exactly one"):
                tool.Input(**value)
        assert not tool.run(context, tool.Input(segment_id="outside-scope", seeds=[1])).ok
        assert not tool.run(
            replace(context, jobs_reserved=6), tool.Input(segment_id=segment["id"], seeds=[1])
        ).ok
        rendered = tool.run(context, tool.Input(cut_id=segment["cuts"][0]["id"], seeds=[1]))
        assert rendered.ok
        # A sibling cut cannot evade the window's in-flight or unread guards.
        for finished in (False, True):
            if finished:
                run_idle(work, setup["fake"])
            fresh = context_for(work, setup)
            assert tool.narrow(fresh) is None
            refused = tool.run(fresh, tool.Input(cut_id=segment["cuts"][1]["id"], seeds=[2]))
            assert not refused.ok and "in flight or unread" in refused.discrepancy
    finally:
        close(work)


def test_stale_sheet_reassembly_waits_for_reference_review_and_plan(tmp_path):
    from harness_eval_tasks import TASKS, close, run_idle

    from gaiavfx.application.sheet_recipes import sheet_reference

    work, setup = next(t for t in TASKS if t.id == "stale-sheet-reassembled").build(tmp_path)
    try:
        direction = DirectionService(sheet_reader=sheet_reference)
        prepare = PrepareCut()
        value = prepare.Input(cut_id=setup["cut_id"])
        context = context_for(work, setup)
        plan = context.state.plans[setup["cut_id"]]
        sheet = next(b for b in plan["bindings"] if b["kind"] == "sheet")
        assert sheet["stale"] and not plan["recommended"]["ready"]
        assert {a["action"] for a in plan["recommended"]["actions"]} == {"view_place"}
        assert needed_jobs(context.state, shots(context.state)[-1]) == 0
        with work.db.transaction() as session:
            before = OperationService().recent(session)
            # A stale view of another angle is only exposed through the sheet.
            angle = direction.read_angle(session, setup["angles"][1])
            view = next(e for e in direction.evidence(session, angle["id"]) if e["approved"])
            direction.update_angle(
                session, angle["id"], angle["revision"], description="New angle prose"
            )
        refused = prepare.run(context, value)  # Revalidate even with the old cached plan.
        assert not refused.ok and "reference_review" in refused.discrepancy
        assert "description changed" in refused.discrepancy
        with work.db.transaction() as session:
            assert OperationService().recent(session) == before
            assert direction.read_evidence(session, view["id"])["inputs"] == view["inputs"]
            direction.approve_evidence(session, view["id"])
            pin = next(k for k in direction.keyframes(session, setup["cut_id"]) if k["approved"])
            cut = direction.read_cut(session, setup["cut_id"])
            direction.update_cut(
                session,
                cut["id"],
                cut["revision"],
                insertion=cut["insertion"] | {"where": "By the entrance"},
            )
        context = context_for(work, setup)
        inspected = InspectCut().run(context, InspectCut.Input(cut_id=setup["cut_id"]))
        binding = next(b for b in inspected.payload["plan"]["bindings"] if b["kind"] == "pin")
        assert binding["stale"] and "the insertion wording changed" in binding["reasons"]
        assert prepare.narrow(context) is None
        assert "reference_review" in prepare.run(context, value).discrepancy
        with work.db.transaction() as session:
            assert OperationService().recent(session) == before
            direction.approve_keyframe(session, pin["id"])
            target = direction.read_angle(session, setup["angles"][2])
            direction.update_angle(
                session, target["id"], target["revision"], description="Reworded target angle"
            )
        context = context_for(work, setup)
        assert prepare.narrow(context) is None
        assert "reference_review" in prepare.run(context, value).discrepancy
        with work.db.transaction() as session:
            assert OperationService().recent(session) == before
            direction.approve_evidence(session, pin["evidence_id"])

        for kind, reference in (
            ("view", pin["evidence_id"]),
            ("pin", pin["id"]),
            ("sheet", sheet["id"]),
        ):
            with work.db.transaction() as session:
                direction.judge_suitability(
                    session,
                    setup["cut_id"],
                    reference_kind=kind,
                    reference_id=reference,
                    verdict="unsuitable",
                    note="The position does not suit this cut",
                )
            context = context_for(work, setup)
            assert prepare.narrow(context) is None
            refused = prepare.run(context, value)
            assert not refused.ok and "reference_review" in refused.discrepancy
            assert (
                "assembled sheet is stale" if kind == "sheet" else "does not suit this cut"
            ) in refused.discrepancy
            with work.db.transaction() as session:
                assert OperationService().recent(session) == before
                direction.judge_suitability(
                    session,
                    setup["cut_id"],
                    reference_kind=kind,
                    reference_id=reference,
                    verdict="suits",
                )

        # A technique that does not request a sheet must not trigger assembly.
        with work.db.transaction() as session:
            cut = direction.read_cut(session, setup["cut_id"])
            direction.update_cut(session, cut["id"], cut["revision"], override_technique="text")
        context = context_for(work, setup)
        assert prepare.narrow(context) is None
        assert "ready to render" in prepare.run(context, value).discrepancy
        with work.db.transaction() as session:
            direction.update_cut(session, cut["id"], cut["revision"] + 1, override_technique=None)

        context = context_for(work, setup)
        context.document.plan_mode = True
        context.document.plan = Plan(
            id="reassembly",
            revision=1,
            state="executing",
            goal="Reassemble approved views",
            proposed_turn=0,
            steps=[PlanStep(kind="prepare", title="Reassemble views")],
            budget_jobs=0,
            grant=PlanGrant(
                budget_jobs=0, step_kinds=["prepare"], approved_turn=1, reserved_at_approval=0
            ),
        )
        context.autonomy = "off"
        context.jobs_reserved = context.jobs_limit = 0
        assert prepare.narrow(context) is not None
        result = prepare.run(context, value)
        assert result.ok and result.payload["kind"] == "assemble"
        assert result.receipts[0]["budget_reserved"] == 0
        assert not context.state.plans[setup["cut_id"]]["recommended"]["ready"]
        run_idle(work, setup["fake"])
        plan = context_for(work, setup).state.plans[setup["cut_id"]]
        assert plan["recommended"]["ready"]
        assert not next(b for b in plan["bindings"] if b["kind"] == "sheet")["stale"]
        with work.db.transaction() as session:
            assert [o["kind"] for o in OperationService().recent(session)].count("assemble") == 2
            assert not any(o["kind"] == "survey" for o in OperationService().recent(session))
    finally:
        close(work)


def test_inspect_relation_scope_and_atomic_place_edits(world):
    work, setup = world
    context = context_for(work, setup)
    tool = InspectCut()
    result = tool.run(context, tool.Input(cut_id=setup["cut_id"]))
    assert result.ok and result.progress
    assert result.payload["legal_now"] == [t.name for t, _ in applicable_tools(context)]
    assert not keys(result.payload) & {"prompt", "graph", "job_id", "seed", "request"}
    assert result.payload["plan"]["recommended"]["technique_id"] == "views_and_pin"
    edit = EditCut()
    revision = context.state.cuts[0]["revision"]
    result = edit.run(
        context,
        edit.Input(
            op="relation",
            cut_id=setup["cut_id"],
            expected_revision=revision,
            relation="reverse",
            crosses_barrier=True,
        ),
    )
    assert result.ok and result.receipts[0]["kind"] == "direction_receipt"
    with work.db.transaction() as session:
        assert (
            DirectionService().read_cut(session, setup["cut_id"])["relation_source"] == "authored"
        )
    stale = edit.run(
        context,
        edit.Input(
            op="relation",
            cut_id=setup["cut_id"],
            expected_revision=revision,
            relation="side",
            crosses_barrier=False,
        ),
    )
    assert not stale.ok and f"revision is {revision + 1}" in stale.discrepancy
    place_tool = EditPlace()
    place = context.state.places[0]
    attempted = place_tool.Input(
        op="set",
        place_id=place["id"],
        expected_revision=place["revision"],
        description="New prose",
        angles=[
            {"angle_id": setup["angles"][0], "expected_revision": 1, "description": "New angle"}
        ],
    )
    assert not place_tool.run(context, attempted).ok
    with work.db.transaction() as session:
        persisted = DirectionService().read_place(session, place["id"])
        assert (
            persisted["revision"] == place["revision"] and persisted["description"] != "New prose"
        )
    attempted.angles[0].expected_revision = 2
    result = place_tool.run(context, attempted)
    assert result.ok and result.changed and not result.receipts
    assert result.payload["after"]["description"] == "New prose"
    assert result.payload["angles"][0]["after"]["description"] == "New angle"


def test_prepare_survey_review_insertion_and_sheet(world):
    from harness_eval_tasks import run_idle

    work, setup = world
    context = context_for(work, setup)
    prepare = PrepareCut()
    value = prepare.Input(cut_id=setup["cut_id"], seed=31)
    assert prepare.narrow(replace(context, autonomy="off")) is None
    assert prepare.narrow(replace(context, jobs_reserved=6)) is None
    result = prepare.run(context, value)
    assert result.ok and result.waits_on and result.receipts[0]["budget_reserved"] == 1
    with work.db.transaction() as session:
        survey = OperationService().operation(session, result.waits_on)
        assert survey["request_key"] == "agent:test-turn"
        assert survey["request"]["plan"]["first_frame_asset"] == setup["plate"]
    attached = prepare.run(context_for(work, setup), value)
    assert attached.ok and attached.waits_on == result.waits_on and not attached.receipts
    assert prepare.narrow(context_for(work, setup)) is None
    run_idle(work, setup["fake"])
    context = context_for(work, setup)
    assert prepare.narrow(context) is None
    opened = prepare.run(context, value)
    assert opened.ok and opened.payload["review"]["kind"] == "angle_views"
    reader = ReadCandidates()
    blind = reader.run(context, reader.Input(operation_id=survey["id"]))
    assert blind.ok and blind.payload["purpose"] == "angle_views"
    assert not keys(blind.payload) & {"seed", "job_id", "graph", "request"}
    with work.db.transaction() as session:
        review = open_views_review(session, survey["id"])
        for member in review["members"]:
            approve_view(session, candidate_id=member["candidate_id"])
        direction = DirectionService()
        cut = direction.read_cut(session, setup["cut_id"])
        cleared = direction.update_cut(session, cut["id"], cut["revision"], insertion=None)
    refused = prepare.run(context_for(work, setup), value)
    assert not refused.ok and "insertion_wording_missing" in refused.discrepancy
    with work.db.transaction() as session:
        direction.update_cut(session, cut["id"], cleared["revision"], insertion=cut["insertion"])
        segment = direction.read_segment(session, cut["segment_id"])
        subject = direction.read_subject(session, segment["content"]["subjects"][0])
        no_identity = direction.update_subject(
            session, subject["id"], subject["revision"], identity_asset_id=None
        )
    refused = prepare.run(context_for(work, setup), value)
    assert not refused.ok and "insertion_needs_one_subject" in refused.discrepancy
    with work.db.transaction() as session:
        direction.update_subject(
            session,
            subject["id"],
            no_identity["revision"],
            identity_asset_id=subject["identity_asset_id"],
        )
    context = context_for(work, setup)
    assert prepare.narrow(context) is not None
    poor = replace(context, jobs_reserved=5)
    assert prepare.narrow(poor) is None
    refused = prepare.run(poor, value)
    assert not refused.ok and "budget_exhausted" in refused.discrepancy
    context.turn_id = "insertion-turn"
    result = prepare.run(context, value)
    assert result.ok and result.receipts[0]["budget_reserved"] == 2
    with work.db.transaction() as session:
        insertion = OperationService().operation(session, result.waits_on)
        assert insertion["request"]["wording"] == setup["wording"]
        assert insertion["request"]["seeds"] == [31, 32]
    run_idle(work, setup["fake"])
    context = context_for(work, setup)
    opened = prepare.run(context, value)
    assert opened.payload["review"]["kind"] == "insertion"
    blind = reader.run(context, reader.Input(operation_id=insertion["id"]))
    assert len(blind.payload["members"]) == 2 and "revealed" not in blind.payload
    with work.db.transaction() as session:
        review = ReviewService().blind(session, insertion["id"])
        ids = [m["candidate_id"] for m in review["members"]]
        ReviewService().save_ranking(
            session,
            review["id"],
            expected_revision=0,
            tiers=[ids],
            usable_tiers=1,
            notes={},
            tags={},
            general="Both usable",
        )
        approve_pin(session, candidate_id=ids[0])
    revealed = reader.run(context, reader.Input(operation_id=insertion["id"]))
    assert {r["seed"] for r in revealed.payload["revealed"]} == {31, 32}
    assert not keys(revealed.payload) & {"job_id", "graph", "request"}
    context = context_for(work, setup)
    context.turn_id = "sheet-turn"
    context.document.plan_mode = True
    context.document.plan = Plan(
        id="assembly-plan",
        revision=1,
        state="executing",
        goal="Assemble approved views",
        proposed_turn=0,
        steps=[PlanStep(kind="prepare", title="Assemble views")],
        budget_jobs=0,
        grant=PlanGrant(
            budget_jobs=0, step_kinds=["prepare"], approved_turn=1, reserved_at_approval=3
        ),
    )
    context.autonomy = "off"
    context.jobs_reserved = context.jobs_limit = 3
    assert prepare.narrow(context) is not None
    result = prepare.run(context, value)
    assert result.ok and result.receipts[0]["budget_reserved"] == 0
    with work.db.transaction() as session:
        sheet = OperationService().operation(session, result.waits_on)
        expected = [
            e["id"]
            for a in DirectionService().angles(session, setup["place_id"])
            for e in DirectionService().evidence(session, a["id"])
            if e["approved"]
        ]
        assert sheet["request"]["tiles"][: len(expected)] == expected
        assert all("blank" in item for item in sheet["request"]["tiles"][len(expected) :])
    run_idle(work, setup["fake"])
    context = context_for(work, setup)
    assert prepare.narrow(context) is None
    refusal = prepare.run(context, value).discrepancy
    assert "References prepared; segment not ready" in refusal
    assert "first segment has no predecessor" in refusal
    assert "ready to render" not in refusal


def test_disarming_during_model_call_refuses_production(world):
    from harness_support import ScriptedClient, action

    from gaiavfx.application.harness.driver import AgentRunner

    work, setup = world

    def disarm(messages, schema):
        assert "prepare_cut" in str(schema)
        work.save_settings(work.settings() | {"agent_autonomy": "off"})
        return action("prepare_cut", cut_id=setup["cut_id"])

    model = ScriptedClient([disarm, action("say", text="Preparation is off.")])
    harness = Harness(work.db, AgentStore(), model, work.settings)
    row = harness.open(setup["movie_id"])
    row = harness.set_plan_mode(row["id"], row["revision"], False)
    harness.post_message(row["id"], "Prepare", "one")
    assert AgentRunner(work.db, harness).run_once()
    assert harness.read(row["id"])["state"] == "idle"
    assert harness.turns(row["id"])[1]["state"] == "rejected"
    with work.db.transaction() as session:
        assert OperationService().recent(session) == []


def test_missing_text_wording_and_relation_refusals(world):
    work, setup = world
    service, prepare = DirectionService(), PrepareCut()
    value = prepare.Input(cut_id=setup["cut_id"])
    with work.db.transaction() as session:
        place = service.read_place(session, setup["place_id"])
        service.update_place(session, place["id"], place["revision"], description="")
    result = prepare.run(context_for(work, setup), value)
    assert not result.ok and "place_text_missing" in result.discrepancy
    with work.db.transaction() as session:
        service.update_place(
            session, place["id"], place["revision"] + 1, description=place["description"]
        )
        angle = service.read_angle(session, setup["angles"][2])
        service.update_angle(session, angle["id"], angle["revision"], description="")
    refusal = prepare.run(context_for(work, setup), value).discrepancy
    assert "place_text_missing: angle reverse" in refusal and angle["id"] in refusal
    with work.db.transaction() as session:
        cut = service.read_cut(session, setup["cut_id"])
        service.update_cut(
            session,
            cut["id"],
            cut["revision"],
            from_angle_id=setup["angles"][1],
            relation="reverse",
            crosses_barrier=True,
            relation_source="authored",
        )
    assert "plate_angle_mismatch" in prepare.run(context_for(work, setup), value).discrepancy
    with work.db.transaction() as session:
        cut = service.read_cut(session, setup["cut_id"])
        service.update_cut(
            session,
            cut["id"],
            cut["revision"],
            relation=None,
            crosses_barrier=None,
            relation_source="unknown",
        )
    context = context_for(work, setup)
    assert prepare.narrow(context) is None
    assert "edit_cut first" in prepare.run(context, value).discrepancy
    with pytest.raises(Exception, match="not available"):
        parse_action(
            {"tool": "prepare_cut", "input": {"cut_id": setup["cut_id"]}}, applicable_tools(context)
        )


@pytest.mark.parametrize("point", ["receipt", "wait"])
@pytest.mark.parametrize("plan_mode", [False, True])
def test_interruption_between_producer_receipt_and_wait_commits(
    world, monkeypatch, point, plan_mode
):
    from harness_support import ScriptedClient, action

    from gaiavfx.application.harness.driver import AgentRunner

    class Crash(BaseException):
        pass

    work, setup = world
    model = ScriptedClient([action("prepare_cut", cut_id=setup["cut_id"], seed=9)])
    harness = Harness(work.db, AgentStore(), model, work.settings)
    row = harness.open(setup["movie_id"])
    if plan_mode:
        model.replies = iter(
            [
                action(
                    "propose_plan",
                    goal="Prepare the courtyard",
                    steps=[
                        {
                            "kind": "prepare",
                            "title": "Survey",
                            "target": setup["cut_id"],
                            "jobs": 1,
                        }
                    ],
                ),
                action("prepare_cut", cut_id=setup["cut_id"], seed=9),
            ]
        )
    else:
        row = harness.set_plan_mode(row["id"], row["revision"], False)
    runner = AgentRunner(work.db, harness)
    harness.post_message(row["id"], "Prepare", "first")
    if plan_mode:
        assert runner.run_once()
        proposed = harness.read(row["id"])
        harness.approve_plan(row["id"], proposed["revision"])
    method = "_update_turn" if point == "receipt" else "_save"
    original = getattr(harness, method)

    def interrupted(*args, **kwargs):
        if point == "receipt" or args[1] == "waiting_operation":
            raise Crash()
        return original(*args, **kwargs)

    monkeypatch.setattr(harness, method, interrupted)
    with pytest.raises(Crash):
        runner.run_once()
    assert not model.active
    turn = harness.turns(row["id"])[-1]
    assert turn["state"] == ("requested" if point == "receipt" else "accepted")
    monkeypatch.setattr(harness, method, original)
    runner.recover()
    assert runner.run_once()
    recovered = harness.read(row["id"])
    assert recovered["state"] == ("failed" if point == "receipt" else "waiting_operation")
    assert harness.payload(recovered)["jobs_reserved"] == 1
    assert len(model.calls) == 1 + plan_mode and not model.active
    with work.db.transaction() as session:
        assert len(OperationService().recent(session)) == 1
    if plan_mode:
        plan = recovered["document"]["plan"]
        assert plan["state"] == "executing"
        assert len(plan["steps"][0]["receipts"]) == 1
        assert plan["steps"][0]["receipts"][0]["budget_reserved"] == 1
    if point == "receipt":
        assert harness.turns(row["id"])[-1]["receipts"][0]["kind"] == "operation"
    else:
        assert recovered["document"]["waiting_on"] == turn["result"]["waits_on"]
        assert not runner.run_once()
