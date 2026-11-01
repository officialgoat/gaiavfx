"""Run scope: which shots a Recast run renders, what the feed reserves for each, and what that costs.

One rule says which shot a run can render (`renderable`): its plan has performers to replace, its windows are analysed
and it has no gap. The feed starts no other shot, and reserves its windows' renders and the bridges its handoff joins
with (`bridge_reservation`); an estimate counts the same renders (`cost`), at the seed timing or, once three renders
were measured, at their median (`measured_seconds`). Two questions read it:
- what the run covers over its range (`run_estimate`, the recast's own estimate: it counts the shots it rendered too,
  and the shots still being analysed with their windows so far);
- what a run started now would render (`estimate_run`, for /estimate and Gaia: only the planned shots not rendered
  yet, each window at the mode's renders).
`analysing` is the start gate's wait: a shot in the run's range whose windows are still being analysed.
`reviewed` is a run's end: once it settles and no shot in its range still waits, the user's decisions on them.
"""

from collections import Counter
from statistics import median

from gaiavfx.application import recast_projection as p
from gaiavfx.shared.errors import ApplicationError

STILL_FRAME_BYTES = 2_500_000  # storage per rendered frame
REVIEWING = {"needs_review", "queued", "rendering"}  # a shot still waiting for the user or the GPU


def renderable(plan) -> bool:
    """Whether a run can render a shot with this plan: performers to replace, its windows analysed, no gap."""
    return bool(plan and plan["performers"] and plan.get("_ready", True) and not plan["gaps"])


def covered(shot, frame_range) -> bool:
    """A shot a run over `frame_range` covers, rendered or not: included, in the range, planned with performers and
    windows and no gap (the run's progress counts these)."""
    plan = shot["plan"]
    return bool(
        shot["included"]
        and plan
        and plan["performers"]
        and not plan["gaps"]
        and plan["windows"]
        and p.in_range(shot, frame_range)
    )


def analysing(shots, frame_range) -> bool:
    """Whether a run over `frame_range` waits for window analyses: an included shot in it is still being analysed."""
    return any(
        s["included"] and s["status"] == "analysing" and p.in_range(s, frame_range) for s in shots
    )


def reviewed(view, frame_range) -> dict | None:
    """The user's decisions on the shots over `frame_range` (None: the whole film) of the recast as read: how many
    were approved, kept original or failed. None while the run is not idle or finished (a paused run, its budget spent
    or paused by the user, keeps its review waiting) or a shot in the range still waits for review or a render."""
    shots = [s for s in view["shots"] if p.in_range(s, frame_range)]
    if view["run"]["state"] not in {"idle", "finished"} or any(
        s["status"] in REVIEWING for s in shots
    ):
        return None
    counts = Counter(s["status"] for s in shots)
    return {status: counts[status] for status in ("approved", "kept_original", "failed")}


def start_blocked(view) -> str | None:
    """Why a run could not render correctly now, which start_run refuses: the film is not analysed, a shot in the
    run's range is still being analysed, or the film's frame rate is unsupported."""
    if view["analysis"]["state"] != "succeeded":
        return "Wait for analysis to finish."
    if view["run"]["analysing"]:
        return "Wait for window analyses to finish."
    if view["film"]["fps_status"] == "unsupported":
        return "This film's frame rate is unsupported."
    return None


def refusals(view) -> dict:
    """Why Plan, Start (or Resume) and Pause would be refused for the recast as read (`recast_state`), in the user's
    words, or None where they may run. start_run enforces what `start_blocked` says; the trackbar also waits for a
    confirmed mapping and an idle run (a run without a mapping feeds nothing, a running one is updated in place)."""
    run = view["run"]
    analysed = view["analysis"]["state"] == "succeeded"
    mapped = any((c["target"] or {}).get("state") == "confirmed" for c in view["characters"])
    plan = (
        "Map a character first"
        if not mapped
        else "Wait for analysis to finish"
        if not analysed
        else "Pause the run before planning"
        if run["state"] == "running"
        else None
    )
    start = start_blocked(view) or (
        "Confirm a character mapping first."
        if not mapped
        else "The run is already active."
        if run["state"] == "running"
        else None
    )
    pause = None if run["state"] == "running" else "No run is running."
    return {"plan_refusal": plan, "start_refusal": start, "pause_refusal": pause}


def bridge_reservation(plan, renders, *, mode=None):
    joins = plan.get("_handoff", {}).get("joins", [])
    last = len(plan.get("windows", [])) - 1
    return sum(
        renders if j["join"] == last and mode != "draft" else 1
        for j in joins
        if j["kind"] == "bridge"
    )


def seed_window_seconds(length, index):
    """Seed timing for one fresh window or bridge."""
    return 220 * length / 73 + 60


def measured_seconds(operations) -> float | None:
    """The median measured render time once three renders were measured; None keeps the seed timing."""
    durations = [d for op in operations if (d := p.render_seconds(op)) is not None]
    return median(durations) if len(durations) >= 3 else None


def cost(plans, renders_of, *, mode, seconds=None) -> dict:
    """What rendering `plans` costs, `renders_of(plan)` renders for each window plus the bridges: renders, GPU seconds
    (each render `seconds` long when measured, else the seed timing) and storage."""
    renders = frames = 0
    total = 0.0
    for plan in plans:
        each = renders_of(plan)
        bridges = bridge_reservation(plan, each, mode=mode)
        renders += bridges
        total += bridges * (seconds or seed_window_seconds(73, 0))
        frames += bridges * 73
        for index, window in enumerate(plan["windows"]):
            renders += each
            total += each * (seconds or seed_window_seconds(window["length"], index))
            frames += (window["end"] - window["start"]) * each
    return {
        "renders": renders,
        "gpu_seconds_low": int(total * 0.7),
        "gpu_seconds_high": int(total * 1.3),
        "storage_bytes": frames * STILL_FRAME_BYTES,
        "basis": "measured" if seconds else "seed",
    }


def run_estimate(recast, shots, plans, analyses, seconds=None) -> dict | None:
    """The recast's own estimate: what its run covers over its range in its mode, each window at the renders its plan
    reserved. It counts the included, planned shots in the range (rendered ones too, so it stays while the run
    renders) and the shots still being analysed, with their windows so far (`windows_pending`: analyses not settled).
    `plans` holds each shot's stored plan (its handoff decides the bridges). None before a shot is planned."""
    frame_range = p.run_range(recast)
    selected = [
        s
        for s in shots
        if s["included"]
        and s["plan"]
        and (not s["plan"]["gaps"] or s["status"] == "analysing")
        and p.in_range(s, frame_range)
    ]
    if not selected:
        return None
    return {
        "shots": len(selected),
        "windows": sum(
            max(len(s["plan"]["windows"]), len(analyses.get(s["id"], []))) for s in selected
        ),
        "windows_pending": sum(
            o["state"] not in p.SETTLED for s in selected for o in analyses.get(s["id"], [])
        ),
        **cost(
            [plans.get(s["id"]) or s["plan"] for s in selected],
            lambda plan: plan["renders_per_window"],
            mode=recast["run_mode"],
            seconds=seconds,
        ),
    }


def estimate_run(state, *, mode, frame_range=None):
    """What a run started now over `frame_range` in `mode` would render: the included shots in the range that are
    planned, renderable and not rendered yet (a shot in review is rendered: the in-app trial, 23 Oct, reserved 14
    renders for one and rendered none), each window at the mode's renders, at the recast's measured timing when it
    has one. `state` is the recast state with its private plans. Returns (the shots with their renders, the
    estimate)."""
    if mode not in {"draft", "final"}:
        raise ApplicationError("Unknown recast run mode.", 422)
    if (
        frame_range is not None
        and not 0 <= frame_range["start"] < frame_range["end"] <= state["film"]["frame_count"]
    ):
        raise ApplicationError("Range must be inside the film.", 422)
    renders = p.window_renders(mode)
    shots = [
        {
            "id": shot["id"],
            "start": shot["start"],
            "end": shot["end"],
            "renders": len(shot["plan"]["windows"]) * renders
            + bridge_reservation(shot["plan"], renders, mode=mode),
            "plan": shot["plan"],
        }
        for shot in sorted(state["shots"], key=lambda s: s["start"])
        if shot["included"]
        and p.in_range(shot, frame_range)
        and shot.get("status", "planned") == "planned"
        and renderable(shot["plan"])
        and shot["plan"]["windows"]
    ]
    estimate = {
        "shots": len(shots),
        "windows": sum(len(s["plan"]["windows"]) for s in shots),
        **cost(
            [s["plan"] for s in shots],
            lambda plan: renders,
            mode=mode,
            seconds=(state.get("run") or {}).get("render_seconds"),
        ),
    }
    return shots, estimate
