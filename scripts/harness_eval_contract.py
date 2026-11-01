"""The scripted contract: what a scripted outcome-family run shows beyond its outcome.

A live model may reach a task's outcome by any valid path (harness_eval_outcomes). A scripted run plays its GOOD or
BAD sequence exactly, so the row's fingerprinted criteria also fix how the run went: one recorded call per durable
turn, its final state, its refusals, the jobs it reserved, its producers in order and the candidates they rendered,
and for some groups every accepted tool in order. The scripted judge has also always checked the product's side of
the run: blind reviews, compiled window graphs, approvals that stay the user's, Undo of a drafted structure, and a
user's choice winning over a later provisional pick (the last two through `probe`, after the evidence is read).

These criteria join the outcome rubric whenever the evidence says the run was scripted (data["scripted"]); a live
run is judged by its outcome alone.
"""

from gaiavfx.shared.errors import ApplicationError

# What a group's scripted runs are also held to (harness_eval_tasks' group column): "refusals", no refusal unless
# the row's criteria name some; "tool_sequence", every accepted tool in order; "references", nothing approved and
# every survey blind; "plan", the approval contract of plan_flow.
GROUPS = {
    "references": {"refusals", "references"},
    "plans": {"tool_sequence", "plan"},
    "reading": {"tool_sequence"},
}
REMOVAL_STEPS = ["extract_frames", None, "fit_frame", None, "fit_frame"]
TAKE_LEAKS = {"seed", "job_id", "graph", "request"}
UNDO_STEPS = (
    "cut_preserves_existing_phase",
    "segment_refuses_phase_edit",
    "phase_undo_then_segment_undo",
)
UNDO_REFUSAL = "The record changed after this command; undo the later command first."


def _keys(value):
    if isinstance(value, dict):
        return set(value) | set().union(*(_keys(v) for v in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_keys(v) for v in value), set())
    return set()


def _tool(turn):
    return (turn["action"] or {}).get("tool")


def contract(task, data):
    """{criterion: passed} for a scripted run's evidence."""
    criteria, group = task.criteria, GROUPS.get(task.group, set())
    state, setup, row = data["final"], data["setup"], data["row"]
    doc, turns = row["document"], data["turns"]
    agents = [t for t in turns if t["role"] == "agent"]
    accepted = [t for t in agents if t["state"] == "accepted"]
    tools = [_tool(t) for t in accepted]
    active = turns[setup.get("check_after", 0) :]
    # The fixture's own predecessor render is not the conversation's work.
    ops = [o for o in state["operations"] if o["id"] != setup.get("fixture_render")]
    renders = [o for o in ops if o["kind"] == "render"]
    reads = [t["result"]["payload"] for t in accepted if _tool(t) == "read_candidates"]
    result = {}

    def require(name, passed):
        result[name] = bool(passed)

    # ---- the run's sequence, every row
    require("scripted_calls", len(data["calls"]) == len(agents))
    final = criteria.get("state", "idle")
    require(
        "final_state",
        row["state"] == final
        and not doc["stop_reason"]
        and (final != "idle" or (tools and tools[-1] == "say")),
    )
    refusals = criteria.get("rejected", 0 if "refusals" in group else None)
    if refusals is not None:
        require("refusals", sum(t["state"] == "rejected" for t in active) == refusals)
    expected = criteria.get("operations")
    require(
        "producers",
        all(o["state"] == "succeeded" for o in ops)
        and (
            expected is None
            or (
                len(ops) == expected
                if isinstance(expected, int)
                else [o["kind"] for o in ops] == expected
            )
        ),
    )
    if "reserved" in criteria:
        require(
            "jobs_reserved", data["payload"]["jobs_reserved"] == criteria["reserved"]
        )
    if "tool_sequence" in group:
        require("tool_sequence", tools == list(task.expected_tools))
    if "resume_markers" in criteria:
        require(
            "resume_markers",
            sum(
                t["role"] == "system"
                and "ended succeeded" in (t["action"] or {}).get("message", "")
                for t in turns
            )
            == criteria["resume_markers"],
        )
    if criteria.get("prelude"):
        require(
            "prelude_only",
            len(ops) == 1
            and ops[0]["kind"] == "survey"
            and all(
                "prepare_cut" not in (t["provenance"] or {}).get("tools", [])
                for t in active
            ),
        )
    require(
        "unranked_reads",
        all(not p.get("ranking") and "revealed" not in p for p in reads),
    )
    require(
        "text_renders_unsheeted",
        all(
            o["request"].get("technique") != "text" or "sheet_asset" not in o["request"]
            for o in renders
        ),
    )

    # ---- what the scripted judge checked per group and per criterion
    result.update(_references(task, data, ops, active, "references" in group))
    result.update(_takes(task, data, ops, renders, reads, agents, accepted))
    if "cut_frame" in criteria:
        result.update(_draft(task, data, accepted))
    if "plan" in group:
        flow = criteria["plan_flow"]
        result.update(_plan(task, data, flow, ops, agents, accepted))
    if criteria.get("reading") or criteria.get("storyline"):
        result.update(_reading(task, data, accepted))
    if "segment_cuts" in criteria:
        result["window"] = _window(data, renders, criteria["segment_cuts"])
    return result


def _current(state, segment_id):
    """A segment's current take selection, as SelectionService.current reads it."""
    return next(
        (
            s
            for s in state["selections"]
            if s["segment_id"] == segment_id and s["superseded_at"] is None
        ),
        None,
    )


def _cut(state, cut_id):
    return next(
        (c for s in state["movie"]["segments"] for c in s["cuts"] if c["id"] == cut_id),
        {},
    )


def _references(task, data, ops, active, untouched):
    """Cut references and their reviews (E1-E7): nothing approved, every survey blind, the policy reported."""
    criteria, state, setup = task.criteria, data["final"], data["setup"]
    result = {}
    if untouched:
        surveyed = {e["angle_id"] for e in state["evidence"]}
        result["references_unapproved"] = not any(
            e["approved"] for e in state["evidence"]
        ) and not any(k["cut_id"] == setup["cut_id"] for k in state["pins"])
        result["blind_surveys"] = all(
            (review := data["blind"].get(o["id"])) is not None
            and review["purpose"] == "angle_views"
            and bool(review["unranked"])
            and {setup["angles"][1], setup["angles"][2]} <= surveyed
            for o in ops
            if o["kind"] == "survey"
        )
    cut = _cut(state, setup.get("cut_id"))
    if "technique" in criteria:
        inspections = [
            t["result"]["payload"]["plan"]
            for t in active
            if _tool(t) == "inspect_cut" and t["state"] == "accepted"
        ]
        choice = inspections[-1]["recommended"] if inspections else None
        result["inspected_policy"] = bool(
            cut.get("relation") == criteria["relation"]
            and cut.get("relation_source") == "authored"
            and choice
            and choice["technique_id"] == criteria["technique"]
            and not choice["ready"]
            and "view_angle" in [a["action"] for a in choice["actions"]]
        )
    if "revision" in criteria:
        refused = [t for t in active if t["state"] == "rejected"]
        result["stale_retry"] = bool(
            cut.get("revision") == criteria["revision"]
            and cut.get("relation") == "reverse"
            and refused
            and f"revision is {criteria['revision'] - 1}"
            in refused[0]["result"]["discrepancy"]
        )
    removals = [o for o in ops if o["kind"] == "removal"]
    if removals:
        result["removal_chain"] = all(
            [j["step"] for j in o["jobs"]] == REMOVAL_STEPS for o in removals
        )
    return result


def _takes(task, data, ops, renders, reads, agents, accepted):
    """Takes, picks, sheets and the chain (E8-E11, G1): blind, unselected, continuing from the right choice."""
    criteria, state, setup = task.criteria, data["final"], data["setup"]
    doc = data["row"]["document"]
    result = {}
    if "candidates" in criteria:
        takes = [p for p in reads if p.get("purpose") == "segment_takes"]
        result["candidates"] = bool(
            len(renders) == 1
            and len(renders[0]["candidates"]) == criteria["candidates"]
            and _current(state, setup["segment_id"]) is None
            and takes
            and all(not _keys(p) & TAKE_LEAKS for p in takes)
        )
    if criteria.get("preselect"):
        # The user chose the predecessor: nothing to pick, and the render continues from that choice.
        result["user_predecessor"] = not any(
            _tool(t) == "pick_provisional" for t in agents
        ) and all(
            (o["request"].get("context_source") or {}).get("authority") == "user"
            and o["request"]["context_source"].get("basis") is None
            for o in renders[:1]
        )
    if criteria.get("preselect") is False:
        selected = _current(state, setup["first_segment_id"])
        picked = "pick_provisional" in [_tool(t) for t in accepted]
        result["provisional_pick"] = bool(
            selected
            and selected["authority"] == "provisional"
            and selected["basis"] == "unjudged"
            and any(
                r.get("kind") == "selection" and r.get("basis") == "unjudged"
                for t in accepted
                for r in t["receipts"]
            )
            and any(
                "unjudged" in entry["text"]
                and setup["first_segment_id"] in entry["text"]
                for entry in doc["board"]["decisions"]
            )
            and all(
                o["request"]["context_source"]["basis"] == "unjudged"
                and o["request"]["context_source"]["authority"] == "provisional"
                for o in renders[:1]
            )
        )
        if picked:
            result["user_choice_wins"] = bool(
                (data.get("probes") or {}).get("user_choice_wins")
            )
    if criteria.get("stale_sheet"):
        sheets = [o for o in ops if o["kind"] == "assemble"]
        approved = {}
        for e in state["evidence"]:
            approved.setdefault(e["angle_id"], [])
            if e["approved"]:
                approved[e["angle_id"]].append(e)
        order = [
            e["id"]
            for items in approved.values()
            for e in sorted(items, key=lambda e: (e["approved_at"] or "", e["id"]))
        ]
        result["sheet_order"] = not (renders and len(sheets) == 2) or bool(
            renders[0]["request"]["sheet_asset"]
            == sheets[-1]["candidates"][0]["asset_id"]
            and [t for t in sheets[-1]["request"]["tiles"] if isinstance(t, str)]
            == order
            and len(order) == 3
        )
    if criteria.get("chain"):
        pins = [k for k in state["pins"] if k["cut_id"] == setup["cut_id"]]
        evidence = [e for e in state["evidence"] if e["angle_id"] in setup["angles"]]
        result["user_approvals"] = bool(
            evidence
            and pins
            and all(
                e["approved_by"] == "user" for e in [*evidence, *pins] if e["approved"]
            )
            and _current(state, setup["segment_id"]) is None
        )
    return result


def _draft(task, data, accepted):
    """A drafted structure (E12): its receipts, its reverse at the cut and the Undo order."""
    criteria, state = task.criteria, data["final"]
    doc = data["row"]["document"]
    segments = state["movie"]["segments"]
    edits = [t for t in accepted if _tool(t).startswith("edit_")]
    receipts = [r for t in edits for r in t["receipts"]]
    second = segments[1] if len(segments) == 2 else {"phases": [], "cuts": []}
    cuts, frame = second["cuts"], criteria["cut_frame"]
    plan = state["plans"].get(cuts[0]["id"], {}) if cuts else {}
    return {
        "draft_contract": bool(
            len(segments) == criteria["segments"]
            and [s["ordinal"] for s in segments] == criteria["ordinals"]
            and all(t["receipts"] for t in edits)
            and len(receipts) == criteria["receipts"]
            and all(
                any(r["id"] in entry["text"] for entry in doc["board"]["decisions"])
                for r in receipts
            )
            and len(second["phases"]) == 2
            and cuts
            and cuts[0]["frame"] == frame
            and (plan.get("recommended") or {}).get("technique_id") == "views_and_pin"
            and any(p["start_frame"] == frame for p in second["phases"])
            and state["structure_valid"]
        ),
        "draft_undo": (data.get("probes") or {}).get("draft_undo") == criteria["undo"],
    }


def _plan(task, data, flow, ops, agents, accepted):
    """A plan's approval contract: proposed before work, the grant as approved, the review it waits for."""
    setup, doc, turns = data["setup"], data["row"]["document"], data["turns"]
    if flow == "off":
        return {
            "direct_tools": all(
                not {"propose_plan", "plan_step"} & set(t["provenance"]["tools"])
                for t in agents
            )
            and len(ops) == 1
            and ops[0]["kind"] == "survey"
            and all(
                t["provenance"]["context"]["state"].startswith("movie/prepare/")
                for t in agents
            )
        }
    before = setup.get("before_approval") or {}
    result = {
        "proposed_first": bool(
            before.get("activity") == "awaiting_approval"
            and before.get("plan") is not None
            and not any(t["receipts"] for t in setup.get("before_turns", []))
        )
    }
    if flow == "reject":
        rejected = setup.get("rejected", {})
        result["rejection_kept"] = bool(
            rejected.get("state") == "idle"
            and rejected.get("plan") is None
            and rejected.get("plan_history", [{}])[-1].get("state") == "rejected"
            and doc["plan"] is None
            and not ops
        )
        return result
    plan = doc["plan"] or {}
    grant = plan.get("grant")
    kind = {"survey": "prepare", "render": "render", "edit": "edit"}[flow]
    result["grant_as_approved"] = bool(
        plan.get("state") == "done"
        and grant
        and grant["budget_jobs"] == task.criteria["jobs"]
        and grant["step_kinds"] == ([kind, "review"] if flow == "render" else [kind])
        and all(
            not {"prepare_cut", "render_candidates", "edit_cut"}
            & set(t["provenance"]["tools"])
            for t in agents
            if t["ordinal"] < grant["approved_turn"]
        )
        and all(
            t["ordinal"] > grant["approved_turn"] for t in accepted if t["receipts"]
        )
    )
    if not grant:
        return result
    if task.criteria.get("revise"):
        result["revision_kept"] = bool(
            plan["revision"] == 2
            and doc["plan_history"][-1]["state"] == "revised"
            and doc["plan_history"][-1]["revision"] == 1
        )
    if flow == "edit":
        result["edit_in_grant"] = bool(
            not ops
            and plan["steps"][0]["receipts"]
            and all(
                not {"prepare_cut", "render_candidates"} & set(t["provenance"]["tools"])
                for t in agents
            )
        )
        return result
    pending = (setup.get("before_review") or {}).get("plan")
    step = 0 if flow == "survey" else 1
    producer = "prepare_cut" if flow == "survey" else "render_candidates"
    result["review_resumed"] = bool(
        len(ops) == 1
        and ops[0]["kind"] == flow
        and ops[0]["state"] == "succeeded"
        and pending
        and (flow != "render" or pending["steps"][0]["status"] == "done")
        and pending["steps"][step]["status"] == "awaiting_user"
        and (pending["steps"][step].get("review") or {}).get("operation_id")
        in {o["id"] for o in ops}
        and setup.get("no_false_resume")
        and setup.get("auto_resumed")
        and any(
            t["role"] == "system"
            and (t["action"] or {})
            .get("message", "")
            .startswith(f"The user reviewed {flow}")
            for t in turns
        )
        and any(
            _tool(t) == producer and t["provenance"]["jobs_limit"] == 1
            for t in accepted
        )
    )
    if flow == "survey":
        # A step status cannot prove that its references were approved. In S7q a
        # survey-only request leaves the cut's remaining preparation explicitly skipped.
        result["review_complete"] = bool(
            ops
            and all(o["id"] not in data["final"]["pending"] for o in ops)
            and plan["steps"][0]["status"] == "skipped"
        )
    else:
        result["review_complete"] = plan["steps"][1]["status"] == "done"
        result["one_revised_take"] = bool(
            len(ops) == 1
            and len(ops[0]["candidates"]) == 1
            and not any(
                r.get("kind") == "selection" for t in agents for r in t["receipts"]
            )
        )
    return result


def _reading(task, data, accepted):
    """Reading attached references (E18, E19) and a storyline drafted under a plan (E20)."""
    criteria, setup = task.criteria, data["setup"]
    doc, turns = data["row"]["document"], data["turns"]
    result = {"no_operations": not data["final"]["operations"]}
    if criteria.get("reading"):
        result["discussion_unrecorded"] = not doc["plan"] and not any(
            t["receipts"] for t in turns
        )
        if criteria["reading"] == "video":
            # The second read reuses the cached frames.
            result["video_cache"] = bool(data["reading_frames"]) and data[
                "sample_calls"
            ] in (None, 1)
    if criteria.get("storyline"):
        segments = data["final"]["movie"]["segments"]
        drafts = [t for t in accepted if _tool(t) == "draft_storyline"]
        plan = doc["plan"]
        receipts = drafts[0]["receipts"] if drafts else []
        result["storyline"] = bool(
            drafts
            and plan
            and plan["grant"]
            and plan["state"] == "done"
            # The draft is one authored intent: its three segments are one Receipt.
            and len(receipts) == 1
            and receipts[0]["kind"] == "direction_receipt"
            and drafts[0]["ordinal"] > plan["grant"]["approved_turn"]
            and not any(t["receipts"] for t in setup.get("before_turns", []))
            and all(
                any(r["id"] in e["text"] for e in doc["board"]["decisions"])
                for r in receipts
            )
            and plan["steps"][0]["receipts"] == receipts
            and [s["ordinal"] for s in segments] == [0, 1, 2]
            and all(s["delivered_frames"] == 102 for s in segments)
            and data["final"]["structure_valid"]
            # One Undo of the draft removes every segment (harness_eval_outcome_state.undo_probe).
            and data["undo_valid"]
        )
    return result


def _window(data, renders, count):
    """A whole segment rendered as one window: chained cuts, both pins at their frames, the right handoff."""
    setup = data["setup"]
    if len(renders) != 1:
        return False
    request = renders[0]["request"]
    segment = next(
        s for s in data["final"]["movie"]["segments"] if s["id"] == setup["segment_id"]
    )
    cuts = segment["cuts"]
    graph = renders[0]["jobs"][0]["graph"]
    prompt = next(
        n["inputs"]["prompt"]
        for n in graph.values()
        if n["class_type"] == "MiniMaxH3ReferenceToVideo"
    )
    guides = [
        n["inputs"]["frame_idx"]
        for n in graph.values()
        if n["class_type"] == "MiniMaxH3AddGuide"
    ]
    review = data["blind"].get(renders[0]["id"]) or {"context": {}}
    return bool(
        all(
            cuts[i]["from_angle_id"] == cuts[i - 1]["to_angle_id"]
            for i in range(1, len(cuts))
        )
        and len(request["cut_ids"]) == count
        and request["segment_id"] == setup["segment_id"]
        # Both cut-relative pins sit at cut + 12 + 22 context frames.
        and (not count or guides == [70, 106])
        # docs/CONTINUATION.md: an uncut continuation follows the rule to masked handoff, a cut keeps paired.
        and ("motion_context" if count else "masked_context") in graph
        and (count or "single continuous take" in prompt.lower())
        and prompt.count("the shot cuts.") == count
        and review["context"].get("cut_ids") == request["cut_ids"]
        and "cut_id" not in review["context"]
    )


# ---------------------------------------------------------------- probes


class _Rollback(Exception):
    pass


def probe(task, work, setup, harness, data):
    """Exercise what the contract checks after the conversation, once its evidence is read: Undo of a drafted
    structure (always rolled back) and a user's choice over a later provisional pick (the user's choice is kept)."""
    result = {}
    if "undo" in task.criteria:
        result["draft_undo"] = draft_undo(work, data, task.criteria["cut_frame"])
    picked = any(
        t["role"] == "agent"
        and t["state"] == "accepted"
        and _tool(t) == "pick_provisional"
        for t in data["turns"]
    )
    if picked and setup.get("fixture_render"):
        result["user_choice_wins"] = user_choice_wins(work, setup, harness, data)
    return result


def draft_undo(work, data, frame):
    """Undo the drafted cut (its phase stays), refuse Undo of a segment whose phase changed, then unwind in order."""
    from gaiavfx.modules.direction.service import DirectionService

    direction = DirectionService()
    outcome = dict.fromkeys(UNDO_STEPS, False)
    receipts = [
        r
        for t in data["turns"]
        if t["role"] == "agent"
        and t["state"] == "accepted"
        and (_tool(t) or "").startswith("edit_")
        for r in t["receipts"]
    ]
    segments = data["final"]["movie"]["segments"]
    cut_receipt = next((r for r in receipts if r.get("command") == "create_cut"), None)
    if len(segments) != 2 or cut_receipt is None:
        return outcome
    second = segments[1]
    try:
        with work.db.transaction() as session:
            direction.undo(session, cut_receipt["id"])
            after = direction.read_segment(session, second["id"])
            phase = next(
                (p for p in after["phases"] if p["start_frame"] == frame), None
            )
            outcome[UNDO_STEPS[0]] = not after["cuts"] and phase is not None
            if phase is None:
                raise _Rollback
            # Human commands exercise the ordered Undo boundary; the harness itself never gains an Undo tool.
            edited = direction.update_phase(
                session,
                phase["id"],
                phase["revision"],
                content={
                    **phase["content"],
                    "description": "The walker stops at the railing.",
                },
            )
            created = next(
                (
                    r
                    for r in receipts
                    if r.get("command") == "add_segment"
                    and direction.read_receipt(session, r["id"])["after"]["record"][
                        "id"
                    ]
                    == second["id"]
                ),
                None,
            )
            if created is None:
                raise _Rollback
            try:
                direction.undo(session, created["id"])
            except ApplicationError as exc:
                outcome[UNDO_STEPS[1]] = exc.status == 409 and str(exc) == UNDO_REFUSAL
            if outcome[UNDO_STEPS[1]]:
                direction.undo(session, edited["receipt"]["id"])
                removed = direction.undo(session, created["id"])
                outcome[UNDO_STEPS[2]] = removed["record"] == {
                    "id": second["id"],
                    "removed": True,
                }
            raise _Rollback
    except (_Rollback, ApplicationError):
        pass
    return outcome


def user_choice_wins(work, setup, harness, data):
    """The user selects the predecessor's take; a later provisional pick must be refused. The selection is kept."""
    from gaiavfx.application.candidate_review import select_take
    from gaiavfx.application.harness.session import AgentSession
    from gaiavfx.application.harness.tools.picks import PickProvisional
    from gaiavfx.modules.editing.selections.service import SelectionService
    from gaiavfx.modules.generation.operations.review import ReviewService

    selections = SelectionService()
    with work.db.transaction() as session:
        selected = selections.current(
            session, setup["movie_id"], setup["first_segment_id"]
        )
        if selected:
            review = ReviewService().blind(session, setup["fixture_render"])
            select_take(
                session,
                review_set_id=review["id"],
                candidate_id=review["members"][0]["candidate_id"],
                authority="user",
                expected_revision=selected["revision"],
            )
    row = data["row"]
    context = harness.context(
        row, AgentSession.model_validate(row["document"]), data["turns"]
    )
    with work.db.transaction() as session:
        revision = selections.revision(
            session, setup["movie_id"], setup["first_segment_id"]
        )
    tool = PickProvisional()
    result = tool.run(
        context,
        tool.Input(
            review_set_id=setup["fixture_render"],
            letter="A",
            expected_revision=revision,
        ),
    )
    return not result.ok and "cannot replace a user selection" in result.discrepancy
