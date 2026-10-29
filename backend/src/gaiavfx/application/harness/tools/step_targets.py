"""Match producer calls to the approved step, independently of model grammar."""

from gaiavfx.application.harness.shots import targeted_shots
from gaiavfx.application.harness.tools.common import current_step
from gaiavfx.shared.errors import ApplicationError


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


def target_ids(context, name):
    step = current_step(context)
    if step and step.kind == "render" and name == "prepare_cut":
        from gaiavfx.application.harness.tools.preparation_progress import render_sheet_targets

        return render_sheet_targets(context)
    kinds = {
        "prepare_cut": "prepare",
        "render_candidates": "render",
        "build_set_from_take": "build",
        "fill_set_more": "build",
    }
    if not step or step.kind != kinds.get(name) or not step.target:
        return None
    if name == "build_set_from_take":
        # A build step builds from the scene it names, or fills the stage it names (ADR 0020 §8).
        return [s["id"] for s in context.state.segments if s["id"] == step.target]
    if name == "fill_set_more":
        return [r["id"] for r in getattr(context.state, "sets", []) if r["id"] == step.target]
    if name == "render_candidates":
        if any(a["id"] == step.target for a in getattr(context.state, "people_analyses", [])):
            return []
        return [
            s["id"]
            for s in context.state.segments
            if step.target == s["id"] or any(c["id"] == step.target for c in s["cuts"])
        ]
    return [shot["id"] for shot in targeted_shots(context.state, step.target)]


def target_refusal(context, name, supplied):
    targets = target_ids(context, name)
    if targets is None:
        return ""
    actual = supplied.get(
        {"prepare_cut": "cut_id", "fill_set_more": "set_id"}.get(name, "segment_id")
    )
    if name == "render_candidates" and not actual:
        actual = next(
            (c["segment_id"] for c in context.state.cuts if c["id"] == supplied.get("cut_id")), None
        )
    if not actual or actual in targets:
        return ""
    step = current_step(context)
    return (
        f"step_target_mismatch: '{step.title}' targets {label(context.state, step.target)}, not {label(context.state, actual)}. Retry {name} with "
        + ", ".join(targets or [step.target])
        + "."
    )


def require_target(context, name, supplied):
    if name in {"render_candidates", "prepare_cut", "build_set_from_take", "fill_set_more"} and (
        reason := target_refusal(context, name, supplied)
    ):
        raise ApplicationError(reason, 422)


def target_problem(context, name):
    step = current_step(context)
    if step and any(r["id"] == step.target for r in getattr(context.state, "recasts", [])):
        return ""
    if name == "prepare_cut" and step and step.kind == "render":
        # Render diagnostics already explain its prerequisites. An unavailable
        # optional sheet recovery is not an invalid preparation target.
        return ""
    targets = target_ids(context, name)
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
            left = context.jobs_limit - context.jobs_reserved
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
