"""Whole-window planning, reference lowering, compatibility and fake execution."""

import json

import pytest
import test_render_cut as fixtures
from fastapi.testclient import TestClient
from test_production_api import AUTH, setup, source_assets

from gaiavfx.application.authored import import_segment
from gaiavfx.application.direction_plans import plan_for_segment
from gaiavfx.application.render_cut import start_render
from gaiavfx.direction_cli import _dispatch, parser
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError

store = fixtures.store
media = fixtures.media


def window(session, service, *, cuts=2, continues=True, different_places=False):
    movie = service.create_movie(session, "Segment render")
    doc = fixtures.document()
    doc["continues"] = continues
    doc["cut_frames"] = [36, 72][:cuts]
    doc["shots"] = [doc["shots"][0]] * (cuts + 1)
    segment = import_segment(session, movie["id"], 1, doc)
    rows = []
    place = service.create_place(session, "Courtyard")
    angle = service.add_angle(session, place["id"], "Forward")
    target_angle = service.add_angle(session, place["id"], "Closer view")
    for i, frame in enumerate(doc["cut_frames"]):
        if i and different_places:
            place = service.create_place(session, "Other courtyard")
            target_angle = service.add_angle(session, place["id"], "Closer view")
        elif i:
            target_angle = angle
        row = service.read_segment(session, segment["id"])
        rows.append(
            service.create_cut(
                session,
                segment["id"],
                row["revision"],
                frame=frame,
                from_angle_id=rows[-1]["to_angle_id"] if rows else angle["id"],
                to_angle_id=target_angle["id"],
                relation="same_direction",
                relation_source="authored",
                crosses_barrier=False,
                receives_predecessor=True,
            )
        )
    return segment, rows


def pin_cuts(session, service, cuts):
    for i, cut in enumerate(cuts):
        service.update_cut(session, cut["id"], cut["revision"], override_technique="views_and_pin")
        evidence = service.record_evidence(session, cut["to_angle_id"], "imported", f"view-{i}")
        service.approve_evidence(session, evidence["id"])
        pin = service.record_keyframe(session, cut["id"], f"pin-{i}", evidence["id"])
        service.approve_keyframe(session, pin["id"])


@pytest.mark.usefixtures("media")
def test_two_cut_pins_share_one_sheet_at_the_first_needed_shot(store, monkeypatch):
    from gaiavfx.application import direction_plans

    db, service = store
    monkeypatch.setattr(direction_plans, "current_sheet", lambda *a: "sheet")
    monkeypatch.setattr(direction_plans, "sheet_staleness", lambda *a: ())
    monkeypatch.setattr(
        direction_plans,
        "sheet_reference",
        lambda *a: {"tiles": [], "panels": 3, "blank_panels": 0, "origin": "survey"},
    )
    with db.transaction() as session:
        segment, cuts = window(session, service)
        pin_cuts(session, service, cuts)
        plan = plan_for_segment(session, segment["id"], context_artifact="context")
        assert plan.ready, plan.refusals
        assert len(plan.cuts) == 2
        rendered = start_render(
            session,
            segment_id=segment["id"],
            seeds=[7],
            context_artifact="context",
            request_key="window",
        )
        # A cut is a compatibility alias for the entire segment, including idempotency.
        assert (
            start_render(
                session,
                cut_id=cuts[1]["id"],
                seeds=[7],
                context_artifact="context",
                request_key="window",
            )["id"]
            == rendered["id"]
        )
        graph = rendered["jobs"][0]["graph"]
        prompt = next(
            n["inputs"]["prompt"]
            for n in graph.values()
            if n["class_type"] == "MiniMaxH3ReferenceToVideo"
        )
        assert "[Shot 2]" in prompt and "[Shot 3]" in prompt
        assert prompt.count("the shot cuts.") == 2
        guides = [
            n["inputs"]["frame_idx"]
            for n in graph.values()
            if n["class_type"] == "MiniMaxH3AddGuide"
        ]
        context = rendered["jobs"][0]["snapshot"]["context_frames"]
        assert guides == [
            36 + context + plan.cuts[0].recommended.keyframe_offset_frames,
            72 + context + plan.cuts[1].recommended.keyframe_offset_frames,
        ]
        assert rendered["request"]["cut_ids"] == [c["id"] for c in cuts]
        assert list(rendered["jobs"][0]["inputs"]).count("sheet") == 1
        # A refusal on the second cut blocks the whole window.
        service.judge_suitability(
            session,
            cuts[1]["id"],
            reference_kind="pin",
            reference_id=service.keyframes(session, cuts[1]["id"])[0]["id"],
            verdict="unsuitable",
            note="Wrong side",
        )
        refused = plan_for_segment(session, segment["id"], context_artifact="context")
        assert not refused.ready
        assert any(
            cuts[1]["id"] in r.cut_ids and "Wrong side" in r.message for r in refused.refusals
        )
        # Messages name the cut as the timeline does, never by its id (usability B19).
        refusal = next(r for r in refused.refusals if cuts[1]["id"] in r.cut_ids)
        assert refusal.message.startswith("Cut 2 at f72: ")
        assert not any(c["id"] in r.message for c in cuts for r in refused.refusals)
        with pytest.raises(ApplicationError, match="Wrong side"):
            start_render(session, cut_id=cuts[0]["id"], seeds=[8], context_artifact="context")
        assert len(OperationService().recent(session)) == 1


@pytest.mark.usefixtures("media")
def test_different_sheets_name_both_cuts_and_rollback(store):
    db, service = store
    with db.transaction() as session:
        segment, cuts = window(session, service, different_places=True)
        pin_cuts(session, service, cuts)
        plan = plan_for_segment(session, segment["id"], context_artifact="context")
        refusal = next(r for r in plan.refusals if r.code == "several_sheets")
        assert refusal.cut_ids == tuple(c["id"] for c in cuts)
        assert refusal.message.startswith("Cut 1 at f36 and Cut 2 at f72 need different")
        assert not any(c["id"] in refusal.message for c in cuts)
        assert "split the segment" in refusal.message
        before = service.read_segment(session, segment["id"])
    with pytest.raises(ApplicationError, match="several_sheets"), db.transaction() as session:
        start_render(
            session, segment_id=segment["id"], seeds=[7], context_artifact="context", budget_limit=1
        )
    with db.transaction() as session:
        assert OperationService().recent(session) == []
        assert service.read_segment(session, segment["id"]) == before


@pytest.mark.parametrize("cut_count", [0, 2])
def test_segment_api_plan_render_and_target_validation(tmp_path, cut_count):
    app = setup(tmp_path)
    service = DirectionService()
    with TestClient(app, base_url="http://localhost") as client:
        assets, artifact = source_assets(app, tmp_path)
        with app.state.workspace.db.transaction() as session:
            segment, cuts = window(session, service, cuts=cut_count)
            service.update_subject(session, "s1", 1, identity_asset_id=assets[0])
        endpoint = f"/api/direction/segments/{segment['id']}/plan"
        plan = client.get(endpoint, headers=AUTH).json()
        assert plan["technique_id"] == ("single_take" if not cuts else "text") and not plan["ready"]
        assert plan["refusals"][0]["code"] == "continuation_source"
        plan = client.get(endpoint, params={"context_artifact": artifact}, headers=AUTH).json()
        assert plan["ready"] and len(plan["cuts"]) == cut_count
        body = {
            "segment_id": segment["id"],
            "seeds": [13],
            "context_artifact": artifact,
            "request_key": "zero",
        }
        for invalid in ({"seeds": [1]}, body | {"cut_id": "cut"}, body | {"segment_id": ""}):
            assert (
                client.post("/api/production/renders", headers=AUTH, json=invalid).status_code
                == 422
            )
        response = client.post("/api/production/renders", headers=AUTH, json=body)
        assert response.status_code == 201, response.text
        repeated = client.post("/api/production/renders", headers=AUTH, json=body)
        assert repeated.status_code == 200 and repeated.json()["id"] == response.json()["id"]
        if cuts:
            alias = {**body, "cut_id": cuts[-1]["id"]}
            del alias["segment_id"]
            response = client.post("/api/production/renders", headers=AUTH, json=alias)
            assert response.status_code == 200 and response.json()["id"] == repeated.json()["id"]
        with app.state.workspace.db.transaction() as session:
            operation = OperationService().operation(session, response.json()["id"])
            graph = operation["jobs"][0]["graph"]
            assert "motion_context" in graph
            if not cuts:
                assert "single continuous take" in json.dumps(graph).lower()
            assert operation["request"]["cut_ids"] == [c["id"] for c in cuts]


@pytest.mark.parametrize("target", ["--segment-id", "--cut-id"])
def test_cli_accepts_each_render_target(target):
    args = parser().parse_args(
        ["--library", "unused", "render", "start", target, "target", "--seeds", "1"]
    )
    assert getattr(args, target[2:].replace("-", "_")) == "target"
    with pytest.raises(SystemExit):
        parser().parse_args(
            [
                "--library",
                "unused",
                "render",
                "start",
                "--segment-id",
                "s",
                "--cut-id",
                "c",
                "--seeds",
                "1",
            ]
        )


@pytest.mark.usefixtures("media")
def test_cli_dispatches_segment_render(store):
    db, service = store
    with db.transaction() as session:
        segment, _ = window(session, service, cuts=0)
        args = parser().parse_args(
            [
                "--library",
                "unused",
                "render",
                "start",
                "--segment-id",
                segment["id"],
                "--seeds",
                "7,8",
                "--context-artifact",
                "context",
            ]
        )
        operation = _dispatch(service, session, args)
        assert operation["request"]["segment_id"] == segment["id"]
        assert operation["request"]["cut_ids"] == []
        assert len(operation["jobs"]) == 2
