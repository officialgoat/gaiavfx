"""An approved reservation bounds both the first round and recovered advancement."""

import pytest
from test_replacement_runner import analysed, drain, read
from test_replacement_runner import work as work  # noqa: PLC0414

from gaiavfx.application.replace_character import replacement_plan, start_replacement
from gaiavfx.modules.direction.application import replacement_policy_text
from gaiavfx.shared.errors import ApplicationError


@pytest.mark.parametrize(
    "check,retry",
    [("residual_source", True), ("background_geometry", False), ("mouth_aperture", False)],
)
def test_draft_reserves_two_freezes_mode_and_retries_only_likeness(work, monkeypatch, check, retry):
    from gaiavfx.application import replace_character as app
    from gaiavfx.modules.direction.replacement import CheckResult

    db, library, runner, _, _ = work
    _, _, request = analysed(work)
    request["treatment_override"] = "rgb"
    monkeypatch.setattr(
        app, "take_checks", lambda *args, **kwargs: (CheckResult(check, "fail", "none", 0.8),)
    )
    with db.transaction() as session:
        operation = start_replacement(session, request, library=library, draft=True)
    assert operation["budget_reserved"] == 2
    assert operation["request"]["draft"] is True
    assert operation["request"]["policy_limits"]["max_rounds"] == 2
    # Recovery must use the frozen policy/mode, even after current defaults change.
    monkeypatch.setattr(app, "replacement_policy_text", lambda: "invalid current policy")
    runner.recover()
    drain(runner)
    finished = read(db, operation["id"])
    assert finished["state"] == "succeeded"
    renders = [j for j in finished["jobs"] if j["executor"] == "comfy" and j["kind"] == "video"]
    assert [j["seed"] for j in renders] == ([7, 8] if retry else [7])
    assert len(renders) <= finished["budget_reserved"]
    assert finished["review"][-1]["arm"] is None


@pytest.mark.parametrize("renders,rounds", [(1, [1]), (2, [2]), (3, [2, 1]), (4, [2, 1, 1])])
def test_requested_reservation_is_frozen_and_caps_recovered_loop(work, renders, rounds):
    db, library, runner, _, _ = work
    _, _, request = analysed(work)
    runner.comfy.shift = 12
    with db.transaction() as session:
        preview = replacement_plan(session, request, library=library, renders=renders)
        assert preview["arm"]["seed_count"] == rounds[0]
        operation = start_replacement(session, request, library=library, renders=renders)
        assert operation["budget_reserved"] == operation["request"]["renders"] == renders
        assert operation["request"]["policy"] == replacement_policy_text()
        assert operation["request"]["policy_limits"]["max_rounds"] == len(rounds)
    runner.recover()
    drain(runner)
    operation = read(db, operation["id"])
    assert operation["state"] == "succeeded", operation
    assert len(operation["candidates"]) <= renders
    actual = [j for j in operation["jobs"] if j["executor"] == "comfy" and j["kind"] == "video"]
    assert len(actual) <= renders
    assert len(actual) == sum(rounds[: len(operation["review"])])


@pytest.mark.parametrize("renders", [0, 5, -1, True, 1.5])
def test_out_of_range_reservations_refuse_before_submission(work, renders):
    db, library, _, _, _ = work
    _, _, request = analysed(work)
    with db.transaction() as session:
        for use_case in (replacement_plan, start_replacement):
            with pytest.raises(ApplicationError, match="renders must be an integer"):
                use_case(session, request, library=library, renders=renders)
