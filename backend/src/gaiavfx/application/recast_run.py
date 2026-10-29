"""Public read-only run costing for the harness; the feeder owns execution."""

from gaiavfx.application import recast_projection as p
from gaiavfx.application.recast_feed import bridge_reservation
from gaiavfx.shared.errors import ApplicationError


def seed_window_seconds(length, index):
    """Seed timing for one fresh window or bridge."""
    return 220 * length / 73 + 60


def estimate_run(state, *, mode, frame_range=None):
    if mode not in {"draft", "final"}:
        raise ApplicationError("Unknown recast run mode.", 422)
    if (
        frame_range is not None
        and not 0 <= frame_range["start"] < frame_range["end"] <= state["film"]["frame_count"]
    ):
        raise ApplicationError("Range must be inside the film.", 422)
    renders = p.window_renders(mode)
    shots = []
    for shot in sorted(state["shots"], key=lambda s: s["start"]):
        if not shot["included"] or not p.in_range(shot, frame_range):
            continue
        plan = shot["plan"]
        # As the feeder (`recast_feed.start_shot`): a shot whose plan has a gap, or is still being analysed, is not
        # rendered (Astra R5; usability round 2, C6: shots still analysing made the count drift), and neither is a
        # shot already rendered: only a planned shot has no replacement windows yet (the in-app trial, 23 Oct: a run
        # over a shot in review reserved 14 renders and rendered none).
        if (
            shot.get("status", "planned") == "planned"
            and plan
            and plan["performers"]
            and not plan["gaps"]
            and plan.get("_ready", True)
            and plan["windows"]
        ):
            shots.append(
                {
                    "id": shot["id"],
                    "start": shot["start"],
                    "end": shot["end"],
                    "renders": len(plan["windows"]) * renders
                    + bridge_reservation(plan, renders, mode=mode),
                    "plan": plan,
                }
            )
    seconds = sum(
        renders * seed_window_seconds(w["length"], index)
        for s in shots
        for index, w in enumerate(s["plan"]["windows"])
    )
    bridges = sum(bridge_reservation(s["plan"], renders, mode=mode) for s in shots)
    seconds += bridges * seed_window_seconds(73, 0)
    estimate = {
        "shots": len(shots),
        "windows": sum(len(s["plan"]["windows"]) for s in shots),
        "renders": sum(s["renders"] for s in shots),
        "gpu_seconds_low": int(seconds * 0.7),
        "gpu_seconds_high": int(seconds * 1.3),
        "storage_bytes": bridges * 73 * 2_500_000
        + sum(
            (w["end"] - w["start"]) * renders * 2_500_000
            for s in shots
            for w in s["plan"]["windows"]
        ),
        "basis": "seed",
    }
    return shots, estimate
