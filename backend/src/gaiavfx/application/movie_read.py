"""Movie read: a movie as the workspace reads it, in one Python read (ADR 0017 §2).

Its segments, each with its plan (a cut's plan inside its segment's, so no cut is planned twice), its split points,
each cut's pins, each segment's selections with whether the take is still current, the receipts, and what Undo and
Redo reverse next. The renderer presents it and works none of it out.
"""

from gaiavfx.application.direction_plans import plan_for_segment, plan_to_dict
from gaiavfx.application.direction_reading import DIRECTION as direction
from gaiavfx.application.selected_takes import selection_state

RECEIPTS = 100  # the history the renderer lists


def movie_read(session, movie_id):
    movie = direction.read_movie(session, movie_id)
    segments = movie["segments"]
    plans = {
        segment["id"]: plan_to_dict(plan_for_segment(session, segment["id"]))
        for segment in segments
    }
    keyframes = {
        cut["id"]: direction.keyframes(session, cut["id"])
        for segment in segments
        for cut in segment["cuts"]
    }
    pictured = {subject["id"] for subject in movie["subjects"] if subject["identity_asset_id"]}
    return {
        "movie": movie,
        "segment_plans": plans,
        # Where a cut's pin sits: its recommended technique's offset, or, while a pin exists, the first offset
        # another technique would use.
        "pin_offsets": {
            cut["cut_id"]: _pin_offset(cut, keyframes[cut["cut_id"]])
            for plan in plans.values()
            for cut in plan["cuts"]
        },
        # The References row gives each identity picture a lane, as many as the most pictured segment needs.
        "identity_lanes": max(
            (len(pictured & set(segment["content"]["subjects"])) for segment in segments), default=0
        ),
        "split_points": {
            segment["id"]: direction.split_points(session, segment["id"]) for segment in segments
        },
        "keyframes": keyframes,
        "selections": {
            segment["id"]: selection_state(session, movie_id, segment["id"]) for segment in segments
        },
        "receipts": direction.receipts(session, movie_id, RECEIPTS),
        "undo": direction.undo_targets(session, movie_id),
    }


def _pin_offset(cut, pins):
    if cut["recommended"] and cut["recommended"]["keyframe_offset_frames"] is not None:
        return cut["recommended"]["keyframe_offset_frames"]
    if not pins:
        return None
    return next(
        (
            choice["keyframe_offset_frames"]
            for choice in cut["alternatives"]
            if choice["keyframe_offset_frames"] is not None
        ),
        None,
    )
