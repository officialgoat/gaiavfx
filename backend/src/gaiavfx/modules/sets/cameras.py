"""Cameras in a set's world (ADR 0018). Poses are OpenCV world-to-camera matrices: x right, y down, z forward. A
heading is in degrees, clockwise seen from above, 0 being the set's forward (+z). numpy and the standard library
only."""

import math

import numpy as np


def rigid(viewmat) -> np.ndarray | None:
    """The pose as a 4×4 array when it is a finite rigid world-to-camera matrix, else None."""
    try:
        array = np.asarray(viewmat, dtype=float)
    except (TypeError, ValueError):
        return None
    if array.shape != (4, 4) or not np.isfinite(array).all():
        return None
    rotation = array[:3, :3]
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-3) or not np.allclose(
        array[3], [0, 0, 0, 1], atol=1e-6
    ):
        return None
    return array


def centre(viewmat) -> np.ndarray:
    """Where the camera stands in the world."""
    array = np.asarray(viewmat, dtype=float)
    return -array[:3, :3].T @ array[:3, 3]


def level(position, heading: float) -> np.ndarray:
    """A level camera standing at `position` and looking along `heading`."""
    h = math.radians(heading % 360)
    forward = np.array([math.sin(h), 0.0, math.cos(h)])
    down = np.array([0.0, 1.0, 0.0])
    rotation = np.stack([np.cross(down, forward), down, forward])
    viewmat = np.eye(4)
    viewmat[:3, :3] = rotation
    viewmat[:3, 3] = -rotation @ np.asarray(position, dtype=float)
    return viewmat
