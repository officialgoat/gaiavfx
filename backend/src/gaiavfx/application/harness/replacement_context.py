"""Session evidence for replacement scope and user-confirmed performer choices."""

import re


def replacement_requested(message):
    return bool(re.search(r"\b(?:replac|swap)\w*", message, re.IGNORECASE))


def session_requested_replacement(turns):
    return any(
        turn["role"] == "human"
        and replacement_requested((turn.get("action") or {}).get("message", ""))
        for turn in turns
    )


def latest_read_analysis(turns, ids):
    """The analysis this session read last (tool result or completion), among `ids`."""
    latest = None
    for turn in turns:
        if turn["state"] != "accepted":
            continue
        payload = (turn.get("result") or {}).get("payload", {})
        for key in ("id", "operation_id"):
            if payload.get(key) in ids:
                latest = payload[key]
    return latest


def session_video_ids(attachments, turns):
    ids = [a["id"] for a in attachments if a["kind"] == "video"]
    for turn in turns:
        if turn["state"] != "accepted":
            continue
        for receipt in turn.get("receipts", []):
            if receipt.get("asset_id") and (
                receipt.get("kind") == "people_analysis"
                or receipt.get("operation_kind") == "person_analysis"
            ):
                ids.append(receipt["asset_id"])
        action = turn.get("action") or {}
        payload = (turn.get("result") or {}).get("payload", {})
        if payload.get("asset_id") and (
            action.get("tool") == "analyze_people"
            or (turn["role"] == "system" and "tracks" in payload)
        ):
            ids.append(payload["asset_id"])
    return list(dict.fromkeys(ids))


def user_confirmed(turns, analysis_id):
    """A question asked while the analysis was visible, then a human message, authorize mapping.

    Visible means reported by a tool turn, or in context when ask_human ran (its payload lists
    the analyses the agent could see), e.g. an analysis made before this session.
    """
    reported = asked = False
    for turn in turns:
        if turn["state"] != "accepted":
            continue
        action = turn.get("action") or {}
        payload = (turn.get("result") or {}).get("payload", {})
        if payload.get("id") == analysis_id and "tracks" in payload:
            reported = True
        if (
            turn["role"] == "agent"
            and action.get("tool") == "ask_human"
            and (reported or analysis_id in payload.get("analyses", []))
        ):
            asked = True
        if asked and turn["role"] == "human" and action.get("message", "").strip():
            return True
    return False


def track_gap(track):
    if not track.get("presence") or "unresolved" in track["presence"]:
        return "presence_unresolved"
    if "present" not in track["presence"]:
        return "performer_absent"
    return ""


def selectable_tracks(analysis, turns):
    """The clip's sole resolved person is the user's referent; every choice uses selected_by=user.

    Multiple resolved people require a reported analysis and subsequent user confirmation.
    No identity matching or identity-margin evidence is inferred by the harness.
    """
    tracks = [t for t in analysis.get("tracks", []) if not track_gap(t)]
    return tracks if len(tracks) == 1 or user_confirmed(turns, analysis["id"]) else []


def cut_failure(operation):
    return any(
        job.get("error_code") == "cross_cut"
        or (isinstance(job.get("error"), dict) and job["error"].get("code") == "cross_cut")
        or "contains a cut at relative frames" in str(job.get("error", "")).lower()
        for job in operation.get("jobs", [])
    )
