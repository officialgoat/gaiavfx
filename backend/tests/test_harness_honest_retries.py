"""S7p: run 7's unchanged survey/pin inputs and misleading resume reports."""

import json
from pathlib import Path

import harness_support
import pytest
from harness_eval_tasks import run_idle
from harness_support import action
from test_harness_e2e_tools import unplated  # noqa: F401
from test_harness_follow_through import post
from test_harness_reference_text import prepare_grant
from test_harness_tools import context_for

from gaiavfx.application.harness.plans import review_outcome
from gaiavfx.application.harness.shots import shots
from gaiavfx.application.harness.tools.edits import EditPlace
from gaiavfx.application.harness.tools.prepare import PrepareCut
from gaiavfx.application.harness.tools.report_truth import (
    correction_claim,
    correction_refusal,
    ranking_refusal,
)
from gaiavfx.application.harness.tools.retries import stated_crossing
from gaiavfx.application.harness.tools.talk import Say
from gaiavfx.application.harness.world import direction, operations, reviews
from gaiavfx.application.prepare_references import (
    approve_view,
    open_insertion_review,
    open_plate_review,
    open_views_review,
)

world = harness_support.world
kernel = harness_support.kernel
RUN7 = json.loads((Path(__file__).parent / "fixtures/harness/run7_retries.json").read_text("utf-8"))
SURVEY_NOTE = RUN7["operations"]["72052df0"]["rejection"]["note"]
PIN_NOTE = RUN7["operations"]["93674061"]["rejection"]["note"]
ANGLE = RUN7["operations"]["72052df0"]["request"]["plan"]["views"][0][1]


def prepare(work, setup, **values):
    ctx = context_for(work, setup)
    ctx.turn_id += str(values.get("seed", 1))
    return PrepareCut().run(ctx, PrepareCut.Input(cut_id=setup["cut_id"], **values))


def survey(work, setup):
    result = prepare(work, setup, seed=31)
    assert result.ok, result.discrepancy
    run_idle(work, setup["fake"])
    with work.db.transaction() as session:
        return open_views_review(session, result.waits_on)


def test_h1_run7_same_survey_text_refused_before_reserving_jobs(world):
    work, setup = world
    review = survey(work, setup)
    with work.db.transaction() as session:
        reviews.reject_references(
            session,
            review["id"],
            expected_revision=review["revision"],
            note=SURVEY_NOTE,
            rejected_by="user",
        )
        before = operations.recent(session)
    result = prepare(work, setup, seed=32)
    assert not result.ok
    assert SURVEY_NOTE in result.discrepancy
    assert "edit_place" in result.discrepancy and "angle" in result.discrepancy
    with work.db.transaction() as session:
        assert operations.recent(session) == before
    # K7: the original run had no edit grant. Give an executable plan-revision route.
    ctx = prepare_grant(context_for(work, setup))
    result = PrepareCut().run(ctx, PrepareCut.Input(cut_id=setup["cut_id"], seed=32))
    assert (
        not result.ok and "propose_plan" in result.discrepancy and "edit step" in result.discrepancy
    )
    # Sound changes cannot satisfy a camera-geography rejection.
    with work.db.transaction() as session:
        place = direction.read_place(session, setup["place_id"])
        direction.update_place(
            session, place["id"], place["revision"], sound="Wind through the courtyard."
        )
    assert not prepare(work, setup, seed=33).ok
    with work.db.transaction() as session:
        cut = direction.read_cut(session, setup["cut_id"])
        angle = direction.read_angle(session, cut["to_angle_id"])
        direction.update_angle(
            session,
            angle["id"],
            angle["revision"],
            description="From inside the gallery behind the black iron railing, looking into the sunlit courtyard.",
        )
    changed = prepare(work, setup, seed=34)
    assert changed.ok, changed.discrepancy
    assert "inside the gallery" in changed.payload["inputs_used"]
    assert "angle text" in changed.payload["revised_inputs"]


def test_h3_run7_stated_crossing_blocks_pin(world):
    work, setup = world
    # Author run 7's target description before surveying so evidence stays fresh.
    with work.db.transaction() as session:
        cut = direction.read_cut(session, setup["cut_id"])
        angle = direction.read_angle(session, cut["to_angle_id"])
        direction.update_angle(session, angle["id"], angle["revision"], description=ANGLE)
        direction.update_cut(session, cut["id"], cut["revision"], crosses_barrier=False)
    review = survey(work, setup)
    with work.db.transaction() as session:
        for member in review["members"]:
            approve_view(session, candidate_id=member["candidate_id"])
    result = prepare(work, setup, seed=32)
    assert not result.ok
    assert "crosses_barrier" in result.discrepancy and "edit_cut" in result.discrepancy
    ctx = prepare_grant(context_for(work, setup))
    refused = PrepareCut().run(ctx, PrepareCut.Input(cut_id=setup["cut_id"]))
    assert (
        not refused.ok
        and "propose_plan" in refused.discrepancy
        and "edit step" in refused.discrepancy
    )
    with work.db.transaction() as session:
        cut = direction.read_cut(session, setup["cut_id"])
        direction.update_cut(session, cut["id"], cut["revision"], crosses_barrier=True)
    assert prepare(work, setup, seed=33).ok


def reject(session, review, note):
    reviews.reject_references(
        session, review["id"], expected_revision=review["revision"], note=note, rejected_by="user"
    )


def approved_views(work, setup):
    review = survey(work, setup)
    with work.db.transaction() as session:
        for member in review["members"]:
            approve_view(session, candidate_id=member["candidate_id"])


def test_h1_run7_pin_refusal_is_atomic_and_changed_facing_passes(world):
    work, setup = world
    approved_views(work, setup)
    wording = RUN7["operations"]["93674061"]["request"]["wording"]
    first = prepare(work, setup, seed=41, insertion=wording)
    assert first.ok, first.discrepancy
    assert all(str(wording[k]) in first.payload["inputs_used"] for k in ("facing", "size", "where"))
    assert "relation: reverse" in first.payload["inputs_used"]
    # Inline wording really edits the cut even before there is an older producer.
    ctx = context_for(work, setup)
    ctx.report_turns = [
        {
            "role": "agent",
            "state": "accepted",
            "action": action("prepare_cut"),
            "result": first.model_dump(),
            "receipts": first.receipts,
        }
    ]
    assert not correction_refusal(ctx, "The insertion wording is corrected.")
    run_idle(work, setup["fake"])
    with work.db.transaction() as session:
        reject(session, open_insertion_review(session, first.waits_on), PIN_NOTE)
        before = direction.read_cut(session, setup["cut_id"])
        receipts = direction.receipts(session, setup["movie_id"])
        producers = operations.recent(session)
    for values in (
        {},
        {"insertion": wording},
        {"insertion": {**wording, "size": "one quarter of the image height"}},
    ):
        result = prepare(work, setup, seed=42, **values)
        assert not result.ok and PIN_NOTE in result.discrepancy and "facing" in result.discrepancy
    with work.db.transaction() as session:
        assert direction.read_cut(session, setup["cut_id"]) == before
        assert direction.receipts(session, setup["movie_id"]) == receipts
        assert operations.recent(session) == producers
    result = prepare(work, setup, seed=43, insertion={**wording, "facing": "screen_left"})
    assert result.ok, result.discrepancy
    assert result.payload["revised_inputs"] == ["facing"]
    assert "facing: screen_left" in result.payload["inputs_used"]


@pytest.mark.parametrize("empty", [True, False])
def test_h1_plate_retries_compare_source_and_wording(unplated, empty):  # noqa: F811
    work, setup = unplated
    plate = {"source_asset": setup["plate"]}
    plate.update(
        {"empty": True}
        if empty
        else {"whom": "the man", "object_pronoun": "him", "behind": "cobblestone paving"}
    )
    result = prepare(work, setup, seed=51, plate=plate)
    assert result.ok, result.discrepancy
    run_idle(work, setup["fake"])
    with work.db.transaction() as session:
        reject(
            session,
            open_plate_review(session, result.waits_on),
            "The plate source and background need revision.",
        )
        before = operations.recent(session)
    result = prepare(work, setup, seed=52, plate=plate)
    assert not result.ok and "Unchanged inputs" in result.discrepancy
    with work.db.transaction() as session:
        assert operations.recent(session) == before
    changed = (
        {**plate, "source_asset": setup["identity"]}
        if empty
        else {**plate, "behind": "weathered cobblestone paving in sunlight"}
    )
    result = prepare(work, setup, seed=53, plate=changed)
    assert result.ok, result.discrepancy


@pytest.mark.parametrize(
    "text",
    [
        "The reverse crosses the railing.",
        "Seen from behind the railing.",
        "From the other side.",
        "The cut should say crosses_barrier: true.",
        "Behind the black iron railing, looking out.",
    ],
)
def test_h3_closed_crossing_phrases(text):
    assert stated_crossing(text)


@pytest.mark.parametrize(
    "text",
    [
        "The reverse does not cross the railing.",
        "Not behind the railing.",
        "No other side is shown.",
        "If it crosses the railing, flip it.",
        "Could the camera cross the railing?",
        "A view along the railing.",
        "crosses_barrier: false",
        "The railing separates the paving from the gallery.",
    ],
)
def test_h3_crossing_negative_controls(text):
    assert not stated_crossing(text)


@pytest.mark.parametrize(
    "text",
    [
        "This time the camera should be inside the gallery.",
        "Now the cut has crosses_barrier: true.",
        "The corrected pin faces left.",
        "I updated the camera angle.",
        "The revised survey is ready.",
    ],
)
def test_h2_closed_correction_patterns(text):
    assert correction_claim(text)


@pytest.mark.parametrize(
    "text",
    [
        "The camera has not been corrected.",
        "This time the camera is not corrected.",
        "If corrected, the survey should show the gallery.",
        "Could the corrected camera work?",
        "I will revise the pin.",
        "The pin needs corrected wording.",
        "You asked for a corrected camera angle.",
        "Now review the survey in Review.",
        "Now the survey is ready in Review.",
        "This time the pin failed.",
        "The survey is ready for review.",
        "Once the camera is corrected, I can survey again.",
    ],
)
def test_h2_negative_language_controls(text):
    assert not correction_claim(text)


@pytest.mark.parametrize("changed", [False, True])
def test_h2_actual_angle_edit_not_revision_or_plan_bookkeeping_is_proof(world, changed):
    work, setup = world
    ctx = context_for(work, setup)
    with work.db.transaction() as session:
        place = direction.read_place(session, setup["place_id"])
        angle = direction.read_angle(session, setup["angles"][2])
    value = EditPlace.Input(
        op="set",
        place_id=place["id"],
        expected_revision=place["revision"],
        angles=[
            {
                "angle_id": angle["id"],
                "expected_revision": angle["revision"],
                "description": "Inside the gallery looking into the courtyard."
                if changed
                else angle["description"],
            }
        ],
    )
    result = EditPlace().run(ctx, value)
    assert result.ok, result.discrepancy
    ctx.report_turns = [
        {
            "role": "agent",
            "state": "accepted",
            "action": action("propose_plan"),
            "result": {"changed": True},
        },
        {
            "role": "agent",
            "state": "accepted",
            "action": action("edit_place", **value.model_dump()),
            "result": result.model_dump(),
        },
    ]
    refusal = correction_refusal(ctx, "This time the camera is inside the gallery.")
    assert bool(refusal) is not changed


def test_h2_once_guard_survives_restart_and_resets_on_review(kernel):
    from gaiavfx.application.harness.loop import Harness
    from gaiavfx.application.harness.session import AgentSession

    post(
        kernel,
        action("say", text=RUN7["claims"]["76"]),
        action("say", text=RUN7["claims"]["76"]),
        message="Report.",
    )
    harness = Harness(kernel.db, kernel.store, None, lambda: kernel.settings)
    row = harness.read(kernel.id)
    turns = harness.turns(kernel.id)
    ctx = harness.context(row, AgentSession.model_validate(row["document"]), turns)
    assert Say().run(ctx, Say.Input(text=RUN7["claims"]["76"])).ok
    turns.append(
        {
            "role": "system",
            "state": "accepted",
            "receipts": [],
            "action": {"message": "The user rejected the pin candidates."},
        }
    )
    ctx = harness.context(row, AgentSession.model_validate(row["document"]), turns)
    assert not Say().run(ctx, Say.Input(text=RUN7["claims"]["76"])).ok


@pytest.mark.parametrize("changed", [False, True])
def test_h2_cut_receipt_proves_real_changes_not_revision_bumps(world, changed):
    from gaiavfx.application.harness.tools.edits import EditCut

    work, setup = world
    ctx = context_for(work, setup)
    with work.db.transaction() as session:
        cut = direction.read_cut(session, setup["cut_id"])
    value = EditCut.Input(
        op="relation",
        cut_id=cut["id"],
        expected_revision=cut["revision"],
        relation=cut["relation"],
        crosses_barrier=not cut["crosses_barrier"] if changed else cut["crosses_barrier"],
    )
    result = EditCut().run(ctx, value)
    assert result.ok, result.discrepancy
    ctx.report_turns = [
        {
            "role": "agent",
            "state": "accepted",
            "action": action("edit_cut", **value.model_dump()),
            "result": result.model_dump(),
            "receipts": result.receipts,
        }
    ]
    assert bool(correction_refusal(ctx, "The cut relation is corrected.")) is not changed


def test_h1_relation_comparison_uses_producer_time_not_current_cut(world):
    from gaiavfx.application.harness.tools.retries import previous_inputs, require_revised

    work, setup = world
    approved_views(work, setup)
    result = prepare(work, setup, seed=61)
    assert result.ok, result.discrepancy
    run_idle(work, setup["fake"])
    with work.db.transaction() as session:
        reject(session, open_insertion_review(session, result.waits_on), "Revise the cut relation.")
        op = operations.operation(session, result.waits_on)
        cut = direction.read_cut(session, setup["cut_id"])
        cut = direction.update_cut(session, cut["id"], cut["revision"], crosses_barrier=False)
        # More than a page of later receipts must not hide the original crossing.
        for _ in range(51):
            cut = direction.update_cut(session, cut["id"], cut["revision"], crosses_barrier=False)
    ctx = context_for(work, setup)
    shot = next(s for s in shots(ctx.state) if s["id"] == cut["id"])
    with work.db.transaction() as session:
        assert previous_inputs(session, op, shot)["crosses_barrier"] is True
        require_revised(session, ctx, [op], shot, "insert", op["request"])


def test_h3_rejection_note_alone_requires_crossing_edit(world):
    work, setup = world
    with work.db.transaction() as session:
        cut = direction.read_cut(session, setup["cut_id"])
        target = direction.read_angle(session, cut["to_angle_id"])
        direction.update_angle(
            session,
            target["id"],
            target["revision"],
            description="Sunlit cobblestone courtyard and olive trees.",
        )
        direction.update_cut(session, cut["id"], cut["revision"], crosses_barrier=False)
    first = survey(work, setup)
    with work.db.transaction() as session:
        reject(session, first, SURVEY_NOTE)
        angle = direction.read_angle(session, target["id"])
        direction.update_angle(
            session,
            angle["id"],
            angle["revision"],
            description="View from the gallery into the sunlit courtyard.",
        )
    changed = prepare(work, setup, seed=62)
    assert changed.ok, changed.discrepancy
    run_idle(work, setup["fake"])
    with work.db.transaction() as session:
        for member in open_views_review(session, changed.waits_on)["members"]:
            approve_view(session, candidate_id=member["candidate_id"])
    result = prepare(work, setup, seed=63)
    assert (
        not result.ok
        and SURVEY_NOTE in result.discrepancy
        and "crosses_barrier: false" in result.discrepancy
    )


def test_h4_real_resume_refuses_run7_claim_then_reports_zero_usable(tmp_path):
    from harness_eval_tasks import TASKS, close
    from harness_support import ScriptedClient

    from gaiavfx.application.harness.driver import AgentRunner
    from gaiavfx.application.harness.loop import Harness
    from gaiavfx.application.harness.session import AgentSession, Plan, PlanStep
    from gaiavfx.modules.agent.service import AgentStore

    task = next(t for t in TASKS if t.id == "provisional-pick-unblocks")
    work, setup = task.build(tmp_path)
    try:
        correct = f'You marked no take usable: "{RUN7["ranking"]["general"]}". We can re-render or revise references.'
        model = ScriptedClient(
            [action("say", text=RUN7["claims"]["95"]), action("say", text=correct)]
        )
        harness = Harness(work.db, AgentStore(), model, work.settings)
        row = harness.open(setup["movie_id"])
        doc = AgentSession.model_validate(row["document"])
        doc.plan = Plan(
            id="render-plan",
            revision=1,
            state="executing",
            goal="Render then review",
            budget_jobs=2,
            proposed_turn=0,
            steps=[
                PlanStep(kind="render", title="Render", status="done"),
                PlanStep(
                    kind="review",
                    title="Review",
                    status="awaiting_user",
                    review_of=setup["fixture_render"],
                ),
            ],
        )
        with work.db.transaction() as session:
            harness.store.update_session(
                session,
                row["id"],
                row["revision"],
                document=doc.model_dump(),
                state="waiting_human",
            )
            review = reviews.blind(session, setup["fixture_render"])
            reviews.save_ranking(
                session,
                review["id"],
                expected_revision=review["ranking"]["revision"],
                tiers=[[m["candidate_id"]] for m in review["members"]],
                usable_tiers=0,
                notes={},
                tags={},
                general=RUN7["ranking"]["general"],
            )
        assert harness.resume_reviewed(row["id"])
        assert not harness.resume_reviewed(row["id"])
        marker = harness.turns(row["id"])[-1]["action"]
        assert marker["review_operation_id"] == review["id"]
        assert (
            "no take usable" in marker["message"]
            and RUN7["ranking"]["general"] in marker["message"]
        )
        assert AgentRunner(work.db, harness).run_once()
        turns = [t for t in harness.turns(row["id"]) if t["role"] == "agent"]
        assert [t["state"] for t in turns] == ["rejected", "accepted"]
        assert turns[-1]["result"]["payload"]["text"] == correct
    finally:
        close(work)


@pytest.mark.parametrize("usable", [0, 1])
def test_h4_say_enforces_zero_usable_report_and_leaves_usable_ranking_normal(
    world, monkeypatch, usable
):
    from gaiavfx.application.harness.tools import report_truth

    work, setup = world
    ctx = context_for(work, setup)
    ctx.report_turns = [
        {"role": "system", "state": "accepted", "action": {"review_operation_id": "op"}}
    ]
    review = {
        "purpose": "segment_takes",
        "unranked": [],
        "ranking": {
            "tiers": [["a"]],
            "usable_tiers": usable,
            "general": RUN7["ranking"]["general"],
        },
    }
    monkeypatch.setattr(report_truth, "maybe_review", lambda *args: review)
    assert bool(ranking_refusal(ctx, RUN7["claims"]["95"])) is (usable == 0)
    text = f'You marked no take usable: "{RUN7["ranking"]["general"]}". We can re-render or revise references.'
    assert not ranking_refusal(ctx, text)
    if not usable:
        assert ranking_refusal(
            ctx, "You marked no take usable. We can re-render."
        )  # quote required
        assert ranking_refusal(ctx, text + " Once selected, it will be locked in with Take A.")
        assert ranking_refusal(ctx, text.replace(". We can", ", selection pending. We can"))
        assert not ranking_refusal(ctx, text + " No selection is pending.")
    # A new human request does not carry an obsolete reporting obligation.
    ctx.report_turns.append(
        {"role": "human", "state": "accepted", "action": {"message": "Discuss another shot."}}
    )
    assert not ranking_refusal(ctx, "Another shot.")


@pytest.mark.parametrize("ordinal", ["59", "76"])
def test_h2_run7_correction_claim_without_edit_refused_once(kernel, ordinal):
    turns = post(
        kernel,
        action("say", text=RUN7["claims"][ordinal]),
        action("say", text="The inputs are unchanged."),
        message="Report the retry.",
    )
    assert [t["state"] for t in turns] == ["rejected", "accepted"]
    assert "unchanged" in turns[0]["result"]["discrepancy"].lower()


def test_h4_run7_zero_usable_resume_quotes_note(monkeypatch):
    from gaiavfx.application.harness import plans

    review = {
        "members": [{"letter": "A", "candidate_id": "a", "asset_id": "image-a"}],
        "ranking": {"tiers": [["a"]], **RUN7["ranking"]},
    }
    monkeypatch.setattr(plans, "maybe_review", lambda *args: review)
    monkeypatch.setattr(plans.direction, "read_segment", lambda *args: {"ordinal": 1})
    monkeypatch.setattr(plans.selections, "current", lambda *args: None)
    text = review_outcome(
        None, "movie", {"id": "op", "kind": "render", "request": {"segment_id": "segment"}}
    )
    assert "no take usable" in text
    assert RUN7["ranking"]["general"] in text
    assert "selection pending" not in text and "Selection may follow" not in text
    assert "re-render" in text and "references" in text
