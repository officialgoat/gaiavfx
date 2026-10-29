"""Pure geometry for the MiniMax H3 views-only reference sheet."""


def views_sheet_layout(
    sizes: list[tuple[int, int]], columns: int, gap: int
) -> tuple[int, int, list[tuple[int, int, int, int]]]:
    """Scale every tile to the shortest height and lay out rows from the top left."""
    if not sizes or columns < 1 or gap < 0 or any(w < 1 or h < 1 for w, h in sizes):
        raise ValueError("views sheet needs positive tile sizes and columns, and a nonnegative gap")

    height = min(h for _, h in sizes)
    scaled = [(round(w * height / h), height) for w, h in sizes]
    rows = [scaled[i : i + columns] for i in range(0, len(scaled), columns)]
    width = max(sum(w for w, _ in row) + gap * (len(row) - 1) for row in rows)
    boxes = []
    y = 0
    for row in rows:
        x = 0
        for w, h in row:
            boxes.append((x, y, w, h))
            x += w + gap
        y += height + gap
    return width, y - gap, boxes


def portrait_sheet_layout(target_size, width, height, gap):
    """Return (x, y, width, height) boxes in target/front/three-quarter/profile order."""
    tw, th = target_size
    left = width // 3
    side = width - left - gap
    small = (side - gap) // 2
    if min(tw, th, left, side, small) <= 0 or gap < 0 or side + gap + small > height:
        raise ValueError("Portrait sheet needs positive sizes and room for all portraits.")
    scale = min(1, left / tw, height / th)
    fitted = (max(1, round(tw * scale)), max(1, round(th * scale)))
    x = left + gap
    return [
        ((left - fitted[0]) // 2, (height - fitted[1]) // 2, *fitted),
        (x, 0, side, side),
        (x, side + gap, small, small),
        (x + small + gap, side + gap, small, small),
    ]
