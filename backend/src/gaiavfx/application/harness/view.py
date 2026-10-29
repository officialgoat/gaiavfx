"""The agent's current view: the bounded world state as the lines the model reads each turn, under VIEW_CEILING."""

from gaiavfx.application.harness.context import VIEW_CEILING, estimate, shorten
from gaiavfx.application.harness.shots import set_candidates, take_shots
from gaiavfx.application.harness.world import clip, plate_survey_notes


def render_view(state, autonomy, jobs_reserved, jobs_limit, *, document=None):
    lines = [
        f"Movie {state.movie['id']} r{state.movie['revision']}: {state.movie['title']}",
        f"Autonomy {autonomy}; {max(0, jobs_limit - jobs_reserved)} jobs left ({jobs_reserved}/{jobs_limit} reserved).",
    ]
    if document and document.plan_mode:
        plan = document.plan
        lines[1] = (
            "Plan mode on; no plan yet. To create or draft a storyline, segments, shots, cuts, "
            "places or cast; change, prepare or render anything, call propose_plan. "
            "Storyline edit steps use draft_storyline after approval. "
            "Use say only for questions, ideas the user asked to discuss, and reports."
        )
        if plan:
            lines[1] = f"Plan {plan.id} rev {plan.revision} {plan.state}"
            if plan.state == "proposed":
                lines[1] += ", awaiting the user"
                if plan.revision_requested:
                    lines[1] += (
                        "; revision requested: " + shorten({"content": plan.note}, 80)["content"]
                    )
            elif plan.state == "executing":
                done = sum(step.status in {"done", "skipped"} for step in plan.steps)
                lines[1] += (
                    f": {done}/{len(plan.steps)} steps done, {max(0, jobs_limit - jobs_reserved)} of {plan.budget_jobs} plan jobs left"
                )
            if plan.state in {"proposed", "approved", "executing"}:
                lines.append("Goal: " + shorten({"content": plan.goal}, 80)["content"])
                lines.extend(
                    f"step {i} [{step.kind}] {step.status}: "
                    + step_target(step, state)
                    + f" ({step.jobs} jobs)"
                    for i, step in enumerate(plan.steps)
                )
    # Keep fresh human selections ahead of expandable production detail.
    if document and document.plan_mode and document.plan and document.plan.state == "executing":
        from types import SimpleNamespace

        from gaiavfx.application.harness.tools.step_targets import target_problem

        context = SimpleNamespace(
            state=state,
            document=document,
            jobs_limit=jobs_limit,
            jobs_reserved=jobs_reserved,
            attachments=[],
        )
        for name in ("render_candidates", "prepare_cut"):
            if problem := target_problem(context, name):
                lines.append(problem)
    for segment in state.segments:
        lines.append(
            f"Selection now, segment {segment['ordinal'] + 1} ({segment['id']}): "
            + selection_text(segment["selection"])
        )
    if any(s.get("continuing_successor") and not s["selection"] for s in state.segments):
        lines.append(
            "Continuation hint: if asked to continue within budget and the predecessor has "
            "takes but no user selection, read_candidates then pick_provisional (unjudged "
            "until ranked). Never pick without that request."
        )
    lines.extend(plate_survey_notes(state))
    for operation in state.operations:
        if operation["review_pending"]:
            lines.append(
                f"Review {operation['kind']} {operation['review_set_id'] or operation['id']}: awaits the user in Review workspace"
            )
    executing = document.plan if document and document.plan_mode else None
    open_builds = {
        receipt.get("id") or receipt.get("operation_id")
        for step in (executing.steps if executing and executing.state == "executing" else [])
        if step.kind == "build" and step.status in {"pending", "awaiting_user"}
        for receipt in step.receipts
    }
    for operation in state.operations:
        if operation["review_pending"]:
            continue
        if operation["kind"] == "set_build":
            # A stage build in the agent's words; decided builds say nothing more, and a stopped one only inside the
            # open build step it stopped in: the live trial's model read an old stopped build's "the build step
            # stays open" and offered to rerun it instead of the stage candidate.
            if operation.get("words") and (
                not operation.get("stopped") or operation["id"] in open_builds
            ):
                lines.append(f"Stage build: {operation['words']}")
            continue
        lines.append(
            f"{operation['kind']} {operation['id']}: {operation['state']}, "
            f"{operation['candidate_count']} candidates"
            + ("; plate review complete, approved" if operation.get("plate_approved") else "")
        )
    for cast in state.cast:
        lines.append(
            f"Cast {cast['id']} r{cast['revision']}: {cast['name']}; identity {cast['identity_asset']}"
        )
    for analysis in state.people_analyses[:6]:
        lines.append(
            f"People analysis {analysis['id']} {analysis['state']}: asset {analysis.get('asset_id')}; "
            f"shot {analysis.get('shot')}; tracks {[(t['track_index'], t['position'], t['presence']) for t in analysis.get('tracks', [])]}; "
            f"gaps {[g['code'] for g in analysis.get('gaps', [])]}. analyze_people reads details."
        )
    if state.recasts:
        lines.append(f"Recasts: {state.recasts}. read_recast reads details.")
    for row in state.sets:
        places = ", ".join(p["name"] for p in state.places if p.get("set_id") == row["id"])
        lines.append(
            f"Stage {row['name']} ({row['id']}): {row['version']}"
            + ("" if row.get("accepted") else ", none accepted")
            + f"; {row['open_issues']} open issues"
            + (f"; built as place {places}" if places else "")
        )
    lines += ["read_set reads a stage's cameras, map, issues and views."] if state.sets else []
    lines += set_candidates(state)
    for segment in state.segments:
        lines.append(
            f"Segment {segment['ordinal'] + 1} ({segment['id']}) r{segment['revision']}: "
            f"{segment['delivered_frames']} frames; continues {segment['continues']}"
        )
        plan = state.segment_plans.get(segment["id"])
        if plan:
            lines.append(
                f"  Render: {plan['title']}; ready {plan['ready']}"
                + (
                    "; " + "; ".join(r["message"] for r in plan["refusals"])
                    if plan["refusals"]
                    else ""
                )
            )
        opening = (plan or {}).get("opening")
        choice = opening and opening["recommended"]
        if choice and not (choice["technique_id"] == "opening_text" and choice["ready"]):
            lines.append(
                f"  First shot (prepare_cut cut_id {segment['id']}): {choice['technique_id']}"
                + (
                    " ready"
                    if choice["ready"]
                    else " not ready: " + ", ".join(a["action"] for a in choice["actions"])
                )
            )
        place = (plan or {}).get("place")
        if place and place["set_id"]:
            # The first shot's angle in its stage, and the step in its way as the References row says it. A first
            # shot that opens on text in a stage's place is not using it (the live eval's model aimed a view without
            # the angle and never asked for the stage's views).
            angle = f"angle_id {opening['angle_id']}" if opening else "no angle"
            if not place["uses_views"]:
                lines.append(
                    f"  First shot ({angle}) is in stage {place['set_name']} but uses text only; link_set asks it "
                    "for the stage's views"
                )
            elif place["blocker"]:
                hint = (
                    "; save_set_view with this angle_id aims one"
                    if place["blocker"] == "No camera for the first shot in the stage"
                    else ""
                )
                lines.append(
                    f"  First shot's stage {place['set_name']} ({angle}): {place['blocker']}{hint}"
                )
        for phase in segment["phases"]:
            lines.append(
                f"  Phase {phase['id']} r{phase['revision']} at {phase['start_frame']}: {phase['description']}"
            )
        for cut in segment["cuts"]:
            choice = state.plans[cut["id"]]["recommended"]
            plan = (
                "unknown"
                if choice is None
                else choice["technique_id"]
                + (
                    (
                        " ready"
                        if state.segment_plans.get(segment["id"], {}).get("ready")
                        else " references ready; segment not ready"
                    )
                    if choice["ready"]
                    else " not ready: " + ", ".join(a["action"] for a in choice["actions"])
                )
            )
            lines.append(
                f"Cut {cut['id']} r{cut['revision']} in segment {segment['ordinal'] + 1} at {cut['frame']}: {cut['from_angle']} -> "
                f"{cut['to_angle']}, relation {cut['relation']} ({cut['relation_source']}), plan {plan}"
            )
            if (
                choice
                and "place_views" in choice["blocks"]
                and not state.sheets.get(cut["place_id"])
            ):
                lines.append(
                    "  Sheet missing: prepare_cut assembles it for free after reference reviews."
                )
    for place in state.places:
        lines.append(
            f"Place {place['id']} r{place['revision']}: {place['name']}, text {place['text']}, "
            f"plate { {k: v for k, v in place['plate'].items() if k != 'source'} if place['plate'] else None }"
            + (f", built as stage {place['set_id']}" if place.get("set_id") else "")
        )
        for angle in place["angles"]:
            lines.append(
                f"  Angle {angle['id']} r{angle['revision']}: {angle['name']}; text {angle['description']}, "
                f"views {angle['approved']} approved/{angle['pending']} pending"
            )
    for asset in state.assets:
        lines.append(f"Source {asset['id']}: {asset['kind']} {clip(asset['name'], 80)}")
    result = ""
    for index, line in enumerate(lines):
        if estimate(result + line) > VIEW_CEILING - 30:
            result += (
                f"\n[{len(lines) - index} more lines; use read_movie for the bounded record view]"
            )
            break
        result += line + "\n"
    return result


def selection_text(selection):
    if not selection:
        return "none"
    label = "user-selected" if selection["authority"] == "user" else "provisional"
    return f"{label}: {selection['asset_id']} r{selection['revision']}"


def step_target(step, state):
    if step.kind == "build" and any(r["id"] == step.target for r in state.sets):
        return f"target exists: {step.target}; fill_set_more; the user decides its new version"
    records = {}
    for rows, followups in (
        (state.cuts, "edit_cut op=relation/insertion; plan_step"),
        (state.segments, "edit_segment op=set/move/remove; edit_phase; edit_cut op=create"),
        (state.cast, "edit_cast op=set/remove"),
        (state.places, "edit_place op=add_angle/set"),
        ([a for p in state.places for a in p["angles"]], "edit_place op=set (angle_id)"),
    ):
        records.update({r["id"]: followups for r in rows})
    # An angle of a stage's place is aimed in the stage (the live eval's model rewrote the angle's words instead).
    for place in state.places:
        if place.get("set_id"):
            records.update(
                {
                    a[
                        "id"
                    ]: f"save_set_view with this angle_id aims it in stage {place['set_id']}; link_set "
                    "uses the stage for a scene opening on it; edit_place op=set only rewrites its words"
                    for a in place["angles"]
                }
            )
    targets = [r.get("record_id") for r in reversed(step.receipts)] + [step.target]
    target = next((t for t in targets if t in records), None)
    if target:
        segment = next((s for s in state.segments if s["id"] == target), None)
        if step.kind == "build" and segment:
            return (
                f"target exists: {target}; build_set_from_take from one of its take's shots "
                f"({take_shots(segment)}); the user decides its v1"
            )
        if step.kind != "edit":
            return (
                f"target exists: {target}; "
                + {
                    "prepare": "prepare_cut; inspect_cut",
                    "render": "render_candidates",
                    "pick": "pick_provisional",
                    "review": "user review only",
                    "answer": "say",
                    "build": "build_set_from_take; the user decides its v1",
                }[step.kind]
            )
        return f"target exists: {target}; follow-up ops: {records[target]}"
    return shorten({"content": step.title}, 40)["content"]
