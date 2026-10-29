"""Cut readiness owns preparation completion, including the zero-job sheet.

Render-step recovery exposes prepare_cut only for sheet assembly on that render
target, under the existing prepare grant. Execution rechecks inside its transaction;
neither the grant nor the render step's job allowance is expanded.
"""

from gaiavfx.application.harness.shots import shot_plan, shot_words, shots, targeted_shots
from gaiavfx.application.harness.tools.common import current_step, granted
from gaiavfx.application.harness.tools.plan_costs import cut_plan_refusal, preparation_needs
from gaiavfx.application.harness.tools.prepare import (
    needed_jobs,
    preparable,
    preparation_actions,
)
from gaiavfx.application.harness.world import IN_FLIGHT, operations_for_shot

# Each need, as the scope words name its producer: a set's renders are the place's views, as a survey is.
NEED_KINDS = {
    "plate": "removal",
    "survey": "survey",
    "views": "survey",
    "pin": "insert",
    "sheet": "assemble",
}


def out_of_scope_skip(context, index, remaining):
    """When the user asked for one kind of preparation, the rest is not this plan's.

    local-e14 plan-propose: "Survey the courtyard … and let me review the views."
    After the approved survey the message offered both "call prepare_cut once more"
    and "mark it skipped"; the model tried the pin, was refused four times as outside
    the request, and stalled. Decide here, from the same keyword map.
    """
    from gaiavfx.application.harness.tools.plan_scope import PREPARATION, producer_kinds

    allowed = producer_kinds(context.latest_user_message or "")
    if not allowed or "prepare" in allowed:
        return ""
    outside = sorted(
        (kind, jobs) for kind, jobs in remaining if NEED_KINDS.get(kind) not in allowed
    )
    if not remaining or len(outside) != len(remaining) or not (allowed & PREPARATION):
        return ""
    left = ", ".join(f"{kind} ({'free' if not jobs else f'{jobs} jobs'})" for kind, jobs in outside)
    return (
        f'preparation_outside_request: the user asked for "{context.latest_user_message[:200]}"; '
        f"that work is done. Mark step {index} skipped with a note that {left} remain outside the "
        "request, then report. Do not prepare more."
    )


def completion_refusal(context, index):
    plan = context.document.plan
    step = plan.steps[index]
    if step.kind != "prepare":
        return ""
    later = {
        c["id"]
        for s in plan.steps[index + 1 :]
        if s.kind == "prepare" and s.status in {"pending", "awaiting_user"}
        for c in targeted_shots(context.state, s.target)
    }
    problems = []
    remaining = set()
    for shot in targeted_shots(context.state, step.target):
        if shot["id"] in later:
            continue
        needs = preparation_needs(context, shot)
        if needs is None:
            problems.append(cut_plan_refusal(context, shot))
            continue
        remaining |= {(kind, jobs) for (kind, _), jobs in needs.items()}
        details = [
            "sheet (free): call prepare_cut once more; it assembles the sheet and costs no jobs"
            if kind == "sheet"
            else f"{kind} ({jobs} jobs)"
            for (kind, _), jobs in needs.items()
        ]
        actions, review = preparation_actions(shot_plan(context.state, shot))
        details.extend(review)
        details.extend(sorted(actions & {"review_view", "review_keyframe"}))
        for operation in operations_for_shot(context.state, shot):
            if operation["kind"] == "render":
                continue
            if operation["state"] in IN_FLIGHT:
                details.append(f"{operation['kind']} is still running")
            elif operation["review_pending"]:
                details.append(f"{operation['kind']} needs the user's review")
        if details:
            problems.append(f"{shot_words(shot)}: " + "; ".join(dict.fromkeys(details)))
    if not problems:
        return ""
    if skip := out_of_scope_skip(context, index, remaining):
        return skip
    return (
        "preparation_incomplete: "
        + "; ".join(problems)
        + ". Keep this step pending until prepared. If the remaining work is outside the "
        "request, mark it skipped with a note or propose a revised plan; do not claim it done."
    )


def render_sheet_targets(context):
    step = current_step(context)
    if not step or step.kind != "render" or not step.target or not granted(context, "prepare"):
        return []
    segments = [
        s
        for s in context.state.segments
        if step.target == s["id"] or any(c["id"] == step.target for c in s["cuts"])
    ]
    result = []
    for segment in segments:
        cut_shots = [
            s
            for s in shots(context.state)
            if s["kind"] == "cut" and s["segment_id"] == segment["id"]
        ]
        needed = [
            s for s in cut_shots if needed_jobs(context.state, s) == 0 and preparable(context, s)
        ]
        ids = {c["id"] for c in needed}
        refusals = context.state.segment_plans[segment["id"]]["refusals"]
        if not refusals or any(
            r["code"] != "cut_binding"
            or not set(r["cut_ids"]) <= ids
            or "A views-only sheet is required" not in r["message"]
            for r in refusals
        ):
            continue
        result.extend(c["id"] for c in needed)
    return result
