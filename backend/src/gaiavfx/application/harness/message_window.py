"""Deterministic index of older turns within the current human request."""

import json
from collections import Counter

RECENT_TURNS = 6


def older_turns_summary(units):
    tools, outcomes, receipts = Counter(), [], []
    for unit in units:
        for message in unit:
            text = message["content"]
            try:
                value = json.loads(text.removeprefix("Tool result: "))
            except (ValueError, TypeError):
                continue
            if not isinstance(value, dict):
                continue
            if message["role"] == "assistant":
                tools[value.get("tool", "action")] += 1
            else:
                if value.get("discrepancy"):
                    outcomes.append(str(value["discrepancy"])[:200])
                for receipt in value.get("receipts", []):
                    receipts.append(
                        f"{receipt.get('command', receipt.get('kind', ''))} {receipt.get('id', '')}"
                    )
    lines = [
        f"Earlier turns of this message ({len(units)} summarized; full transcript retained):",
        "Actions: " + ", ".join(f"{name} ×{count}" for name, count in tools.items())[:300],
    ]
    if outcomes:
        lines.append(
            "Earlier refusals (not current state): " + "; ".join(dict.fromkeys(outcomes[-3:]))
        )
    if receipts:
        lines.append("Recorded receipts: " + "; ".join(receipts[-3:]))
    lines.append("The current view and plan below are authoritative for present state.")
    return {"role": "user", "content": "\n".join(lines)}


def window_current(units, budget, token_counter, *, skip_at_least=0):
    """Keep the human request and at least six complete recent turns verbatim."""
    # units[0] is the human request, including its attachment links.
    for skipped in range(skip_at_least, max(0, len(units) - RECENT_TURNS - 1) + 1):
        messages = [*units[0]]
        if skipped:
            messages.append(older_turns_summary(units[1 : skipped + 1]))
        messages.extend(m for unit in units[skipped + 1 :] for m in unit)
        if token_counter(messages) <= budget:
            return messages, skipped
    return None
