"""Recast analysis projection; no candidate provenance escapes the blind view."""

import json
from collections import Counter, OrderedDict
from copy import deepcopy
from fractions import Fraction
from functools import lru_cache
from hashlib import sha256
from importlib import import_module
from uuid import NAMESPACE_URL, uuid5
from weakref import WeakKeyDictionary

import numpy as np

from gaiavfx.infrastructure.database import new_id
from gaiavfx.modules.direction.application import replacement_policy_text
from gaiavfx.modules.direction.replacement import parse_replacement_policy
from gaiavfx.modules.editing.recast.service import RecastService
from gaiavfx.modules.generation.operations.lifecycle import OPERATION_TERMINAL
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError

store = RecastService()
operations = OperationService()
SETTLED = OPERATION_TERMINAL


def window_renders(mode):
    """Upper bound reserved for each new window, shared by estimates and execution."""
    current = policy()
    return (
        (sum(current.draft.renders_per_round) if current.draft else 1)
        if mode == "draft"
        else current.renders
    )


def render_cost(operation):
    """Settled operations return unused reservations, including final-mode windows."""
    if operation["state"] not in SETTLED:
        return operation["budget_reserved"]
    return sum(
        j["executor"] == "comfy" and j["kind"] in {"image", "video"} for j in operation["jobs"]
    )


W_MODULES = (
    "gaiavfx.application.shot_replacement",
    "gaiavfx.application.shot_assembly",
    "gaiavfx.application.replace_character",
    "gaiavfx.modules.direction.windows",
    "gaiavfx.modules.direction.replacement",
)
_CACHES = WeakKeyDictionary()


def window_function(name):
    for module_name in W_MODULES:
        try:
            module = import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name == module_name:
                continue
            raise ApplicationError(
                f"Window integration {module_name} is unavailable: {exc}", 409
            ) from exc
        function = getattr(module, name, None)
        if callable(function):
            return function
    raise ApplicationError(f"Window integration function {name} is not available.", 409)


def cache(library, kind):
    return _CACHES.setdefault(library, {}).setdefault(kind, OrderedDict())


def remember(cache, key, value, limit=256):
    cache[key] = deepcopy(value)
    cache.move_to_end(key)
    while len(cache) > limit:
        cache.popitem(last=False)


def clear_caches(library):
    _CACHES.pop(library, None)


def digest(value):
    return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def operation_token(operation):
    return digest(
        [
            operation["id"],
            operation["state"],
            operation["updated_at"],
            [(j["id"], j["state"], j["outputs"]) for j in operation["jobs"]],
        ]
    )


def report_token(session, recast, report=None):
    op = operations.operation(session, recast["analysis_operation_id"])
    artifacts = [o["artifact_id"] for j in op["jobs"] for o in j["outputs"] if o.get("artifact_id")]
    return digest([artifacts, operation_token(op), report if not artifacts else None])


@lru_cache(maxsize=8)
def parsed_policy(text):
    return parse_replacement_policy(text)


def policy():
    return parsed_policy(replacement_policy_text())


def report_for(session, library, recast):
    if not recast["analysis_operation_id"]:
        return None
    from gaiavfx.application.film_analysis import film_analysis_report

    return film_analysis_report(session, library, recast["analysis_operation_id"])


def character_margins(session, library, recast, report, owners, requests):
    """W's identity margin recomputed over the user's characters instead of machine clusters.

    A merge joins clusters of one person (profiles, looking down), so a cluster margin can
    compare a face with the same person's other cluster and read as ambiguous. Each request
    is (character, film_tracks votes, start, end); its rows are the voted film tracks' faces
    inside [start, end), and centroids are normalised face means per owning character. Fewer
    than three faces give None, as in W.
    """
    from gaiavfx.application.film_analysis import film_faces

    loaded = cache(library, "film_faces")
    key = recast["analysis_operation_id"]
    if key not in loaded:
        loaded[key] = film_faces(session, library, key)
        while len(loaded) > 1:
            loaded.popitem(last=False)
    faces = loaded[key]
    embeddings = np.asarray(faces["embedding"], dtype=np.float32)
    frames = np.asarray(faces["frame"])

    def unit(vector):
        norm = np.linalg.norm(vector)
        return vector / norm if norm else vector

    rows_by_track = {t["id"]: t["faces"] for t in report["tracks"]}
    members = {}
    for track, character in owners.items():
        if character and rows_by_track.get(track):
            members.setdefault(character, []).extend(rows_by_track[track])
    centroids = {c: unit(embeddings[rows].mean(axis=0)) for c, rows in members.items()}
    result = []
    for character, votes, start, end in requests:
        rows = [
            row
            for track, count in votes.items()
            if count > 0
            for row in rows_by_track.get(track, ())
            if start <= frames[row] < end
        ]
        if len(rows) < 3 or character not in centroids:
            result.append(None)
            continue
        mean = unit(embeddings[rows].mean(axis=0))
        other = max((float(mean @ v) for c, v in centroids.items() if c != character), default=0.0)
        result.append(float(np.clip(float(mean @ centroids[character]) - other, -2, 2)))
    return result


def new_character(recast, cluster_id=None):
    ordinal = max((c["ordinal"] for c in recast["characters"]), default=-1) + 1
    character = {
        "id": str(uuid5(NAMESPACE_URL, f"{recast['id']}:{cluster_id}")) if cluster_id else new_id(),
        "recast_id": recast["id"],
        "cluster_id": cluster_id,
        "label": f"Character {ordinal + 1}",
        "hidden": False,
        "ordinal": ordinal,
        "target_asset_id": None,
        "target_label": None,
        "target_state": None,
        "target_proposed_by": None,
        "reference_asset_id": None,
        "reference_state": None,
        # Voice decisions (ADR 0016 §1): P5 sets them; P4 stores the columns empty.
        "voice_asset_ids": None,
        "voice_state": None,
        "voice_proposed_by": None,
        "voice_model_state": None,
        "voice_model_artifact_id": None,
    }
    recast["characters"].append(character)
    return character


def shot_id(shot):
    return f"s{shot['index']:04d}"


def sync(recast, report):
    if not report:
        return
    clusters = {c["cluster_id"] for c in recast["characters"]}
    overridden = {o["track_id"] for o in recast["overrides"]}
    for cluster in report["clusters"]:
        # Deleted merge sources cannot spring back on the next read.
        if cluster["id"] not in clusters and set(cluster["track_ids"]) - overridden:
            new_character(recast, cluster["id"])
    shots = {s["shot_id"] for s in recast["shots"]}
    for shot in report["shots"]:
        if shot_id(shot) not in shots:
            recast["shots"].append(
                {
                    "recast_id": recast["id"],
                    "shot_id": shot_id(shot),
                    "included": None,
                    "priority": None,
                    "plan": None,
                    "window_analysis_ops": [],
                    "window_replacement_ops": [],
                    "layers": [],
                    "bridge_ops": [],
                    "assembly_op": None,
                    "mode": None,
                }
            )


def track_characters(recast, report):
    characters = {c["cluster_id"]: c["id"] for c in recast["characters"] if c["cluster_id"]}
    result = {t: characters.get(c["id"]) for c in report["clusters"] for t in c["track_ids"]}
    result.update(
        {
            o["track_id"]: None if o["character_id"] == "unknown" else o["character_id"]
            for o in recast["overrides"]
        }
    )
    return result


def appearance_link(recast, analysis_operation_id, track_id):
    return next(
        (
            link
            for link in recast.get("appearance_links", [])
            if link["analysis_operation_id"] == analysis_operation_id
            and link["track_id"] == track_id
        ),
        None,
    )


def identity(track, owners, link=None):
    if link and link["state"] == "confirmed":
        return link["character_id"], None
    votes = Counter()
    for track_id, count in track.get("identity", {}).get("film_tracks", {}).items():
        if count > 0:
            owner = owners.get(track_id)
            if link and link["state"] == "rejected" and owner == link["character_id"]:
                owner = None
            votes[owner] += count
    # Unknown faces (sparse, small or distorted) abstain rather than conflict, unless they
    # outnumber the known winner more than two to one (a stray face inside a stranger's mask).
    unknown = votes.pop(None, 0)
    if not votes:
        return None, "confirm_appearance"
    character, count = votes.most_common(1)[0]
    if count * 3 < sum(votes.values()) * 2:
        return None, "identity_conflict"
    if unknown > 2 * count:
        return None, "confirm_appearance"
    return character, None


def face(artifact, frame, recast_id, track_id=None):
    return (
        {
            "artifact_id": artifact,
            "url": f"/api/recasts/{recast_id}/faces/{artifact}",
            "frame": frame,
            "track_id": track_id,
        }
        if artifact
        else None
    )


def presence(tracks, report):
    # Film detections sample at stride; never bridge a shot boundary or an unseen interval.
    stride = report["params"]["stride"]
    ends = {s["index"]: s["end"] for s in report["shots"]}
    ranges = sorted((f, min(f + stride, ends[t["shot"]])) for t in tracks for f in t["frames"])
    merged = []
    for start, end in ranges:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def character_views(recast, report):
    if not report:
        return [], []
    owners = track_characters(recast, report)
    similar = similar_characters(recast, report, owners)
    result, unknown = [], []
    for character in recast["characters"]:
        tracks = [t for t in report["tracks"] if owners.get(t["id"]) == character["id"]]
        ranges = presence(tracks, report)
        best = max(tracks, key=lambda t: t["quality"], default=None)
        target = (
            {
                "asset_id": character["target_asset_id"],
                "label": character["target_label"],
                "state": character["target_state"],
                "proposed_by": character["target_proposed_by"],
                "reference": (
                    {
                        "asset_id": character.get("reference_asset_id"),
                        "state": character["reference_state"],
                    }
                    if character.get("reference_state")
                    else None
                ),
            }
            if character["target_state"]
            else None
        )
        result.append(
            {
                "id": character["id"],
                "label": character["label"],
                "hidden": character["hidden"],
                "face": face(best["crop_artifact"], best["frames"][0], recast["id"], best["id"])
                if best
                else None,
                "screen_time_frames": sum(b - a for a, b in ranges),
                "presence": ranges,
                "shot_count": len({t["shot"] for t in tracks}),
                "track_count": len(tracks),
                "tracks": [
                    {
                        "id": track["id"],
                        "shot_id": shot_id({"index": track["shot"]}),
                        "frames_with_face": track["face_count"],
                        "face": face(
                            track["crop_artifact"], track["frames"][0], recast["id"], track["id"]
                        ),
                    }
                    for track in tracks
                ],
                "target": target,
                "similar": similar.get(character["id"], []),
            }
        )
    for track in report["tracks"]:
        if not owners.get(track["id"]) and track["crop_artifact"]:
            unknown.append(
                face(track["crop_artifact"], track["frames"][0], recast["id"], track["id"])
            )
    return result, unknown


def similar_characters(recast, report, owners):
    """Project cluster evidence onto current track owners, never auto-merge."""
    visible = {c["id"] for c in recast["characters"] if not c["hidden"]}
    # Pairs the user set aside as "not the same person" are never suggested again (ADR 0017, CP3).
    dismissed = {(n["character_a"], n["character_b"]) for n in recast.get("not_same", [])}
    clusters = {
        c["id"]: {owners.get(t) for t in c["track_ids"]} & visible for c in report["clusters"]
    }
    pairs = {}
    for suggestion in report.get("suggestions", []):
        for a in clusters.get(suggestion["a"], set()):
            for b in clusters.get(suggestion["b"], set()):
                pair = tuple(sorted((a, b)))
                if a != b and pair not in dismissed:
                    pairs[pair] = max(pairs.get(pair, -1), suggestion["max_similarity"])
    result = {}
    for (a, b), similarity in pairs.items():
        for source, target in ((a, b), (b, a)):
            result.setdefault(source, []).append({"character_id": target, "similarity": similarity})
    for values in result.values():
        values.sort(key=lambda v: (-v["similarity"], v["character_id"]))
    return result


def window_report(session, library, operation_id):
    op = operations.operation(session, operation_id)
    if op["state"] != "succeeded":
        return None
    token = operation_token(op)
    reports = cache(library, "windows")
    if token not in reports:
        outputs = [
            o
            for j in op["jobs"]
            if j["step"] == "people_report"
            for o in j["outputs"]
            if o.get("output_key") == "people" and o.get("artifact_id")
        ]
        if len(outputs) != 1:
            raise ApplicationError("Window analysis has no complete people report.", 409)
        try:
            value = json.loads(library.read_blob(session, outputs[0]["artifact_id"]))
        except (ValueError, TypeError) as exc:
            raise ApplicationError(f"Invalid window report: {exc}", 409) from exc
        remember(reports, token, value)
    return deepcopy(reports[token])


def face_index(session, library, recast):
    token = report_token(session, recast)
    indexes = cache(library, "faces")
    if token not in indexes:
        report = report_for(session, library, recast)
        artifacts = set()
        if report:
            artifacts.update(t["crop_artifact"] for t in report["tracks"])
            artifacts.update(s["poster_artifact"] for s in report["shots"])
            artifacts.update(c["face_artifact"] for c in report["clusters"])
        remember(indexes, token, artifacts, limit=16)
    return indexes[token]


def appearances(session, library, recast, report, shot, stored, *, margin_min=None):
    owners = track_characters(recast, report)
    targets = {c["id"] for c in recast["characters"] if c["target_state"] == "confirmed"}
    margin_min = policy().identity_margin_min if margin_min is None else margin_min
    overridden = {o["track_id"] for o in recast["overrides"]}
    groups = {}
    for track in report["tracks"]:
        if track["shot"] != shot["index"]:
            continue
        owner = owners.get(track["id"])
        group = groups.setdefault(
            owner, {"character_id": owner, "track_ids": [], "frames": set(), "confirmed": False}
        )
        group["track_ids"].append(track["id"])
        group["frames"].update(track["frames"])
        group["confirmed"] |= owner in targets and track["id"] in overridden
    for op_id in stored["window_analysis_ops"]:
        try:
            window = window_report(session, library, op_id)
        except ApplicationError:
            continue  # The planning projection carries the typed error for this shot.
        if not window:
            continue
        for track in window["tracks"]:
            if track.get("concept") != "person":
                continue
            link = appearance_link(recast, op_id, track["id"])
            linked = link is not None and link["state"] == "confirmed"
            owner, gap = identity(track, owners, link)
            clear = (track.get("identity", {}).get("margin") or 0) >= margin_min
            if not linked and owner not in groups and (gap or not clear):
                owner = None
            group = groups.setdefault(
                owner, {"character_id": owner, "track_ids": [], "frames": set(), "confirmed": False}
            )
            group["confirmed"] |= linked or (
                owner in targets
                and not gap
                and (
                    clear
                    or any(
                        t in overridden for t in track.get("identity", {}).get("film_tracks", {})
                    )
                )
            )
    return [
        {
            "character_id": g["character_id"],
            "track_ids": g["track_ids"],
            "frames_with_face": len(g["frames"]),
            "coverage": len(g["frames"]) / (shot["end"] - shot["start"]),
            "confirmed": g["confirmed"],
        }
        for g in groups.values()
    ]


def assemblies(session, stored):
    old = (stored["plan"] or {}).get("_history", [])
    values = (
        [h["assembly"] for h in old if h.get("assembly")]
        + ([stored["assembly_op"]] if stored["assembly_op"] else [])
        + [layer["assembly_op"] for layer in stored.get("layers", []) if layer["assembly_op"]]
    )

    return sorted(
        dict.fromkeys(values), key=lambda op: operations.operation(session, op)["created_at"]
    )


def all_window_ops(stored):
    return list(
        dict.fromkeys(
            [o for h in (stored["plan"] or {}).get("_history", []) for o in h["operations"]]
            + [
                o
                for h in (stored["plan"] or {}).get("_history", [])
                for o in h.get("bridge_ops", [])
            ]
            + stored["window_replacement_ops"]
            + stored.get("bridge_ops", [])
            + [o for layer in stored.get("layers", []) for o in layer["operations"]]
        )
    )


def layer_status(session, library, layer):
    """Progress only: no parent, assembly or candidate association in blind state."""
    windows = [operations.operation(session, o) for o in layer["operations"]]
    assembly = operations.operation(session, layer["assembly_op"]) if layer["assembly_op"] else None
    settled = sum(o["state"] in SETTLED for o in windows)
    if layer["cancelled"] or any(
        o["cancel_requested"] or o["state"] == "cancelled" for o in windows
    ):
        state = "cancelled"
    elif settled < len(windows):
        state = "rendering"
    elif assembly is None:
        state = "failed" if any(o["state"] == "failed" for o in windows) else "rendering"
    elif assembly["state"] not in SETTLED:
        state = "rendering"
    else:
        state = (
            "settled"
            if window_function("shot_versions")(session, library, assembly["id"])
            else "failed"
        )
    return {
        "character_id": layer["character_id"],
        "state": state,
        "windows_settled": settled,
        "windows_total": len(windows),
        "renders_reserved": sum(o["budget_reserved"] for o in windows),
    }


def versions(session, library, stored):
    result, seen = [], set()
    for assembly in assemblies(session, stored):
        for version in sorted(
            window_function("shot_versions")(session, library, assembly), key=lambda v: v["take"]
        ):
            if version["id"] not in seen:
                seen.add(version["id"])
                result.append(version)
    return result


def letter(index):
    value = ""
    while index >= 0:
        value = chr(65 + index % 26) + value
        index = index // 26 - 1
    return value


def public_versions(values):
    fields = ("check", "verdict", "calibrated", "measured", "reason")
    return [
        {
            "id": v["id"],
            "letter": letter(i),
            "asset_id": v["preview_asset"],
            "checks": [{k: c[k] for k in fields} for c in v["checks"]],
        }
        for i, v in enumerate(values)
    ]


def film_view(session, library, recast, report):
    asset = library.get(session, recast["source_asset_id"])
    info = asset["info"]
    fps = Fraction(*(report["fps"])) if report else Fraction(info.get("frame_rate", "0/1"))
    status = {
        Fraction(24): "tested",
        Fraction(24000, 1001): "developing",
        Fraction(25): "developing",
        Fraction(30000, 1001): "experimental",
        Fraction(30): "experimental",
    }.get(fps, "unsupported")
    # A frame count of zero explicitly means not counted yet, never a duration-based guess.
    return {
        "asset_id": asset["id"],
        "name": asset["name"],
        "fps_num": fps.numerator,
        "fps_den": fps.denominator,
        "frame_count": report["frame_count"] if report else info.get("frames", 0),
        "width": report["width"] if report else info["width"],
        "height": report["height"] if report else info["height"],
        "has_audio": info["has_audio"],
        "fps_status": status,
        "letterbox": report["letterbox"] if report else None,
    }


def analysis_view(session, recast):
    op_id = recast["analysis_operation_id"]
    if not op_id:
        return {
            "operation_id": None,
            "state": "not_started",
            "stage": None,
            "done": 0,
            "total": 0,
            "error": None,
        }
    op = operations.operation(session, op_id)
    jobs = op["jobs"]
    done = sum(j["state"] == "succeeded" for j in jobs)
    first = next((j for j in jobs if j["state"] != "succeeded"), None)
    stage = (
        {"film_shots": "shots", "film_cast": "cast"}.get(first["step"], "faces")
        if first
        else "done"
    )
    state = (
        "succeeded"
        if op["state"] == "succeeded"
        else "failed"
        if op["state"] in OPERATION_TERMINAL
        else "running"
    )
    return {
        "operation_id": op_id,
        "state": state,
        "stage": stage,
        "done": done,
        "total": len(jobs),
        "error": op["error"] or next((j["error"] for j in jobs if j["error"]), None),
    }


def in_range(shot, frame_range):
    return not frame_range or (
        shot["start"] < frame_range["end"] and shot["end"] > frame_range["start"]
    )


def run_range(recast):
    return (
        {"start": recast["run_range_start"], "end": recast["run_range_end"]}
        if recast["run_range_start"] is not None
        else None
    )


def render_seconds(operation):
    """Measured job execution only. Missing timing evidence keeps the seed estimate."""
    from datetime import datetime

    if operation["state"] not in {"succeeded", "partial"}:
        return None
    total, renders = 0.0, 0
    for job in operation["jobs"]:
        history = job.get("state_history", [])
        start = next(
            (h.get("updated_at") or h.get("at") for h in history if h["state"] == "running"), None
        )
        finish = next(
            (
                h.get("updated_at") or h.get("at")
                for h in reversed(history)
                if h["state"] in SETTLED
            ),
            None,
        )
        start = start or job.get("started_at")
        finish = (
            finish
            or job.get("finished_at")
            or (job["updated_at"] if job["state"] in SETTLED else None)
        )
        render = job["executor"] == "comfy" and job["kind"] in {"video", "image"}
        if render and (job.get("submitted") or start or job["state"] == "succeeded"):
            if not start or not finish:
                return None
            renders += 1
        if start and finish:
            elapsed = (
                datetime.fromisoformat(finish) - datetime.fromisoformat(start)
            ).total_seconds()
            if elapsed < 0:
                return None
            total += elapsed
    return total / renders if renders else None
