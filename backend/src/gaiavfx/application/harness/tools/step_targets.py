"""What an approved step targets, in Gaia's words, and why it cannot be prepared or rendered yet. Each tool says
which ids its step allows (`Tool.step_targets`)."""

from gaiavfx.application.harness.tools.common import current_step


def label(state, target):
    segment = next((s for s in state.segments if s["id"] == target), None)
    if segment:
        return f"segment {segment['ordinal'] + 1} ({target})"
    cut = next((c for c in state.cuts if c["id"] == target), None)
    if cut:
        return f"cut {target} at f{cut['frame']} in {label(state, cut['segment_id'])}"
    for place in state.places:
        for row in (place, *place["angles"]):
            if row["id"] == target:
                return f"{row['name']} ({target})"
    return target


def target_problem(context, name):
    step = current_step(context)
    if step and any(r["id"] == step.target for r in getattr(context.state, "recasts", [])):
        return ""
    if name == "prepare_cut" and step and step.kind == "render":
        # Render diagnostics already explain its prerequisites. An unavailable
        # optional sheet recovery is not an invalid preparation target.
        return ""
    from gaiavfx.application.harness.tools import DEFAULT_TOOLS

    targets = next(t for t in DEFAULT_TOOLS if t.name == name).step_targets(context)
    if targets is None:
        return ""
    step = current_step(context)
    if not targets:
        if name == "render_candidates" and any(
            a["id"] == step.target for a in getattr(context.state, "people_analyses", [])
        ):
            return ""
        return f"{label(context.state, step.target)} is outside this scope or has no preparable cut. Read its segment before revising the plan."
    reasons = []
    for target in targets:
        if name == "render_candidates":
            segment = next(s for s in context.state.segments if s["id"] == target)
            predecessor = segment.get("predecessor")
            selected = predecessor.get("selection") if predecessor else None
            if selected and selected["stale"]:
                reasons.append(
                    f"needs a fresh selected take of segment {predecessor['segment_number']} ({predecessor['id']}); take {selected['asset_id']} is stale ({', '.join(selected['stale_reasons'])}). Ask the user to select a fresh take or propose a revised plan to re-render the predecessor"
                )
            refusals = context.state.segment_plans[target]["refusals"]
            reasons.extend(r["message"] for r in refusals)
        else:
            from gaiavfx.application.harness.shots import shot_plan, shots
            from gaiavfx.application.harness.tools.prepare import (
                preparation_actions,
                preparation_cost,
            )
            from gaiavfx.application.harness.world import IN_FLIGHT, operations_for_shot

            # A cut's plan, or a first shot's: its segment's opening (the live trial's prepare step on a scene's angle
            # read state.plans by the segment's id and stopped the conversation).
            shot = next(s for s in shots(context.state) if s["id"] == target)
            plan = shot_plan(context.state, shot)
            choice = plan["recommended"]
            if choice is None:
                reasons.extend(g["message"] for g in plan["gaps"])
                continue
            actions, review = preparation_actions(plan)
            reasons.extend(review)
            for operation in operations_for_shot(context.state, shot):
                if operation["state"] in IN_FLIGHT:
                    reasons.append(f"operation {operation['id']} is still running")
                elif operation["review_pending"]:
                    reasons.append(
                        f"reference {operation['id']} awaits the user in Review workspace"
                    )
            if actions & {"review_view", "review_keyframe"}:
                reasons.append("the reference needs the user's approval in Review workspace")
            needed = preparation_cost(context, shot)
            left = context.budget.left
            if needed is not None and needed > left:
                reasons.append(
                    f"needs {needed} preparation jobs but only {left} remain; revise the plan's budget for user approval"
                )
            elif needed is None and not actions and not review:
                reasons.append("has nothing left to prepare")
    if reasons:
        return f"Step target {label(context.state, step.target)}: " + "; ".join(
            dict.fromkeys(reasons)
        )
    return ""
