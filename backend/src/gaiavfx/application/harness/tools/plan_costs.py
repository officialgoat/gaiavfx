"""Budget known preparation actions and enforce each producer step's allowance."""

import re

from gaiavfx.application.harness.preparation import VIEW_ACTIONS
from gaiavfx.application.harness.replacement_context import (
    latest_read_analysis,
    replacement_requested,
    session_requested_replacement,
)
from gaiavfx.application.harness.shots import shot_plan, shot_words, shots, targeted_shots
from gaiavfx.application.harness.tools.common import current_step
from gaiavfx.application.harness.tools.input_hints import known_empty_image
from gaiavfx.application.prepare_references import view_producer
from gaiavfx.application.set_seed import fill_renders
from gaiavfx.shared.errors import ApplicationError


def step_jobs_left(context):
    step = current_step(context)
    if not step or step.kind not in {"prepare", "render", "build"}:
        return None
    spent, settled = {}, {}
    for receipt in step.receipts:
        if receipt.get("kind") in {"operation", "recast_run"}:
            operation_id = receipt["id"]
            if receipt.get("settled"):
                # A stopped build's settled spend replaces what it reserved (ADR 0020 §8).
                settled[operation_id] = receipt.get("budget_reserved", 0)
            else:
                spent[operation_id] = max(
                    spent.get(operation_id, 0), receipt.get("budget_reserved", 0)
                )
    return max(0, step.jobs - sum((spent | settled).values()))


def afford_step(context, needed):
    remaining = step_jobs_left(context)
    if remaining is not None and needed > remaining:
        raise ApplicationError(
            f"step_budget_exhausted: producer needs {needed} jobs, but step "
            f"'{current_step(context).title}' has {remaining} jobs remaining. "
            "Revise the plan's step budget for user approval.",
            422,
        )


def preparation_needs(context, shot):
    """Ordered producers, deduplicated across shots by their actual target."""
    from gaiavfx.application.harness.tools.prepare import preparation_actions

    plan = shot_plan(context.state, shot)
    choice = plan["recommended"]
    if not choice:
        return None
    actions, _ = preparation_actions(plan)
    place = next(p for p in context.state.places if p["id"] == shot["place_id"])
    needs = {}
    if actions & VIEW_ACTIONS and not view_producer(place).plate:
        needs[("views", place["id"])] = 0
    elif actions & VIEW_ACTIONS:
        if not place["plate"] or place["plate"]["angle_id"] != shot["plate_angle_id"]:
            empty = any(
                a["kind"] == "image" and known_empty_image(context, a["id"])
                for a in [*context.attachments, *context.state.assets]
            )
            needs[("plate", place["id"])] = 0 if empty else (1 if place["plate"] else 2)
        needs[("survey", place["id"])] = 1
    if "insert_subject" in actions:
        needs[("pin", shot["id"])] = 2
    if "place_views" in choice["blocks"] and not context.state.sheets.get(place["id"]):
        needs[("sheet", place["id"])] = 0
    return needs


def named_actions(title):
    actions = {
        kind
        for kind, pattern in {
            "plate": r"\b(?:plate|remov\w*)\b",
            "survey": r"\b(?:survey\w*|views?)\b",
            "pin": r"\b(?:pins?|insert\w*)\b",
            "sheet": r"\b(?:sheet\w*|assembl\w*)\b",
        }.items()
        if re.search(pattern, title, re.IGNORECASE)
    }
    return actions


def unknown_relation(cut_id):
    return (
        f"Cut {cut_id}: declaring its relation decides the technique; a reverse across a "
        "barrier needs views and a pin. Declare the relation with edit_cut op=relation, "
        "then budget plate, survey (1 job), pin (2 jobs) and sheet (0 jobs) from its plan."
    )


def cut_plan_refusal(context, shot):
    plan = shot_plan(context.state, shot)
    if any(g["code"] == "same_angle_cut" for g in plan["gaps"]):
        return (
            "the cut needs a different target angle: add it with edit_place add_angle "
            "(the empty place from that camera), then cut to it"
        )
    return unknown_relation(shot["id"])


def check_plan_costs(context, steps):
    """Refuse guessed costs and render-before-preparation; never silently grant more work."""
    covered, prepared = set(), set()
    problems = []
    analyses = getattr(context.state, "people_analyses", [])
    for step in steps:
        recast = next(
            (r for r in getattr(context.state, "recasts", []) if r["id"] == step.target), None
        )
        if step.kind == "render" and recast:
            estimate = recast.get("estimate")
            costs = {o["renders"] for o in recast.get("range_estimates", [])}
            if estimate:
                costs.add(estimate["renders"])
            if step.jobs not in costs:
                problems.append(
                    "recast_jobs: read the recast and reserve its exact estimated renders in one render step; use a smaller planned range if needed."
                )
            continue
        ready = [a["id"] for a in analyses if a["state"] == "succeeded"]
        if (
            step.kind == "render"
            and not step.target
            and ready
            and (
                replacement_requested(step.title)
                or session_requested_replacement(context.report_turns)
            )
        ):
            # The single analysed shot, or the one this session read last, is the target.
            target = (
                ready[0] if len(ready) == 1 else latest_read_analysis(context.report_turns, ready)
            )
            if not target:
                problems.append(
                    "replacement_target: set the render step's target to one people analysis id: "
                    + ", ".join(ready)
                )
                continue
            step.target = target
        analysis = next((a for a in analyses if a["id"] == step.target), None)
        if step.kind == "render" and analysis:
            if analysis["state"] != "succeeded":
                problems.append(
                    "analysis_missing: finish people analysis before proposing replacement."
                )
            if step.jobs == 0:
                step.jobs = min(4, max(0, context.settings.get("agent_budget_jobs", 6)))
            if not 1 <= step.jobs <= 4:
                problems.append("replacement_jobs: reserve 1 to 4 renders for the analysed shot.")
            continue
        if step.kind == "build":
            # A build fills its stage's unseen space unless the agent says otherwise, so its step reserves the
            # probes' renders (ADR 0020 §7); a build that does not fill spends none of them.
            renders = fill_renders()
            if step.jobs == 0:
                step.jobs = renders
            if step.jobs != renders:
                problems.append(
                    f"build_jobs: a build step reserves the {renders} renders its filling may spend, "
                    f"not {step.jobs}."
                )
            continue
        targets = targeted_shots(context.state, step.target)
        if step.kind in {"prepare", "render"} and not step.target and context.state.cuts:
            from gaiavfx.application.harness.tools.step_targets import label

            options = (
                [label(context.state, c["id"]) for c in context.state.cuts]
                if step.kind == "prepare"
                else [label(context.state, s["id"]) for s in context.state.segments]
            )
            problems.append(
                f"{step.kind}_target_required: set target on '{step.title}' so its "
                "preparation prerequisites and exact job costs can be checked; one of: "
                + "; ".join(options[:6])
            )
            continue
        if step.kind == "prepare" and targets:
            needs = {}
            for shot in targets:
                required = preparation_needs(context, shot)
                if required is None:
                    problems.append(cut_plan_refusal(context, shot))
                    continue
                needs.update(required)
            named = named_actions(step.title)
            # A title may split costed work across steps; zero-cost actions (an empty
            # plate fit, the sheet assembly) belong to any step that prepares the cut.
            # e2e run 7: "Survey…, insert Arin, survey place" (3 jobs) was refused six
            # times for "plate, sheet" although both were free.
            chosen = {
                key: cost
                for key, cost in needs.items()
                if not named or key[0] in named or cost == 0
            }
            chosen = {key: cost for key, cost in chosen.items() if key not in covered}
            needed = sum(chosen.values())
            if step.jobs != needed:
                detail = ", ".join(f"{kind} {cost}" for (kind, _), cost in chosen.items())
                problems.append(
                    f"prepare_jobs: '{step.title}' needs {needed} jobs ({detail or 'no remaining producer jobs'}), "
                    f"not {step.jobs}. Plate fit 0, removal 2 (replacement 1), survey 1, pin 2, sheet 0. "
                    "Use these counts and retain human reference reviews."
                )
            covered.update(chosen)
            prepared.update(shot["id"] for shot in targets)
        elif step.kind == "render":
            segments = [
                s
                for s in context.state.segments
                if step.target == s["id"] or any(c["id"] == step.target for c in s["cuts"])
            ]
            rendered = [
                shot
                for shot in shots(context.state)
                if shot["segment_id"] in {s["id"] for s in segments}
            ]
            for shot in rendered:
                plan = shot_plan(context.state, shot)
                if not plan["recommended"]:
                    problems.append(cut_plan_refusal(context, shot))
                    continue
                needs = preparation_needs(context, shot)
                missing = set(needs) - covered
                if missing or (not plan["recommended"]["ready"] and shot["id"] not in prepared):
                    actions = sorted({key[0] for key in missing}) or [
                        a["action"] for a in plan["recommended"]["actions"]
                    ]
                    problems.append(
                        f"render_prerequisites: {shot_words(shot)} is not ready: {', '.join(actions)}. "
                        + (
                            "Add earlier prepare steps targeting this cut (or its segment/place), then render. "
                            if shot["kind"] == "cut"
                            else "Add earlier prepare steps targeting its segment (or place), then render. "
                        )
                        + "A prepare step whose title names specific actions covers only those; name the "
                        "missing ones in a step title, or use a title that names none to cover them all."
                    )
            for segment in segments:
                refusals = context.state.segment_plans[segment["id"]]["refusals"]
                unresolved = [
                    r["message"]
                    for r in refusals
                    if r["code"]
                    not in {
                        "cut_not_ready",
                        "opening_not_ready",
                        "cut_binding",
                        "technique_unavailable",
                        "continuation_source",
                    }
                ]
                if unresolved:
                    problems.append("render_prerequisites: " + "; ".join(unresolved))
    return "; ".join(dict.fromkeys(problems))
