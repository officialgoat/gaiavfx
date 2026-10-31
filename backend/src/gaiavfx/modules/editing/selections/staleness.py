"""Read-only comparison of a take's recorded basis with the authored movie now, in Editing's words."""

# Every take records these; one that did not is not current.
RECORDED = (
    ("segment_revision", "Segment", "segment"),
    ("phase_revisions", "Phases", "phase"),
    ("subject_revisions", "Subjects", "subject"),
)
# Recorded since cuts and pins became part of a take's basis; a take that did not record them is not compared on them.
LATER = (("cut_revisions", "Cuts", "cut"),)


def changed_since_take(
    request: dict, current: dict | None, *, segment_id=None, unchanged=None
) -> list[str]:
    """Why a take rendered from `request` no longer matches `current`, the segment's basis now. `unchanged(kind,
    identity, now, then)` says whether a revision that moved was put back by Undo."""
    if current is None:
        return ["The segment no longer exists."]
    reasons = []
    for key, label, kind in RECORDED + LATER:
        if key not in request:
            if (key, label, kind) in RECORDED:
                reasons.append(f"{label} revisions were not recorded for this take.")
            continue
        before, after = request[key], current[key]
        if before == after:
            continue
        restored = False
        if unchanged:
            if not isinstance(before, dict):
                restored = unchanged(kind, segment_id, after, before)
            elif before.keys() == after.keys():
                restored = all(
                    unchanged(kind, identity, after[identity], revision)
                    for identity, revision in before.items()
                )
        if not restored:
            reasons.append(f"{label} changed since this take.")
    if "opening_angle_id" in request and request["opening_angle_id"] != current.get(
        "opening_angle_id"
    ):
        reasons.append("Opening angle changed since this take.")
    pins = request.get("pins") or {}
    if any(current.get("pins", {}).get(cut_id) != asset for cut_id, asset in pins.items()):
        reasons.append("Pin replaced since this take.")
    return reasons
