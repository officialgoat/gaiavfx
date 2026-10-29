"""Context assembly under a token budget.

The summary, whiteboard, current view and human request stay. Older messages
leave first, then older turns of the current message are summarized.
"""

import json
from dataclasses import dataclass, field

from gaiavfx.application.harness.board import render_board
from gaiavfx.application.harness.message_window import window_current
from gaiavfx.shared.errors import ApplicationError

# The system prompt, whiteboard and current view bound genuinely bounded content, so they
# stay fixed caps. History does not: it is whatever the window has left.
SYSTEM_CEILING = 700
HEAD_CEILING = 1800
SCHEMA_CEILING = 4500
BOARD_CEILING = 1500
VIEW_CEILING = 2000
SUMMARY_CEILING = 450
# History used to be a constant 3000 tuned for a 16k window, which meant a larger context
# bought no memory at all. Measured over 979 recorded turns it sat pinned at its maximum
# (2998) and discarded up to 27 messages in a single turn, every one of them on the longest
# task -- the only task with failures left. It is now the remainder of the context.
MINIMUM_TURNS_BUDGET = 1024
# Legacy ungrouped callers retain a per-result cap. Durable sessions use whole
# human-message groups and never shorten the current request or recent turns.
MESSAGE_SHARE = 0.5
MESSAGE_CEILING_MAX = 6000
OUTPUT_RESERVE = 1024
SAFETY = 256
IMAGE_TOKENS = 2048
TEMPLATE_RESERVE = 512


class TurnMessages(list):
    """Rendered history with human-message boundaries, never sent as wire metadata."""

    def __init__(self):
        super().__init__()
        self.starts = []
        self.turn_starts = []


def request_tokens(messages):
    # UUIDs and escaped JSON cost more than ordinary prose. Count serialized wire
    # messages at two UTF-8 bytes/token, plus chat framing, rather than content at
    # three. The transport's runtime tokenizer remains the admission backstop.
    return sum(
        (len(json.dumps(m, ensure_ascii=False).encode("utf-8")) + 1) // 2 + 16 for m in messages
    )


def estimate(text):
    return (len(str(text).encode("utf-8")) + 2) // 3


def message_tokens(messages):
    total = 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            total += estimate(content)
        elif isinstance(content, list):
            total += sum(
                estimate(part.get("text", "")) for part in content if isinstance(part, dict)
            )
    return total


def shorten(message, limit):
    content = message.get("content")
    if not isinstance(content, str) or estimate(content) <= limit:
        return message
    marker = " …[truncated]"
    keep = max(0, limit * 3 - len(marker.encode("utf-8")) - 2)
    return {
        **message,
        "content": content.encode("utf-8")[:keep].decode("utf-8", errors="ignore") + marker,
    }


@dataclass
class Assembly:
    messages: list
    report: dict = field(default_factory=dict)
    history_offset: int = 0
    history_groups: list[int] = field(default_factory=list)
    current_units: list[list] = field(default_factory=list)
    current_skipped: int = 0


def assemble_reading(
    system,
    instruction,
    question,
    parts,
    schema,
    context_size,
    output,
    *,
    schema_billed=False,
    state="movie/plan/reading",
):
    """A reading sees only instructions, pixels and a question, never ordinary history."""
    content = [{"type": "text", "text": instruction}, *parts]
    if question:
        content.append({"type": "text", "text": question})
    messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
    text = message_tokens(messages)
    images = sum(part.get("type") == "image_url" for part in parts)
    schema_tokens = estimate(json.dumps(schema, separators=(",", ":"), ensure_ascii=False))
    for name, size, ceiling in (
        ("system", estimate(system), SYSTEM_CEILING),
        ("head", text, HEAD_CEILING),
        ("schema", schema_tokens, SCHEMA_CEILING),
    ):
        if size > ceiling:
            raise ApplicationError(
                f"The reading context {name} uses {size} > {ceiling} tokens.", 422
            )
    reserve = schema_tokens if schema_billed else 0
    fixed = text + images * IMAGE_TOKENS + reserve + output + SAFETY
    if fixed > context_size:
        raise ApplicationError(
            f"The reading context needs {fixed} tokens including {images * IMAGE_TOKENS} image tokens; the {context_size}-token context cannot hold them.",
            422,
        )
    return Assembly(
        messages,
        {
            "state": state,
            "system": estimate(system),
            "head": text,
            "schema": schema_tokens,
            "images": images,
            "image_reserve": images * IMAGE_TOKENS,
            "schema_reserve": reserve,
            "fixed": fixed,
            "context": context_size,
            "output_reserve": output,
            "branches": 1,
            "board": 0,
            "view": 0,
            "summary": 0,
            "turns": 0,
            "dropped_turns": 0,
            "turns_budget": context_size - fixed,
            "message_ceiling": 0,
        },
    )


def turn_messages(turns):
    messages = TurnMessages()
    for turn in turns:
        if turn["state"] == "requested":
            continue
        messages.turn_starts.append(len(messages))
        if turn["role"] == "human" or not messages.starts:
            messages.starts.append(len(messages))
        if turn["role"] in {"human", "system"}:
            action = turn.get("action") or {}
            content = action.get("message", "")
            for attachment in action.get("attachments", []):
                content += (
                    f"\n[attached {attachment['kind']} {attachment['asset_id']} "
                    + json.dumps(attachment["name"], ensure_ascii=False)
                    + "]"
                )
            messages.append({"role": "user", "content": content})
        elif turn["state"] != "requested":
            if turn.get("action"):
                messages.append(
                    {"role": "assistant", "content": json.dumps(turn["action"], ensure_ascii=False)}
                )
            if turn.get("result"):
                messages.append(
                    {
                        "role": "user",
                        "content": "Tool result: " + json.dumps(turn["result"], ensure_ascii=False),
                    }
                )
    return messages


def assemble(
    system,
    brief,
    catalog,
    schema,
    board,
    view,
    messages,
    context_size,
    output=OUTPUT_RESERVE,
    safety=SAFETY,
    schema_billed=False,
    state="",
    summary="",
    knowledge="",
):
    # Measure every schema. Reserve its tokens only when the connection bills them;
    # a sampler grammar does not consume the prompt window.
    schema_tokens = estimate(json.dumps(schema, separators=(",", ":"))) if schema else 0
    # The brief is the top rung of the ladder and the only one never shown. It reached
    # the model only if it thought to call read_movie, and a cloud session asked why
    # "the brief itself didn't come through" before planning a movie out of nothing. It
    # sits in the head rather than the view because it is stable: beside the cast and the
    # segments it would invalidate the cached prefix every time one of those moved.
    head = system + ((f"\n\nThe human's brief for this movie:\n{brief}") if brief else "")
    head += ("\n\nAvailable tools:\n" + catalog) if catalog else ""
    head += ("\n\nKnowledge:\n" + knowledge) if knowledge else ""
    head_tokens = estimate(head)
    board_text = render_board(board)
    board_tokens = estimate(board_text)
    view_tokens = estimate(view)
    problems = []
    summary_text = "Summary so far:\n" + summary if summary else ""
    summary_tokens = estimate(summary_text)
    if summary_tokens > SUMMARY_CEILING:
        problems.append(f"the summary uses {summary_tokens} > {SUMMARY_CEILING} tokens")
    if estimate(system) > SYSTEM_CEILING:
        problems.append(f"the system uses {estimate(system)} > {SYSTEM_CEILING} tokens")
    if schema_tokens > SCHEMA_CEILING:
        problems.append(f"the schema uses {schema_tokens} > {SCHEMA_CEILING} tokens")
    if head_tokens > HEAD_CEILING:
        problems.append(f"the head uses {head_tokens} > {HEAD_CEILING} tokens")
    if board_tokens > BOARD_CEILING:
        problems.append(f"the whiteboard uses {board_tokens} > {BOARD_CEILING} tokens")
    if view_tokens > VIEW_CEILING:
        problems.append(f"the current view uses {view_tokens} > {VIEW_CEILING} tokens")
    if problems:
        raise ApplicationError(
            "The agent context cannot be assembled: " + "; ".join(problems) + ".",
            422,
        )
    schema_reserve = schema_tokens if schema_billed else 0
    prefix = [
        {"role": "system", "content": head},
        {"role": "user", "content": "Whiteboard:\n" + (board_text or "(empty)")},
        *([{"role": "user", "content": summary_text}] if summary else []),
    ]
    suffix = [{"role": "user", "content": "Current view:\n" + view}]
    fixed = request_tokens(prefix + suffix) + TEMPLATE_RESERVE + output + safety + schema_reserve
    if fixed + MINIMUM_TURNS_BUDGET > context_size:
        raise ApplicationError(
            f"The agent context needs {fixed} tokens for the system, whiteboard, current view, "
            f"response schema, output reserve and safety, leaving less than the "
            f"{MINIMUM_TURNS_BUDGET} tokens of history a turn needs; the {context_size}-token "
            "context cannot hold them. The whiteboard and current view are never dropped.",
            422,
        )
    turns_budget = context_size - fixed
    message_ceiling = min(MESSAGE_CEILING_MAX, max(1, int(turns_budget * MESSAGE_SHARE)))
    kept, kept_tokens = [], 0
    dropped_messages = []
    starts = getattr(messages, "starts", [])
    current_units, current_skipped, current_dropped = [], 0, 0
    if starts:
        groups = [messages[a:b] for a, b in zip(starts, [*starts[1:], len(messages)], strict=True)]
        kept = list(groups[-1])
        kept_tokens = request_tokens(kept)
        boundaries = [n for n in messages.turn_starts if n >= starts[-1]]
        current_units = [
            messages[a:b] for a, b in zip(boundaries, [*boundaries[1:], len(messages)], strict=True)
        ]
        if kept_tokens > turns_budget:
            window = window_current(current_units, turns_budget, request_tokens)
            if window is None:
                raise ApplicationError(
                    "The human request and recent turns exceed the history budget. "
                    "They were not truncated; use a larger agent context.",
                    422,
                )
            kept, current_skipped = window
            current_dropped = sum(len(u) for u in current_units[1 : current_skipped + 1])
            kept_tokens = request_tokens(kept)
        for index in range(len(groups) - 2, -1, -1):
            cost = request_tokens(groups[index])
            if current_skipped or kept_tokens + cost > turns_budget:
                dropped_messages = starts[: index + 1]
                break
            kept = groups[index] + kept
            kept_tokens += cost
    for message in reversed(messages if not starts else []):
        message = shorten(message, message_ceiling)
        cost = request_tokens([message])
        if kept and (
            kept_tokens + cost > turns_budget or fixed + kept_tokens + cost > context_size
        ):
            break
        if not kept and fixed + cost > context_size:
            # The newest turn is never dropped; shrink it to whatever the context still holds.
            message = shorten(message, max(0, (context_size - fixed - 64) * 2 // 3))
            cost = request_tokens([message])
        kept.insert(0, message)
        kept_tokens += cost
    # The view changes every time a segment is authored, while the head and the history
    # only grow. It used to sit ahead of the history, so one authored line invalidated
    # the cached prefix of the whole conversation: local f_keep fell 0.954 -> 0.743 as
    # the view gained content. Volatile state now comes last, after the history it must
    # not evict from the prefix cache.
    assembled = [*prefix, *kept, *suffix]
    report = {
        "state": state,
        "system": estimate(system),
        "head": head_tokens,
        "schema": schema_tokens,
        "branches": len(schema.get("oneOf", [schema])) if schema else 0,
        "fixed": fixed,
        "board": board_tokens,
        "summary": summary_tokens,
        "view": view_tokens,
        "turns": kept_tokens,
        "dropped_turns": len(messages) - len(kept) + bool(current_skipped),
        "summarized_current_turns": current_skipped,
        "summarized_current_messages": current_dropped,
        "dropped_messages": len(dropped_messages),
        "dropped_message_starts": dropped_messages,
        "current_message_turns": len(groups[-1]) if starts else 0,
        "template_reserve": TEMPLATE_RESERVE,
        "total_reserved": fixed + kept_tokens,
        "output_reserve": output,
        "schema_reserve": schema_reserve,
        "turns_budget": turns_budget,
        "message_ceiling": message_ceiling,
        "context": context_size,
    }
    return Assembly(
        messages=assembled,
        report=report,
        history_offset=len(prefix),
        history_groups=[
            *[len(g) for g in groups[len(dropped_messages) : -1]],
            len(groups[-1]) - current_dropped + bool(current_skipped),
        ]
        if starts
        else [],
        current_units=current_units,
        current_skipped=current_skipped,
    )


BUDGET_ADMISSION_PREFIXES = (
    "Request budget ",
    "The assistant request exceeds this connection's context budget.",
)


def call_with_window(model, settings, assembly, schema, cancelled, log_path, retain_report):
    """Retry tokenizer admission refusals without rerunning inference or losing current work."""
    while True:
        try:
            return model.call(settings, assembly.messages, schema, cancelled, log_path)
        except ApplicationError as exc:
            if cancelled():
                raise
            if not str(exc).startswith(BUDGET_ADMISSION_PREFIXES):
                raise
            offset = assembly.history_offset
            report = assembly.report
            if len(assembly.history_groups) >= 2:
                count = assembly.history_groups.pop(0)
                del assembly.messages[offset : offset + count]
                report["dropped_message_starts"].append(report["dropped_turns"])
                report["dropped_turns"] += count
                report["dropped_messages"] += 1
            else:
                window = (
                    window_current(
                        assembly.current_units,
                        request_tokens(assembly.messages[offset:-1]) - 1,
                        request_tokens,
                        skip_at_least=assembly.current_skipped + 1,
                    )
                    if assembly.current_units
                    else None
                )
                if window is None:
                    raise ApplicationError(
                        "The human request, recent turns and fixed context exceed the model's measured budget. "
                        "They were not truncated; use a larger agent context.",
                        422,
                    ) from exc
                kept, skipped = window
                dropped = sum(
                    len(u)
                    for u in assembly.current_units[assembly.current_skipped + 1 : skipped + 1]
                )
                report["dropped_turns"] += dropped
                report["summarized_current_messages"] += dropped
                report["summarized_current_turns"] = skipped
                assembly.current_skipped = skipped
                assembly.messages[offset:-1] = kept
                assembly.history_groups = [len(kept)]
            report["turns"] = request_tokens(assembly.messages[offset:-1])
            report["total_reserved"] = report["fixed"] + report["turns"]
            report["tokenizer_rewindows"] = report.get("tokenizer_rewindows", 0) + 1
            retain_report(report)
