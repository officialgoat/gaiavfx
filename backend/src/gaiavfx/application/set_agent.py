"""The agent's view of sets and its proposals (ADR 0018 §7): compact words, no file ids, sizes in rooms."""

from __future__ import annotations

from gaiavfx.application.sets import STORE, aim_view, clip_words, list_sets, set_state
from gaiavfx.modules.sets import cameras
from gaiavfx.shared.errors import ApplicationError

ROLE_WORDS = {
    "observed": "panorama",
    "generated": "explored view",
    "trajectory": "in-between frame",
    "appearance-only": "colour only",
}


def agent_sets(session, library, first=()) -> list[dict]:
    """The sets in compact words for the agent's context: no file ids, no provenance paths. At most eight: `first`
    (the stages of places in scope, then those the conversation used), then the newest."""
    rows = list_sets(session, library)
    rank = {set_id: n for n, set_id in enumerate(dict.fromkeys(first))}
    rows.sort(key=lambda row: rank.get(row["id"], len(rank)))
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "version": f"{row['shown_version']['state']} v{row['shown_version']['ordinal']}",
            "accepted": bool(row["current_version_id"]),
            "keyframes": row["keyframes"],
            "open_issues": row["open_issues"],
        }
        for row in rows[:8]
    ]


def agent_read_set(session, library, set_id: str) -> dict:
    """One set as the agent reads it: the shown version, the map in words, numbered issues, fixes and views.
    Sizes are in rooms; nothing is metric (§5)."""
    from gaiavfx.application.set_seed import fill_renders

    state = set_state(session, library, set_id)
    version = state["version"]
    meta = version["map_meta"] or {}
    coverage = meta.get("coverage") or {}
    room = meta.get("room")
    roles: dict[str, int] = {}
    for key in version["keyframes"]:
        word = ROLE_WORDS[key["role"]]
        roles[word] = roles.get(word, 0) + 1
    grid = meta.get("grid")
    return {
        "id": state["id"],
        "name": state["name"],
        "place": clip_words(state["place_text"], 600),
        "version": f"{version['state']} v{version['ordinal']} ({version['origin']})",
        "current": state["current_version_id"] == version["id"],
        "keyframes": roles,
        "cameras": [
            {k: c.get(k) for k in ("label", "heading", "cell")} for c in meta.get("cameras", [])
        ],
        "map": {
            "explored_rooms": (
                round(coverage["area"] / room**2, 1) if coverage.get("area") and room else None
            ),
            "enclosed": round(coverage["closure"], 2) if "closure" in coverage else None,
            "grid": (
                f"{grid['cols']} columns A… by {grid['rows']} rows 1…, row 1 toward heading 0"
                if grid
                else None
            ),
            "walls": [
                {k: w.get(k) for k in ("label", "heading", "cell")} for w in meta.get("walls", [])
            ][:8],
        },
        "issues": [
            {
                "number": n + 1,
                "id": issue["id"],
                "kind": issue["kind"],
                "author": issue["author"],
                "status": issue["status"],
                "why": clip_words(issue["why"], 300),
                "cells": issue["cells"],
                "cameras": issue["cameras"],
            }
            for n, issue in enumerate(state["issues"])
            if issue["status"] != "dismissed"
        ][:20],
        "queued_fixes": [
            {"action": f["action"], "shows": clip_words(f["shows"], 200), "author": f["author"]}
            for f in state["fixes"]
            if f["state"] == "queued"
        ],
        "views": [{"name": v["name"], "fov": round(v["fov"])} for v in state["views"]],
        "build": _build_words(state),
        "judge_note": "Judge issues are proposals from a model and can be wrong; the user decides.",
        "the_users": "Accepting or discarding versions, keeping or excluding stills and running queued fixes are "
        "the user's, in the stage workbench.",
        # The agent's own way to grow a stage (ADR 0020 §8); the live trial's model asked where instead.
        "fill_more": (
            f"To fill in what no view of it has seen yet, all around, propose a plan with one build step targeting "
            f"{state['id']} ({fill_renders()} jobs); fill_set_more runs in it, about 15 to 25 minutes, and the user "
            "decides the new version."
            if state["current_version_id"]
            else "It has no accepted version yet; filling more waits for the user to accept one."
        ),
    }


def seed_words(session, operation) -> str:
    """A build from a take in the agent's own words (ADR 0020 §8): what happened and what the agent may do next.
    `seed_state` keeps the user's words; these never address the user as "you" or carry an id the agent cannot use."""
    from gaiavfx.application.direction_reading import DIRECTION
    from gaiavfx.application.set_seed import seed_state

    seed = seed_state(session, operation)
    name = seed["name"]
    decided = [d for d in operation.get("review") or [] if d.get("decided_by") == "set_seed"]
    last = decided[-1] if decided else {}
    if seed["state"] == "building":
        label = seed["label"].rstrip("…")
        return f"{name}: {label[0].lower()}{label[1:]}; the conversation waits for the build."
    if seed["state"] == "refused":
        then = (
            "Filling its unseen space (fill_unseen) builds from a still or turning camera: offer that, and build "
            "again only in a plan the user approves."
            if last.get("reason") == "no_parallax" and not operation["request"].get("fill")
            else "Another shot or another take may build: ask the user."
        )
        return f"Building {name} was refused: {seed['message']} The build step is skipped. {then}"
    if seed["state"] == "failed":
        spent = (
            f", after {settled(operation)['budget_reserved']} of its {operation['budget_reserved']} renders"
            if operation["budget_reserved"]
            else ""
        )
        return f"Building {name} stopped before it finished{spent}" + after_stop(
            operation, "build", bool(spent)
        )
    place_id = operation["request"].get("place_id")
    place = DIRECTION.read_place(session, place_id) if place_id else None
    used = place is not None and place.get("set_id") == seed["set_id"]
    if seed["waiting"]:
        built = f"{name} is built" + (f" and {last['filled']}" if last.get("filled") else "")
        return (
            f"{built}. Its v1 waits for the user's decision in the stage workbench; the build step completes when "
            "they accept or discard it. Tell the user where it waits: accepting is theirs."
        )
    first = STORE.get(session, seed["set_id"])["versions"][0]
    if first["state"] == "accepted":
        return f"The user accepted v1 of {name}. " + (
            f"{place['name']} is built as it, so the scenes there read its views. Continue the plan."
            if used
            else "No place is built as it yet: link_set uses it for a scene. Continue the plan."
        )
    return (
        f"The user discarded v1 of {name}; the stage has no accepted version. "
        + (
            f"{place['name']} stays built as it, so the scenes there have no views from it until the user accepts "
            "a version or uses another stage. "
            if used
            else ""
        )
        + "The build step is skipped: tell the user and ask what they want."
    )


# Why a build stopped, in the agent's words; a stop the next build would meet the same way is not retried blindly.
STOP_CAUSES = {
    "stopped": "the user stopped it",
    "comfyui": "ComfyUI did not answer",
    "engines": "the stage engines are not set up",
    "memory": "the GPU ran out of memory",
}


def after_stop(operation, verb: str, reserved: bool, reason: str = "") -> str:
    """The end of a stopped build's sentence and what the agent may do next: build once more only when nothing it
    knows would stop the next build the same way. `reason` is the build's own, for a cause not named here."""
    from gaiavfx.application.set_seed import stop_cause

    cause = stop_cause(operation)
    # The build's own reason is a sentence of its own (the live trial read "…: The build stopped … as it was..").
    said = f": {STOP_CAUSES[cause]}." if cause else f". {reason.rstrip('.')}." if reason else "."
    if cause == "stopped":
        return f"{said} The build step stays open: ask the user before you {verb} again."
    if cause:
        return (
            f"{said} Another build stops the same way until that changes: tell the user, and {verb} once more in "
            "this step only when they say it is fixed."
        )
    if not reserved:
        return (
            f"{said} The build step stays open: {verb} once more in it, or tell the user and ask."
        )
    return (
        f"{said} The build step stays open with the rest: {verb} once more in it if they are enough, propose a "
        "revised plan if not, or tell the user and ask."
    )


def seed_outcome(session, operation) -> dict:
    """A system turn for a build from a take that ended or was decided: the agent's words as its message, the user's
    as its notice, and a receipt that keeps the stage in the agent's context. `decided` is the user's decision on v1
    (accepted or discarded), None before it."""
    from gaiavfx.application.set_seed import seed_state

    seed = seed_state(session, operation)
    receipt = {"kind": "set_build", "set_id": seed["set_id"], "operation_id": operation["id"]}
    decided = None
    if seed["state"] == "done" and not seed["waiting"]:
        decided = STORE.get(session, seed["set_id"])["versions"][0]["state"]
    return {
        "action": {"message": seed_words(session, operation), "notice": seed["message"]},
        "receipts": ([receipt] if seed["set_id"] else [])
        + ([settled(operation)] if seed["state"] == "failed" else []),
        "decided": decided,
    }


def agent_fill_more(session, library, set_id) -> dict:
    """The agent fills more of a stage's unseen space (ADR 0020 §8), as the user's Fill more unseen space and Run
    queued fixes do, from the stage's accepted version. Refused while any other fix is queued or runs: Run queued
    fixes would run the user's exclusions, kept stills and reshoots too. One transaction checks, queues and starts."""
    from gaiavfx.application.set_build import start_set_build
    from gaiavfx.application.sets import FILL_MORE

    row = STORE.get(session, set_id)
    if not row["current_version_id"]:
        raise ApplicationError(
            f"{row['name']} has no accepted version to fill from; the user accepts one first.", 409
        )
    pending = [f for f in STORE.fixes(session, set_id) if f["state"] in ("queued", "running")]
    if any(f["state"] == "running" for f in pending):
        raise ApplicationError(f"A build of {row['name']} is already running.", 409)
    if pending:
        raise ApplicationError(
            f"The user has {len(pending)} fixes queued on {row['name']}; Run queued fixes would run them with "
            "the filling. Ask the user to run or cancel them first.",
            409,
        )
    STORE.queue_fix(
        session,
        set_id,
        action="extend",
        author="agent",
        target={},
        shows=FILL_MORE,
        issue_id=None,
    )
    return start_set_build(session, library, set_id, base_version_id=row["current_version_id"])


def repair_state(session, operation) -> dict:
    """A build that fills more of a stage (a repair, ADR 0020 §8) in the user's words, in `seed_state`'s shape:
    building, failed (stopped) or done, with the version it proposed and whether it still waits for the user."""
    from gaiavfx.application import set_fill
    from gaiavfx.application.set_build import STOPPED

    row = STORE.get(session, operation["request"]["set_id"])
    name = row["name"]
    decided = [d for d in operation.get("review") or [] if d.get("decided_by") == "set_build"]
    last = decided[-1] if decided else {}
    base = {
        "operation_id": operation["id"],
        "set_id": row["id"],
        "name": name,
        "shot": None,
        "waiting": False,
        "can_retry": False,
    }
    if last.get("stage") == "done":
        version = next(v for v in row["versions"] if v["id"] == last["version_id"])
        n, waiting = version["ordinal"], version["state"] == "proposed"
        return base | {
            "state": "done",
            "label": f"v{n} to review" if waiting else f"v{n} {version['state']}",
            "message": f"{name} v{n} is built: {version['note']} "
            + (
                "It waits for you in the stage workbench."
                if waiting
                else f"v{n} {version['state']}."
            ),
            "waiting": waiting,
        }
    if last.get("stage") == "stopped" or operation["state"] in STOPPED:
        from gaiavfx.application.set_seed import STOP_WORDS, stop_cause

        cause = stop_cause(operation)
        reason = (
            " was stopped before it finished."
            if cause == "stopped"
            else f" stopped before it finished: {STOP_WORDS[cause]}"
            if cause
            else f" stopped before it finished. {last['reason'].rstrip('.')}."
            if last.get("reason")
            else " stopped before it finished."
        )
        return base | {
            "state": "failed",
            "label": "Stopped before it finished",
            "message": f"Filling more of {name}{reason}",
        }
    filling = set_fill.latest(operation)[0] not in ("prepare", "splat")
    label = set_fill.progress(operation) if filling else "Rebuilding the stage…"
    return base | {
        "state": "building",
        "label": label,
        "message": f"{name}: {label[0].lower()}{label[1:]}",
    }


def repair_outcome(session, operation) -> dict:
    """`seed_outcome` for a build that fills more of a stage: the agent's words, the user's, its receipts and the
    user's decision on the version it proposed. A discarded version leaves the stage's accepted one in place."""
    state = repair_state(session, operation)
    name = state["name"]
    receipts = [{"kind": "set_build", "set_id": state["set_id"], "operation_id": operation["id"]}]
    decided = None
    if state["state"] == "building":
        words = (
            f"{name}: {state['label'].rstrip('…')[0].lower()}{state['label'].rstrip('…')[1:]}; the "
        )
        words += "conversation waits for the build."
    elif state["state"] == "failed":
        receipts.append(settled(operation))
        spent = receipts[-1]["budget_reserved"]
        stops = [d for d in operation.get("review") or [] if d.get("stage") == "stopped"]
        words = (
            f"Filling more of {name} stopped before it finished, after {spent} of its "
            f"{operation['budget_reserved']} renders"
            + after_stop(operation, "fill", True, stops[-1].get("reason", "") if stops else "")
        )
    else:
        row = STORE.get(session, state["set_id"])
        version_id = operation["review"][-1]["version_id"]
        version = next(v for v in row["versions"] if v["id"] == version_id)
        n = version["ordinal"]
        if state["waiting"]:
            words = (
                f"{name} v{n} is built ({version['note'].rstrip('.')}). It waits for the user's decision in the "
                "stage workbench; the build step completes when they accept or discard it. Tell the user where it "
                "waits: accepting is theirs."
            )
        elif version["state"] == "accepted":
            decided = "accepted"
            words = f"The user accepted v{n} of {name}; the scenes that use it read it now. Continue the plan."
        else:
            decided = "discarded"
            base = next(v for v in row["versions"] if v["id"] == version["parent_id"])
            words = (
                f"The user discarded v{n} of {name}; v{base['ordinal']} stays the stage's. The build step is "
                "skipped: tell the user and ask what they want."
            )
    return {
        "action": {"message": words, "notice": state["message"]},
        "receipts": receipts,
        "decided": decided,
    }


def build_state(session, operation) -> dict:
    """A stage build in the user's words: from a take (`seed_state`) or filling more of a stage."""
    from gaiavfx.application.set_seed import seed_state

    if operation["request"].get("mode") == "seed":
        return seed_state(session, operation)
    return repair_state(session, operation)


def build_outcome(session, operation) -> dict:
    """A stage build's system turn and the user's decision: from a take or filling more of a stage."""
    if operation["request"].get("mode") == "seed":
        return seed_outcome(session, operation)
    return repair_outcome(session, operation)


def settled(operation) -> dict:
    """What a stopped build spent, as an operation receipt that settles its reservation (ADR 0020 §8): the probes it
    allocated, a failed one included. The renders it never allocated return to its step and the conversation."""
    from gaiavfx.application import set_fill

    return {
        "kind": "operation",
        "id": operation["id"],
        "operation_kind": operation["kind"],
        "budget_reserved": min(operation["budget_reserved"], set_fill.renders_used(operation)),
        "settled": True,
    }


def _build_words(state: dict) -> str | None:
    """The newest build in one sentence for the agent: running, waiting for the user's review, or stopped."""
    build = state["build"]
    if not build:
        return None
    ordinals = {v["id"]: v for v in state["versions"]}
    base = ordinals.get(build["base_version_id"])
    built = ordinals.get(build["version_id"])
    if build["state"] in ("queued", "running", "awaiting_review"):
        stage = (
            build["filling"].rstrip("…").lower()
            if build.get("filling")
            else "fitting the splat on the GPU"
            if build["stage"] == "splat"
            else "preparing"
        )
        return f"A build from v{base['ordinal'] if base else '?'} is running ({stage})."
    if built and built["state"] == "proposed":
        return (
            f"v{built['ordinal']} was built and waits for the user's review in the stage workbench."
        )
    if built:
        return f"The last build made v{built['ordinal']}, which the user {built['state']}."
    return f"The last build stopped ({build['state']}): {clip_words(build['error'] or 'no reason', 200)}"


def agent_mark_issue(session, library, set_id, *, kind, why, cells, cameras) -> dict:
    """The agent proposes an issue on the set's shown version (author `agent`); the user sees it in the workbench."""
    state = set_state(session, library, set_id)
    known = {c.get("label") for c in (state["version"]["map_meta"] or {}).get("cameras", [])}
    unknown = [c for c in cameras if c not in known]
    if unknown:
        raise ApplicationError(f"Unknown cameras {unknown}; read_set lists them.", 422)
    STORE.add_issue(
        session,
        set_id,
        version_id=state["version"]["id"],
        kind=kind,
        author="agent",
        why=why,
        cells=cells,
        cameras=cameras,
    )
    return agent_read_set(session, library, set_id)


def agent_save_view(
    session, library, set_id, *, name, camera, heading, angle_id=None
) -> tuple[dict, str]:
    """The agent saves a level camera standing at a keyframe camera and looking along `heading` (degrees, 0 = the
    set's forward, clockwise seen from above); with `angle_id`, it is that scene angle's camera (ADR 0019 §2), as
    when the user aims a view in the workbench. Aiming approves nothing: the render is reviewed in Prepare. Returns
    the set as the agent reads it and the saved view's id."""
    state = set_state(session, library, set_id)
    key = next((k for k in state["version"]["keyframes"] if k["label"] == camera), None)
    if key is None:
        raise ApplicationError(f"No camera {camera} in this stage; read_set lists them.", 422)
    level = cameras.level(cameras.centre(key["viewmat"]), heading)
    view_id = STORE.save_view(
        session,
        set_id,
        version_id=state["version"]["id"],
        name=name,
        viewmat=level.round(6).tolist(),
        fov=50.0,
        width=1024,
        height=576,
        author="agent",
    )
    if angle_id is not None:
        aim_view(
            session, library, set_id, view_id, angle_id=angle_id, version_id=state["version"]["id"]
        )
    return agent_read_set(session, library, set_id), view_id


def link_words(preview: dict) -> str:
    """What using a set for a scene changed, in the words Use set's preview gives the user (set_scenes)."""
    words = [
        f"{preview['place_name']} is built as the stage {preview['set_name']} for every scene in it"
        + (f" (it was {preview['relinks_from']})" if preview["relinks_from"] else "")
    ]
    if preview["other_scenes"]:
        words.append(
            "the other scenes there read it too: "
            + ", ".join(f"{s['label']} of {s['movie']}" for s in preview["other_scenes"])
        )
    words.append(f"this scene opens on {preview['angle_name']} and asks for the stage's views")
    if preview["first_cut_changes"]:
        words.append("its first cut now starts there, which clears that cut's relation")
    if not preview["accepted"]:
        words.append(f"{preview['set_name']} has no accepted version yet; the user accepts one")
    if not preview["camera"]:
        words.append(
            f"{preview['angle_name']} has no camera in the stage yet: save_set_view with its angle_id aims one, "
            "or the user aims one in the stage workbench"
        )
    return (
        "; ".join(words)
        + ". Undo restores the scene's first shot; the place stays built as the stage."
    )


def agent_use_set(
    session, segment_id, *, expected_revision, set_id, angle_id=None
) -> tuple[dict, str]:
    """Use a set for a scene, as the user's Use set does, and say what changed."""
    from gaiavfx.application.set_scenes import use_set, use_set_preview

    preview = use_set_preview(session, segment_id, set_id=set_id, angle_id=angle_id)
    row = use_set(
        session, segment_id, expected_revision=expected_revision, set_id=set_id, angle_id=angle_id
    )
    return row, link_words(preview)
