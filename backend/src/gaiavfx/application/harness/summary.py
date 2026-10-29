"""A bounded deterministic conversation index, built only from durable facts."""

from gaiavfx.application.harness.context import SUMMARY_CEILING, estimate
from gaiavfx.application.harness.history import references_read


def conversation_summary(document, turns, operation_rows, review_notes=()):
    humans = [turn for turn in turns if turn["role"] == "human"]
    lines = [f"Messages: {len(humans)}"]
    if humans:
        lines.append("Last request: " + (humans[-1].get("action") or {}).get("message", "")[:200])
    lines.extend(review_notes)
    plan = document.plan
    if plan:
        lines.append(f"Plan {plan.state}: {plan.goal[:200]}")
    receipts = [
        r for t in turns if t["state"] == "accepted" for r in t["receipts"] if r.get("command")
    ]
    if receipts:
        lines.append(
            "Receipts: "
            + "; ".join(
                f"{r['command']} {r.get('target_kind', '')} {r.get('target_id', '')}"
                for r in receipts[-5:]
            )
        )
    if operation_rows:
        lines.append(
            "Operations: "
            + "; ".join(
                f"{row['id']} {row['kind']} {row['state']}"
                + (" (plate review complete, approved)" if row.get("plate_approved") else "")
                for row in operation_rows
            )
        )
    attachments = dict.fromkeys(
        a["name"] for t in humans for a in (t.get("action") or {}).get("attachments", [])
    )
    if attachments:
        lines.append("Attachments: " + "; ".join(attachments))
    references = references_read(turns)
    if references:
        lines.append("References read: " + "; ".join(references))
    # Preserve all five receipt links. Trim verbose categories evenly, retaining
    # their labels and making omissions explicit; the transcript remains authoritative.
    lines = [line[:800] + "…" if len(line) > 800 else line for line in lines]
    result = "\n".join(lines)
    while len(result) > 1200 or estimate("Summary so far:\n" + result) > SUMMARY_CEILING:
        choices = [i for i, line in enumerate(lines) if not line.startswith("Receipts:")]
        index = max(choices, key=lambda i: len(lines[i].encode("utf-8")))
        lines[index] = lines[index].rstrip("…")[:-1] + "…"
        result = "\n".join(lines)
    return result
