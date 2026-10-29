"""The agent's stage tools (ADR 0018 §7, 0019 §7, 0020 §8): readings, issue proposals, saved views, using a stage
for a scene, and builds inside an approved plan's build step. Accepting or discarding versions, keeping stills and
importing stay human."""

import logging
import re
from typing import Literal

from pydantic import Field

from gaiavfx.application.harness.actions import ToolResult, pin_enum
from gaiavfx.application.harness.tools.common import (
    TransactionTool,
    current_step,
    direction_receipt,
    operation_receipt,
    scoped,
)
from gaiavfx.application.harness.tools.plan_costs import afford_step
from gaiavfx.application.set_agent import (
    agent_fill_more,
    agent_mark_issue,
    agent_read_set,
    agent_save_view,
    agent_sets,
    agent_use_set,
    repair_state,
)
from gaiavfx.application.set_seed import fill_renders, seed_preview, start_seed
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError

IssueKind = Literal[
    "shape",
    "space",
    "duplicate",
    "stairs",
    "obstacle",
    "floaters",
    "hole",
    "split",
    "doubled",
    "two-way",
    "other",
]


def requested(message):
    return bool(
        re.search(r"\b(?:sets?|stages?|locations?|environments?|splats?)\b", message, re.IGNORECASE)
    )


def refresh_sets(session, state, library, turns, message):
    """Sets enter the agent's context when a scoped place is built as one (ADR 0019 §7), when the user talks about
    them, or when an earlier turn read one."""
    if library is None:
        return
    retained = [
        r["set_id"]
        for t in reversed(turns)
        if t["state"] == "accepted"
        for r in t["receipts"]
        if r.get("set_id")
    ]
    built = [place["set_id"] for place in state.places if place.get("set_id")]
    if not retained and not built and not requested(message):
        return
    try:
        state.sets = agent_sets(session, library, first=[*built, *retained])
    except Exception:  # set context must never end the agent session
        logging.getLogger(__name__).exception("Set context unavailable")


def rows(context):
    return getattr(context.state, "sets", [])


class ReadSet(TransactionTool):
    name = "read_set"
    description = (
        "Read a saved stage (a place built once): its version, cameras K0… with headings and grid cells, the map in "
        "rooms, numbered issues with their author (human, agent or Judge), queued fixes and saved views."
    )

    class Input(StrictModel):
        set_id: str

    def narrow(self, context):
        return (
            pin_enum(self.Input.model_json_schema(), "set_id", [r["id"] for r in rows(context)])
            if rows(context)
            else None
        )

    def execute(self, session, context, value):
        scoped(rows(context), value.set_id, "Stage")
        return ToolResult(
            payload=agent_read_set(session, context.library, value.set_id),
            progress=True,
            receipts=[{"kind": "set_read", "set_id": value.set_id}],
        )


class MarkSetIssue(ReadSet):
    name = "mark_set_issue"
    description = (
        "Propose an issue on a stage for the user to see in its workbench, as the agent. Name grid cells (A1…) "
        "and cameras (K0…) from read_set. It fixes nothing; the user decides."
    )

    class Input(StrictModel):
        set_id: str
        kind: IssueKind
        why: str = Field(min_length=1, max_length=600)
        cells: list[str] = Field(default_factory=list, max_length=12)
        cameras: list[str] = Field(default_factory=list, max_length=6)

    def execute(self, session, context, value):
        scoped(rows(context), value.set_id, "Stage")
        payload = agent_mark_issue(
            session,
            context.library,
            value.set_id,
            kind=value.kind,
            why=value.why,
            cells=value.cells,
            cameras=value.cameras,
        )
        issue = next((i for i in reversed(payload["issues"]) if i["author"] == "agent"), None)
        return ToolResult(
            payload=payload,
            changed=True,
            receipts=[
                {"kind": "set_issue", "set_id": value.set_id, "issue_id": issue and issue["id"]}
            ],
        )


class SaveSetView(ReadSet):
    name = "save_set_view"
    description = (
        "Save a named camera on a stage for shots: standing at a camera from read_set (K0…) and looking along a "
        "heading in degrees, clockwise from above. With angle_id it is that scene angle's camera, for its views. "
        "The user can look at it and adjust it in the workbench."
    )

    class Input(StrictModel):
        set_id: str
        name: str = Field(min_length=1, max_length=80)
        camera: str = Field(min_length=2, max_length=6)
        heading: float = Field(ge=0, lt=360)
        angle_id: str | None = None

    def execute(self, session, context, value):
        scoped(rows(context), value.set_id, "Stage")
        payload, view_id = agent_save_view(
            session,
            context.library,
            value.set_id,
            name=value.name,
            camera=value.camera,
            heading=value.heading,
            angle_id=value.angle_id,
        )
        # A scene opening on the angle with text only does not use the camera yet (the stage eval's model aimed it
        # and never asked the scene for the stage's views).
        plans = context.state.segment_plans
        waiting = [
            s
            for s in context.state.segments
            if value.angle_id
            and ((plans.get(s["id"]) or {}).get("place") or {}).get("set_id") == value.set_id
            and not plans[s["id"]]["place"]["uses_views"]
            and (plans[s["id"]].get("opening") or {}).get("angle_id") == value.angle_id
        ]
        if waiting:
            payload = payload | {
                "next": "; ".join(
                    f"Segment {s['ordinal'] + 1} ({s['id']}) opens on this angle with text only: link_set asks "
                    "its first shot for the stage's views"
                    for s in waiting
                )
                + "."
            }
        return ToolResult(
            payload=payload,
            changed=True,
            receipts=[
                {
                    "kind": "set_view",
                    "set_id": value.set_id,
                    "view_id": view_id,
                    "angle_id": value.angle_id,
                }
            ],
        )


class LinkSet(TransactionTool):
    name = "link_set"
    description = (
        "Use a stage for a scene, as the user's Use stage does: the place its first shot opens in is built as the "
        "stage for every scene there, and the first shot asks for the stage's views. The result says what changed."
    )

    class Input(StrictModel):
        segment_id: str
        expected_revision: int = Field(ge=1)
        set_id: str
        angle_id: str | None = None  # the angle the first shot opens on, when it opens on none yet

    def narrow(self, context):
        if not rows(context) or not context.state.segments:
            return None
        schema = pin_enum(
            self.Input.model_json_schema(), "set_id", [r["id"] for r in rows(context)]
        )
        return pin_enum(schema, "segment_id", [s["id"] for s in context.state.segments])

    def execute(self, session, context, value):
        scoped(rows(context), value.set_id, "Stage")
        scoped(context.state.segments, value.segment_id, "Segment")
        row, words = agent_use_set(
            session,
            value.segment_id,
            expected_revision=value.expected_revision,
            set_id=value.set_id,
            angle_id=value.angle_id,
        )
        receipt = direction_receipt(row)
        return ToolResult(
            payload={"id": row["id"], "revision": row["revision"], "message": words},
            changed=True,
            receipts=[receipt, {"kind": "set_link", "set_id": value.set_id}],
        )


class FillSetMore(TransactionTool):
    name = "fill_set_more"
    description = (
        "Fill more of a stage's unseen space from its accepted version, as the user's Fill more unseen space does: "
        "15 to 25 minutes and up to 6 renders, which its build step reserves. Refused while the user has other "
        "fixes queued on the stage. The user decides the new version."
    )

    class Input(StrictModel):
        set_id: str

    def narrow(self, context):
        ids = [r["id"] for r in rows(context) if r.get("accepted")]
        step = current_step(context)
        if step and step.kind == "build" and step.target in ids:
            ids = [step.target]  # a build step fills the stage it names
        return pin_enum(self.Input.model_json_schema(), "set_id", ids) if ids else None

    def execute(self, session, context, value):
        scoped(rows(context), value.set_id, "Stage")
        afford_step(context, fill_renders())
        operation = agent_fill_more(session, context.library, value.set_id)
        state = repair_state(session, operation)
        return ToolResult(
            payload={
                "operation_id": operation["id"],
                # The chat shows these words as the build's card, as build_set_from_take's (the live trial read "the
                # user's decision" there).
                "message": f"Filling more of {state['name']}: up to {operation['budget_reserved']} renders, about "
                "15 to 25 minutes on the GPU. It runs in Jobs while you keep working, and you can cancel it there; "
                "its new version waits for you in the stage workbench.",
            },
            receipts=[operation_receipt(operation)],
            waits_on=operation["id"],
            changed=True,
        )


class BuildSetFromTake(TransactionTool):
    name = "build_set_from_take"
    description = (
        "Build a new stage from one shot of a scene's selected take, as the user's Build stage does. With "
        "fill_unseen (on by default) it fills in what the take never saw, also from a camera that stands still or "
        "only turns: 15 to 25 minutes and up to 6 renders, which its build step reserves. Without: about 3 "
        "minutes, and a still or turning camera is refused. The user decides its v1."
    )

    class Input(StrictModel):
        segment_id: str
        shot: int = Field(default=0, ge=0)  # 0 the first shot, n the shot after cut n
        name: str | None = Field(default=None, max_length=120)
        fill_unseen: bool = True

    def narrow(self, context):
        plans = context.state.segment_plans
        ids = [
            s["id"]
            for s in context.state.segments
            if s["id"] in plans and not plans[s["id"]].get("build_refusal")
        ]
        step = current_step(context)
        if step and step.kind == "build" and step.target in ids:
            ids = [step.target]  # a build step builds from the scene it names
        return pin_enum(self.Input.model_json_schema(), "segment_id", ids) if ids else None

    def execute(self, session, context, value):
        scoped(context.state.segments, value.segment_id, "Segment")
        preview = seed_preview(
            session,
            context.library,
            segment_id=value.segment_id,
            shot=value.shot,
            fill_unseen=value.fill_unseen,
        )
        if preview["refusal"]:
            raise ApplicationError(preview["refusal"], 409)
        afford_step(context, preview["renders"])
        name = value.name or preview["name"]
        # The place is built as the new set when it has none, as the user's dialog does by default.
        operation = start_seed(
            session,
            context.library,
            segment_id=value.segment_id,
            shot=value.shot,
            name=name,
            use_for_place=preview["can_use_for_place"],
            fill_unseen=value.fill_unseen,
        )
        chosen = next(s for s in preview["shots"] if s["index"] == value.shot)
        return ToolResult(
            payload={
                "operation_id": operation["id"],
                "message": f"Building {name} from {chosen['label']} ({chosen['times']}). {preview['estimate']}",
            },
            receipts=[operation_receipt(operation)],
            waits_on=operation["id"],
            changed=True,
        )
