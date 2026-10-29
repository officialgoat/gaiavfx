"""Reusable place prose and receipt-bearing insertion wording use Direction invariants."""

import harness_support
import pytest

from gaiavfx.application.authored import load_survey, validate_insertion
from gaiavfx.application.prepare_references import survey_plan_for_place
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.shared.errors import ApplicationError

kernel = harness_support.kernel


def test_place_angle_text_revisions_and_survey_parity(kernel, monkeypatch):
    from harness_eval_tasks import authored

    db, service = kernel.db, DirectionService()
    source = authored("SV-C2B")
    monkeypatch.setattr(
        "gaiavfx.modules.direction.service.timestamp",
        lambda: "2026-09-30T12:00:00.000000+00:00",
    )
    with db.transaction() as session:
        place = service.create_place(session, "Courtyard")
        first = service.add_angle(session, place["id"], "opening")
        angles = [service.add_angle(session, place["id"], name) for name, _ in source["views"]]
        assert len({angle["created_at"] for angle in [first, *angles]}) == len(angles) + 1
        service.update_place(
            session,
            place["id"],
            1,
            description=source["what"],
            sound=source["sound"],
            overhead=source["overhead"],
        )
        service.update_angle(session, first["id"], 1, description=source["first_view"])
        for angle, (_, description) in zip(angles, source["views"], strict=True):
            service.update_angle(session, angle["id"], 1, description=description)
        service.set_plate(session, place["id"], asset_id="plate", angle_id=first["id"])
        expected = load_survey(
            {key: source[key] for key in ("what", "sound", "first_view", "overhead")}
            | {
                "first_angle_id": first["id"],
                "first_frame_asset": "plate",
                "views": [
                    [angle["id"], description]
                    for angle, (_, description) in zip(angles, source["views"], strict=True)
                ],
            }
        )
        assert survey_plan_for_place(session, place["id"], first_angle_id=first["id"]) == expected
        for method, record_id in (
            (service.update_place, place["id"]),
            (service.update_angle, first["id"]),
        ):
            with pytest.raises(ApplicationError) as stale:
                method(session, record_id, 1, description="lost")
            assert stale.value.status == 409
            with pytest.raises(ApplicationError) as empty:
                method(session, record_id, 2)
            assert empty.value.status == 422
        with pytest.raises(ApplicationError, match="plate shows angle opening"):
            survey_plan_for_place(session, place["id"], first_angle_id=angles[0]["id"])
        for field in ("description", "sound", "overhead"):
            current = service.read_place(session, place["id"])
            changed = service.update_place(session, place["id"], current["revision"], **{field: ""})
            with pytest.raises(ApplicationError, match=f"Place Courtyard has no {field}"):
                survey_plan_for_place(session, place["id"], first_angle_id=first["id"])
            service.update_place(
                session, place["id"], changed["revision"], **{field: current[field]}
            )
        service.update_angle(session, first["id"], 2, description="")
        with pytest.raises(ApplicationError, match="Angle opening has no description"):
            survey_plan_for_place(session, place["id"], first_angle_id=first["id"])
        unplated = service.create_place(session, "Unplated")
        a = service.add_angle(session, unplated["id"], "opening")
        service.update_angle(session, a["id"], 1, description="View")
        service.update_place(
            session, unplated["id"], 1, description="Place", sound="Air", overhead="Above"
        )
        with pytest.raises(ApplicationError, match="Place Unplated has no plate"):
            survey_plan_for_place(session, unplated["id"], first_angle_id=a["id"])


def test_cut_wording_receipt_validation_and_undo(kernel):
    from harness_eval_tasks import authored
    from test_direction_store import make_cut

    service = DirectionService()
    wording = {
        key: value
        for key, value in authored("IN-DZ").items()
        if key not in {"seed", "view", "subject"}
    }
    with kernel.db.transaction() as session:
        _, _, _, cut = make_cut(service, session)
        edited = service.update_cut(session, cut["id"], 1, insertion=wording)
        assert edited["insertion"] == validate_insertion(wording)
        assert edited["receipt"]["after"]["insertion"] == wording
        assert edited["receipt"]["before"]["insertion"] is None
        with pytest.raises(ApplicationError, match="behind is required for toward_camera"):
            service.update_cut(
                session,
                cut["id"],
                2,
                insertion=wording | {"facing": "toward_camera", "behind": None},
            )
        assert service.read_cut(session, cut["id"])["revision"] == 2
        service.undo(session, edited["receipt"]["id"])
        assert service.read_cut(session, cut["id"])["insertion"] is None
