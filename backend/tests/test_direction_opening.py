"""A segment's opening shot (ADR 0019 §1): its angle, its coverage, its plan, and the commands that keep the first
cut and the opening one truth."""

from pathlib import Path

import pytest
from test_direction_store import make_segment

from gaiavfx.application import direction_plans
from gaiavfx.application.workspace import Workspace
from gaiavfx.infrastructure.database import Database
from gaiavfx.integrations import minimax_h3
from gaiavfx.modules.direction.application import default_policy
from gaiavfx.modules.direction.coverage import (
    COVERAGE_WORDS,
    Coverage,
    Evidence,
    OpeningContext,
    assess_opening,
)
from gaiavfx.modules.direction.persistence import MovieRecord
from gaiavfx.modules.direction.planning import Action, Available, plan_opening
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.direction.techniques import parse_policy
from gaiavfx.modules.library.application import LibraryService
from gaiavfx.shared.errors import ApplicationError

MIGRATIONS = Path(__file__).parents[1] / "migrations"
POLICY = default_policy()


def opening(**changes):
    values = {
        "segment_id": "s",
        "angle": "a",
        "previous_angle": None,
        "receives_predecessor": False,
        "evidence": (),
    }
    return OpeningContext(**(values | changes))


def view(approved=True, angle="a"):
    return Evidence("e", angle, "set_view", "asset", approved)


def plan(context, available=None, override=None):
    return plan_opening(
        context,
        POLICY,
        minimax_h3.capabilities(),
        minimax_h3.knowledge_pack(),
        available,
        override=override,
    )


# ---- coverage and the plan -------------------------------------------------------------------------------------


def test_an_opening_continues_only_on_the_same_angle():
    same = opening(previous_angle="a", receives_predecessor=True)
    other = opening(previous_angle="b", receives_predecessor=True)
    assert assess_opening(same).coverage is Coverage.CONTINUING
    assert assess_opening(other).coverage is Coverage.NEW
    assert "onto another angle" in assess_opening(other).reasons[0]
    assert assess_opening(opening()).coverage is Coverage.NEW


def test_reviewed_evidence_establishes_an_opening_and_pending_evidence_does_not():
    assert assess_opening(opening(evidence=(view(),))).coverage is Coverage.ESTABLISHED
    pending = assess_opening(opening(evidence=(view(approved=False),)))
    assert pending.coverage is Coverage.NEW
    assert "waiting for review" in pending.reasons[-1]


def test_text_stays_the_default_and_a_sheet_is_an_option_until_the_ranks():
    for context in (opening(), opening(evidence=(view(),))):
        result = plan(context)
        assert result.recommended.technique_id == "opening_text" and result.recommended.ready
    established = plan(opening(evidence=(view(),)))
    assert [c.technique_id for c in established.alternatives] == ["opening_views"]
    assert "Not yet proven" in established.alternatives[0].limitations[0]


def test_an_explicit_request_for_the_sheet_requires_its_views():
    asked = plan(opening(), override="opening_views")
    assert asked.override and asked.recommended.technique_id == "opening_views"
    assert not asked.recommended.ready
    assert {a.action for a in asked.recommended.actions} == {
        Action.VIEW_ANGLE,
        Action.VIEW_PLACE,
    }
    ready = plan(
        opening(evidence=(view(),)),
        Available(approved_view_angles=frozenset({"a"}), place_views=2),
        override="opening_views",
    )
    assert ready.recommended.ready


def test_an_opening_cannot_take_a_cut_technique():
    with pytest.raises(ValueError, match="plans a cut"):
        plan(opening(), override="views_and_pin")


@pytest.mark.parametrize(
    "extra",
    ['relation = ["side"]', "side_facts = true", 'blocks = ["direction_text", "pinned_frame"]'],
)
def test_an_opening_row_has_no_previous_angle_sides_or_pin(extra):
    row = [
        "schema = 1",
        "[relation]",
        "same_direction_max_degrees = 45.0",
        "reverse_min_degrees = 135.0",
        "[[technique]]",
        'id = "x"',
        'shot = "opening"',
        'coverage = ["new"]',
        'status = "default"',
    ]
    row.append(extra if extra.startswith("blocks") else 'blocks = ["direction_text"]')
    if not extra.startswith("blocks"):
        row.append(extra)
    with pytest.raises(ValueError, match="an opening has no"):
        parse_policy("\n".join(row))


# ---- the commands keep the first cut and the opening one truth ---------------------------------------------------


@pytest.fixture
def store(tmp_path):
    db = Database(tmp_path)
    metadata = MovieRecord.metadata
    metadata.create_all(
        db.engine,
        tables=[table for table in metadata.sorted_tables if table.name.startswith("direction_")],
    )
    try:
        yield db, DirectionService()
    finally:
        db.close()


def angles(service, session, *names):
    place = service.create_place(session, "Shack")
    return [service.add_angle(session, place["id"], name)["id"] for name in names]


def cut(service, session, segment_id, frame, source, target):
    revision = service.read_segment(session, segment_id)["revision"]
    return service.create_cut(
        session,
        segment_id=segment_id,
        expected_revision=revision,
        frame=frame,
        from_angle_id=source,
        to_angle_id=target,
        relation="reverse",
        crosses_barrier=True,
        relation_source="authored",
        receives_predecessor=False,
    )


def test_a_zero_cut_segment_opens_on_the_angle_it_is_given_and_undo_clears_it(store):
    db, service = store
    with db.transaction() as session:
        door, _ = angles(service, session, "Door", "Stove")
        segment = make_segment(service, session)
        changed = service.update_segment(
            session, segment["id"], segment["revision"], opening_angle_id=door
        )
        assert changed["opening_angle_id"] == door
        service.undo(session, changed["receipt"]["id"])
        assert service.read_segment(session, segment["id"])["opening_angle_id"] is None


def test_the_first_cut_and_the_opening_stay_one_truth(store):
    db, service = store
    with db.transaction() as session:
        door, stove, window = angles(service, session, "Door", "Stove", "Window")
        segment = make_segment(service, session)
        first = cut(service, session, segment["id"], 36, door, stove)
        assert service.read_segment(session, segment["id"])["opening_angle_id"] == door
        # Opening the segment elsewhere starts its first cut there, with the relation to be stated again.
        revision = service.read_segment(session, segment["id"])["revision"]
        moved = service.update_segment(session, segment["id"], revision, opening_angle_id=window)
        cut_now = service.read_cut(session, first["id"])
        assert (cut_now["from_angle_id"], cut_now["relation"]) == (window, None)
        service.undo(session, moved["receipt"]["id"])
        assert service.read_cut(session, first["id"])["from_angle_id"] == door
        assert service.read_segment(session, segment["id"])["opening_angle_id"] == door
        # Changing the first cut's start changes the opening.
        edited = service.update_cut(
            session, first["id"], cut_now["revision"] + 1, from_angle_id=window
        )
        assert edited["from_angle_id"] == window
        assert service.read_segment(session, segment["id"])["opening_angle_id"] == window
        with pytest.raises(ApplicationError, match="first cut's starting angle"):
            revision = service.read_segment(session, segment["id"])["revision"]
            service.update_segment(session, segment["id"], revision, opening_angle_id=None)


def test_removing_cuts_keeps_the_opening_the_cuts_left(store):
    db, service = store
    with db.transaction() as session:
        door, stove, window = angles(service, session, "Door", "Stove", "Window")
        segment = make_segment(service, session)
        first = cut(service, session, segment["id"], 36, door, stove)
        second = cut(service, session, segment["id"], 70, stove, window)
        revision = service.read_segment(session, segment["id"])["revision"]
        service.remove_cut(session, first["id"], revision)
        # The shot after the removed cut is now the first one.
        assert service.read_segment(session, segment["id"])["opening_angle_id"] == stove
        revision = service.read_segment(session, segment["id"])["revision"]
        service.remove_cut(session, second["id"], revision)
        assert service.read_segment(session, segment["id"])["opening_angle_id"] == stove


def test_the_second_half_of_a_split_opens_on_the_angle_in_effect(store):
    db, service = store
    with db.transaction() as session:
        door, stove = angles(service, session, "Door", "Stove")
        segment = make_segment(service, session)
        cut(service, session, segment["id"], 20, door, stove)
        revision = service.read_segment(session, segment["id"])["revision"]
        left, right = service.split_segment(session, segment["id"], revision, 36)["segments"]
        assert (left["opening_angle_id"], right["opening_angle_id"]) == (door, stove)
        assert right["opening_technique"] is None


# ---- the window: text scenes unchanged, an explicit sheet request kept --------------------------------------------


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "library"
    LibraryService(root, MIGRATIONS).create("Openings")
    workspace = Workspace(root)
    yield workspace
    workspace.db.close()


def fresh_segment(service, session):
    movie = service.create_movie(session, "Shack scenes")
    subject = service.add_subject(
        session,
        movie["id"],
        movie["revision"],
        name="Ivo",
        description="A fisher",
        keeps="coat",
        identity_asset_id=None,
    )
    return service.add_segment(
        session,
        movie["id"],
        subject["receipt"]["after"]["revision"],
        content={
            "subjects": [subject["id"]],
            "place": {"noun": "shack", "description": "A fishing shack.", "landmarks": ""},
        },
        delivered_frames=97,
        continues=False,
        phases=[(0, {"description": "The stove glows.", "screen_direction": "", "sound": ""})],
    )


def test_a_text_scene_with_an_opening_angle_renders_as_before(workspace):
    service = DirectionService()
    with workspace.db.transaction() as session:
        door, _ = angles(service, session, "Door", "Stove")
        segment = fresh_segment(service, session)
        before = direction_plans.plan_for_segment(session, segment["id"])
        service.update_segment(session, segment["id"], segment["revision"], opening_angle_id=door)
        after = direction_plans.plan_for_segment(session, segment["id"])
    assert (before.technique_id, before.ready) == ("single_take", True)
    assert (after.technique_id, after.title, after.ready) == (
        "single_take",
        "Single continuous take",
        True,
    )
    assert after.opening.recommended.technique_id == "opening_text"


def test_asking_for_the_sheet_waits_for_its_views_instead_of_falling_back(workspace):
    service = DirectionService()
    with workspace.db.transaction() as session:
        door, _ = angles(service, session, "Door", "Stove")
        segment = fresh_segment(service, session)
        service.update_segment(
            session,
            segment["id"],
            segment["revision"],
            opening_angle_id=door,
            opening_technique="opening_views",
        )
        result = direction_plans.plan_for_segment(session, segment["id"])
    assert not result.ready
    assert result.technique_id == "opening_views"
    assert result.refusals[0].code == "opening_not_ready"
    assert result.refusals[0].message.startswith("Opening: ")


def test_the_opening_and_a_place_set_over_http(tmp_path):
    from fastapi.testclient import TestClient
    from test_direction_api import AUTH

    from gaiavfx.api.app import create_app

    LibraryService(tmp_path, MIGRATIONS).create("Openings API")
    app = create_app(tmp_path, MIGRATIONS, "test-token", enable_runner=False)
    with TestClient(app, base_url="http://localhost", headers=AUTH) as client:
        with app.state.workspace.db.transaction() as session:
            service = DirectionService()
            door, _ = angles(service, session, "Door", "Stove")
            segment = fresh_segment(service, session)
            place_id = service.read_angle(session, door)["place_id"]
        patched = client.patch(
            f"/api/direction/segments/{segment['id']}",
            json={
                "expected_revision": segment["revision"],
                "opening_angle_id": door,
                "opening_technique": "opening_views",
            },
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["opening_angle_id"] == door
        plan = client.get(f"/api/direction/segments/{segment['id']}/plan").json()
        assert plan["opening"]["angle_id"] == door and plan["opening"]["override"]
        assert plan["refusals"][0]["code"] == "opening_not_ready"
        cleared = client.patch(
            f"/api/direction/segments/{segment['id']}",
            json={"expected_revision": patched.json()["revision"], "opening_technique": None},
        )
        assert cleared.json()["opening_technique"] is None
        assert client.get(f"/api/direction/segments/{segment['id']}/plan").json()["ready"]
        missing = client.put(
            f"/api/direction/places/{place_id}/set", json={"expected_revision": 1, "set_id": "nope"}
        )
        assert missing.status_code == 404
        # The place says who makes its views, so the UI's Prepare steps decide nothing.
        assert client.get(f"/api/direction/places/{place_id}").json()["view_producer"] == "survey"


def test_one_rule_names_who_makes_a_places_views():
    """ADR 0019 §3, §7 (Fable and Astra, round 6): plans say which views a shot needs; one application rule names
    the producer, and the API's places carry it."""
    from gaiavfx.api.direction import PlaceDTO
    from gaiavfx.application.prepare_references import view_producer

    survey, built = view_producer({"set_id": None}), view_producer({"set_id": "set-1"})
    assert (survey.operation, survey.plate, survey.missing) == (
        "survey",
        True,
        "Prepare survey views",
    )
    assert (built.operation, built.plate, built.missing) == (
        "set_views",
        False,
        "Render views from the stage",
    )
    row = {
        "id": "p",
        "name": "Shack",
        "description": "",
        "sound": "",
        "overhead": "",
        "revision": 1,
        "plate_asset_id": None,
        "plate_angle_id": None,
        "created_at": "2026-10-27T00:00:00Z",
    }
    assert PlaceDTO(**row).model_dump()["view_producer"] == "survey"
    assert PlaceDTO(**row, set_id="set-1").model_dump()["view_producer"] == "set_views"


def test_a_place_names_its_set_with_a_revision_check(store):
    db, service = store
    with db.transaction() as session:
        place = service.create_place(session, "Shack")
        linked = service.set_place_set(session, place["id"], place["revision"], set_id="set-1")
        assert (linked["set_id"], linked["revision"]) == ("set-1", place["revision"] + 1)
        with pytest.raises(ApplicationError, match="stale"):
            service.set_place_set(session, place["id"], place["revision"], set_id=None)
        cleared = service.set_place_set(session, place["id"], linked["revision"], set_id=None)
        assert cleared["set_id"] is None


def test_every_coverage_has_the_words_every_view_shows():
    # ADR 0017 §2: the timeline and both Properties cards read these words, never the token.
    assert set(COVERAGE_WORDS) == set(Coverage)
    assert all(words and words[0].isupper() for words in COVERAGE_WORDS.values())
