"""Outcome language accepts alternate wording without accepting false promises."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))

from harness_eval_outcome_state import authority
from harness_eval_outcomes import DEFINITIONS, review_report
from harness_eval_tasks import TASKS


def test_every_e_task_has_an_outcome_definition():
    assert set(DEFINITIONS) == {t.id for t in TASKS}


def test_new_place_without_plate_is_not_a_human_approval():
    state = {"evidence": [], "pins": [], "selections": [], "reviews": {}, "places": []}
    initial = authority(state)
    state["places"].append({"id": "new-place", "plate_asset_id": None})
    assert authority(state) == initial
    state["places"][0]["plate_asset_id"] = "unapproved-plate"
    assert authority(state) != initial


@pytest.mark.parametrize(
    "text",
    [
        "Review the survey views in the Review workspace.",
        "Open Review to compare the candidates.",
        "The plate candidates are ready in **Review**.",
    ],
)
def test_review_destinations_accept_natural_wording(text):
    assert review_report(
        [{"state": "accepted", "action": {"tool": "say", "input": {"text": text}}}]
    )


def test_chat_ranking_is_not_a_review_handoff():
    assert not review_report(
        [
            {
                "state": "accepted",
                "action": {"tool": "ask_human", "input": {"question": "Rank the views here."}},
            }
        ]
    )
