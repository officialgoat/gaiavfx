"""Pure frame coverage for shot-local replacement."""

from math import isfinite


def latent_chain_allowed(window_count, close_ups, performers, max_windows):
    """Only bounded, single-performer close-ups qualify for the longer latent chain."""
    close_ups = tuple(close_ups)
    return (
        type(window_count) is int
        and type(max_windows) is int
        and 3 <= window_count <= max_windows
        and type(performers) is int
        and performers == 1
        and len(close_ups) == window_count
        and all(value is True for value in close_ups)
    )


def shot_crop_scale_loss(shot_scale, window_scales) -> float:
    """Measure loss against the smallest independently planned window scale."""
    window_scales = tuple(window_scales)
    if not window_scales or any(
        type(value) not in (int, float) or not isfinite(value) or value <= 0
        for value in (shot_scale, *window_scales)
    ):
        raise ValueError("Shot and window scales must be finite and positive")
    return max(0.0, 1 - shot_scale / min(window_scales))


def shot_handoff(window_count):
    if type(window_count) is not int or window_count < 1:
        raise ValueError("A shot needs at least one window")
    return "none" if window_count == 1 else "latent" if window_count == 2 else "bridge"


def bridge_layout(windows, shot_end, *, length=73, lead=47, context=22):
    """Film-relative spans; pad short final bridges instead of sliding them back."""
    if not 0 < 2 * context < length or not context < lead < length:
        raise ValueError("Invalid bridge geometry")
    result = []
    for join in range(1, len(windows)):
        previous = windows[join - 1]
        end = previous["end"] if isinstance(previous, dict) else previous[1]
        start = end - lead
        result.append(
            {
                "join": join,
                "start": start,
                "end": min(start + length, shot_end),
                "generated": (start + context, min(start + length - context, shot_end)),
                "window_frames": length,
            }
        )
    return result


def delivery_plan(windows, bridges, blend=4):
    """Ordered half-open segments and integer blend weights, independent of pixels.

    Sources are ("window", zero-based index) or ("bridge", one-based join).
    A blended segment starts at weight_start and increments by one per frame.
    """
    if not windows or type(blend) is not int or blend < 0:
        raise ValueError("Invalid delivery geometry")
    spans = [(w["start"], w["end"]) if isinstance(w, dict) else w[:2] for w in windows]
    by_join = {b["join"]: b for b in bridges}
    if len(by_join) != len(bridges) or any(j < 1 or j >= len(windows) for j in by_join):
        raise ValueError("Invalid bridge joins")
    result = []
    cursor, shot_end = spans[0][0], spans[-1][1]

    def add(start, end, source, right=None, weight=0, denominator=1):
        nonlocal cursor
        end = min(end, shot_end)
        if end <= start:
            return
        if start != cursor:
            raise ValueError("Delivery spans overlap or leave a gap")
        result.append(
            {
                "start": start,
                "end": end,
                "source": source,
                "right": right,
                "weight_start": weight,
                "denominator": denominator,
            }
        )
        cursor = end

    for join in range(1, len(spans)):
        left, right = ("window", join - 1), ("window", join)
        if join in by_join:
            b = by_join[join]
            g0, g1 = b["generated"]
            middle = ("bridge", join)
            add(cursor, g0 - blend, left)
            add(cursor, g0, left, middle, 1, blend + 1)
            add(cursor, g1, middle)
            add(cursor, g1 + blend, middle, right, 1, blend + 1)
        else:
            start, end = spans[join][0], spans[join - 1][1]
            if not start < end < spans[join][1]:
                raise ValueError("Invalid composite window overlap")
            add(cursor, start, left)
            add(cursor, end, left, right, 0, max(1, end - start - 1))
    add(cursor, shot_end, ("window", len(spans) - 1))
    return result


def shot_windows(
    start: int, end: int, *, length: int = 73, overlap: int = 22
) -> list[tuple[int, int, int]]:
    """Cover [start, end), stopping as soon as the last window covers the end.

    Including the overlap in the remainder avoids a redundant short tail: every
    subsequent window has at least overlap + 1 frames. No source frame is dropped.
    """
    if (
        any(type(v) is not int for v in (start, end, length, overlap))
        or not 0 <= start < end
        or length < 5
        or (length - 5) % 17
        or not 0 <= overlap < length
    ):
        raise ValueError("Invalid shot/window geometry")
    result = []
    while True:
        stop = min(start + length, end)
        frames = stop - start
        generated = 5 + 17 * max(0, (frames - 5 + 16) // 17)
        result.append((start, stop, generated))
        if stop == end:
            return result
        start += length - overlap
