"""Film recast use cases. All public edits are revision-checked transactions."""

import json
import re
from copy import deepcopy
from statistics import median

from gaiavfx.application import recast_edits as journaling
from gaiavfx.application import recast_feed as feeding
from gaiavfx.application import recast_lines as lining
from gaiavfx.application import recast_planning as planning
from gaiavfx.application import recast_projection as p
from gaiavfx.application import recast_voice as voicing
from gaiavfx.application.recast_feed import (
    advance_reference,
    cancel_reference,
    reference_operations,
)
from gaiavfx.application.recast_run import seed_window_seconds
from gaiavfx.application.target_faces import (
    TargetReportPending,
    TargetReportRefused,
    start_target_report,
    target_report,
)
from gaiavfx.infrastructure.database import timestamp
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.errors import ApplicationError


def _load(session, library, recast_id, expected_revision=None):
    if expected_revision is not None:
        p.store.lock(session, recast_id, expected_revision)
    recast = p.store.load(session, recast_id)
    report = p.report_for(session, library, recast)
    p.sync(recast, report)
    return recast, report


def _finish(session, library, recast, journal=None, **entry):
    """One revision per command; a journaled cast edit also records its rows (recast_edits)."""
    recast["revision"] += 1
    recast["updated_at"] = timestamp()
    report = p.report_for(session, library, recast)
    planning.refresh_all(session, library, recast, report)
    if journal is not None:
        journaling.finish(session, p.store, recast, report, journal, **entry)
    p.store.save(session, recast)
    return recast_state(session, library, recast["id"])


def _journaled(session, library, recast, journal, command, **facts):
    summary = journaling.summary_for(command, journal, recast, **facts)
    return _finish(session, library, recast, journal=journal, command=command, summary=summary)


def _character(recast, character_id):
    return _find(recast["characters"], "id", character_id, "Character")


def _find(values, key, value, name):
    found = next((v for v in values if v[key] == value), None)
    if found is None:
        raise ApplicationError(f"{name} not found in this recast.", 404)
    return found


def _shot(recast, shot_id):
    return _find(recast["shots"], "shot_id", shot_id, "Shot")


def _range(value, report):
    if value is not None and (
        not report or not 0 <= value["start"] < value["end"] <= report["frame_count"]
    ):
        raise ApplicationError("The range must be inside the film with start before end.", 422)


def _mode(mode):
    if mode not in {"draft", "final"}:
        raise ApplicationError("Unknown run mode.", 422)


def create_recast(session, library, *, source_asset_id, title, request_key):
    existing = p.store.for_request(session, request_key)
    if existing:
        row = p.store.load(session, existing)
        if row["source_asset_id"] != source_asset_id or (
            title is not None and row["title"] != title
        ):
            raise ApplicationError("Request key already belongs to another recast request.", 409)
        return {**recast_state(session, library, existing), "_created": False}
    asset = library.get(session, source_asset_id)
    if asset["kind"] != "video":
        raise ApplicationError("A recast source must be a Library video.", 422)
    recast = p.store.create(
        session,
        source_asset_id=source_asset_id,
        title=title or asset["name"][:120],
        request_key=request_key,
    )
    from gaiavfx.application.film_analysis import start_film_analysis

    operation = start_film_analysis(
        session, library, source_asset_id, request_key=planning.key(recast["id"], "film")
    )
    recast["analysis_operation_id"] = operation["id"]
    p.store.save(session, recast)
    return {**recast_state(session, library, recast["id"]), "_created": True}


def list_recasts(session, library):
    result = []
    for recast_id in p.store.ids(session):
        state = recast_state(session, library, recast_id)
        row = p.store.load(session, recast_id)
        result.append(
            {
                "id": recast_id,
                "title": state["title"],
                "source_asset_id": state["film"]["asset_id"],
                "revision": state["revision"],
                "analysis_state": state["analysis"]["state"],
                "shots_total": len(state["shots"]),
                "shots_approved": sum(
                    s["status"] in {"approved", "kept_original"} for s in state["shots"]
                ),
                "updated_at": row["updated_at"],
            }
        )
    return result


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


def _estimate(recast, shots, operations, analyses=None):
    analyses = analyses or {}
    selected = [
        s
        for s in shots
        if s["included"]
        and s["plan"]
        and (not s["plan"]["gaps"] or s["status"] == "analysing")
        and p.in_range(s, p.run_range(recast))
    ]
    stored = {s["shot_id"]: s["plan"] for s in recast.get("shots", [])}
    plans = [stored.get(s["id"]) or s["plan"] for s in selected]
    if not plans:
        return None
    durations = [duration for op in operations if (duration := p.render_seconds(op)) is not None]
    measured = len(durations) >= 3
    count = 0
    seconds = 0
    frames = 0
    for plan in plans:
        bridges = feeding.bridge_reservation(
            plan, plan["renders_per_window"], mode=recast["run_mode"]
        )
        count += bridges
        seconds += bridges * (median(durations) if measured else seed_window_seconds(73, 0))
        frames += bridges * 73
        for index, window in enumerate(plan["windows"]):
            count += plan["renders_per_window"]
            seconds += plan["renders_per_window"] * (
                median(durations) if measured else seed_window_seconds(window["length"], index)
            )
            frames += (window["end"] - window["start"]) * plan["renders_per_window"]
    return {
        "shots": len(plans),
        "windows": sum(
            max(len(s["plan"]["windows"]), len(analyses.get(s["id"], []))) for s in selected
        ),
        "windows_pending": sum(
            o["state"] not in p.SETTLED for s in selected for o in analyses.get(s["id"], [])
        ),
        "renders": count,
        "gpu_seconds_low": int(seconds * 0.7),
        "gpu_seconds_high": int(seconds * 1.3),
        "storage_bytes": frames * 2_500_000,
        "basis": "measured" if measured else "seed",
    }


def recast_state(session, library, recast_id, *, private_plans=False, expressive_library=None):
    """RecastDTO. Read only: it starts nothing. `expressive_library` (the Settings folder) is given only by the page's
    read, which then compares each built voice with the expressive recordings (`library_changed`)."""
    from gaiavfx.application.recast_conform import conform_states

    recast, report = _load(session, library, recast_id)
    planning.refresh_all(session, library, recast, report)
    margin_min = p.policy().identity_margin_min
    characters, unknown = p.character_views(recast, report)
    for character in characters:
        if target := character["target"]:
            face = target_report(session, library, target["asset_id"])
            target["face"] = face["state"] if face else "checking"
    targets = planning.mapped(recast)
    shots, all_ops = [], []
    analyses = {}
    cast_by_shot = {}
    settled_shots, active_shots = set(), set()
    for shot in report["shots"] if report else []:
        stored = _shot(recast, p.shot_id(shot))
        appearances = p.appearances(
            session, library, recast, report, shot, stored, margin_min=margin_min
        )
        cast_by_shot[stored["shot_id"]] = appearances
        mapped = any(a["character_id"] in targets for a in appearances)
        included = mapped if stored["included"] is None else stored["included"]
        try:
            versions = p.public_versions(p.versions(session, library, stored))
        except ApplicationError as exc:
            planning.error(stored, exc)
            versions = []
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
    selected = [
        s
        for s in shots
        if s["included"]
        and s["plan"]
        and s["plan"]["performers"]
        and not s["plan"]["gaps"]
        and s["plan"]["windows"]
        and p.in_range(s, p.run_range(recast))
    ]
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
    return {
        "id": recast_id,
        "title": recast["title"],
        "revision": recast["revision"],
        "film": p.film_view(session, library, recast, report),
        "analysis": p.analysis_view(session, recast),
        "dialogue": dialogue,
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
        },
        "estimate": _estimate(
            recast, shots, [o for o in all_ops if not o["request"].get("head")], analyses
        ),
    }


def patch_character(
    session, library, recast_id, character_id, *, expected_revision, label=None, hidden=None
):
    recast, report = _load(session, library, recast_id, expected_revision)
    journal = journaling.capture(recast, report)
    character = _character(recast, character_id)
    if label is not None:
        character["label"] = label
    if hidden is not None:
        character["hidden"] = hidden
    return _journaled(
        session, library, recast, journal, "patch_character", character_id=character_id
    )


def _override(recast, track_ids, to):
    recast["overrides"] = [o for o in recast["overrides"] if o["track_id"] not in track_ids]
    recast["overrides"].extend(
        {"recast_id": recast["id"], "track_id": t, "character_id": to, "created_at": timestamp()}
        for t in track_ids
    )


def merge_characters(session, library, recast_id, *, expected_revision, into, sources):
    recast, report = _load(session, library, recast_id, expected_revision)
    # Moving line decisions ties the merge's undo to the current dialogue analysis, as a line command's.
    dialogue = (
        lining.current_id(session, library, recast)
        if lining.merge_touches_lines(recast, sources)
        else None
    )
    journal = journaling.capture(recast, report, dialogue_operation_id=dialogue)
    remaining = _character(recast, into)
    if into in sources:
        raise ApplicationError("A merge source cannot be its destination.", 422)
    for source in sources:
        _character(recast, source)
    # A voice moves to the character that remains when it has none: all five columns of the first source with
    # recordings (ADR 0016 §1). Otherwise the sources' voices go with them; their builds stay Library evidence.
    if not remaining.get("voice_asset_ids"):
        donor = next(
            (
                found
                for found in (_character(recast, s) for s in sources)
                if found.get("voice_asset_ids")
            ),
            None,
        )
        if donor is not None:
            remaining.update({k: deepcopy(donor[k]) for k in journaling.VOICE})
    owners = p.track_characters(recast, report)
    _override(recast, [t for t, c in owners.items() if c in sources], into)
    for link in recast.get("appearance_links", []):
        if link["character_id"] in sources:
            link["character_id"] = into
    recast["lines"] = lining.merge_rows(recast.get("lines", []), set(sources), into)
    # A source set aside from a character stays set aside as the character that remains: A is C, and A is not B, so C
    # is not B. A pair that merged into itself goes.
    pairs = {}
    for row in recast["not_same"]:
        a, b = (into if c in sources else c for c in (row["character_a"], row["character_b"]))
        if a != b:
            first, second = sorted((a, b))
            pairs.setdefault((first, second), {**row, "character_a": first, "character_b": second})
    recast["not_same"] = list(pairs.values())
    recast["characters"] = [c for c in recast["characters"] if c["id"] not in sources]
    return _journaled(
        session, library, recast, journal, "merge_characters", into=into, sources=sources
    )


def set_not_same(session, library, recast_id, *, expected_revision, pairs):
    """The user's "Not the same person" (ADR 0017, CP3): the pair's suggestion stays hidden, in every browser."""
    recast, report = _load(session, library, recast_id, expected_revision)
    journal = journaling.capture(recast, report)
    stored = {(n["character_a"], n["character_b"]) for n in recast["not_same"]}
    for a, b in pairs:
        if a == b:
            raise ApplicationError("A character cannot be set aside from itself.", 422)
        _character(recast, a)
        _character(recast, b)
        pair = tuple(sorted((a, b)))
        if pair not in stored:
            stored.add(pair)
            recast["not_same"].append(
                {
                    "recast_id": recast["id"],
                    "character_a": pair[0],
                    "character_b": pair[1],
                    "created_at": timestamp(),
                }
            )
    return _journaled(session, library, recast, journal, "set_not_same")


def move_tracks(session, library, recast_id, *, expected_revision, track_ids, to):
    recast, report = _load(session, library, recast_id, expected_revision)
    journal = journaling.capture(recast, report)
    if not report or set(track_ids) - {t["id"] for t in report["tracks"]}:
        raise ApplicationError("A track does not belong to this film report.", 422)
    new = to == "new"
    if new:
        to = p.new_character(recast)["id"]
    elif to != "unknown":
        _character(recast, to)
    _override(recast, set(track_ids), to)
    return _journaled(
        session, library, recast, journal, "move_tracks", track_ids=track_ids, to=to, new=new
    )


def named_character(recast, character_id, user_message):
    """A model's evidence or guessed identity cannot supply the user's naming decision."""
    mentioned = [
        c["id"]
        for c in recast["characters"]
        if not c["hidden"]
        and any(
            re.search(r"(?<!\w)" + re.escape(c[k]) + r"(?!\w)", user_message, re.IGNORECASE)
            for k in ("id", "label")
        )
    ]
    return mentioned == [character_id]


def _appearance_track(session, library, recast, shot_id, analysis_operation_id, track_id):
    shot = _shot(recast, shot_id)
    if analysis_operation_id not in shot["window_analysis_ops"]:
        raise ApplicationError("Analysis does not belong to this shot's current windows.", 422)
    window = p.window_report(session, library, analysis_operation_id)
    if not window:
        raise ApplicationError("Window analysis must finish before linking a person.", 409)
    track = _find(window["tracks"], "id", track_id, "Window track")
    if track.get("concept") != "person":
        raise ApplicationError("Only person tracks can have appearance links.", 422)
    return track


def shot_people(session, library, state, number):
    """A shot by its number (Shot 001 is 1) with the person tracks of its finished window analyses: where each person
    stands, how much of the window they are present and their current link. Read-only; an appearance proposal needs
    these ids (the film conversation's trial, 22 October, F3)."""
    shot = next((s for s in state["shots"] if s["index"] + 1 == number), None)
    if shot is None:
        raise ApplicationError(f"Shot {number} is not in this film.", 404)
    recast = p.store.load(session, state["id"])
    windows = []
    for operation_id in _shot(recast, shot["id"])["window_analysis_ops"]:
        window = p.window_report(session, library, operation_id)
        if not window:
            windows.append({"analysis_operation_id": operation_id, "finished": False})
            continue
        people = []
        for track in window["tracks"]:
            if track.get("concept") != "person":
                continue
            box, width = track["box"], window["native"][0]
            centre = (box["x0"] + box["x1"]) / 2 / width
            link = p.appearance_link(recast, operation_id, track["id"])
            people.append(
                {
                    "track_id": track["id"],
                    "position": "left"
                    if centre < 1 / 3
                    else "right"
                    if centre > 2 / 3
                    else "centre",
                    "present": round(
                        track["presence"].count("present") / len(track["presence"]), 2
                    ),
                    "link": link and {k: link[k] for k in ("character_id", "state", "proposed_by")},
                }
            )
        windows.append({"analysis_operation_id": operation_id, "finished": True, "people": people})
    return {
        "id": shot["id"],
        "number": number,
        "start": shot["start"],
        "end": shot["end"],
        "status": shot["status"],
        "windows": windows,
    }


def _propose_appearance(
    recast, shot_id, analysis_operation_id, track_id, character_id, proposed_by, evidence
):
    existing = p.appearance_link(recast, analysis_operation_id, track_id)
    if existing and existing["state"] in {"confirmed", "rejected"}:
        # A single current decision owns this track. Proposals cannot erase a rejection
        # (including by proposing another character); the human can clear or replace it.
        raise ApplicationError("Clear the human appearance decision before proposing again.", 409)
    if existing and proposed_by == "analysis" and existing["proposed_by"] in {"agent", "user"}:
        raise ApplicationError("Analysis cannot overwrite a pending agent or user proposal.", 409)
    if not isinstance(evidence, dict):
        raise ApplicationError("Appearance evidence must be a JSON object.", 422)
    try:
        json.dumps(evidence, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ApplicationError("Appearance evidence must be finite JSON.", 422) from exc
    row = {
        "recast_id": recast["id"],
        "shot_id": shot_id,
        "analysis_operation_id": analysis_operation_id,
        "track_id": track_id,
        "character_id": character_id,
        "state": "proposed",
        "proposed_by": proposed_by,
        "evidence": deepcopy(evidence),
        "created_at": existing["created_at"] if existing else timestamp(),
        "decided_at": None,
    }
    if existing:
        existing.update(row)
    else:
        recast.setdefault("appearance_links", []).append(row)


def propose_appearance(
    session,
    library,
    recast_id,
    shot_id,
    analysis_operation_id,
    track_id,
    character_id,
    *,
    expected_revision,
    proposed_by,
    evidence,
    user_message="",
):
    recast, _ = _load(session, library, recast_id, expected_revision)
    _character(recast, character_id)
    if proposed_by not in {"analysis", "agent", "user"}:
        raise ApplicationError("Unknown appearance proposer.", 422)
    if proposed_by == "agent" and not named_character(recast, character_id, user_message):
        raise ApplicationError("The user must name the character before an agent proposal.", 422)
    _appearance_track(session, library, recast, shot_id, analysis_operation_id, track_id)
    _propose_appearance(
        recast, shot_id, analysis_operation_id, track_id, character_id, proposed_by, evidence
    )
    return _finish(session, library, recast)


def _decide_appearance(
    session,
    library,
    recast_id,
    shot_id,
    analysis_operation_id,
    track_id,
    character_id,
    *,
    expected_revision,
    state,
    authority,
):
    if authority != "user":
        raise ApplicationError("Only a human can decide an appearance link.", 403)
    recast, _ = _load(session, library, recast_id, expected_revision)
    _character(recast, character_id)
    _appearance_track(session, library, recast, shot_id, analysis_operation_id, track_id)
    link = p.appearance_link(recast, analysis_operation_id, track_id)
    if link is None or link["character_id"] != character_id:
        if link:
            recast["appearance_links"].remove(link)
        _propose_appearance(
            recast, shot_id, analysis_operation_id, track_id, character_id, "user", {}
        )
        link = p.appearance_link(recast, analysis_operation_id, track_id)
    link.update(state=state, decided_at=timestamp())
    return _finish(session, library, recast)


def confirm_appearance(
    session,
    library,
    recast_id,
    shot_id,
    analysis_operation_id,
    track_id,
    character_id,
    *,
    expected_revision,
    authority="user",
):
    return _decide_appearance(
        session,
        library,
        recast_id,
        shot_id,
        analysis_operation_id,
        track_id,
        character_id,
        expected_revision=expected_revision,
        state="confirmed",
        authority=authority,
    )


def reject_appearance(
    session,
    library,
    recast_id,
    shot_id,
    analysis_operation_id,
    track_id,
    character_id,
    *,
    expected_revision,
    authority="user",
):
    return _decide_appearance(
        session,
        library,
        recast_id,
        shot_id,
        analysis_operation_id,
        track_id,
        character_id,
        expected_revision=expected_revision,
        state="rejected",
        authority=authority,
    )


def clear_appearance(
    session,
    library,
    recast_id,
    shot_id,
    analysis_operation_id,
    track_id,
    *,
    expected_revision,
    authority="user",
):
    if authority != "user":
        raise ApplicationError("Only a human can clear an appearance link.", 403)
    recast, _ = _load(session, library, recast_id, expected_revision)
    _shot(recast, shot_id)
    link = p.appearance_link(recast, analysis_operation_id, track_id)
    if not link or link["shot_id"] != shot_id:
        raise ApplicationError("Appearance link not found in this shot.", 404)
    recast["appearance_links"].remove(link)
    return _finish(session, library, recast)


def _target(
    session, library, recast_id, character_id, expected_revision, asset_id, label, *, authority
):
    recast, report = _load(session, library, recast_id, expected_revision)
    journal = journaling.capture(recast, report)
    character = _character(recast, character_id)
    if authority == "agent" and character["target_state"] == "confirmed":
        raise ApplicationError(
            "A confirmed human mapping cannot be replaced by an agent proposal.", 409
        )
    try:
        asset = MediaLibrary.asset_info(session, asset_id)
    except ApplicationError as exc:
        raise ApplicationError("Replacement targets must be Library images.", 422) from exc
    if asset["kind"] != "image":
        raise ApplicationError("Replacement targets must be Library images.", 422)
    start_target_report(session, library, asset_id, retry_failed=authority == "user")
    if character["target_asset_id"] != asset_id:
        cancel_reference(session, recast, character)
        character.update(reference_asset_id=None, reference_state=None)
    character.update(
        target_asset_id=asset_id,
        target_label=label,
        target_proposed_by=authority,
        target_state="confirmed" if authority == "user" else "proposed",
    )
    if authority != "user":
        return _finish(session, library, recast)  # an agent proposal is not the user's edit
    advance_reference(session, library, recast, character, retry_failed=True)
    return _journaled(
        session, library, recast, journal, "set_target", character_id=character_id, label=label
    )


def set_target(session, library, recast_id, character_id, *, expected_revision, asset_id, label):
    return _target(
        session,
        library,
        recast_id,
        character_id,
        expected_revision,
        asset_id,
        label,
        authority="user",
    )


def propose_target(
    session, library, recast_id, character_id, *, expected_revision, asset_id, label
):
    return _target(
        session,
        library,
        recast_id,
        character_id,
        expected_revision,
        asset_id,
        label,
        authority="agent",
    )


def confirm_target(session, library, recast_id, character_id, *, expected_revision):
    recast, _ = _load(session, library, recast_id, expected_revision)
    character = _character(recast, character_id)
    if not character["target_state"]:
        raise ApplicationError("No target has been proposed.", 409)
    if MediaLibrary.asset_info(session, character["target_asset_id"])["kind"] != "image":
        raise ApplicationError("Replacement targets must be Library images.", 422)
    character["target_state"] = "confirmed"
    start_target_report(session, library, character["target_asset_id"], retry_failed=True)
    advance_reference(session, library, recast, character, retry_failed=True)
    return _finish(session, library, recast)


def clear_target(session, library, recast_id, character_id, expected_revision):
    recast, report = _load(session, library, recast_id, expected_revision)
    journal = journaling.capture(recast, report)
    character = _character(recast, character_id)
    cancel_reference(session, recast, character)
    character.update(
        target_asset_id=None,
        target_label=None,
        target_state=None,
        target_proposed_by=None,
        reference_asset_id=None,
        reference_state=None,
    )
    return _journaled(session, library, recast, journal, "clear_target", character_id=character_id)


_UNSET = object()


def patch_shot(
    session, library, recast_id, shot_id, *, expected_revision, included=_UNSET, priority=_UNSET
):
    recast, _ = _load(session, library, recast_id, expected_revision)
    shot = _shot(recast, shot_id)
    if included is not _UNSET:
        shot["included"] = included
    if priority is not _UNSET:
        shot["priority"] = priority
    return _finish(session, library, recast)


def plan_recast(session, library, recast_id, *, expected_revision, range=None):
    recast, report = _load(session, library, recast_id, expected_revision)
    if not report:
        raise ApplicationError("Film analysis must finish before planning.", 409)
    _range(range, report)
    targets = planning.mapped(recast)
    for shot in report["shots"]:
        stored = _shot(recast, p.shot_id(shot))
        if stored["included"] is False or not p.in_range(shot, range):
            continue
        performers = [
            a["character_id"]
            for a in p.appearances(session, library, recast, report, shot, stored)
            if a["character_id"] in targets
        ]
        if not performers:
            continue
        if not stored["plan"]:
            stored["plan"] = planning.blank_plan(performers)
        if any(g["code"] == "planning_error" for g in stored["plan"]["gaps"]):
            fingerprint = stored["plan"].pop("_fingerprint", None)
            p.cache(library, "plans").pop((recast_id, stored["shot_id"], fingerprint), None)
        stored["plan"].pop("_analysis_error", None)
        if not stored["window_analysis_ops"]:
            try:
                with session.begin_nested():
                    analyses = p.window_function("start_window_analyses")(
                        session,
                        library,
                        source_asset=recast["source_asset_id"],
                        shot_start=shot["start"],
                        shot_end=shot["end"],
                        faces_operation=recast["analysis_operation_id"],
                        request_key=planning.key(recast_id, stored["shot_id"], "analysis"),
                    )
                stored["window_analysis_ops"] = [o["id"] for o in analyses]
            except ApplicationError as exc:
                planning.error(stored, exc)
                stored["plan"]["_analysis_error"] = str(exc)
    planning.refresh_all(session, library, recast, report)
    return _finish(session, library, recast)


def start_run(session, library, recast_id, *, expected_revision, mode, range, render_budget):
    recast, report = _load(session, library, recast_id, expected_revision)
    _mode(mode)
    _range(range, report)
    if not report or not any(s["plan"] for s in recast["shots"]):
        raise ApplicationError("Plan the recast before starting a run.", 409)
    used = feeding.used(session, recast)
    if type(render_budget) is not int or not 1 <= render_budget <= 10000 or render_budget < used:
        raise ApplicationError("The budget cannot be below renders already reserved.", 422)
    recast.update(
        run_state="running",
        run_reason=None,
        run_mode=mode,
        render_budget=render_budget,
        run_range_start=range["start"] if range else None,
        run_range_end=range["end"] if range else None,
    )
    from gaiavfx.application.recast_feed import promote_drafts

    promote_drafts(session, recast, report)
    return _finish(session, library, recast)


def retry_analysis(session, library, recast_id, *, expected_revision):
    """Start a fresh film analysis after one ended without success; user decisions are kept."""
    recast, _ = _load(session, library, recast_id, expected_revision)
    previous = p.operations.operation(session, recast["analysis_operation_id"])
    if previous["state"] not in {"failed", "cancelled", "partial", "needs_attention"}:
        raise ApplicationError("Film analysis is running or finished; nothing to retry.", 409)
    from gaiavfx.application.film_analysis import start_film_analysis

    operation = start_film_analysis(
        session,
        library,
        recast["source_asset_id"],
        request_key=planning.key(recast["id"], "film", recast["revision"] + 1),
    )
    recast["analysis_operation_id"] = operation["id"]
    return _finish(session, library, recast)


def pause_run(session, library, recast_id, *, expected_revision):
    recast, _ = _load(session, library, recast_id, expected_revision)
    recast["run_state"] = "paused"
    recast["run_reason"] = "Paused by the user."
    return _finish(session, library, recast)


def cancel_run(session, library, recast_id, *, expected_revision):
    recast, _ = _load(session, library, recast_id, expected_revision)
    recast["run_state"] = "idle"
    recast["run_reason"] = None
    for shot in recast["shots"]:
        for layer in shot.get("layers", []):
            if p.layer_status(session, library, layer)["state"] != "rendering":
                continue
            layer["cancelled"] = True
            for op_id in layer["operations"] + (
                [layer["assembly_op"]] if layer["assembly_op"] else []
            ):
                if p.operations.operation(session, op_id)["state"] not in p.SETTLED:
                    p.operations.request_cancel(session, op_id)
        if not shot["window_replacement_ops"]:
            continue
        assembly = (
            p.operations.operation(session, shot["assembly_op"]) if shot["assembly_op"] else None
        )
        if assembly and assembly["state"] in p.SETTLED:
            continue
        for op_id in (
            shot["window_replacement_ops"]
            + shot.get("bridge_ops", [])
            + ([shot["assembly_op"]] if shot["assembly_op"] else [])
        ):
            op = p.operations.operation(session, op_id)
            if op["state"] not in p.SETTLED:
                p.operations.request_cancel(session, op_id)
        planning.archive(shot, cancelled=True)
    return _finish(session, library, recast)


def choose_version(session, library, recast_id, shot_id, *, expected_revision, version):
    recast, _ = _load(session, library, recast_id, expected_revision)
    shot = _shot(recast, shot_id)
    if version not in {"original", "unchosen"} and version not in {
        v["id"] for v in p.versions(session, library, shot)
    }:
        raise ApplicationError("Choose an existing shot version, original, or unchosen.", 422)
    recast["revision"] += 1
    recast["updated_at"] = timestamp()
    p.store.choose(session, recast, shot_id, version)
    p.store.save(session, recast)
    return recast_state(session, library, recast_id)


def retake_shot(session, library, recast_id, shot_id, *, expected_revision, mode):
    from gaiavfx.application.recast_feed import start_shot

    recast, report = _load(session, library, recast_id, expected_revision)
    _mode(mode)
    shot = _shot(recast, shot_id)
    if (
        not shot["assembly_op"]
        or p.operations.operation(session, shot["assembly_op"])["state"] not in p.SETTLED
    ):
        raise ApplicationError("Wait for the current shot assembly before a retake.", 409)
    planning.archive(shot)
    film_shot = _find(report["shots"], "index", int(shot_id[1:]), "Shot")
    planning.refresh(session, library, recast, report, film_shot, shot)
    if not start_shot(session, library, recast, film_shot, shot, mode):
        raise ApplicationError("The shot is not ready or the render budget is exhausted.", 409)
    return _finish(session, library, recast)


def start_head_pass(
    session, library, recast_id, shot_id, *, expected_revision, version, character_id
):
    from gaiavfx.application.recast_feed import used

    recast, report = _load(session, library, recast_id, expected_revision)
    shot = _shot(recast, shot_id)
    plan = shot["plan"] or {}
    if character_id not in plan.get("performers", []):
        raise ApplicationError("Choose a mapped character in this shot's plan.", 422)
    parent = next((v for v in p.versions(session, library, shot) if v["id"] == version), None)
    if parent is None:
        raise ApplicationError("Choose an existing settled shot version.", 422)
    layers = shot.setdefault("layers", [])
    if any(
        layer["parent_version"] == version
        and layer["character_id"] == character_id
        and p.layer_status(session, library, layer)["state"] in {"rendering", "settled"}
        for layer in layers
    ):
        raise ApplicationError("A head pass already exists for this version and character.", 409)
    if recast["render_budget"] - used(session, recast) < len(parent["windows"]) + len(
        parent.get("bridges", [])
    ):
        raise ApplicationError("Increase the render budget", 409)
    assembly_id = next(
        a
        for a in p.assemblies(session, shot)
        if any(v["id"] == version for v in p.window_function("shot_versions")(session, library, a))
    )
    # The root body's performer order belongs to that attempt, not today's mapping.
    ancestor = p.operations.operation(session, assembly_id)
    while ancestor["request"].get("parent"):
        ancestor = p.operations.operation(session, ancestor["request"]["parent"]["assembly_op"])
    original_characters = next(
        (
            h.get("performers", plan["performers"])
            for h in plan.get("_history", [])
            if h["assembly"] == ancestor["id"]
        ),
        plan["performers"],
    )
    if character_id not in original_characters:
        raise ApplicationError("The character was not replaced in this parent version.", 422)
    index = original_characters.index(character_id)
    binding = {"tracks": {}, "targets": {}, "selected_by": "user"}
    analyses = []
    for operation_id in ancestor["request"]["window_operations"]:
        body = p.operations.operation(session, operation_id)["request"]
        if index >= len(body["facts"]["performers"]):
            raise ApplicationError("The character was not replaced in this parent version.", 422)
        original = body["facts"]["performers"][index]
        analysis = body["analysis_operation"]
        analyses.append(analysis)
        binding["tracks"][analysis] = original["track_id"]
        binding["targets"][analysis] = original["target_asset"]
    pending, refusal = False, None
    for asset_id in dict.fromkeys(binding["targets"].values()):
        face = target_report(session, library, asset_id)
        if face is None or face["state"] == "failed":
            try:
                with session.begin_nested():
                    start_target_report(session, library, asset_id, retry_failed=True)
                face = target_report(session, library, asset_id)
            except ApplicationError:
                face = {"state": "failed"}
        if face is None or face["state"] == "checking":
            pending = True
            continue
        if face["state"] == "no_face":
            refusal = refusal or "Head pass needs a target with a human face"
        if face["state"] == "failed":
            refusal = refusal or "The target picture could not be checked."
    if refusal:
        raise TargetReportRefused(refusal, 422)
    if pending:
        raise TargetReportPending()
    film_shot = _find(report["shots"], "index", int(shot_id[1:]), "Shot")
    with session.begin_nested():
        operations = p.window_function("start_shot_head_pass")(
            session,
            library,
            source_asset=recast["source_asset_id"],
            window_analyses=analyses,
            performer=binding,
            parent_version=parent,
            seed=planning.head_seed(recast, film_shot, len(layers)),
            request_key=planning.key(recast_id, shot_id, "window-head", len(layers)),
        )
        if len(operations) != len(parent["windows"]) + len(parent.get("bridges", [])) or sum(
            o["budget_reserved"] for o in operations
        ) != len(operations):
            raise ApplicationError("Head pass exceeded its shot reservation.", 409)
        layers.append(
            {
                "parent_version": version,
                "parent_assembly": assembly_id,
                "character_id": character_id,
                "operations": [o["id"] for o in operations],
                "assembly_op": None,
                "cancelled": False,
            }
        )
        return _finish(session, library, recast)


def reveal_shot(session, library, recast_id, shot_id):
    recast, _ = _load(session, library, recast_id)
    shot = _shot(recast, shot_id)
    return {
        "shot_id": shot_id,
        "versions": [
            {
                "id": v["id"],
                "letter": p.letter(i),
                "windows": v["windows"],
                **({"bridges": v["bridges"], "joins": v["joins"]} if "bridges" in v else {}),
                "layer": v.get("layer"),
                "parent": v.get("parent"),
            }
            for i, v in enumerate(p.versions(session, library, shot))
        ],
    }


def start_conform(session, library, recast_id, *, mode, range, codec, request_key):
    from gaiavfx.application.recast_conform import start_conform as conform

    return conform(
        session, library, recast_id, mode=mode, range=range, codec=codec, request_key=request_key
    )


def feed(session, library):
    from gaiavfx.application.recast_feed import feed as run_feed

    return run_feed(session, library)


def face_path(session, library, recast_id, artifact_id):
    recast = p.store.load(session, recast_id)
    if artifact_id not in p.face_index(session, library, recast):
        raise ApplicationError("Face artifact not found in this recast.", 404)
    return library.artifact_path(session, artifact_id)


def approve_reference(session, library, recast_id, character_id, *, expected_revision):
    recast, _ = _load(session, library, recast_id, expected_revision)
    character = _character(recast, character_id)
    if character.get("reference_state") != "waiting" or character["target_state"] != "confirmed":
        raise ApplicationError("No face sheet is waiting for approval.", 409)
    raw = character["target_asset_id"]
    sheet = character["reference_asset_id"]
    _, _, portraits, assembly = reference_operations(session, recast, character)
    if (
        not portraits
        or not assembly
        or not any(c["asset_id"] == sheet for c in assembly["candidates"])
    ):
        raise ApplicationError("Face sheet provenance is missing.", 409)
    library.approve_derivative(
        session,
        sheet,
        raw,
        {
            "type": "generated_reference",
            "subject": "recast_target",
            "recast_id": recast_id,
            "character_id": character_id,
            "source_asset": raw,
            "portraits_operation": portraits["id"],
            "sheet_operation": assembly["id"],
            "approved_by": "user",
            "approved_at": timestamp(),
            "recast_revision": recast["revision"] + 1,
        },
    )
    character["reference_state"] = "approved"
    start_target_report(session, library, sheet)
    return _finish(session, library, recast)


def reject_reference(session, library, recast_id, character_id, *, expected_revision):
    recast, _ = _load(session, library, recast_id, expected_revision)
    character = _character(recast, character_id)
    if character.get("reference_state") != "waiting" or character["target_state"] != "confirmed":
        raise ApplicationError("No face sheet is waiting for approval.", 409)
    character["reference_state"] = "rejected"
    return _finish(session, library, recast)
