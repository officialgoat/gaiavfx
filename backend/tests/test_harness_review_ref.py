"""A plan step names what the user reviews as a typed ReviewRef; sessions saved with the older `review_of` strings
load, and a plan's current step is its first open one."""

import pytest

from gaiavfx.application.harness.session import Plan, PlanStep


@pytest.mark.parametrize(
    ("legacy", "review"),
    [
        ("op-1", {"kind": "operation", "operation_id": "op-1"}),
        ("seed:op-2", {"kind": "seed", "operation_id": "op-2"}),
        ("build:op-3", {"kind": "build", "operation_id": "op-3"}),
        ("recast:rc-1", {"kind": "recast", "recast_id": "rc-1", "start": None, "end": None}),
        ("recast:rc-1@0-150", {"kind": "recast", "recast_id": "rc-1", "start": 0, "end": 150}),
        ("", None),
    ],
)
def test_a_saved_review_of_string_loads_as_its_review(legacy, review):
    saved = {"kind": "review", "title": "Review", "status": "awaiting_user", "review_of": legacy}
    step = PlanStep.model_validate(saved)
    assert (step.review.model_dump() if step.review else None) == review
    stored = step.model_dump()
    assert "review_of" not in stored and PlanStep.model_validate(stored) == step
    assert step.reviews("op-1") is (legacy == "op-1")


def test_the_current_step_is_the_first_open_one():
    steps = [
        {"kind": "edit", "title": "Edit", "status": "done"},
        {"kind": "render", "title": "Render", "status": "awaiting_user", "review_of": "op-9"},
        {"kind": "review", "title": "Review"},
    ]
    plan = Plan.model_validate(
        {
            "id": "p",
            "revision": 1,
            "goal": "A scene",
            "steps": steps,
            "budget_jobs": 2,
            "proposed_turn": 0,
        }
    )
    assert plan.current() is plan.steps[1] and [s.open for s in plan.steps] == [False, True, True]
    for step in plan.steps:
        step.status = "done"
    assert plan.current() is None
