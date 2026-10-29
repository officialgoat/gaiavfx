"""Spatial selectors shared by replacement use cases and agent presentation."""


def position(track, native):
    box = track["box"]
    x = (box["x0"] + box["x1"]) / (2 * native[0])
    horizontal = "left" if x < 1 / 3 else "right" if x > 2 / 3 else "centre"
    depth = "foreground" if (box["y1"] - box["y0"]) / native[1] >= 0.5 else "background"
    return horizontal, depth


def selector_positions(tracks, native):
    positions = [f"at the {position(t, native)[0]}" for t in tracks]
    if len(tracks) == 2 and positions[0] == positions[1]:
        order = sorted(
            range(2),
            key=lambda i: (
                tracks[i]["box"]["x0"] + tracks[i]["box"]["x1"],
                tracks[i]["box"]["y0"] + tracks[i]["box"]["y1"],
            ),
        )
        for index, side in zip(order, ("left", "right"), strict=True):
            positions[index] = f"on the {side} of the pair"
    return positions
