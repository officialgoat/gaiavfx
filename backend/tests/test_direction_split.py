"""Split/merge preserve Direction identity and never rewrite downstream history."""

import pytest
from fastapi.testclient import TestClient
from test_direction_store import store as direction_store
from test_production_api import AUTH, setup

from gaiavfx.application.selected_takes import selection_state
from gaiavfx.modules.direction.persistence import SuitabilityRecord
from gaiavfx.modules.direction.service import DirectionService, SegmentSplitRefusal
from gaiavfx.modules.editing.selections.service import SelectionService
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.review import ReviewService
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError

store = direction_store


def seed(service, session):
    movie = service.create_movie(session, "Split")
    for sid in ("a", "b", "voice", "unused"):
        service.add_subject(
            session,
            movie["id"],
            service.read_movie(session, movie["id"])["revision"],
            subject_id=sid,
            name=sid,
            description="A person",
            keeps=[],
            identity_asset_id=None,
        )
    content = {
        "subjects": ["a", "b", "voice", "unused"],
        "place": {"noun": "room", "description": "A room", "landmarks": "door"},
        "style": "Soft light",
        "events": [{"at_frame": f, "description": "Door opens"} for f in (10, 45, 65)],
        "speech": [
            {
                "at_frame": 45,
                "speaker_id": "voice",
                "text": "Hello",
                "language": "en",
                "off_screen": True,
            }
        ],
        "states": [
            {"at_frame": f, "subject_id": "voice", "attribute": "mood", "value": "calm"}
            for f in (20, 60)
        ],
    }
    phases = [
        (
            f,
            {
                "description": f"Action {f}",
                "screen_direction": "",
                "sound": "",
                "cast": ["a" if f < 45 else "b"],
            },
        )
        for f in (0, 30, 45, 60, 80)
    ]
    segment = service.add_segment(
        session,
        movie["id"],
        service.read_movie(session, movie["id"])["revision"],
        content=content,
        delivered_frames=120,
        continues=False,
        phases=phases,
    )
    service.add_segment(
        session,
        movie["id"],
        service.read_movie(session, movie["id"])["revision"],
        content=content,
        delivered_frames=120,
        continues=True,
        phases=phases,
    )
    place = service.create_place(session, "Room")
    angle = service.add_angle(session, place["id"], "Door")
    target_angle = service.add_angle(session, place["id"], "Closer view")
    for frame in (30, 60):
        service.create_cut(
            session,
            segment["id"],
            service.read_segment(session, segment["id"])["revision"],
            frame=frame,
            from_angle_id=angle["id"] if frame == 30 else target_angle["id"],
            to_angle_id=target_angle["id"] if frame == 30 else angle["id"],
        )
    return service.read_segment(session, segment["id"])


def tables(session, *, direction=True, revisions=True, receipts=True):
    """Raw SQL values keep JSON byte comparisons honest; no ORM normalization."""
    names = (
        session.connection()
        .exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        .scalars()
    )
    result = {}
    for name in list(names):
        if name.startswith("direction_") != direction or (
            not receipts and name == "direction_receipts"
        ):
            continue
        columns = [
            r[1]
            for r in session.connection().exec_driver_sql(f'PRAGMA table_info("{name}")')
            if revisions or r[1] != "revision"
        ]
        quoted = ", ".join(f'"{c}"' for c in columns)
        result[name] = sorted(
            session.connection().exec_driver_sql(f'SELECT {quoted} FROM "{name}"').all(), key=repr
        )
    return result


def split(service, session, segment, frame):
    return service.split_segment(session, segment["id"], segment["revision"], frame)


@pytest.mark.parametrize("frame", [30, 45, 60])
def test_split_undo_restores_every_direction_row_and_redo_identity(store, frame):
    db, service = store
    with db.transaction() as session:
        original = seed(service, session)
        before = tables(session, revisions=False, receipts=False)
        receipts = service.receipts(session, original["movie_id"])
        result = split(service, session, original, frame)
        left, right = result["segments"]
        assert left["id"] == original["id"]
        assert (left["delivered_frames"], right["delivered_frames"]) == (frame, 120 - frame)
        assert right["continues"] and not left["continues"]
        assert right["ordinal"] == left["ordinal"] + 1
        assert right["phases"][0]["start_frame"] == 0
        assert [c["frame"] for c in left["cuts"]] == [f for f in (30, 60) if f < frame]
        assert [c["frame"] for c in right["cuts"]] == [f - frame for f in (30, 60) if f > frame]
        assert {p["id"] for s in result["segments"] for p in s["phases"]} == {
            p["id"] for p in original["phases"]
        }
        for half in (left, right):
            service.assemble_segment(session, half["id"])
        assert len(service.receipts(session, original["movie_id"])) == len(receipts) + 1
        inverse = service.undo(session, result["receipt"]["id"])
        assert tables(session, revisions=False, receipts=False) == before
        for old in receipts:
            assert service.read_receipt(session, old["id"]) == old
        assert service.read_segment(session, left["id"])["revision"] == original["revision"] + 2
        redone = service.undo(session, inverse["id"])
        assert redone["record"]["segments"][1]["id"] == right["id"]
        service.undo(session, redone["id"])
        assert tables(session, revisions=False, receipts=False) == before


def test_phase_split_partitions_timing_and_cast_including_offscreen_subjects(store):
    db, service = store
    with db.transaction() as session:
        original = seed(service, session)
        left, right = split(service, session, original, 45)["segments"]
        assert left["content"]["subjects"] == ["a", "voice"]
        assert right["content"]["subjects"] == ["b", "voice"]
        assert [e["at_frame"] for e in left["content"]["events"]] == [10]
        assert [e["at_frame"] for e in right["content"]["events"]] == [0, 20]
        assert left["content"]["speech"] == []
        assert right["content"]["speech"] == [{**original["content"]["speech"][0], "at_frame": 0}]
        assert [s["at_frame"] for s in left["content"]["states"]] == [20]
        assert [s["at_frame"] for s in right["content"]["states"]] == [15]
        for half in (left, right):
            assert half["content"]["place"] == original["content"]["place"]
            assert half["content"]["style"] == original["content"]["style"]


@pytest.mark.parametrize("cast", [[], None])
def test_empty_or_unauthored_phase_cast_preserves_original_subjects(store, cast):
    db, service = store
    with db.transaction() as session:
        original = seed(service, session)
        for phase in original["phases"]:
            content = {k: v for k, v in phase["content"].items() if k != "cast"}
            if cast is not None:
                content["cast"] = cast
            service.update_phase(session, phase["id"], phase["revision"], content=content)
        original = service.update_segment(
            session,
            original["id"],
            original["revision"],
            content={
                k: v
                for k, v in original["content"].items()
                if k not in {"events", "speech", "states"}
            },
        )
        for half in split(service, session, original, 60)["segments"]:
            assert half["content"]["subjects"] == original["content"]["subjects"]


@pytest.mark.parametrize("frame", [0, -1, 31, 120, True, 45.0])
def test_invalid_boundary_refuses_without_any_write(store, frame):
    db, service = store
    with db.transaction() as session:
        original = seed(service, session)
        before = tables(session)
        with pytest.raises(SegmentSplitRefusal, match="Split at a cut or a phase boundary") as exc:
            split(service, session, original, frame)
        assert exc.value.code == "not_a_boundary"
        assert tables(session) == before


@pytest.mark.parametrize("protection", ["keyframes", "suitability_history", "superseded_history"])
def test_protected_boundary_cut_refuses_and_rolls_back(store, protection):
    db, service = store
    with db.transaction() as session:
        original = seed(service, session)
        cut = original["cuts"][1]
        evidence = service.record_evidence(session, cut["to_angle_id"], "survey_view", "view")
        if protection == "keyframes":
            service.record_keyframe(session, cut["id"], "pin", evidence["id"])
        else:
            judgment = service.judge_suitability(
                session,
                cut["id"],
                reference_kind="view",
                reference_id=evidence["id"],
                verdict="suits",
            )
            if protection == "superseded_history":
                session.get(SuitabilityRecord, judgment["id"]).superseded_at = "superseded"
                session.flush()
        before = tables(session)
    with (
        pytest.raises(SegmentSplitRefusal, match="phase boundary instead") as exc,
        db.transaction() as session,
    ):
        split(service, session, original, 60)
    assert exc.value.code == (
        "cut_has_keyframes" if protection == "keyframes" else "cut_has_suitability_history"
    )
    with db.transaction() as session:
        assert tables(session) == before
        # A different phase boundary can move a protected cut without rewriting its history.
        result = split(service, session, original, 45)
        assert result["segments"][1]["cuts"][0]["id"] == cut["id"]
        service.undo(session, result["receipt"]["id"])


@pytest.mark.parametrize(
    "half,kind",
    [
        (0, "segment"),
        (1, "segment"),
        (0, "phase"),
        (1, "phase"),
        (1, "cut"),
        (1, "cut_insertion"),
        (1, "keyframe"),
        (1, "suitability"),
        (1, "order"),
    ],
)
def test_undo_guards_both_halves_and_children(store, half, kind):
    db, service = store
    with db.transaction() as session:
        original = seed(service, session)
        initial = tables(session, revisions=False, receipts=False)
        result = split(service, session, original, 45)
        segment = result["segments"][half]
        if kind == "segment":
            # Same-value edits also count as a later command.
            edit = service.update_segment(
                session, segment["id"], segment["revision"], continues=segment["continues"]
            )
        elif kind == "phase":
            phase = segment["phases"][0]
            edit = service.update_phase(
                session, phase["id"], phase["revision"], content=phase["content"]
            )
        elif kind in {"cut", "cut_insertion"}:
            cut = segment["cuts"][0]
            edit = service.update_cut(
                session,
                cut["id"],
                cut["revision"],
                **({"frame": cut["frame"]} if kind == "cut" else {"insertion": None}),
            )
        elif kind == "order":
            edit = service.move_segment(
                session,
                segment["id"],
                service.read_movie(session, original["movie_id"])["revision"],
                ordinal=0,
            )
        else:
            cut = segment["cuts"][0]
            evidence = service.record_evidence(session, cut["to_angle_id"], "survey_view", "view")
            if kind == "keyframe":
                service.record_keyframe(session, cut["id"], "pin", evidence["id"])
            else:
                service.judge_suitability(
                    session,
                    cut["id"],
                    reference_kind="view",
                    reference_id=evidence["id"],
                    verdict="suits",
                )
        before = tables(session)
        with pytest.raises(ApplicationError, match="changed after"):
            service.undo(session, result["receipt"]["id"])
        assert tables(session) == before
        if kind in {"segment", "phase", "cut", "cut_insertion", "order"}:
            service.undo(session, edit["receipt"]["id"])
            service.undo(session, result["receipt"]["id"])
            assert tables(session, revisions=False, receipts=False) == initial


def test_redo_and_stale_command_protection(store):
    db, service = store
    with db.transaction() as session:
        original = seed(service, session)
        result = split(service, session, original, 60)
        with pytest.raises(ApplicationError, match="revision is stale"):
            split(service, session, original, 30)
        inverse = service.undo(session, result["receipt"]["id"])
        with pytest.raises(ApplicationError, match="already undone"):
            service.undo(session, result["receipt"]["id"])
        restored = service.read_segment(session, original["id"])
        edit = service.update_segment(
            session, restored["id"], restored["revision"], continues=False
        )
        with pytest.raises(ApplicationError, match="changed after"):
            service.undo(session, inverse["id"])
        undone_edit = service.undo(session, edit["receipt"]["id"])
        service.undo(session, undone_edit["id"])
        with pytest.raises(ApplicationError, match="changed after"):
            service.undo(session, inverse["id"])


def test_selection_stales_without_rewriting_any_downstream_row(store):
    db, service = store
    with db.transaction() as session:
        original = seed(service, session)
        operations = OperationService()
        operation = operations.create(
            session,
            kind="render",
            request={
                "segment_id": original["id"],
                "segment_revision": original["revision"],
                "phase_revisions": {p["id"]: p["revision"] for p in original["phases"]},
                "subject_revisions": {sid: 1 for sid in original["content"]["subjects"]},
            },
            jobs=[JobSpec("image", "local", "fake", step="fixture")],
            budget_limit=None,
        )
        candidate = operations.add_candidate(
            session, operation["jobs"][0]["id"], "asset", "test", "image"
        )
        ReviewService().create(
            session,
            purpose="segment_takes",
            candidate_ids=[candidate["id"]],
            context={"movie_id": original["movie_id"], "segment_id": original["id"]},
        )
        SelectionService().select(
            session,
            movie_id=original["movie_id"],
            segment_id=original["id"],
            candidate_id=candidate["id"],
            asset_id="asset",
            authority="user",
            expected_revision=0,
        )
        assert not selection_state(session, original["movie_id"], original["id"])["current"][
            "stale"
        ]
        before = tables(session, direction=False)
        result = split(service, session, original, 60)
        assert selection_state(session, original["movie_id"], original["id"])["current"]["stale"]
        assert (
            selection_state(session, original["movie_id"], result["segments"][1]["id"])["current"]
            is None
        )
        assert tables(session, direction=False) == before
        service.undo(session, result["receipt"]["id"])
        assert tables(session, direction=False) == before


def test_http_split_contract_and_typed_refusal(tmp_path):
    app = setup(tmp_path)
    with TestClient(app, base_url="http://localhost", headers=AUTH) as client:
        with app.state.workspace.db.transaction() as session:
            original = seed(DirectionService(), session)
        url = f"/api/direction/segments/{original['id']}"
        assert client.post(
            url + "/split", json={"expected_revision": original["revision"], "frame": 12}
        ).json() == {"code": "not_a_boundary", "detail": "Split at a cut or a phase boundary"}
        points = client.get(url + "/split-points").json()
        assert [p["frame"] for p in points] == [30, 45, 60, 80]
        response = client.post(
            url + "/split", json={"expected_revision": original["revision"], "frame": 60}
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert len(result["segments"]) == 2
        assert result["segments"][0]["cuts"][0]["movie_id"] == original["movie_id"]
        assert (
            client.post(f"/api/direction/receipts/{result['receipt']['id']}/undo").status_code
            == 200
        )
        assert not any("gaiavfx__api__" in name for name in app.openapi()["components"]["schemas"])


def test_split_rolls_back_if_receipt_write_fails(store, monkeypatch):
    db, service = store
    with db.transaction() as session:
        original = seed(service, session)
        before = tables(session)

    def fail(*args, **kwargs):
        raise RuntimeError("receipt failure")

    monkeypatch.setattr(service, "_split_receipt", fail)
    with pytest.raises(RuntimeError, match="receipt failure"), db.transaction() as session:
        split(service, session, original, 60)
    with db.transaction() as session:
        assert tables(session) == before


def test_cut_without_matching_phase_carries_active_direction_and_undo_removes_copy(store):
    db, service = store
    with db.transaction() as session:
        original = seed(service, session)
        phase = next(p for p in original["phases"] if p["start_frame"] == 60)
        service.remove_phase(session, phase["id"], original["revision"])
        original = service.read_segment(session, original["id"])
        before = tables(session, revisions=False, receipts=False)
        result = split(service, session, original, 60)
        first = result["segments"][1]["phases"][0]
        assert first["start_frame"] == 0
        assert first["content"] == next(
            p["content"] for p in original["phases"] if p["start_frame"] == 45
        )
        service.undo(session, result["receipt"]["id"])
        assert tables(session, revisions=False, receipts=False) == before


def test_nested_splits_must_be_undone_in_order_and_prove_restored_revisions(store):
    db, service = store
    with db.transaction() as session:
        original = seed(service, session)
        before = tables(session, revisions=False, receipts=False)
        first = split(service, session, original, 30)
        second = split(service, session, first["segments"][1], 30)
        with pytest.raises(ApplicationError, match="changed after"):
            service.undo(session, first["receipt"]["id"])
        service.undo(session, second["receipt"]["id"])
        service.undo(session, first["receipt"]["id"])
        assert tables(session, revisions=False, receipts=False) == before
