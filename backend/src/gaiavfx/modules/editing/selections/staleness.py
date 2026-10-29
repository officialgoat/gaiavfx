"""Read-only comparison of a selected take's frozen and current authored revisions."""


def changed_since_take(
    request: dict, current: dict | None, *, segment_id=None, unchanged=None
) -> list[str]:
    if current is None:
        return ["The segment no longer exists."]
    reasons = []
    for key, label in (
        ("segment_revision", "Segment"),
        ("phase_revisions", "Phases"),
        ("subject_revisions", "Subjects"),
    ):
        if key not in request:
            reasons.append(f"{label} revisions were not recorded for this take.")
        elif request[key] != current[key]:
            before, after = request[key], current[key]
            restored = False
            if unchanged:
                if key == "segment_revision":
                    restored = unchanged("segment", segment_id, after, before)
                elif isinstance(before, dict) and before.keys() == after.keys():
                    kind = "phase" if key == "phase_revisions" else "subject"
                    restored = all(
                        unchanged(kind, identity, after[identity], revision)
                        for identity, revision in before.items()
                    )
            if not restored:
                reasons.append(f"{label} changed since this take.")
    return reasons
