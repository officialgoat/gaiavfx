"""Bounded, disjoint reads of the movie and a cut's Direction plan."""

from dataclasses import replace

from gaiavfx.application.direction_plans import plan_for_cut, plan_for_segment, plan_to_dict
from gaiavfx.application.harness.actions import Tool, ToolResult, pin_enum
from gaiavfx.application.harness.shots import take_shots
from gaiavfx.application.harness.tools.common import TransactionTool, scoped
from gaiavfx.application.harness.tools.plan import ACTION_KINDS
from gaiavfx.application.harness.world import (
    LIST,
    direction,
    fields,
    operations_for_shot,
    selections,
)
from gaiavfx.application.render_cut import compiler_gaps
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError


def bounded_content(value):
    if isinstance(value, str):
        return value[:8000]
    if isinstance(value, list):
        return [bounded_content(item) for item in value[:LIST]]
    if isinstance(value, dict):
        return {key: bounded_content(item) for key, item in list(value.items())[:LIST]}
    return value


class ReadMovie(Tool):
    name = "read_movie"
    description = "Read the movie's structure, references and current production state."

    def _run(self, context, value):
        return ToolResult(payload=context.state.payload(), progress=True)


class InspectCut(TransactionTool):
    name = "inspect_cut"
    description = "Read one cut's plan, gaps, references and legal actions."

    class Input(StrictModel):
        cut_id: str

    def narrow(self, context):
        ids = [cut["id"] for cut in context.state.cuts]
        return pin_enum(self.Input.model_json_schema(), "cut_id", ids) if ids else None

    def execute(self, session, context, value):
        from gaiavfx.application.harness.tools import applicable_tools

        cut = scoped(context.state.cuts, value.cut_id, "Cut")
        segment = scoped(context.state.segments, cut["segment_id"], "Segment")
        place = scoped(context.state.places, cut["place_id"], "Place")
        # Narrow the very same tools on this cut, retaining its containing segment.
        state = replace(
            context.state,
            segments=[
                {**segment, "cuts": [cut]}
                for segment in context.state.segments
                if segment["id"] == cut["segment_id"]
            ],
        )
        legal = [tool.name for tool, _ in applicable_tools(replace(context, state=state))]
        plan = plan_to_dict(plan_for_cut(session, value.cut_id))
        render = plan_to_dict(plan_for_segment(session, cut["segment_id"]))
        actions = [a for gap in plan["gaps"] for a in gap["actions"]]
        if plan["recommended"]:
            actions.extend(plan["recommended"]["actions"])
        return ToolResult(
            payload={
                "message": (
                    f"This cut (segment {segment['ordinal'] + 1}, f{cut['frame']}, "
                    f"{cut['from_angle']} -> {cut['to_angle']}) already has an authored relation. "
                    if cut["relation"] and cut["relation_source"] == "authored"
                    else ""
                )
                + "Legal actions are options; follow the requested segment and frame. Do not prepare unless asked.",
                "plan": plan,
                "segment_render": fields(render, "ready", "refusals"),
                "plan_step_kinds": {
                    a["action"]: ACTION_KINDS[a["action"]]
                    for a in actions
                    if a["action"] in ACTION_KINDS
                },
                "cut": cut,
                "place": place,
                "keyframes": [
                    fields(k, "id", "asset_id", "approved")
                    for k in direction.keyframes(session, value.cut_id)[:LIST]
                ],
                "operations": operations_for_shot(context.state, cut),
                "legal_now": legal,
                "compiler_gaps": compiler_gaps(session, value.cut_id),
            },
            progress=True,
        )


class ReadSegment(TransactionTool):
    name = "read_segment"
    description = "Read one segment's prose, phases, cuts, selection history and assembly errors."

    class Input(StrictModel):
        segment_id: str

    def narrow(self, context):
        ids = [s["id"] for s in context.state.segments]
        return pin_enum(self.Input.model_json_schema(), "segment_id", ids) if ids else None

    def execute(self, session, context, value):
        summary = scoped(context.state.segments, value.segment_id, "Segment")
        segment = direction.read_segment(session, value.segment_id)
        try:
            direction.assemble_segment(session, value.segment_id)
            assembled = True
        except ApplicationError as exc:
            if exc.status != 422:
                raise
            assembled = {"error": str(exc)[:2000]}
        return ToolResult(
            payload={
                **fields(segment, "id", "revision", "delivered_frames", "continues"),
                "segment_number": segment["ordinal"] + 1,
                "content": bounded_content(segment["content"]),
                "phases": [
                    bounded_content(fields(p, "id", "revision", "start_frame", "content"))
                    for p in segment["phases"][:LIST]
                ],
                "cuts": summary["cuts"],
                "selection": summary["selection"],
                "selection_history": [
                    fields(s, "authority", "basis", "revision", "asset_id")
                    for s in selections.history(session, context.movie_id, value.segment_id)[-LIST:]
                ],
                "renders": [
                    o
                    for o in context.state.operations
                    if o["kind"] == "render" and o["segment_id"] == value.segment_id
                ],
                "assembled": assembled,
                "plan": plan_to_dict(plan_for_segment(session, value.segment_id)),
                # build_set_from_take's shot numbers for this scene's take (the plan's build_refusal says why none)
                "take_shots": take_shots(summary),
            },
            progress=True,
        )
