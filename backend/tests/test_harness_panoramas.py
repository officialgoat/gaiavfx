"""Gaia and panoramas: two panoramas of a place in a prepare step titled for them, the user's choice in Library > Stages
(never Gaia's), and a stage built from the chosen one in a build step. The shared scene drives real transactions with
fake inference only."""

from PIL import Image
from test_harness_build import context, drain, harness, planned
from test_set_seed import scene as scene  # noqa: PLC0414

from gaiavfx.application import review_approvals
from gaiavfx.application.harness.session import AgentSession, Plan, PlanGrant, PlanStep
from gaiavfx.application.harness.tools import applicable_tools
from gaiavfx.application.harness.tools.panoramas import BuildStageFromPanorama, MakePanorama
from gaiavfx.application.harness.tools.plan_check import check
from gaiavfx.application.harness.view import panorama_lines
from gaiavfx.application.harness.world import direction
from gaiavfx.application.panoramas import RENDERS
from gaiavfx.application.set_seed import fill_renders
from gaiavfx.modules.generation.operations.service import OperationService

OPERATIONS = OperationService()
DRAW = "Draw two panoramas of the box room"


def offered(h, row, document):
    return {tool.name for tool, _ in applicable_tools(context(h, row, document))}


def panorama_plan(s, *steps):
    """A session whose approved plan executes `steps` ((kind, title, target, jobs)) in order."""
    h = harness(s)
    row = h.open(s.segment["movie_id"])
    document = AgentSession.model_validate(row["document"])
    document.plan_mode = True
    kinds = [kind for kind, *_ in steps]
    document.plan = Plan(
        id="plan",
        revision=1,
        state="executing",
        goal="A stage of the box room from words",
        proposed_turn=0,
        steps=[PlanStep(kind=k, title=t, target=g, jobs=j) for k, t, g, j in steps],
        budget_jobs=0,
        grant=PlanGrant(budget_jobs=0, step_kinds=kinds, approved_turn=1, reserved_at_approval=0),
    )
    return h, row, document


def test_panoramas_are_made_only_in_a_step_titled_for_them(scene):
    s = scene
    h, row, document = planned(s, "prepare")
    assert "make_panorama" not in offered(h, row, document)
    h, row, document = panorama_plan(s, ("prepare", DRAW, s.place["id"], RENDERS))
    assert "make_panorama" in offered(h, row, document)
    assert "build_stage_from_panorama" not in offered(h, row, document)  # nothing chosen yet


def test_the_plan_prices_panoramas_and_builds_only_from_the_place_it_drew(scene):
    s = scene
    h, row, document = panorama_plan(s, ("prepare", DRAW, "", 0))
    ctx = context(h, row, document)
    steps = [
        PlanStep(kind="prepare", title=DRAW, target="", jobs=0),
        PlanStep(
            kind="build",
            title="Build the box room stage from the chosen panorama",
            target=s.place["id"],
            jobs=fill_renders(),
        ),
    ]
    assert check(ctx, steps) is None
    # The only place is the one the panoramas are of, and they reserve one attempt's renders.
    assert steps[0].target == s.place["id"] and steps[0].jobs == RENDERS
    wrong = [PlanStep(kind="prepare", title=DRAW, target=s.place["id"], jobs=2)]
    assert "panorama_jobs" in check(ctx, wrong).discrepancy
    nowhere = [
        PlanStep(
            kind="build",
            title="Build the box room stage from the chosen panorama",
            target=s.place["id"],
            jobs=fill_renders(),
        )
    ]
    refused = check(ctx, nowhere).discrepancy
    assert refused.startswith("build_target:") and "chosen panorama" not in refused
    # One step draws both panoramas of a place (the live eval's model planned A and B apart).
    apart = [
        PlanStep(kind="prepare", title=f"Draw panorama {letter}", target="", jobs=RENDERS)
        for letter in "AB"
    ]
    assert check(ctx, apart).discrepancy.startswith("panorama_once:")
    # Building a stage from the chosen one is a build step, though its title names a panorama.
    as_prepare = [
        PlanStep(kind="prepare", title=DRAW, target="", jobs=RENDERS),
        PlanStep(kind="prepare", title="Build stage from chosen panorama", target="", jobs=4),
    ]
    refused = check(ctx, as_prepare).discrepancy
    assert refused.startswith("wrong_step_kind:") and s.place["id"] in refused


def test_gaia_draws_waits_for_the_user_s_choice_and_builds_from_it(scene):
    s = scene
    h, row, document = panorama_plan(
        s,
        ("prepare", DRAW, s.place["id"], RENDERS),
        (
            "build",
            "Build the box room stage from the chosen panorama",
            s.place["id"],
            fill_renders(),
        ),
    )
    started = MakePanorama().run(
        context(h, row, document),
        MakePanorama.Input(
            place_id=s.place["id"],
            description="A small box room with a checkered floor",
            landmarks=[{"what": "A door", "heading": 180}],
        ),
    )
    assert started.ok, started
    assert "the user chooses one in Library > Stages" in started.payload["message"]
    document.plan.steps[0].receipts.extend(started.receipts)
    drain(s.run)
    with s.workspace.db.transaction() as session:
        made = OPERATIONS.operation(session, started.waits_on)
    assert made["state"] == "succeeded" and len(made["candidates"]) == 2
    ctx = context(h, row, document)
    (line,) = panorama_lines(ctx.state)
    assert "awaits the user's choice in Library > Stages, never yours" in line
    assert "build_stage_from_panorama" not in offered(h, row, document)

    # The user chooses B; Gaia hears it and the build step may build from it.
    chosen = made["candidates"][1]["id"]
    with s.workspace.db.transaction() as session:
        review_approvals.choose_panorama(session, s.workspace.library, chosen)
    document.plan.steps[0].status = "done"
    ctx = context(h, row, document)
    (line,) = panorama_lines(ctx.state)
    assert f"the user chose B ({chosen})" in line
    assert "build_stage_from_panorama" in offered(h, row, document)
    built = BuildStageFromPanorama().run(
        ctx, BuildStageFromPanorama.Input(candidate_id=chosen, fill_unseen=False)
    )
    assert built.ok, built
    assert built.payload["message"].startswith("Building ")
    drain(s.run)
    with s.workspace.db.transaction() as session:
        stage = OPERATIONS.operation(session, built.waits_on)
    assert stage["request"]["source"] == "panorama"
    assert stage["review"][-1]["stage"] == "done"


def test_the_place_s_own_words_stand_in_for_a_description(scene):
    s = scene
    h, row, document = panorama_plan(s, ("prepare", DRAW, s.place["id"], RENDERS))
    door = [{"what": "A door", "heading": 180}]
    refused = MakePanorama().run(
        context(h, row, document), MakePanorama.Input(place_id=s.place["id"], landmarks=door)
    )
    assert not refused.ok and "Write the place in description" in refused.discrepancy
    with s.workspace.db.transaction() as session:
        place = direction.read_place(session, s.place["id"])
        direction.update_place(
            session, place["id"], place["revision"], description="A small box room. Pale walls."
        )
    # As the live eval's model called it: no place_id (the step names the place) and a panorama's 360 as hfov.
    started = MakePanorama().run(
        context(h, row, document), MakePanorama.Input(landmarks=door, hfov=360)
    )
    assert started.ok, started
    with s.workspace.db.transaction() as session:
        request = OPERATIONS.operation(session, started.waits_on)["request"]
    assert request["place"] == "A small box room. Pale walls."
    assert request["place_id"] == s.place["id"] and request["mode"] == "description"


def test_from_the_scene_gaia_grows_the_place_s_approved_plate(scene, tmp_path):
    s = scene
    h, row, document = panorama_plan(s, ("prepare", DRAW, s.place["id"], RENDERS))
    refused = MakePanorama().run(context(h, row, document), MakePanorama.Input(from_plate=True))
    assert not refused.ok and "has no approved plate yet" in refused.discrepancy
    Image.new("RGB", (320, 180), (120, 110, 100)).save(tmp_path / "plate.png")
    with s.workspace.db.transaction() as session:
        plate = s.workspace.library.register(
            session,
            s.workspace.library.prepare(tmp_path / "plate.png", "plate.png"),
            {"type": "import"},
        )["id"]
        direction.set_plate(session, s.place["id"], asset_id=plate, angle_id=s.angles["door"]["id"])
    started = MakePanorama().run(context(h, row, document), MakePanorama.Input(from_plate=True))
    assert started.ok, started
    with s.workspace.db.transaction() as session:
        request = OPERATIONS.operation(session, started.waits_on)["request"]
    assert request["mode"] == "picture" and request["ask"]["picture_id"] == plate
    assert request["place_id"] == s.place["id"]


def test_gaia_cannot_build_from_a_panorama_the_user_did_not_choose(scene):
    s = scene
    h, row, document = panorama_plan(s, ("prepare", DRAW, s.place["id"], RENDERS))
    started = MakePanorama().run(
        context(h, row, document),
        MakePanorama.Input(place_id=s.place["id"], description="A small box room"),
    )
    drain(s.run)
    with s.workspace.db.transaction() as session:
        candidate = OPERATIONS.operation(session, started.waits_on)["candidates"][0]["id"]
    h, row, document = panorama_plan(
        s, ("build", "Build the box room stage from a panorama", s.place["id"], fill_renders())
    )
    refused = BuildStageFromPanorama().run(
        context(h, row, document), BuildStageFromPanorama.Input(candidate_id=candidate)
    )
    assert not refused.ok and "the user chose" in refused.discrepancy
