"""Authoring guidance for readable shots and continuous action across segments."""

import re

from gaiavfx.application.harness.world import direction
from gaiavfx.modules.direction.insertion import screen_facing
from gaiavfx.shared.errors import ApplicationError


def require_predecessor(continues, ordinal, segments):
    if continues and not any(s["ordinal"] == ordinal - 1 for s in segments):
        raise ApplicationError(
            "continues: true has no predecessor. Use continues: false for the opening beat; "
            "a continuing beat needs the previous segment.",
            422,
        )


def require_cut_timing(segment, frame, cuts=(), cut_id=None):
    # These are editorial guardrails for the agreed 24 fps workflow, not a compiler grid.
    end = min(
        (c["frame"] for c in cuts if c["id"] != cut_id and c["frame"] > frame),
        default=segment["delivered_frames"],
    )
    problems = []
    if end - frame < 24:
        problems.append("leave at least 24 frames for the shot after the cut")
    previous = max(
        (c["frame"] for c in cuts if c["id"] != cut_id and c["frame"] < frame), default=None
    )
    if previous is not None and frame - previous < 24:
        problems.append(f"leave at least 24 frames for the shot after the cut at frame {previous}")
    # Cut frames are delivered frames; the motion context precedes frame 0 in the
    # render window. This guards an editorial rule, not a model limit: show about a
    # second of the continued motion before cutting away (users may choose otherwise).
    if segment["continues"] and frame < 22:
        problems.append("keep about a second of the continued shot before the cut")
    if problems:
        raise ApplicationError(
            "; ".join(problems) + "; a reverse after a continuous shot belongs at frame 36.", 422
        )


AXIAL = re.compile(
    r"\b(?:back (?:now )?(?:to|towards?) (?:the )?(?:camera|lens)|"
    r"(?:towards?|away from) (?:the )?(?:camera|lens)|"
    r"fac(?:e[sd]?|ing) (?:the )?(?:camera|lens))\b",
    re.IGNORECASE,
)


def axial_requested(text):
    return axial_phrase(text) is not None


def axial_phrase(text):
    for clause in re.split(r"[.!?;,\n]", text):
        if (match := AXIAL.search(clause)) and not re.search(
            r"\b(?:not|never|don't|no)\b", clause, re.IGNORECASE
        ):
            return match.group()
    return None


def require_lateral_shot(content, cut, user_message):
    if cut.get("relation") != "reverse" or cut.get("crosses_barrier") is not True:
        return
    if axial_requested(user_message):
        return
    text = " ".join(content.get(k, "") for k in ("description", "screen_direction"))
    if phrase := axial_phrase(text):
        raise ApplicationError(
            f'"{phrase}" violates the rule: "A barrier reverse keeps the walker lateral, '
            'with screen direction flipped"; axial facing needs an explicit user or existing cut request.',
            422,
        )


def require_segment_continuity(
    session, segment_id, user_message="", *, check_timing=False, previous_segment=None
):
    segment = direction.read_segment(session, segment_id)
    cuts = segment["cuts"]
    problems = []
    if check_timing:
        phases = sorted(segment["phases"], key=lambda p: p["start_frame"])
        ends = [p["start_frame"] for p in phases[1:]] + [segment["delivered_frames"]]
        for phase, end in zip(phases, ends):
            start = phase["start_frame"]
            if end - start < 12:
                problems.append(
                    f"Phase at f{start} has {end - start} frames; every phase needs at least "
                    "12 frames, including the cut's shot phase. Merge the text into the "
                    "neighbouring phase."
                )
    # Only already-authored cut intent can grant an exception. A new phase or
    # draft shot cannot authorize its own axial wording.
    requested = set()
    if previous_segment:
        for cut in previous_segment["cuts"]:
            shot = next(
                (p for p in previous_segment["phases"] if p["start_frame"] == cut["frame"]),
                None,
            )
            if shot and any(
                axial_requested(shot["content"].get(k, ""))
                for k in ("description", "screen_direction")
            ):
                requested.add(cut["id"])
    for phase in segment["phases"]:
        for cut in cuts:
            if cut["frame"] <= phase["start_frame"] and cut["id"] not in requested:
                try:
                    require_lateral_shot(phase["content"], cut, user_message)
                except ApplicationError as exc:
                    problems.append(f"Phase at f{phase['start_frame']}: {exc}")
                    break
    if problems:
        raise ApplicationError(" ".join(problems), 422)
    # Phases can be authored before their cut in separate commands. Once the first
    # cut exists, its entire incoming shot must preserve the predecessor's motion.
    if not cuts or not segment["continues"] or segment["ordinal"] == 0:
        return
    previous = direction.read_movie(session, segment["movie_id"])["segments"][
        segment["ordinal"] - 1
    ]
    if not previous["phases"]:
        return
    expected = previous["phases"][-1]["content"].get("screen_direction", "")
    facing = screen_facing(expected)
    if not facing:
        return
    end = cuts[0]["frame"]
    for phase in segment["phases"]:
        if (
            phase["start_frame"] < end
            and screen_facing(phase["content"].get("screen_direction", "")) != facing
        ):
            raise ApplicationError(
                f"frames 0–{end} continue segment {segment['ordinal']}: {expected}. "
                "Keep the predecessor's screen direction before the first cut.",
                422,
            )
