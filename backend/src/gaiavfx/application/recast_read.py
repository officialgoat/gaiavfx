"""The Recast read: what the film's page and Gaia see of a recast, as one RecastDTO, with the one rule for a
shot's status and why a head pass may not start on one of its versions. Read only: it starts nothing.
"""

from copy import deepcopy

from gaiavfx.application import recast_feed as feeding
from gaiavfx.application import recast_lines as lining
from gaiavfx.application import recast_planning as planning
from gaiavfx.application import recast_projection as p
from gaiavfx.application import recast_run as run_scope
from gaiavfx.application import recast_shell as shell
from gaiavfx.application import recast_voice as voicing
from gaiavfx.application.target_faces import target_report
from gaiavfx.shared.errors import ApplicationError

# What a performer's target face check leaves a head pass, in the words `recast.head_pass` refuses with.
HEAD_PASS_FACE = {
    "face": None,
    "no_face": "Head pass needs a target with a human face",
    "failed": "The target picture could not be checked.",
}
FACE_CHECKING = "Checking the target picture's face, which needs ComfyUI"


def replaced_in(session, shot, assembly_id) -> dict:
    """Who the version an assembly made replaced, and with what: each character of its root attempt, in that
    attempt's performer order, with the window analyses, tracks and target pictures its window requests froze. A head
    pass re-renders a version's heads with the pictures its bodies were rendered with, whatever the mapping says now.
    """
    plan = shot["plan"] or {}
    root = p.operations.operation(session, assembly_id)
    while root["request"].get("parent"):
        root = p.operations.operation(session, root["request"]["parent"]["assembly_op"])
    performers = plan.get("performers", [])
    characters = next(
        (
            h.get("performers", performers)
            for h in plan.get("_history", [])
            if h["assembly"] == root["id"]
        ),
        performers,
    )
    bodies = [
        p.operations.operation(session, o)["request"] for o in root["request"]["window_operations"]
    ]
    replaced = {}
    for index, character_id in enumerate(characters):
        if any(index >= len(body["facts"]["performers"]) for body in bodies):
            continue
        frozen = [body["facts"]["performers"][index] for body in bodies]
        replaced[character_id] = {
            "analyses": [body["analysis_operation"] for body in bodies],
            "tracks": {b["analysis_operation"]: f["track_id"] for b, f in zip(bodies, frozen)},
            "targets": {b["analysis_operation"]: f["target_asset"] for b, f in zip(bodies, frozen)},
        }
    return replaced


def _face_refusal(session, library, asset_ids, faces) -> str | None:
    """Why the pictures a character was rendered with cannot take a head pass, in `recast.start_head_pass`'s words: a
    picture without a human face, or one that could not be checked, before one still being checked."""
    pending = False
    for asset_id in asset_ids:
        if asset_id not in faces:
            report = target_report(session, library, asset_id)
            faces[asset_id] = report["state"] if report else "checking"
        if faces[asset_id] == "checking":
            pending = True
        elif refusal := HEAD_PASS_FACE.get(faces[asset_id]):
            return refusal
    return FACE_CHECKING if pending else None


def _head_pass(session, library, shot, version, replaced, faces) -> dict:
    """Which characters a head pass on this version may re-render (in the plan's order), each with why not."""
    if version["assembly"] not in replaced:
        replaced[version["assembly"]] = replaced_in(session, shot, version["assembly"])
    bindings = replaced[version["assembly"]]
    characters = [
        {
            "character_id": c,
            "refusal": _face_refusal(
                session, library, dict.fromkeys(bindings[c]["targets"].values()), faces
            ),
        }
        for c in (shot["plan"] or {}).get("performers", [])
        if c in bindings
    ]
    refusals = [c["refusal"] for c in characters]
    if not characters:
        refusal = "Choose a mapped character in this shot's plan."
    else:
        refusal = None if None in refusals else refusals[0]
    return {"head_pass": characters, "head_pass_refusal": refusal}


def _head_pass_refusal(shot) -> str | None:
    """Why a head pass may start on none of the shot's versions: it has none, or it has not settled."""
    if not shot["versions"]:
        return "This shot has no settled version."
    if shot["status"] in {"analysing", "queued", "rendering"}:
        return "Wait for this shot to settle before starting a head pass."
    return None


def _status(mapped, included, plan, versions, choice, windows, assembly, layers=()):
    if any(layer["state"] == "rendering" for layer in layers):
        return "rendering"
    if not mapped:
        return "absent"
    if not included:
        return "excluded"
    if (
        windows
        and (versions or (plan and plan.get("_history")))
        and (
            any(o["state"] not in p.SETTLED for o in windows)
            or not assembly
            or assembly["state"] not in p.SETTLED
        )
    ):
        # A retake whose windows all wait for the GPU reads as queued, not rendering (usability round 1, B4).
        pending = [o for o in windows if o["state"] not in p.SETTLED]
        return "queued" if pending and all(o["state"] == "queued" for o in pending) else "rendering"
    if choice:
        return "kept_original" if choice["version"] == "original" else "approved"
    if versions:
        return "needs_review"
    if plan and not plan.get("_ready", True):
        return "analysing"
    if plan and plan["gaps"]:
        return "gap"
    if windows:
        pending = [o for o in windows if o["state"] not in p.SETTLED]
        if pending:
            return "queued" if all(o["state"] == "queued" for o in pending) else "rendering"
        if assembly and assembly["state"] not in p.SETTLED:
            return "rendering"
        if all(o["state"] in {"failed", "cancelled", "needs_attention"} for o in windows) or (
            assembly and assembly["state"] in p.SETTLED
        ):
            return "failed"
        return "rendering"
    return "planned" if plan else "unplanned"


def recast_state(session, library, recast_id, *, private_plans=False, expressive_library=None):
    """RecastDTO. Read only: it starts nothing. `expressive_library` (the Settings folder) is given only by the page's
    read, which then compares each built voice with the expressive recordings (`library_changed`)."""
    from gaiavfx.application.recast_conform import conform_states

    recast, report = shell.load(session, library, recast_id)
    planning.refresh_all(session, library, recast, report)
    margin_min = p.policy().identity_margin_min
    characters, unknown = p.character_views(recast, report)
    for character in characters:
        if target := character["target"]:
            report_ = target_report(session, library, target["asset_id"])
            target["face"] = report_["state"] if report_ else "checking"
    targets = planning.mapped(recast)
    replaced, faces = {}, {}
    shots, all_ops = [], []
    analyses = {}
    cast_by_shot = {}
    settled_shots, active_shots = set(), set()
    for shot in report["shots"] if report else []:
        stored = shell.shot(recast, p.shot_id(shot))
        appearances = p.appearances(
            session, library, recast, report, shot, stored, margin_min=margin_min
        )
        cast_by_shot[stored["shot_id"]] = appearances
        mapped = any(a["character_id"] in targets for a in appearances)
        included = mapped if stored["included"] is None else stored["included"]
        try:
            recorded = p.versions(session, library, stored)
        except ApplicationError as exc:
            planning.error(stored, exc)
            recorded = []
        versions = p.public_versions(recorded)
        for version, record in zip(versions, recorded):
            version.update(_head_pass(session, library, stored, record, replaced, faces))
        choices = [c for c in recast["choices"] if c["shot_id"] == stored["shot_id"]]
        choice = (
            {
                "version": choices[-1]["version"],
                "chosen_at": choices[-1]["chosen_at"],
                "revision": choices[-1]["recast_revision"],
            }
            if choices and choices[-1]["version"] != "unchosen"
            else None
        )
        plan = deepcopy(stored["plan"])
        analyses[stored["shot_id"]] = [
            p.operations.operation(session, o) for o in stored["window_analysis_ops"]
        ]
        windows = [p.operations.operation(session, o) for o in stored["window_replacement_ops"]]
        all_ops.extend(p.operations.operation(session, o) for o in p.all_window_ops(stored))
        assembly = (
            p.operations.operation(session, stored["assembly_op"])
            if stored["assembly_op"]
            else None
        )
        if assembly and assembly["state"] in p.SETTLED:
            settled_shots.add(stored["shot_id"])
        elif windows:
            active_shots.add(stored["shot_id"])
        if plan:
            plan = {
                k: v
                for k, v in plan.items()
                if not k.startswith("_") or (private_plans and k == "_handoff")
            }
            for window, operation in zip(plan["windows"], windows):
                window.update(operation_id=operation["id"], state=operation["state"])
            if windows and not assembly and feeding.in_flight(session, stored):
                for window in plan["windows"][len(windows) :]:
                    window["state"] = "waiting"
        layers = [p.layer_status(session, library, layer) for layer in stored.get("layers", [])]
        if any(layer["state"] == "rendering" for layer in layers):
            active_shots.add(stored["shot_id"])
        status = _status(
            mapped, included, stored["plan"], versions, choice, windows, assembly, layers
        )
        shots.append(
            {
                "id": stored["shot_id"],
                "index": shot["index"],
                "start": shot["start"],
                "end": shot["end"],
                "poster": p.face(shot["poster_artifact"], shot["start"], recast_id),
                "appearances": appearances,
                "appearance_links": [
                    {
                        k: link[k]
                        for k in (
                            "track_id",
                            "analysis_operation_id",
                            "character_id",
                            "state",
                            "proposed_by",
                            "evidence",
                            "created_at",
                            "decided_at",
                        )
                    }
                    for link in recast.get("appearance_links", [])
                    if link["shot_id"] == stored["shot_id"]
                ],
                "included": included,
                "priority": stored["priority"],
                "plan": plan,
                "versions": versions,
                "layers": layers,
                "choice": choice,
                "status": status,
                "retaking": bool(
                    status in {"rendering", "queued"}
                    and stored["plan"]
                    and stored["plan"].get("_history")
                    and windows
                    and (
                        any(o["state"] not in p.SETTLED for o in windows)
                        or not assembly
                        or assembly["state"] not in p.SETTLED
                    )
                ),
            }
        )
    for shot in shots:
        shot["head_pass_refusal"] = _head_pass_refusal(shot)
    selected = [s for s in shots if run_scope.covered(s, p.run_range(recast))]
    seconds = run_scope.measured_seconds([o for o in all_ops if not o["request"].get("head")])
    current = next((s["id"] for s in selected if s["id"] in active_shots), None)
    done = sum(
        s["id"] in settled_shots
        and bool(s["versions"])
        and s["status"] in {"needs_review", "approved", "kept_original"}
        for s in selected
    )
    # The film's shared dialogue analysis is read here, never started (ADR 0016; only the Voice step starts it).
    dialogue, line_counts, by_character = lining.recast_parts(
        session, library, recast, report, cast_by_shot
    )
    stored = {c["id"]: c for c in recast["characters"]}
    for character in characters:
        character.update(
            by_character.get(character["id"], {"lines_proposed": 0, "lines_confirmed": 0})
        )
        # The voice build is read here, never started (ADR 0016 §4; only build_voice and retry_voice start it).
        character["voice"] = voicing.voice_view(
            session,
            library,
            recast,
            stored[character["id"]],
            character["lines_confirmed"],
            expressive_library,
        )
    view = {
        "id": recast_id,
        "title": recast["title"],
        "revision": recast["revision"],
        "film": p.film_view(session, library, recast, report),
        "analysis": p.analysis_view(session, recast),
        "dialogue": dialogue,
        "voice_recipe": voicing.recipe(),
        "lines": line_counts,
        "characters": characters,
        "unknown_faces": unknown,
        "not_same": [
            {"character_a": n["character_a"], "character_b": n["character_b"]}
            for n in recast["not_same"]
        ],
        "edits": p.store.edits(session, recast_id, 20),
        "shots": shots,
        "conforms": conform_states(session, recast_id),
        "run": {
            "state": recast["run_state"],
            "reason": recast["run_reason"],
            "mode": recast["run_mode"],
            "range": p.run_range(recast),
            "render_budget": recast["render_budget"],
            "renders_used": sum(p.render_cost(o) for o in all_ops),
            "shots_done": done,
            "shots_total": len(selected),
            "current_shot_id": current,
            # the start gate waits for these (the renderer reads it, never re-derives it)
            "analysing": run_scope.analysing(shots, p.run_range(recast)),
            "render_seconds": seconds,
        },
        "estimate": run_scope.run_estimate(
            recast,
            shots,
            {s["shot_id"]: s["plan"] for s in recast.get("shots", [])},
            analyses,
            seconds,
        ),
    }
    view["run"] |= run_scope.refusals(view)
    return view
