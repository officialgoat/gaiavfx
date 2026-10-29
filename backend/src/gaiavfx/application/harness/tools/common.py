"""Small transaction and refusal helpers shared by the world tools."""

import re

from gaiavfx.application.harness.actions import Tool, ToolResult
from gaiavfx.application.harness.board import write_entry
from gaiavfx.shared.errors import ApplicationError


def refusal(message):
    return ToolResult(ok=False, discrepancy=str(message))


TOOL_KINDS = {
    **dict.fromkeys(("edit_cut", "edit_place", "edit_segment", "edit_phase", "edit_cast"), "edit"),
    "draft_storyline": "edit",
    "prepare_cut": "prepare",
    "render_candidates": "render",
    "replace_character": "render",
    "start_recast_run": "render",
    "pick_provisional": "pick",
    "save_set_view": "edit",
    "link_set": "edit",
    "build_set_from_take": "build",
    "fill_set_more": "build",
}

# ADR 0012 §8 (Amendment 7): a film's agent reads the recast, proposes casts and
# appearances, plans and talks, and starts a recast run only inside an approved plan.
# Analysis, retakes, head passes, pausing, cancelling, choosing, confirming and
# conforming stay human; none of them is a harness tool.
FILM_TOOLS = frozenset(
    {
        "read_recast",
        "propose_cast",
        "propose_appearance",
        "start_recast_run",
        "propose_plan",
        "plan_step",
        "say",
        "ask_human",
        "board_write",
        "describe_reference",
    }
)


def current_step(context):
    document = context.document
    if not document or not document.plan_mode or not document.plan:
        return None
    if document.plan.state != "executing":
        return None
    return next((s for s in document.plan.steps if s.status in {"pending", "awaiting_user"}), None)


# Edits that supply a preparation's missing inputs (place and angle text, phase
# screen direction, the cut's relation or insertion, cast identity). Drafting and
# segment structure are not preparation; offering them overflowed the schema.
PREPARATION_EDITS = {"edit_place", "edit_phase", "edit_cut", "edit_cast"}


def operation_receipt(operation, **extra) -> dict:
    """The receipt of an operation a tool started or joined: what it is and what it reserved, read from the operation
    itself, so the session's budget (`commands.jobs_reserved`) and the plan step's (`plan_costs.step_jobs_left`)
    count it. `kind` names the receipt; `operation_kind` the producer (one JSON key cannot carry both)."""
    return {
        "kind": "operation",
        "id": operation["id"],
        "operation_kind": operation["kind"],
        "budget_reserved": operation["budget_reserved"],
        **extra,
    }


def step_applicable(context, name):
    """Focus the grammar on the current step; the immutable grant still owns authority."""
    step = current_step(context)
    if not step:
        return True
    if step.kind == "render" and name == "prepare_cut":
        from gaiavfx.application.harness.tools.preparation_progress import render_sheet_targets

        return bool(render_sheet_targets(context))
    return (
        name
        in {
            "read_movie",
            "read_segment",
            "inspect_cut",
            "read_candidates",
            "describe_reference",
            "analyze_reference_video",
            "analyze_people",
            "read_recast",
            "propose_cast",
            "propose_appearance",
            "read_set",
            "mark_set_issue",
            "plan_step",
            "ask_human",
            "say",
            "propose_plan",
        }
        or TOOL_KINDS.get(name) == step.kind
        or (step.kind == "prepare" and name in PREPARATION_EDITS)
    )


def granted(context, kind):
    document = context.document
    if not document or not document.plan_mode:
        # A build always needs an approved plan's grant (ADR 0019 §7): no autonomy setting stands in for it.
        return kind != "build"
    plan = document.plan
    return bool(
        plan
        and plan.state == "executing"
        and plan.grant
        and kind in plan.grant.step_kinds
        and not any(step.status == "awaiting_user" for step in plan.steps)
    )


def permitted(context, kind):
    if context.document and context.document.plan_mode:
        return granted(context, kind)
    return kind == "edit" or context.autonomy in (
        {"prepare", "render"} if kind == "prepare" else {"render"}
    )


def tool_schema(context, tool):
    """One availability decision for the grammar and recovery instructions."""
    if getattr(context, "scope_kind", "") == "film" and tool.name not in FILM_TOOLS:
        return None
    if tool.reading_refusal(context) or not step_applicable(context, tool.name):
        return None
    if tool.name in TOOL_KINDS and not granted(context, TOOL_KINDS[tool.name]):
        return None
    return tool.narrow(context)


def available(context, name):
    from gaiavfx.application.harness.tools import DEFAULT_TOOLS

    tool = next((tool for tool in DEFAULT_TOOLS if tool.name == name), None)
    return tool is not None and tool_schema(context, tool) is not None


def revision_guidance(context, kind="edit"):
    if available(context, "propose_plan"):
        return f"Use propose_plan to propose a revised plan adding an {kind} step; the user approves it before execution."
    return "Read any pending references or results, then propose a revised plan for user approval."


def truthful_refusal(context, message):
    """Do not let a lower-level refusal advertise an unavailable harness tool."""
    from gaiavfx.application.harness.tools import DEFAULT_TOOLS

    missing = [
        t.name
        for t in DEFAULT_TOOLS
        if re.search(rf"\b{t.name}\b", message)
        and not available(context, t.name)
        and (t.name != "say" or re.search(r"\b(?:[Uu]se|[Cc]all|with) say\b|\bsay:", message))
    ]
    for name in missing:
        message = re.sub(rf"\b{name}(?:\.[\w.]+)?\b", "the required action", message)
    plan = context.document.plan if context.document else None
    lacking = [
        TOOL_KINDS[name]
        for name in missing
        if name in TOOL_KINDS
        and plan
        and context.document.plan_mode
        and plan.state == "executing"
        and plan.grant
        and TOOL_KINDS[name] not in plan.grant.step_kinds
    ]
    return (revision_guidance(context, lacking[0]) + " " + message) if lacking else message


def revision(row, expected, kind, tool):
    if row["revision"] != expected:
        raise ApplicationError(
            f"{kind} revision is {row['revision']}. Call {tool} again with "
            f"expected_revision {row['revision']} after re-reading if needed.",
            409,
        )


def candidates(rows):
    return (
        ", ".join(
            f"{row.get('name') or row.get('title') or 'record'} ({row['id']})" for row in rows[:8]
        )
        or "none; create a record first"
    )


def scoped(rows, record_id, kind):
    row = next((row for row in rows if row["id"] == record_id), None)
    if row is None:
        if not record_id:
            raise ApplicationError(f"{kind} is required here: {candidates(rows)}.", 422)
        raise ApplicationError(
            f"{kind} is not in this session's scope. Candidates: {candidates(rows)}. "
            "Use read_movie for more scoped records.",
            422,
        )
    return row


def direction_receipt(row):
    receipt = row["receipt"]
    return {
        "kind": "direction_receipt",
        **{key: receipt[key] for key in ("id", "command", "target_kind", "target_id")},
        "record_id": row["id"],
    }


def edited(context, row, *, structural=False, receipts=None):
    receipts = receipts if receipts is not None else [direction_receipt(row)]
    if structural:
        for receipt in receipts:
            write_entry(
                context.board,
                "decisions",
                f"{receipt['command']} {receipt['target_kind']} {receipt['target_id']} "
                f"(receipt {receipt['id']})",
                context.turn,
            )
    return ToolResult(
        payload={
            **{key: row[key] for key in ("id", "revision", "removed") if key in row},
            "revision": row.get(
                "revision", row.get("receipt", {}).get("after", {}).get("revision")
            ),
            **{
                r["target_kind"] + "_revision": r["after"]["revision"]
                for r in [row.get("receipt", {})]
                if "revision" in r.get("after", {})
            },
        },
        receipts=receipts,
        changed=True,
    )


class TransactionTool(Tool):
    def _run(self, context, value):
        from gaiavfx.application.harness.tools.step_targets import require_target

        try:
            require_target(context, self.name, value.model_dump())
            if self.name in TOOL_KINDS and (
                not granted(context, TOOL_KINDS[self.name])
                or not step_applicable(context, self.name)
            ):
                raise ApplicationError(
                    "Not in the approved plan's current step. "
                    + revision_guidance(context, TOOL_KINDS[self.name]),
                    422,
                )
            if self.name in TOOL_KINDS:
                text = value.model_dump()
                # Insertion resolves known ids and reports all invalid fields together.
                text.pop("insertion", None)
                validate_text(text)
            with context.db.transaction() as session:
                return self.execute(session, context, value)
        except ApplicationError as exc:
            return refusal(exc)


UUID_TEXT = re.compile(
    r"(?i)(?<![0-9a-f])(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32})(?![0-9a-f])"
)
TEXT_FIELDS = {
    "name",
    "title",
    "description",
    "shot",
    "style",
    "sound",
    "overhead",
    "noun",
    "landmarks",
    "screen_direction",
    "who",
    "looks",
    "plate",
    "keep",
    "where",
    "size",
    "behind",
    "distance_description",
    "along",
    "barrier",
    "feet_placement",
    "occlude",
    "whom",
}


def validate_text(value, field=""):
    """ID fields are typed separately; prose must not accidentally become an asset binding."""
    if isinstance(value, dict):
        for key, item in value.items():
            validate_text(item, key)
    elif isinstance(value, list):
        for item in value:
            validate_text(item, field)
    elif isinstance(value, str) and UUID_TEXT.search(value):
        if field == "whom":
            raise ApplicationError(
                "Write who to remove in plain words, for example 'Arin' or 'the man in the plaid shirt'; "
                "use empty: true if nobody is in the image",
                422,
            )
        if field == "keeps":
            raise ApplicationError(
                "Use identity_asset for the picture; keeps lists what the person keeps, "
                "such as a satchel. Set edit_cast.identity_asset_id.",
                422,
            )
        if field in TEXT_FIELDS:
            raise ApplicationError(
                f"{field} is text, not an asset id. Use edit_cast.identity_asset_id for "
                "an identity picture or prepare_cut.plate.source_asset for a place image.",
                422,
            )
