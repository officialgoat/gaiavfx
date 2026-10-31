"""Persisted render inputs, continuation selection and import rollback."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_direction_content import assemble_records, extended_document

from gaiavfx.application.authored import import_segment, segment_records
from gaiavfx.application.render_cut import start_render
from gaiavfx.modules.editing.selections.service import SelectionService
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.errors import ApplicationError


def document():
    data = json.loads((Path(__file__).parent / "fixtures/parity/authored/DZ.json").read_text())
    segment = data["segment"]
    segment["subjects"][0]["identity_asset"] = "identity"
    return segment


def add_cut(service, session, segment):
    place = service.create_place(session, "Courtyard")
    angle = service.add_angle(session, place["id"], "Forward")
    target_angle = service.add_angle(session, place["id"], "Closer view")
    return service.create_cut(
        session,
        segment["id"],
        segment["revision"],
        frame=36,
        from_angle_id=angle["id"],
        to_angle_id=target_angle["id"],
        relation="same_direction",
        crosses_barrier=False,
        relation_source="authored",
        receives_predecessor=True,
    )


def test_import_reuses_cast_and_rolls_back_failed_commands(store):
    db, service = store
    data = document()
    with db.transaction() as session:
        movie = service.create_movie(session, "parity")
        first = import_segment(session, movie["id"], 1, data)
        assert first["content_schema"] == 1
        assert all(phase["content_schema"] == 1 for phase in first["phases"])
        second = import_segment(session, movie["id"], 3, data)
        # Each import is one authored intent: its new cast member and its segment are one Receipt.
        imported = [
            r for r in service.receipts(session, movie["id"]) if r["command"] == "add_segment"
        ]
        assert [r["id"] for r in imported] == [second["receipt"]["id"], first["receipt"]["id"]]
        assert imported[-1]["before"]["revision"] == 1
        assert len(service.subjects(session, movie["id"])) == 1
        assert len(service.read_movie(session, movie["id"])["segments"]) == 2
        before = service.read_movie(session, movie["id"])
        receipts = service.receipts(session, movie["id"])
    changed = deepcopy(data)
    changed["subjects"][0]["keeps"] = "different"
    # A new subject is inserted before the conflicting one: the caller's transaction rolls it back.
    changed["subjects"].insert(0, {**changed["subjects"][0], "id": "new"})
    for payload, revision, message in (
        (changed, 4, "already defined differently"),
        (data, 3, "stale"),
    ):
        with pytest.raises(ApplicationError, match=message) as error, db.transaction() as session:
            import_segment(session, movie["id"], revision, payload)
        assert error.value.status == 409
        with db.transaction() as session:
            assert service.read_movie(session, movie["id"]) == before
            assert service.receipts(session, movie["id"]) == receipts
    with db.transaction() as session:
        other = service.create_movie(session, "other")
    with (
        pytest.raises(ApplicationError, match="Subject id s1 already exists"),
        db.transaction() as session,
    ):
        import_segment(session, other["id"], 1, data)
    with pytest.raises(ApplicationError), db.transaction() as session:
        import_segment(session, movie["id"], 4, data, at=99)
    with db.transaction() as session:
        assert service.read_movie(session, movie["id"]) == before


def test_schema_two_import_persists_timed_content_and_mixed_phase_versions(store):
    db, service = store
    data = extended_document()
    del data["shots"][1]["cast"]
    expected = assemble_records(segment_records(data), data["cut_frames"])
    with db.transaction() as session:
        movie = service.create_movie(session, "timed")
        imported = import_segment(session, movie["id"], 1, data)
        assert imported["content_schema"] == 2
        assert [phase["content_schema"] for phase in imported["phases"]] == [2, 1, 2]
        add_cut(service, session, imported)
        assert service.assemble_segment(session, imported["id"]) == expected
        before = service.read_segment(session, imported["id"])
    with db.transaction() as session:
        assert service.assemble_segment(session, imported["id"]) == expected
        assert service.read_segment(session, imported["id"]) == before
        assert service.read_subject(session, "s1")["identity_asset_id"] is None


@pytest.mark.usefixtures("media")
@pytest.mark.parametrize("authority", ["user", "provisional"])
def test_predecessor_selection_and_immutable_request(store, authority):
    db, service = store
    operations = OperationService()
    selections = SelectionService()
    with db.transaction() as session:
        movie = service.create_movie(session, "parity")
        first = import_segment(session, movie["id"], 1, document())
        cut = add_cut(service, session, first)
        first_render = start_render(
            session, cut_id=cut["id"], seeds=[1], context_artifact="imported"
        )
        assert first_render["request"]["context_source"] == {"kind": "artifact"}
        second = import_segment(session, movie["id"], 3, document())
        second_cut = add_cut(service, session, second)
        candidate = operations.add_candidate(
            session, first_render["jobs"][0]["id"], "take", "video", "video"
        )
        with pytest.raises(ApplicationError, match="Select a take of segment 1 first"):
            start_render(session, cut_id=second_cut["id"], seeds=[2])
        selection = selections.select(
            session,
            movie_id=movie["id"],
            segment_id=first["id"],
            candidate_id=candidate["id"],
            asset_id="take",
            authority=authority,
            basis="unjudged" if authority == "provisional" else None,
            expected_revision=0,
        )
        with pytest.raises(ApplicationError, match="captured no continuation context"):
            start_render(session, cut_id=second_cut["id"], seeds=[2])
        operations.link_artifact(session, candidate["id"], "captured")
        render = start_render(session, cut_id=second_cut["id"], seeds=[2])
        request = render["request"]
        assert request["context_artifact"] == "captured"
        assert request["context_source"] == {
            "kind": "selection",
            "segment_id": first["id"],
            "selection_id": selection["id"],
            "candidate_id": candidate["id"],
            "authority": authority,
            "basis": "unjudged" if authority == "provisional" else None,
        }
        assert request["segment"] == document()
        assert request["segment_revision"] == 2
        assert request["phase_revisions"] == {row["id"]: 1 for row in second["phases"]}
        assert request["subject_revisions"] == {"s1": 1}
        explicit = start_render(
            session, cut_id=second_cut["id"], seeds=[3], context_artifact="override"
        )
        assert explicit["request"]["context_source"] == {"kind": "artifact"}
        assert explicit["request"]["context_artifact"] == "override"
        service.update_subject(session, "s1", 1, keeps="new coat")
        service.update_phase(
            session,
            second["phases"][0]["id"],
            1,
            content={"description": "Wait.", "screen_direction": "", "sound": ""},
        )
        service.update_segment(session, second["id"], 2, delivered_frames=126)
        assert operations.operation(session, render["id"])["request"] == request
        assert selections.current(session, movie["id"], second["id"]) is None
        # Reordering changes the predecessor instead of retaining an old continuation link.
        service.move_segment(session, second["id"], 4, ordinal=0)
        with pytest.raises(ApplicationError, match="first segment has no predecessor"):
            start_render(session, cut_id=second_cut["id"], seeds=[4])


@pytest.mark.usefixtures("media")
def test_split_continuation_needs_a_current_take_of_its_predecessor(store):
    """Usability C14 (T8-F1): a split keeps the takes with the first segment and marks them
    changed, so the new continuation waits for a current take instead of reading ready."""
    from gaiavfx.application.direction_plans import plan_for_segment
    from gaiavfx.application.selected_takes import selection_state

    db, service = store
    operations = OperationService()
    selections = SelectionService()

    def select_new_take(segment_id, seed, expected_revision):
        render = start_render(
            session, segment_id=segment_id, seeds=[seed], context_artifact="imported"
        )
        candidate = operations.add_candidate(
            session, render["jobs"][0]["id"], f"take-{seed}", "video", "video"
        )
        operations.link_artifact(session, candidate["id"], f"captured-{seed}")
        selections.select(
            session,
            movie_id=movie["id"],
            segment_id=segment_id,
            candidate_id=candidate["id"],
            asset_id=f"take-{seed}",
            authority="user",
            expected_revision=expected_revision,
        )

    with db.transaction() as session:
        movie = service.create_movie(session, "split")
        first = import_segment(session, movie["id"], 1, document())
        add_cut(service, session, first)
        select_new_take(first["id"], 1, 0)
        first = service.read_segment(session, first["id"])
        result = service.split_segment(session, first["id"], first["revision"], 36)
        second = result["segments"][1]
        assert second["continues"] and second["ordinal"] == 1
        assert selection_state(session, movie["id"], first["id"])["current"]["stale"]
        plan = plan_for_segment(session, second["id"])
        stale = "Select a current take of segment 1 first."
        assert not plan.ready
        assert [r.message for r in plan.refusals if r.code == "continuation_source"] == [stale]
        with pytest.raises(ApplicationError, match=f"continuation_source: {stale}") as error:
            start_render(session, segment_id=second["id"], seeds=[2])
        assert error.value.status == 422
        assert len(operations.recent(session, kind="render")) == 1
        # An explicit continuation artifact still bypasses the predecessor's selection.
        assert plan_for_segment(session, second["id"], context_artifact="imported").ready
        # A current take of the first segment unblocks the continuation.
        select_new_take(first["id"], 3, 1)
        assert not selection_state(session, movie["id"], first["id"])["current"]["stale"]
        plan = plan_for_segment(session, second["id"])
        assert plan.ready, plan.refusals
        render = start_render(session, segment_id=second["id"], seeds=[4])
        assert render["request"]["context_artifact"] == "captured-3"


@pytest.mark.usefixtures("media")
def test_render_refusals_leave_no_operations(store, monkeypatch):
    db, service = store
    with db.transaction() as session:
        movie = service.create_movie(session, "parity")
        segment = import_segment(session, movie["id"], 1, document())
        cut = add_cut(service, session, segment)
        with pytest.raises(ApplicationError, match="first segment has no predecessor") as error:
            start_render(session, cut_id=cut["id"], seeds=[1])
        assert error.value.status == 422
        monkeypatch.setattr(MediaLibrary, "artifact", lambda *args: {"kind": "other"})
        with pytest.raises(ApplicationError, match="must be H3 context"):
            start_render(session, cut_id=cut["id"], seeds=[1], context_artifact="bad")
        # Missing identity is reported with its authored subject id, not as an unrelated 404.
        monkeypatch.undo()
        with pytest.raises(
            ApplicationError, match="Unknown identity asset identity for subject s1"
        ) as error:
            start_render(session, cut_id=cut["id"], seeds=[1], context_artifact="unused")
        assert error.value.status == 422
        service.update_phase(session, segment["phases"][1]["id"], 1, start_frame=48)
        with pytest.raises(ApplicationError, match="Cut frames without phases:.*36"):
            start_render(session, cut_id=cut["id"], seeds=[1], context_artifact="unused")
        assert OperationService().recent(session) == []


def test_compiler_gaps_report_structure_without_rendering(store):
    from gaiavfx.application.shot_references import cut_gaps as compiler_gaps

    db, service = store
    with db.transaction() as session:
        movie = service.create_movie(session, "gaps")
        segment = import_segment(session, movie["id"], 1, document())
        cut = add_cut(service, session, segment)
        assert compiler_gaps(session, cut["id"]) == []
        service.update_phase(session, segment["phases"][1]["id"], 1, start_frame=48)
        result = compiler_gaps(session, cut["id"])
        assert result[0]["code"] == "structure"
        assert "Cut frames without phases" in result[0]["message"]
        assert OperationService().recent(session) == []


def test_compiler_gaps_use_render_references_and_sheet_capability(store, sheets):
    from gaiavfx.application import shot_references

    db, service = store
    with db.transaction() as session:
        movie = service.create_movie(session, "sheet gaps")
        segment = import_segment(session, movie["id"], 1, document())
        place = service.create_place(session, "Courtyard")
        source = service.add_angle(session, place["id"], "gate")
        target = service.add_angle(session, place["id"], "reverse")
        cut = service.create_cut(
            session,
            segment["id"],
            segment["revision"],
            frame=36,
            from_angle_id=source["id"],
            to_angle_id=target["id"],
            relation="reverse",
            crosses_barrier=True,
            relation_source="authored",
            receives_predecessor=True,
        )
        ev = service.record_evidence(session, target["id"], "imported", "view")
        service.approve_evidence(session, ev["id"])
        pin = service.record_keyframe(session, cut["id"], "pin", ev["id"])
        service.approve_keyframe(session, pin["id"])
        subject = service.add_subject(
            session,
            movie["id"],
            3,
            name="Bea",
            description="A walker",
            keeps=["coat"],
            identity_asset_id="second-identity",
        )
        row = service.read_segment(session, segment["id"])
        service.update_segment(
            session,
            row["id"],
            row["revision"],
            content={**row["content"], "subjects": ["s1", subject["id"]]},
        )
        sheets(panels=2)
        result = shot_references.cut_gaps(session, cut["id"])
        assert "sheet_several_subjects" in {gap["code"] for gap in result}
        assert all(set(gap) == {"code", "message"} for gap in result)
        assert OperationService().recent(session) == []


@pytest.mark.usefixtures("media")
@pytest.mark.parametrize("edit", ["cut", "opening"])
def test_a_take_records_its_cuts_and_opening_and_an_edit_to_them_changes_it(store, edit):
    """A take's basis covers its cuts and the angle it opens on: Program and Export call it changed after such an
    edit, and current again once the edit is undone."""
    from gaiavfx.application import take_basis
    from gaiavfx.application.selected_takes import selection_state

    db, service = store
    operations = OperationService()
    with db.transaction() as session:
        movie = service.create_movie(session, "basis")
        segment = import_segment(session, movie["id"], 1, document())
        cut = add_cut(service, session, segment)
        segment = service.read_segment(session, segment["id"])
        render = start_render(
            session, segment_id=segment["id"], seeds=[1], context_artifact="imported"
        )
        assert render["request"]["cut_revisions"] == {cut["id"]: cut["revision"]}
        assert render["request"]["opening_angle_id"] == segment["opening_angle_id"]
        assert render["request"]["pins"] == {}
        candidate = operations.add_candidate(
            session, render["jobs"][0]["id"], "take", "video", "video"
        )
        SelectionService().select(
            session,
            movie_id=movie["id"],
            segment_id=segment["id"],
            candidate_id=candidate["id"],
            asset_id="take",
            authority="user",
            expected_revision=0,
        )

        def reasons():
            program = selection_state(session, movie["id"], segment["id"])["current"]
            # Export judges the selected take with the same function.
            assert (
                take_basis.judge(session, segment["id"])(candidate["id"])
                == program["stale_reasons"]
            )
            return program["stale_reasons"]

        assert reasons() == []
        cut = service.read_cut(session, cut["id"])
        if edit == "cut":
            changed = service.update_cut(
                session,
                cut["id"],
                cut["revision"],
                relation="reverse",
                crosses_barrier=False,
                relation_source="authored",
            )
            expected = "Cuts changed since this take."
        else:
            place = service.read_angle(session, cut["from_angle_id"])["place_id"]
            wide = service.add_angle(session, place, "Wide")
            changed = service.update_cut(
                session, cut["id"], cut["revision"], from_angle_id=wide["id"]
            )
            assert service.read_segment(session, segment["id"])["opening_angle_id"] == wide["id"]
            expected = "Opening angle changed since this take."
        assert expected in reasons()
        service.undo(session, changed["receipt"]["id"])
        assert reasons() == []
