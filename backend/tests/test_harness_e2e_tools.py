"""S7h regressions derived from the 3 October courtyard conversation."""

import json

import harness_support
import pytest
from harness_support import action
from test_harness_tools import context_for

from gaiavfx.application.harness.actions import parse_action
from gaiavfx.application.harness.session import Plan, PlanGrant, PlanStep
from gaiavfx.application.harness.shots import shots
from gaiavfx.application.harness.tools.common import validate_text
from gaiavfx.application.harness.tools.edits import EditCast, EditCut
from gaiavfx.application.harness.tools.places import EditPlace
from gaiavfx.application.harness.tools.prepare import PrepareCut
from gaiavfx.application.harness.view import render_view, selection_text
from gaiavfx.application.harness.world import direction
from gaiavfx.shared.errors import ApplicationError

world = harness_support.world
UUID = "fe051e20-8d03-4103-a360-7ed7c4433b2c"


@pytest.fixture
def unplated(tmp_path):
    from harness_eval_tasks import close, dz_library

    work, setup = dz_library(tmp_path, plate=False)
    try:
        yield work, setup
    finally:
        close(work)


def call(tool, context, **values):
    _, value = parse_action(action(tool.name, **values), [(tool, tool.narrow(context))])
    return tool.run(context, value)


def test_identity_binding_and_prose_ids_refused_without_writes(world):
    work, setup = world
    ctx = context_for(work, setup)
    tool = EditCast()
    ctx.attachments = [{"id": setup["identity"], "kind": "image", "name": "Arin"}]
    # Attachments remain bindable even when a bounded Library listing omits them.
    ctx.state.assets = [a for a in ctx.state.assets if a["id"] != setup["identity"]]
    value = {
        "op": "add",
        "expected_revision": ctx.state.movie["revision"],
        "name": "Arin",
        "description": "Plaid shirt",
        "keeps": [setup["identity"]],
    }
    refused = call(tool, ctx, **value)
    assert not refused.ok and "Use identity_asset for the picture" in refused.discrepancy
    with work.db.transaction() as session:
        assert (
            direction.read_movie(session, setup["movie_id"])["revision"]
            == value["expected_revision"]
        )
    value.update(keeps=["tan satchel"], identity_asset_id=setup["identity"])
    for invalid in (setup["take"], UUID):
        with pytest.raises(ApplicationError):
            call(tool, ctx, **(value | {"identity_asset_id": invalid}))
        assert not tool.run(ctx, tool.Input(**(value | {"identity_asset_id": invalid}))).ok
    result = call(tool, ctx, **value)
    assert result.ok and result.payload["revision"] == 1
    assert result.payload["movie_revision"] == value["expected_revision"] + 1
    with work.db.transaction() as session:
        subject = direction.read_subject(session, result.payload["id"])
        assert subject["identity_asset_id"] == setup["identity"] and subject["keeps"] == [
            "tan satchel"
        ]
    place = ctx.state.places[0]
    refused = call(
        EditPlace(),
        ctx,
        op="set",
        place_id=place["id"],
        expected_revision=place["revision"],
        sound=setup["plate"],
    )
    assert not refused.ok and "plate.source_asset" in refused.discrepancy
    with work.db.transaction() as session:
        assert direction.read_place(session, place["id"])["revision"] == place["revision"]


@pytest.mark.parametrize(
    "field",
    ["description", "sound", "style", "overhead", "screen_direction", "keeps", "behind", "name"],
)
def test_uuid_text_fields_including_nested_fields_have_asset_hint(field):
    with pytest.raises(ApplicationError, match="identity_asset"):
        validate_text({"nested": [{field: UUID if field != "keeps" else [UUID]}]})
    validate_text({field: "At frame 36 keep the red plaid shirt and tan satchel."})


def test_cut_creation_angle_ownership_duplicate_and_exact_revision_retry(world):
    work, setup = world
    ctx = context_for(work, setup)
    place_tool, cut_tool = EditPlace(), EditCut()
    # The reverse is a view of the courtyard, not another location.
    values = {
        "op": "add_angle",
        "place_id": setup["place_id"],
        "name": "reverse inside",
        "description": "Looking toward the courtyard across the railing",
    }
    with pytest.raises(ApplicationError):
        call(place_tool, ctx, **(values | {"place_id": "invented"}))
    assert not place_tool.run(ctx, place_tool.Input(**(values | {"place_id": "invented"}))).ok
    assert not place_tool.run(ctx, place_tool.Input(op="add_angle", place_id=setup["place_id"])).ok
    added = call(place_tool, ctx, **values)
    assert added.ok and added.payload["revision"] == 2
    angle = added.payload["angle"]
    with work.db.transaction() as session:
        other = direction.create_place(session, "Other location")
        other_angle = direction.add_angle(session, other["id"], "reverse")
        movie = direction.read_movie(session, setup["movie_id"])
        second = direction.add_segment(
            session,
            movie["id"],
            movie["revision"],
            content=movie["segments"][0]["content"],
            delivered_frames=96,
            continues=True,
            phases=[(0, {"description": "Walk", "screen_direction": "", "sound": ""})],
        )
    ctx = context_for(work, setup)
    values = {
        "op": "create",
        "segment_id": second["id"],
        "expected_revision": second["revision"],
        "frame": 36,
        "from_angle_id": setup["angles"][0],
        "to_angle_id": angle["id"],
        "relation": "reverse",
        "crosses_barrier": True,
        "phase": {"description": "The courtyard behind Arin"},
    }
    refused = call(cut_tool, ctx, **(values | {"to_angle_id": other_angle["id"]}))
    assert not refused.ok and "same place" in refused.discrepancy
    with work.db.transaction() as session:
        unchanged = direction.read_segment(session, second["id"])
        assert unchanged["revision"] == second["revision"] and len(unchanged["phases"]) == 1
    stale = call(cut_tool, ctx, **(values | {"expected_revision": 999}))
    assert (
        stale.discrepancy
        == "Segment revision is 1. Call edit_cut again with expected_revision 1 after re-reading if needed."
    )
    result = call(cut_tool, ctx, **values)
    assert result.ok and result.payload["revision"] == 1 and result.payload["segment_revision"] == 3
    repeated = call(cut_tool, context_for(work, setup), **values)
    assert (
        not repeated.ok
        and f"A cut already exists at f36 (id {result.payload['id']}); use op=relation"
        in repeated.discrepancy
    )
    with work.db.transaction() as session:
        segment = direction.read_segment(session, second["id"])
        assert len(segment["cuts"]) == 1 and segment["revision"] == 3
        assert segment["cuts"][0]["relation"] == "reverse" and segment["cuts"][0]["crosses_barrier"]
    # A create receipt resolves the plan's existing target even when the title still says create.
    ctx = context_for(work, setup)
    ctx.document.plan_mode = True
    ctx.document.plan = Plan(
        id="plan",
        revision=1,
        state="executing",
        goal="Create reverse",
        proposed_turn=0,
        budget_jobs=0,
        grant=PlanGrant(
            budget_jobs=0, step_kinds=["edit"], approved_turn=1, reserved_at_approval=0
        ),
        steps=[
            PlanStep(
                kind="edit",
                title="Create cut at f36",
                target=second["id"],
                receipts=result.receipts,
            )
        ],
    )
    view = render_view(ctx.state, "off", 0, 0, document=ctx.document)
    assert f"target exists: {result.payload['id']}" in view and "op=relation/insertion" in view
    assert "Create cut at f36" not in view
    assert f"Place {setup['place_id']}" in view and "reverse inside" in view


def test_plate_prefers_attached_or_imported_place_image_and_pins_its_angles(unplated, monkeypatch):
    work, setup = unplated
    with work.db.transaction() as session:
        other = direction.create_place(session, "Elsewhere")
        other_angle = direction.add_angle(session, other["id"], "wrong")
    ctx = context_for(work, setup)
    ctx.attachments = [
        {"id": setup["identity"], "name": "Arin", "kind": "image"},
        {"id": setup["plate"], "name": "Courtyard", "kind": "image"},
    ]
    ctx.state.segments[0]["selection"] = {"asset_id": setup["take"]}
    tool = PrepareCut()
    refused = tool.run(ctx, tool.Input(cut_id=setup["cut_id"]))
    value = json.loads(refused.discrepancy.split("Example input: ")[1])
    assert value["plate"]["source_asset"] == setup["plate"] and "frame" not in value["plate"]
    assert value["plate"]["angle_id"] in setup["angles"]
    parsed = parse_action(action("prepare_cut", **value), [(tool, tool.narrow(ctx))])[1]
    parsed.plate.angle_id = other_angle["id"]
    with pytest.raises(ApplicationError):
        call(tool, ctx, **parsed.model_dump(exclude_none=True))
    result = tool.run(ctx, parsed)
    assert not result.ok and "cut's place" in result.discrepancy
    with work.db.transaction() as session:
        direction.record_evidence(session, setup["angles"][0], "imported", setup["plate"])
    from gaiavfx.modules.library.media_application import MediaLibrary

    imported_sources = MediaLibrary.imported_sources
    monkeypatch.setattr(
        MediaLibrary,
        "imported_sources",
        lambda session, limit: [
            a for a in imported_sources(session, limit) if a["id"] != setup["plate"]
        ],
    )
    ctx = context_for(work, setup)
    assert next(a for a in ctx.state.assets if a["id"] == setup["plate"])["name"].startswith(
        "Imported image for"
    )
    from gaiavfx.application.harness.tools.input_hints import plate_input

    imported = json.loads(
        plate_input(ctx, next(s for s in shots(ctx.state) if s["kind"] == "cut")).split(
            "Example input: "
        )[1]
    )
    assert imported["plate"]["source_asset"] == setup["plate"]


def test_selection_state_is_plain_and_preserves_authority():
    assert selection_text(None) == "none"
    selected = {"authority": "user", "asset_id": UUID, "revision": 4}
    assert selection_text(selected).startswith("user-selected:")
    assert selection_text(selected | {"authority": "provisional"}).startswith("provisional:")


def test_resume_text_reports_ranking_and_selection_without_provenance(monkeypatch):
    from gaiavfx.application.harness import plans

    review = {
        "members": [
            {"letter": "A", "candidate_id": "a", "asset_id": "image-a"},
            {"letter": "B", "candidate_id": "b", "asset_id": "image-b"},
        ],
        "ranking": {"tiers": [["a"], ["b"]]},
    }
    monkeypatch.setattr(plans, "maybe_review", lambda *args: review)
    monkeypatch.setattr(plans.direction, "read_segment", lambda *args: {"ordinal": 0})
    monkeypatch.setattr(
        plans.selections, "current", lambda *args: {"authority": "user", "asset_id": "image-a"}
    )
    text = plans.review_outcome(
        None, "movie", {"id": "op", "kind": "render", "request": {"segment_id": "segment"}}
    )
    assert "The user ranked A > B and selected A for segment 1" in text
    assert not any(word in text for word in ("seed", "job_id", "technique"))
