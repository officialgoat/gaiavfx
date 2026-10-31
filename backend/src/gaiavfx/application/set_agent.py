"""The agent's view of sets and its proposals (ADR 0018 §7): compact words, no file ids, sizes in rooms."""

from __future__ import annotations

from gaiavfx.application import set_fill
from gaiavfx.application.set_build import after_stop, settled
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
            f"{state['id']} ({fill_renders()} jobs); fill_set_more runs in it, about {set_fill.FILL_TIME}, and the user "
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
