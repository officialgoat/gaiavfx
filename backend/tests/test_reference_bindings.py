"""Current reference bindings, independent human judgments and rollback."""

from dataclasses import replace

import pytest
import test_direction_store as direction_fixtures
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_direction_store import make_cut
from test_production_api import AUTH, run_idle, setup, source_assets

from gaiavfx.application.direction_plans import plan_for_cut, plan_to_dict
from gaiavfx.application.prepare_references import (
    approve_pin,
    approve_view,
    assemble_sheet,
    current_sheet,
    sheet_staleness,
    sheet_tiles_for_place,
)
from gaiavfx.application.render_cut import start_render
from gaiavfx.application.sheet_recipes import sheet_reference
from gaiavfx.modules.direction.coverage import Coverage, assess_cut
from gaiavfx.modules.direction.persistence import (
    EvidenceRecord,
    KeyframeRecord,
    SuitabilityRecord,
)
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.prescreen import evaluate
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError

store = direction_fixtures.store

WORDING = {
    "who": "Arin",
    "looks": "a walker",
    "pronouns": "he",
    "facing": "toward_camera",
    "plate": "a courtyard",
    "keep": "the railing",
    "where": "in the courtyard",
    "size": "one third",
    "behind": "the wall",
}


def references(service, session, *, view_asset="view", pin_asset="pin", subject_asset=None):
    place, source, target, cut = make_cut(service, session)
    evidence = service.record_evidence(session, target["id"], "imported", view_asset)
    evidence = service.approve_evidence(session, evidence["id"])
    pin = service.record_keyframe(session, cut["id"], pin_asset, evidence["id"], subject_asset)
    pin = service.approve_keyframe(session, pin["id"])
    return place, source, target, cut, evidence, pin


def binding_map(service, session, cut):
    return {b.kind: b for b in service.context_and_available(session, cut["id"])[2]}


def test_view_inputs_stale_dependencies_and_reapproval(store):
    db, service = store
    with db.transaction() as session:
        place, _, target, cut, view, pin = references(service, session)
        assert view["inputs"] == {
            "plate_asset_id": None,
            "plate_angle_id": None,
            "place": {"description": "", "sound": "", "overhead": ""},
            "angle": {"description": ""},
        }
        assert pin["inputs"] == {
            "evidence_id": view["id"],
            "to_angle_id": target["id"],
            "insertion": None,
            "subject_asset_id": None,
        }
        initial = binding_map(service, session, cut)
        assert not any(b.stale for b in initial.values())
        assert initial == binding_map(service, session, cut)
        service.update_angle(session, target["id"], 1, description="The reverse view")
        context, available, bindings = service.context_and_available(session, cut["id"])
        assert all(b.stale for b in bindings)
        assert "the angle's description changed since approval" in bindings[0].reasons
        assert assess_cut(context).coverage == Coverage.NEW
        assert available.place_views == 0 and not available.approved_keyframe_cuts
        plan = plan_for_cut(session, cut["id"])
        assert not plan.recommended.ready
        action = next(a for a in plan.recommended.actions if a.action == "insert_subject")
        assert "stale" in action.reason and "angle's description changed" in action.reason
        assert plan_to_dict(plan)["bindings"][1]["stale"] is True
        with pytest.raises(ApplicationError, match="stale.*angle's description changed") as error:
            start_render(session, cut_id=cut["id"], seeds=[1])
        assert error.value.status == 422
        refreshed = service.approve_evidence(session, view["id"])
        assert refreshed["inputs"]["angle"]["description"] == "The reverse view"
        assert not any(b.stale for b in binding_map(service, session, cut).values())
        assert service.keyframes(session, cut["id"])[0] == pin  # no mutation of dependent history
        with pytest.raises(ApplicationError) as error:
            service.approve_evidence(session, view["id"])
        assert error.value.status == 409
        for field in ("description", "sound", "overhead"):
            current = service.read_place(session, place["id"])
            service.update_place(session, place["id"], current["revision"], **{field: "changed"})
            assert (
                f"the place's {field} changed since approval"
                in binding_map(service, session, cut)["view"].reasons
            )
            service.approve_evidence(session, view["id"])
        service.set_plate(session, place["id"], asset_id="plate", angle_id=target["id"])
        assert binding_map(service, session, cut)["view"].stale
        assert service.approve_evidence(session, view["id"])["inputs"]["plate_asset_id"] == "plate"


def test_pin_wording_target_and_subject_staleness(store):
    db, service = store
    with db.transaction() as session:
        place, _, _, cut, _, pin = references(service, session)
        cut = service.update_cut(session, cut["id"], cut["revision"], insertion=WORDING)
        bindings = binding_map(service, session, cut)
        assert not bindings["view"].stale
        assert bindings["pin"].reasons == ("the insertion wording changed",)
        refreshed = service.approve_keyframe(session, pin["id"])
        assert refreshed["inputs"]["insertion"] == WORDING
        assert not binding_map(service, session, cut)["pin"].stale
        with pytest.raises(ApplicationError) as error:
            service.approve_keyframe(session, pin["id"])
        assert error.value.status == 409
        target = service.add_angle(session, place["id"], "Closer reverse")
        cut = service.update_cut(session, cut["id"], cut["revision"], to_angle_id=target["id"])
        _, available, bindings = service.context_and_available(session, cut["id"])
        assert not available.approved_keyframe_cuts
        assert len(bindings) == 1 and bindings[0].kind == "pin"
        assert "the cut's target angle changed" in bindings[0].reasons
        # Old pin remains an immutable reference to its source evidence.
        assert service.keyframes(session, cut["id"])[0] == refreshed


def test_approve_pin_records_inserted_subject_and_reapproves(store):
    db, service = store
    with db.transaction() as session:
        _, _, target, cut = make_cut(service, session)
        segment = service.read_segment(session, cut["segment_id"])
        subject = service.read_subject(session, segment["content"]["subjects"][0])
        subject = service.update_subject(
            session, subject["id"], subject["revision"], identity_asset_id="identity"
        )
        view = service.record_evidence(session, target["id"], "survey_view", "view")
        service.approve_evidence(session, view["id"])
        operations = OperationService()
        op = operations.create(
            session,
            kind="insert",
            request={"cut_id": cut["id"], "evidence_id": view["id"], "subject_asset": "identity"},
            jobs=[JobSpec("image", "local", "minimax_h3", step="fit_frame")],
            budget_limit=None,
        )
        candidate = operations.add_candidate(session, op["jobs"][0]["id"], "pin", "fitted", "image")
        pin = approve_pin(session, candidate_id=candidate["id"])
        assert pin["subject_asset_id"] == pin["inputs"]["subject_asset_id"] == "identity"
        assert not binding_map(service, session, cut)["pin"].stale
        service.update_cut(session, cut["id"], cut["revision"], insertion=WORDING)
        assert approve_pin(session, candidate_id=candidate["id"])["inputs"]["insertion"] == WORDING
        service.update_subject(
            session, subject["id"], subject["revision"], identity_asset_id="new-identity"
        )
        assert (
            "the subject's identity picture changed"
            in binding_map(service, session, cut)["pin"].reasons
        )


def test_null_approval_inputs_are_legacy_fresh(store):
    db, service = store
    with db.transaction() as session:
        _, _, target, cut, view, pin = references(service, session)
        session.get(EvidenceRecord, view["id"]).inputs = None
        session.get(KeyframeRecord, pin["id"]).inputs = None
        service.update_angle(session, target["id"], 1, description="changed")
        bindings = binding_map(service, session, cut)
        assert all(
            not b.stale and b.reasons == ("inputs were not recorded at approval",)
            for b in bindings.values()
        )


def test_judgment_is_separate_superseded_and_invalidated_by_new_approval(store):
    db, service = store
    with db.transaction() as session:
        _, source, target, cut, view, pin = references(service, session)
        before = service.read_evidence(session, view["id"])
        judgment = service.judge_suitability(
            session,
            cut["id"],
            reference_kind="view",
            reference_id=view["id"],
            verdict="unsuitable",
            note="looks against this camera",
        )
        assert service.read_evidence(session, view["id"]) == before
        plan = plan_for_cut(session, cut["id"])
        assert not plan.recommended.ready
        assert plan.bindings[0].suitability == "unsuitable"
        assert any("looks against this camera" in a.reason for a in plan.recommended.actions)
        with pytest.raises(
            ApplicationError, match="unsuitable.*looks against this camera"
        ) as error:
            start_render(session, cut_id=cut["id"], seeds=[1])
        assert error.value.status == 422
        next_judgment = service.judge_suitability(
            session,
            cut["id"],
            reference_kind="view",
            reference_id=view["id"],
            verdict="suits",
            note="The entrance fits this shot",
        )
        assert service.suitability(session, cut["id"])[0]["id"] == next_judgment["id"]
        assert session.get(SuitabilityRecord, judgment["id"]).superseded_at is not None
        assert plan_for_cut(session, cut["id"]).recommended.ready
        service.update_angle(session, target["id"], 1, description="different")
        service.approve_evidence(session, view["id"])
        assert service.suitability(session, cut["id"])[0]["stale"] is True
        assert binding_map(service, session, cut)["view"].suitability == "unjudged"
        assert (
            binding_map(service, session, cut)["view"].suitability_note
            == "The entrance fits this shot"
        )
        assert session.get(SuitabilityRecord, next_judgment["id"]).verdict == "suits"
        other = service.record_evidence(session, source["id"], "imported", "other")
        _, _, _, other_cut = make_cut(service, session)
        with pytest.raises(ApplicationError) as error:
            service.judge_suitability(
                session,
                other_cut["id"],
                reference_kind="pin",
                reference_id=pin["id"],
                verdict="suits",
            )
        assert error.value.status == 422
        for kind, reference_id, verdict, note in (
            ("view", other["id"], "suits", ""),
            ("other", pin["id"], "suits", ""),
            ("pin", pin["id"], "maybe", ""),
            ("pin", pin["id"], "suits", "x" * 501),
        ):
            count = len(list(session.scalars(select(SuitabilityRecord))))
            with pytest.raises(ApplicationError) as error:
                service.judge_suitability(
                    session,
                    cut["id"],
                    reference_kind=kind,
                    reference_id=reference_id,
                    verdict=verdict,
                    note=note,
                )
            assert error.value.status == 422
            assert len(list(session.scalars(select(SuitabilityRecord)))) == count


def test_unapproved_judgment_does_not_approve_and_transaction_rolls_back(store):
    db, service = store
    with db.transaction() as session:
        _, _, target, cut = make_cut(service, session)
        view = service.record_evidence(session, target["id"], "imported", "view")
        operations = OperationService()
        op = operations.create(
            session,
            kind="test",
            request={},
            jobs=[JobSpec("image", "local", "test", step="test")],
            budget_limit=None,
        )
        candidate = operations.add_candidate(session, op["jobs"][0]["id"], "view", "image", "image")
    with pytest.raises(RuntimeError), db.transaction() as session:
        operations.record_prescreen(
            session,
            candidate["id"],
            {
                "version": 1,
                "measured_at": "2026-10-02T00:00:00+00:00",
                "checks": evaluate("image", None, {"kind": "image"}),
            },
        )
        service.judge_suitability(
            session, cut["id"], reference_kind="view", reference_id=view["id"], verdict="suits"
        )
        assert not service.read_evidence(session, view["id"])["approved"]
        assert service.read_evidence(session, view["id"])["inputs"] is None
        service.approve_evidence(session, view["id"])
        assert service.suitability(session, cut["id"])[0]["stale"]
        raise RuntimeError("rollback all three commands")
    with db.transaction() as session:
        assert operations.candidate(session, candidate["id"])["prescreen"] is None
        assert service.read_evidence(session, view["id"]) == view
        assert service.suitability(session, cut["id"]) == []


def test_sheets_suitability_and_render_through_api(tmp_path):
    app = setup(tmp_path / "library")
    with TestClient(app, base_url="http://localhost", headers=AUTH) as client:
        assets, artifact = source_assets(app, tmp_path)
        db = app.state.workspace.db
        service = DirectionService(sheet_reader=sheet_reference)
        with db.transaction() as session:
            place, source, target, cut, view, _ = references(
                service, session, view_asset=assets[0], pin_asset=assets[1]
            )
            segment = service.read_segment(session, cut["segment_id"])
            subject = service.read_subject(session, segment["content"]["subjects"][0])
            service.update_subject(
                session, subject["id"], subject["revision"], identity_asset_id=assets[2]
            )
            assemble_sheet(
                session,
                place_id=place["id"],
                tiles=sheet_tiles_for_place(session, place["id"]),
                origin="survey",
            )
        run_idle(app)
        with db.transaction() as session:
            sheet = current_sheet(session, place["id"])
            assert sheet is not None
            # Unjudged references are renderable.
            assert (
                start_render(
                    session,
                    cut_id=cut["id"],
                    seeds=[1],
                    sheet_asset=sheet,
                    context_artifact=artifact,
                )["state"]
                == "queued"
            )
        route = f"/api/direction/cuts/{cut['id']}/suitability"
        verdict = {
            "reference_kind": "sheet",
            "reference_id": sheet,
            "verdict": "unsuitable",
            "note": "This sheet hides the entrance",
        }
        response = client.post(route, json=verdict)
        assert response.status_code == 201, response.text
        assert not response.json()["stale"]
        assert client.get(route).json() == [response.json()]
        plan = client.get(f"/api/direction/cuts/{cut['id']}/plan").json()
        assert plan["bindings"][-1]["suitability"] == "unsuitable"
        assert not plan["recommended"]["ready"]
        render = {
            "cut_id": cut["id"],
            "seeds": [2],
            "sheet_asset": sheet,
            "context_artifact": artifact,
        }
        response = client.post("/api/production/renders", json=render)
        assert response.status_code == 422 and "hides the entrance" in response.text
        assert client.post(route, json=verdict | {"verdict": "suits"}).status_code == 201
        assert client.post(route, json=verdict | {"reference_id": assets[0]}).status_code == 422
        assert client.post(route, json=verdict | {"reference_id": "missing"}).status_code == 404
        assert client.post(route, json=verdict | {"verdict": "maybe"}).status_code == 422
        assert (
            client.post("/api/direction/cuts/missing/suitability", json=verdict).status_code == 404
        )
        assert client.get("/api/direction/cuts/missing/suitability").status_code == 404
        with db.transaction() as session:
            # The arrangement is the assembly's choice (the research sheets differ in order).
            tiles = sheet_tiles_for_place(session, place["id"])
            assert not sheet_staleness(session, place["id"], list(reversed(tiles)))
            other = service.record_evidence(session, source["id"], "imported", assets[2])
            service.approve_evidence(session, other["id"])
            assert current_sheet(session, place["id"]) is None
            binding = plan_for_cut(session, cut["id"]).bindings[-1]
            assert binding.stale and "added or changed" in binding.reasons[0]
            with pytest.raises(ApplicationError, match="sheet is stale") as error:
                start_render(session, **render)
            assert error.value.status == 422
            # Cancel the earlier render; this test runs only the local sheet job.
            for op in OperationService().recent(session, kind="render"):
                OperationService().request_cancel(session, op["id"])
            assemble_sheet(
                session,
                place_id=place["id"],
                tiles=sheet_tiles_for_place(session, place["id"]),
                origin="survey",
            )
        run_idle(app)
        with db.transaction() as session:
            assert current_sheet(session, place["id"]) != sheet
            assert not plan_for_cut(session, cut["id"]).bindings[-1].stale
            # Explicitly supplying the old sheet is refused even when a fresh one exists.
            with pytest.raises(ApplicationError, match="sheet is stale"):
                start_render(session, **render)
        with db.transaction() as session:
            service.update_angle(session, target["id"], 1, description="new wording")
        plan = client.get(f"/api/direction/cuts/{cut['id']}/plan").json()
        assert next(b for b in plan["bindings"] if b["kind"] == "pin")["stale"]
        approved = client.post(f"/api/direction/evidence/{view['id']}/approve")
        assert (
            approved.status_code == 200
            and approved.json()["inputs"]["angle"]["description"] == "new wording"
        )
        assert client.post(f"/api/direction/evidence/{view['id']}/approve").status_code == 409


def test_survey_view_reapproval_routes_through_direction(store):
    db, service = store
    with db.transaction() as session:
        place, _, target, _, view, _ = references(service, session)
        operations = OperationService()
        operation = operations.create(
            session,
            kind="survey",
            request={"place_id": place["id"]},
            jobs=[JobSpec("image", "local", "minimax_h3", step="extract_frames")],
            budget_limit=None,
        )
        candidate = operations.add_candidate(
            session, operation["jobs"][0]["id"], view["asset_id"], target["id"], "image"
        )
        service.update_angle(session, target["id"], 1, description="new wording")
        assert (
            approve_view(session, candidate_id=candidate["id"])["inputs"]["angle"]["description"]
            == "new wording"
        )
        with pytest.raises(ApplicationError) as error:
            approve_view(session, candidate_id=candidate["id"])
        assert error.value.status == 409


def test_coverage_ignores_only_stale_approved_evidence(store):
    db, service = store
    with db.transaction() as session:
        _, _, _, cut, _, _ = references(service, session)
        context, _, _ = service.context_and_available(session, cut["id"])
        assert assess_cut(context).coverage == Coverage.ESTABLISHED
        assert (
            assess_cut(
                replace(context, evidence=tuple(replace(e, stale=True) for e in context.evidence))
            ).coverage
            == Coverage.NEW
        )


def test_suitability_history_prevents_cut_deletion_by_undo(store):
    db, service = store
    with db.transaction() as session:
        _, _, target, cut = make_cut(service, session)
        view = service.record_evidence(session, target["id"], "imported", "view")
        service.judge_suitability(
            session, cut["id"], reference_kind="view", reference_id=view["id"], verdict="suits"
        )
        before = service.read_segment(session, cut["segment_id"])
        with pytest.raises(ApplicationError, match="suitability history") as error:
            service.undo(session, cut["receipt"]["id"])
        assert error.value.status == 409
        assert service.read_segment(session, cut["segment_id"]) == before
        assert len(service.suitability(session, cut["id"])) == 1
