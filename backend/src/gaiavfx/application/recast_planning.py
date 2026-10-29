"""Cached read-only plans from immutable evidence and editorial corrections."""

import json
from copy import deepcopy
from hashlib import sha256
from math import isfinite

from gaiavfx.application import recast_projection as p
from gaiavfx.modules.direction.application import replacement_policy_text
from gaiavfx.modules.direction.windows import (
    bridge_layout,
    latent_chain_allowed,
    shot_crop_scale_loss,
    shot_handoff,
)
from gaiavfx.shared.errors import ApplicationError

# Stored plans are reused while their fingerprint holds; bump this when planning rules change,
# so a fix re-plans shots that were planned under the old rules.
PLANNER_VERSION = 10


def key(*parts):
    # W appends per-window, round and assembly suffixes to these keys.
    return sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:12]


def seed(recast, shot):
    return int(sha256(recast["id"].encode()).hexdigest()[:12], 16) + shot["index"]


def head_seed(recast, film_shot, layer_index):
    return seed(recast, film_shot) + 1000 * (1 + layer_index)


def mapped(recast):
    return {c["id"]: c for c in recast["characters"] if c["target_state"] == "confirmed"}


def gap(code, message):
    return {"code": code, "message": message}


def blank_plan(performers, history=()):
    return {
        "performers": performers[:2],
        "grouping": "joint" if len(performers) >= 2 else "single",
        "windows": [],
        "renders_per_window": 1,
        "gaps": [],
        "canvas_scale": None,
        "_history": list(history),
        "_ready": False,
        "_bindings": [],
        "_handoff": {"kind": "none", "joins": []},
    }


def error(stored, message):
    if not stored["plan"]:
        stored["plan"] = blank_plan([])
    stored["plan"]["gaps"] = [gap("planning_error", str(message))]
    stored["plan"]["_ready"] = True


def archive(shot, *, cancelled=False):
    """Retain reservations and versions, but remove the old attempt from feeding."""
    shot["plan"]["_history"].append(
        {
            "assembly": shot["assembly_op"],
            "performers": shot["plan"]["performers"],
            "operations": shot["window_replacement_ops"],
            "bridge_ops": shot.get("bridge_ops", []),
            "mode": shot["mode"],
            "cancelled": cancelled,
        }
    )
    shot.update(assembly_op=None, window_replacement_ops=[], bridge_ops=[], mode=None)
    shot["plan"].pop("_fingerprint", None)


def request_for(recast, shot, window, operation_id, bindings):
    # Preview and execution share film-track overrides and window-specific confirmations.
    return {
        "source_asset": recast["source_asset_id"],
        "analysis_operation": operation_id,
        "seed": seed(recast, shot),
        "performers": [
            {
                "track_id": binding["tracks"][operation_id],
                "target_asset": binding["target_asset"],
                "selected_by": binding.get("selections", {}).get(
                    operation_id, binding["selected_by"]
                ),
                "identity_margin": binding.get("margins", {}).get(operation_id),
            }
            for binding in bindings
            if operation_id in binding["tracks"]
        ],
    }


def _compute(session, library, recast, report, shot, stored, policy):
    targets = mapped(recast)
    appearances = p.appearances(
        session, library, recast, report, shot, stored, margin_min=policy.identity_margin_min
    )
    performers = [a["character_id"] for a in appearances if a["character_id"] in targets]
    plan = blank_plan(performers, stored["plan"].get("_history", []))
    stored["plan"] = plan
    plan["renders_per_window"] = p.window_renders(recast["run_mode"])
    if len(performers) > 2:
        plan["gaps"] = [
            gap("too_many_performers", "At most two mapped characters can be replaced in one shot.")
        ]
        plan["_ready"] = True
        return
    if not performers:
        plan["_ready"] = True
        return
    owners = p.track_characters(recast, report)
    bindings = {c: {} for c in performers}
    windows = []
    complete = bool(stored["window_analysis_ops"])
    for op_id in stored["window_analysis_ops"]:
        operation = p.operations.operation(session, op_id)
        window = p.window_report(session, library, op_id)
        if window is None:
            if operation["state"] in p.SETTLED:
                plan["gaps"].append(
                    gap("analysis_failed", "Window analysis did not finish successfully.")
                )
            else:
                complete = False
            continue
        windows.append((op_id, window))
        for track in window["tracks"]:
            if track.get("concept") != "person":
                continue
            link = p.appearance_link(recast, op_id, track["id"])
            owner, problem = p.identity(track, owners, link)
            votes = track.get("identity", {}).get("film_tracks", {})
            if problem == "identity_conflict" and any(
                owners.get(t) in targets and n > 0 for t, n in votes.items()
            ):
                plan["gaps"].append(
                    gap(problem, "Faces of different characters in one person track.")
                )
            elif not problem and owner in bindings:
                if op_id in bindings[owner]:
                    plan["gaps"].append(
                        gap(
                            "identity_conflict",
                            "Several person tracks identify the same mapped character.",
                        )
                    )
                bindings[owner][op_id] = track["id"]
            # Faceless, unknown and unmapped people remain protected bystanders.
    plan["_ready"] = complete
    if not complete:
        return
    for tracks in bindings.values():
        if set(tracks) != set(stored["window_analysis_ops"]):
            plan["gaps"].append(
                gap(
                    "unbound_performer",
                    "A mapped character appears in the film but has no bound person track in a window. "
                    "Confirm the character's appearance in the remaining windows if present, "
                    "or exclude this shot to continue.",
                )
            )
    # A performer bound through any track the user moved is user-confirmed in every window
    # (W's planner then needs no identity margin); otherwise identity evidence must hold.
    windows_by_id = dict(windows)
    overrides = {o["track_id"] for o in recast["overrides"]}

    def selected_by(tracks):
        if tracks and all(
            (link := p.appearance_link(recast, op_id, track_id)) and link["state"] == "confirmed"
            for op_id, track_id in tracks.items()
        ):
            return "user"
        for op_id, track_id in tracks.items():
            link = p.appearance_link(recast, op_id, track_id)
            if link and link["state"] == "confirmed":
                continue  # This decision overrides the votes in this window only.
            window_tracks = {t["id"]: t for t in windows_by_id[op_id]["tracks"]}
            voted = window_tracks[track_id].get("identity", {}).get("film_tracks", {})
            if overrides.intersection(voted):
                return "user"
        return "identity"

    plan["_bindings"] = [
        {
            "tracks": bindings[c],
            "target_asset": effective_target(targets[c]),
            "selected_by": selected_by(bindings[c]),
            "character_id": c,
        }
        for c in performers
    ]
    for binding in plan["_bindings"]:
        selections = {
            op_id: "user"
            for op_id, track_id in binding["tracks"].items()
            if (link := p.appearance_link(recast, op_id, track_id)) and link["state"] == "confirmed"
        }
        if selections:
            binding["selections"] = selections
    if len(performers) == 2 and any(
        (link := p.appearance_link(recast, op_id, track_id)) and link["state"] == "confirmed"
        for tracks in bindings.values()
        for op_id, track_id in tracks.items()
    ):
        plan["reasons"] = [
            (
                "Confirmed appearances make this a joint replacement. "
                "Single-performer close-up and FaceSwap rules do not apply."
            )
        ]
    # Identity selection needs a margin over the user's characters, not machine clusters.
    pending = [
        (binding, op_id, track_id, c)
        for c, binding in zip(performers, plan["_bindings"], strict=True)
        for op_id, track_id in binding["tracks"].items()
        if binding.get("selections", {}).get(op_id, binding["selected_by"]) == "identity"
    ]
    if pending:
        votes = {
            (op_id, t["id"]): t.get("identity", {}).get("film_tracks", {})
            for op_id, window in windows
            for t in window["tracks"]
        }
        margins = p.character_margins(
            session,
            library,
            recast,
            report,
            owners,
            [
                (
                    c,
                    votes[(op_id, track_id)],
                    windows_by_id[op_id]["shot_start"],
                    windows_by_id[op_id]["shot_end"],
                )
                for _, op_id, track_id, c in pending
            ],
        )
        for (binding, op_id, _, _), margin in zip(pending, margins, strict=True):
            binding.setdefault("margins", {})[op_id] = margin
    scales = []
    close_ups = []
    from gaiavfx.application.replace_character import replacement_plan

    for i, (op_id, window) in enumerate(windows):
        generated_length = p.window_function("shot_windows")(
            window["shot_start"], window["shot_end"]
        )[0][2]
        plan["windows"].append(
            {
                "index": i,
                "start": window["shot_start"],
                "end": window["shot_end"],
                "length": generated_length,
                "operation_id": None,
                "state": "planned",
            }
        )
        request = request_for(recast, shot, window, op_id, plan["_bindings"])
        if len(request["performers"]) == len(performers) and not plan["gaps"]:
            preview = replacement_plan(
                session, request, library=library, draft=recast["run_mode"] == "draft"
            )
            plan["gaps"].extend(preview["gaps"])
            close_ups.append(preview.get("facts", {}).get("close_up", False))
            if preview.get("crop_preview"):
                scale = preview["crop_preview"].get("scale")
                if type(scale) in (int, float) and isfinite(scale) and scale > 0:
                    scales.append(scale)
    plan["canvas_scale"] = min(scales) if scales else None
    if plan["windows"] and not plan["gaps"]:
        kind = shot_handoff(len(plan["windows"]))
        handoff = {"kind": kind, "joins": []}
        requests = [request_for(recast, shot, w, op, plan["_bindings"]) for op, w in windows]
        if kind == "latent":
            try:
                handoff["crop"] = p.window_function("shared_crop_for_windows")(
                    session, library, requests=requests
                )
            except ValueError:
                handoff.update(kind="crossfade", reason="no_shared_crop")
        elif kind == "bridge":
            if policy.shot_crop is not None:
                reason = None
                if len(scales) != len(plan["windows"]):
                    reason = "scale_unavailable"
                else:
                    try:
                        crop = p.window_function("shared_crop_for_windows")(
                            session, library, requests=requests
                        )
                    except ValueError:
                        reason = "no_shared_crop"
                    else:
                        try:
                            loss = shot_crop_scale_loss(crop.get("scale"), scales)
                        except ValueError:
                            reason = "scale_unavailable"
                if reason is not None:
                    handoff["shot_crop"] = {"used": False, "reason": reason}
                    detail = (
                        "no shared crop fits; scale loss unavailable"
                        if reason == "no_shared_crop"
                        else "scale loss unavailable for the shot or one or more windows"
                    )
                    handoff["reasons"] = [
                        (
                            f"Shot-wide crop not used: {detail}. "
                            "Windows and joins keep their own crops."
                        )
                    ]
                else:
                    allowed = loss <= policy.shot_crop.max_scale_loss
                    handoff["shot_crop"] = {"used": allowed, "scale_loss": loss}
                    if allowed:
                        handoff["crop"] = crop
                        plan["canvas_scale"] = crop["scale"]
                        handoff["reasons"] = [
                            (
                                f"Shot-wide crop used: {loss:.1%} scale loss against the minimum "
                                "per-window scale. Every fresh window and bridge shares it; "
                                "pinned windows skip framing escalation."
                            )
                        ]
                    else:
                        handoff["shot_crop"]["reason"] = "scale_loss"
                        handoff["reasons"] = [
                            (
                                f"Shot-wide crop not used: {loss:.1%} scale loss against the minimum "
                                f"per-window scale exceeds {policy.shot_crop.max_scale_loss:.1%}. "
                                "Windows and joins keep their own crops."
                            )
                        ]
            if policy.latent_chain is not None and latent_chain_allowed(
                len(windows), close_ups, len(performers), policy.latent_chain.max_windows
            ):
                if policy.shot_crop is None:
                    try:
                        handoff["crop"] = p.window_function("shared_crop_for_windows")(
                            session, library, requests=requests
                        )
                    except ValueError:
                        pass
                if handoff.get("crop"):
                    handoff.update(kind="latent", reason="close_up_chain")
                    plan["canvas_scale"] = handoff["crop"]["scale"]
                    handoff["reasons"] = [
                        (
                            "Masked latent close-up chain on one shared crop: "
                            f"{len(windows)} windows, one performer. "
                            f"Cap: {policy.latent_chain.max_windows} windows. {policy.latent_chain.note}"
                        )
                    ]
                    plan.setdefault("reasons", []).extend(handoff["reasons"])
            for layout in (
                bridge_layout(plan["windows"], shot["end"]) if handoff["kind"] == "bridge" else []
            ):
                join = layout["join"]
                row = {"join": join, "kind": "bridge", **layout}
                try:
                    row["crop"] = handoff.get("crop") or p.window_function(
                        "shared_crop_for_windows"
                    )(session, library, requests=requests[join - 1 : join + 1])
                except ValueError:
                    row.update(kind="crossfade", reason="no_shared_crop")
                handoff["joins"].append(row)
        plan["_handoff"] = handoff
    plan["gaps"] = list({g["code"]: g for g in plan["gaps"]}.values())


def refresh(
    session, library, recast, report, shot, stored, *, policy_text=None, report_identity=None
):
    if not stored["plan"] or stored["window_replacement_ops"] or stored["included"] is False:
        return
    if stored["plan"].get("_analysis_error"):
        error(stored, stored["plan"]["_analysis_error"])
        return
    policy_text = replacement_policy_text() if policy_text is None else policy_text
    report_identity = (
        p.report_token(session, recast, report) if report_identity is None else report_identity
    )
    fingerprint = p.digest(
        [
            PLANNER_VERSION,
            report_identity,
            p.digest(recast["overrides"]),
            p.digest(
                sorted(
                    (
                        link["analysis_operation_id"],
                        link["track_id"],
                        link["character_id"],
                        link["state"],
                    )
                    for link in recast.get("appearance_links", [])
                    if link["shot_id"] == stored["shot_id"]
                    and link["state"] in {"confirmed", "rejected"}
                )
            ),
            p.digest(policy_text),
            [
                (
                    c["id"],
                    c["cluster_id"],
                    effective_target(c),
                    c["target_state"],
                )
                for c in recast["characters"]
            ],
            recast["run_mode"],
            [
                p.operation_token(p.operations.operation(session, o))
                for o in stored["window_analysis_ops"]
            ],
        ]
    )
    plans = p.cache(library, "plans")
    cache_key = (recast["id"], stored["shot_id"], fingerprint)
    history = stored["plan"].get("_history", [])
    if stored["plan"].get("_fingerprint") == fingerprint:
        return
    if cache_key not in plans:
        try:
            _compute(session, library, recast, report, shot, stored, p.parsed_policy(policy_text))
        except ApplicationError as exc:
            error(stored, exc)
        stored["plan"]["_fingerprint"] = fingerprint
        p.remember(plans, cache_key, stored["plan"])
    stored["plan"] = deepcopy(plans[cache_key])
    stored["plan"]["_history"] = history


def refresh_all(session, library, recast, report):
    if report:
        policy_text = replacement_policy_text()
        report_identity = p.report_token(session, recast, report)
        by_id = {s["shot_id"]: s for s in recast["shots"]}
        for shot in report["shots"]:
            stored = by_id[p.shot_id(shot)]
            try:
                refresh(
                    session,
                    library,
                    recast,
                    report,
                    shot,
                    stored,
                    policy_text=policy_text,
                    report_identity=report_identity,
                )
            except ApplicationError as exc:
                error(stored, exc)


def effective_target(character):
    return (
        character["reference_asset_id"]
        if character.get("reference_state") == "approved"
        else character["target_asset_id"]
    )
