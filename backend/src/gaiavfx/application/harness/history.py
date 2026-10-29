"""Read obligations derived from durable turns, never editable board prose."""

from gaiavfx.application.harness.world import maybe_review, operations


def plate_rejections(session, state):
    notes = {}
    for op in reversed(state.operations):
        if op["kind"] == "removal":
            review = maybe_review(session, op["id"])
            if review and review.get("rejection"):
                request = operations.operation(session, op["id"])["request"]
                notes[request["source_asset"]] = review["rejection"]["note"]
    return notes


def recent_user_words(turns):
    return [(t.get("action") or {}).get("message", "") for t in turns if t["role"] == "human"][-6:]


def attachments(turns):
    return [
        {"id": a["asset_id"], "kind": a["kind"], "name": a["name"]}
        for t in turns
        if t["role"] == "human"
        for a in (t.get("action") or {}).get("attachments", [])
    ]


def references_read(turns):
    references, pending = {}, ""
    for turn in turns:
        if turn["state"] != "accepted":
            continue
        action = turn.get("action") or {}
        focus = (turn.get("result") or {}).get("focus")
        if action.get("tool") in {"describe_reference", "analyze_reference_video"} and focus:
            pending = focus["asset_id"]
        elif action.get("tool") == "reference_notes" and pending:
            references[pending] = action.get("input") or {}
            pending = ""
        elif action.get("tool") == "read_recast":
            for receipt in turn["receipts"]:
                if receipt.get("kind") == "recast_read" and receipt.get("asset_id"):
                    references[receipt["asset_id"]] = {"recast_id": receipt["recast_id"]}
        elif action.get("tool") == "analyze_people" or turn["role"] == "system":
            payload = (turn.get("result") or {}).get("payload", {})
            if payload.get("asset_id") and ("tracks" in payload or "gaps" in payload):
                references[payload["asset_id"]] = payload
    return references


def latest_user_message(turns):
    latest = next((t for t in reversed(turns) if t["role"] == "human"), {})
    return (latest.get("action") or {}).get("message", "")


def unread_attachments(turns):
    latest = next((t for t in reversed(turns) if t["role"] == "human"), {})
    read = references_read(turns)
    return list(
        dict.fromkeys(
            a["asset_id"]
            for a in (latest.get("action") or {}).get("attachments", [])
            if a["asset_id"] not in read
        )
    )


def message_renders(turns):
    """Include recovery receipts, but exclude other sessions and earlier messages."""
    started = set()
    for turn in turns:
        if turn["role"] == "human":
            started.clear()
        elif turn["state"] == "accepted":
            started.update(
                r["id"]
                for r in turn["receipts"]
                if r.get("kind") == "operation"
                and r.get("operation_kind") in {"render", "character_replacement"}
            )
    return started
