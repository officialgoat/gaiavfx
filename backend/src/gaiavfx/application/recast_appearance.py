"""On-request appearance proposals. Mask work happens before the writer transaction."""

from collections import OrderedDict

import numpy as np

from gaiavfx.application import recast as app
from gaiavfx.application import recast_projection as p
from gaiavfx.application.replacement_steps import _mask_data
from gaiavfx.modules.direction.replacement import Box, Track, minor_bystander_reason
from gaiavfx.shared.errors import ApplicationError

# Provisional clothing-similarity gate for suggestions, not a calibrated identity bound.
APPEARANCE_SIMILARITY_MIN = 0.8


def _histogram(track):
    value = track.get("appearance")
    if value is None:
        return None
    try:
        hist = np.asarray(value, dtype=np.float64)
    except (ValueError, TypeError):
        return None
    if hist.shape != (12, 6) or not np.isfinite(hist).all() or (hist < 0).any() or hist.sum() <= 0:
        return None
    return hist / hist.sum()


def rank_proposals(recast, report, windows, shot_id, load_mask, policy):
    """Rank candidates; a score is evidence to show a human, never authority to bind."""
    owners = p.track_characters(recast, report)
    characters = {c["id"] for c in recast["characters"] if not c["hidden"]}
    order = [p.shot_id(s) for s in report["shots"]]
    index = order.index(shot_id)
    nearby = set(order[max(0, index - 4) : index + 5])
    references = {c: [] for c in characters}

    def owner(op_id, track):
        identity, problem = p.identity(track, owners, p.appearance_link(recast, op_id, track["id"]))
        return identity if not problem else None

    for sid in nearby:
        for op_id, window in windows.get(sid, []):
            for track in window["tracks"]:
                if track.get("concept") != "person":
                    continue
                character = owner(op_id, track)
                hist = _histogram(track)
                if character in references and hist is not None:
                    references[character].append(hist)
    means = {c: np.mean(hists, axis=0) for c, hists in references.items() if hists}
    # Bound resident mask memory even for long shots and crowded frames.
    masks = OrderedDict()

    def mask(track, window):
        key = track["mask_artifact"]
        if key not in masks:
            value = load_mask(key)
            expected = (window["shot_end"] - window["shot_start"], *reversed(window["native"]))
            if value.shape != expected:
                raise ApplicationError("Appearance masks do not cover the analysed window.", 409)
            masks[key] = value
            while len(masks) > 2:
                masks.popitem(last=False)
        masks.move_to_end(key)
        return masks[key]

    proposals = []
    reports = windows.get(shot_id, [])
    for k, (op_id, window) in enumerate(reports):
        window_owners = {
            owner(op_id, track) for track in window["tracks"] if track.get("concept") == "person"
        }
        for track in window["tracks"]:
            if track.get("concept") != "person" or owner(op_id, track) is not None:
                continue
            link = p.appearance_link(recast, op_id, track["id"])
            if link and (
                link["state"] in {"confirmed", "rejected"}
                or link["proposed_by"] in {"agent", "user"}
            ):
                continue
            if minor_bystander_reason(
                Track(track["id"], Box(**track["box"]), tuple(track["presence"]), None, "identity"),
                window["native"],
                policy,
            ):
                continue
            histogram = _histogram(track)
            scores = []
            for character in sorted(characters - window_owners):
                continuity = None
                for neighbour in (k - 1, k + 1):
                    if not 0 <= neighbour < len(reports):
                        continue
                    other_op, other = reports[neighbour]
                    lo = max(window["shot_start"], other["shot_start"])
                    hi = min(window["shot_end"], other["shot_end"])
                    if hi - lo != 22:
                        continue
                    for reference in other["tracks"]:
                        if (
                            reference.get("concept") != "person"
                            or owner(other_op, reference) != character
                        ):
                            continue
                        a = mask(track, window)[
                            lo - window["shot_start"] : hi - window["shot_start"]
                        ]
                        b = mask(reference, other)[
                            lo - other["shot_start"] : hi - other["shot_start"]
                        ]
                        if a.shape != b.shape:
                            continue
                        union = np.logical_or(a, b).sum()
                        if union:
                            iou = float(np.logical_and(a, b).sum() / union)
                            continuity = max(continuity or 0.0, iou)
                appearance = (
                    float(np.clip(np.sqrt(histogram * means[character]).sum(), 0, 1))
                    if histogram is not None and character in means
                    else None
                )
                if continuity is not None or appearance is not None:
                    scores.append(
                        {
                            "character_id": character,
                            "continuity": continuity,
                            "appearance": appearance,
                        }
                    )
            # Overlap continuity is stronger evidence than clothing; neither confirms identity.
            scores.sort(
                key=lambda row: (
                    -(row["continuity"] or 0),
                    -(row["appearance"] or 0),
                    row["character_id"],
                )
            )
            if scores and (
                (scores[0]["continuity"] or 0) > 0
                or (scores[0]["appearance"] or 0) > APPEARANCE_SIMILARITY_MIN
            ):
                proposals.append(
                    {
                        "shot_id": shot_id,
                        "analysis_operation_id": op_id,
                        "track_id": track["id"],
                        "character_id": scores[0]["character_id"],
                        "evidence": {
                            "method": "continuity_and_appearance",
                            "candidates": scores,
                            "appearance_threshold": {
                                "value": APPEARANCE_SIMILARITY_MIN,
                                "calibrated": False,
                            },
                        },
                    }
                )
    return proposals


def suggest_appearances(database, library, recast_id, shot_id, *, expected_revision):
    # Read immutable reports without acquiring Editing's writer lock.
    with database.transaction() as session:
        recast, report = app._load(session, library, recast_id)
        if recast["revision"] != expected_revision:
            raise ApplicationError("The recast changed; reload before suggesting appearances.", 409)
        app._shot(recast, shot_id)
        if not report:
            raise ApplicationError("Film analysis must finish before suggesting appearances.", 409)
        film_token = p.report_token(session, recast, report)
        order = [p.shot_id(shot) for shot in report["shots"]]
        index = order.index(shot_id)
        nearby = set(order[max(0, index - 4) : index + 5])
        windows = {
            shot["shot_id"]: [
                (op, window)
                for op in shot["window_analysis_ops"]
                if (window := p.window_report(session, library, op)) is not None
            ]
            for shot in recast["shots"]
            if shot["shot_id"] in nearby
        }
        tokens = {
            op: p.operation_token(p.operations.operation(session, op))
            for rows in windows.values()
            for op, _ in rows
        }
        analysis_ids = {
            s["shot_id"]: s["window_analysis_ops"]
            for s in recast["shots"]
            if s["shot_id"] in nearby
        }
    proposals = rank_proposals(
        recast, report, windows, shot_id, lambda artifact: _mask_data(library, artifact), p.policy()
    )
    with database.transaction() as session:
        current, current_report = app._load(session, library, recast_id, expected_revision)
        if (
            p.report_token(session, current, current_report) != film_token
            or analysis_ids
            != {
                s["shot_id"]: s["window_analysis_ops"]
                for s in current["shots"]
                if s["shot_id"] in nearby
            }
            or any(
                p.operation_token(p.operations.operation(session, op)) != token
                for op, token in tokens.items()
            )
        ):
            raise ApplicationError("Window analyses changed; request suggestions again.", 409)
        for proposal in proposals:
            app._propose_appearance(current, **proposal, proposed_by="analysis")
        return app._finish(session, library, current)
