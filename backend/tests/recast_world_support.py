"""The Recast test world: a real Editing and Generation store with fixture adapters at Recast's seams.

Recast reaches the work it never does itself through four seams: film analysis and dialogue analysis (modules it
imports at call time), shot work (`recast_take.shot_work`) and voice builds (`recast_voice`'s call-time module). The
world answers each with a fixture adapter that records what it was asked and creates real Generation operations whose
jobs the tests settle; it never runs inference. One world can hold several films, each with its own analysis report.

Pytest uses it through the `world`, `voice` and `builds` fixtures; the browser test server installs the same adapters
in its process (tests/browser_recast.py), parking each fixture job at `claimed` so the live runner leaves it to the
spec. The helpers below the fixtures are the commands tests share.
"""

import io
import json
import math
import shutil
import sys
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from gaiavfx.api.recast import RecastDTO
from gaiavfx.application import (
    recast,
    recast_derivatives,
    recast_lines,
    recast_shell,
    recast_take,
    replace_character,
)
from gaiavfx.application import recast_projection as p
from gaiavfx.infrastructure.database import Database
from gaiavfx.modules.generation.operations.lifecycle import OPERATION_TERMINAL, JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.application import LibraryService
from gaiavfx.modules.library.assets import AssetRecord
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.modules.recast.windows import shot_windows
from gaiavfx.shared.errors import ApplicationError

MIGRATIONS = Path(__file__).parents[1] / "migrations"
FILM_ANALYSIS = "gaiavfx.application.film_analysis"
DIALOGUE_ANALYSIS = "gaiavfx.application.dialogue_analysis"
VOICE_BUILD = "gaiavfx.application.voice_build"


def film_report(source_asset, shots=4, length=73):
    """A film of `shots` shots of `length` frames at 24 fps, one face track per shot, all in cluster c01."""
    return {
        "schema": 1,
        "source_asset": source_asset,
        "fps": [24, 1],
        "frame_count": shots * length,
        "width": 1920,
        "height": 1080,
        "letterbox": None,
        "shots": [
            {"index": i + 1, "start": i * length, "end": (i + 1) * length, "poster_artifact": None}
            for i in range(shots)
        ],
        "tracks": [
            {
                "id": f"t{i + 1}",
                "shot": i + 1,
                "frames": [i * length + f for f in range(0, length, 5)],
                "faces": list(range(15 * i, 15 * i + 15)),
                "boxes": [[0, 0, 20, 20]] * 15,
                "face_count": 15,
                "quality": 0.9,
                "crop_artifact": None,
            }
            for i in range(shots)
        ],
        "clusters": [
            {
                "id": "c01",
                "track_ids": [f"t{i + 1}" for i in range(shots)],
                "screen_time_frames": shots * length,
                "shot_count": shots,
                "presence": [[0, shots * length]],
                "face_artifact": None,
            }
        ],
        "unknown_track_ids": [],
        "params": {"stride": 5},
    }


@dataclass
class Film:
    """A film's analysis as the fixture film analysis reports it; tests may change it after the recast exists."""

    report: dict
    face_embeddings: dict = field(
        default_factory=dict
    )  # track id -> embedding; other faces point one way


class RecastWorld:
    """A real store with Recast's fixture adapters. `film` names the film `create` and `report` use."""

    def __init__(self, db, library, *, park=False):
        self.db, self.library, self.park = db, library, park
        self.ops = OperationService()
        self.blobs = {}
        self.films = {}
        self.film = "film"
        self.calls = {"analysis": [], "replacement": [], "assembly": [], "plan": []}
        self.window_reports = {}
        self.versions = {}  # assembly operation id -> the versions it made
        self.previews = ["preview"]  # the preview assets versions show, in turn
        self.film_api = _module(FILM_ANALYSIS, FilmAnalysis(self))
        self.window_api = _module("fake_shot_work", ShotWork(self), names=recast_take.MODULES)
        self.real_planner = replace_character.replacement_plan
        self._read_blob = library.read_blob

    @property
    def report(self):
        return self.films[self.film].report

    @property
    def face_embeddings(self):
        return self.films[self.film].face_embeddings

    def install(self, patcher):
        """Put the adapters at Recast's seams; `patcher` is a pytest MonkeyPatch or anything with its setattr and
        setitem."""
        patcher.setattr(self.library, "read_blob", self.read_blob)
        patcher.setitem(sys.modules, FILM_ANALYSIS, self.film_api)
        patcher.setattr(recast_take, "shot_work", self.window_api)
        patcher.setattr(replace_character, "replacement_plan", self.replacement_plan)

    def read_blob(self, session, artifact):
        if artifact not in self.blobs:
            return self._read_blob(session, artifact)
        blob = self.blobs[artifact]
        return blob if isinstance(blob, bytes) else json.dumps(blob)

    def film_of(self, asset_id):
        """The film an asset is: one the world added, or else the world's film (any other video reads as it)."""
        return self.films.get(asset_id) or self.films[self.film]

    def add_film(self, asset_id, report=None):
        self.films[asset_id] = Film(report or film_report(asset_id))
        return self.films[asset_id]

    def add_fixture_assets(self):
        """The pytest world's Library: a film, two pictures and a recording, by name, with no files behind them."""
        with self.db.transaction() as session:
            for asset_id, kind in (
                ("film", "video"),
                ("picture", "image"),
                ("picture2", "image"),
                ("audio", "audio"),
            ):
                session.add(
                    AssetRecord(
                        id=asset_id,
                        sha256=asset_id,
                        name=asset_id,
                        kind=kind,
                        path=asset_id,
                        mime="application/octet-stream",
                        info={
                            "width": 1920,
                            "height": 1080,
                            "frame_rate": "24/1",
                            "has_audio": True,
                        },
                    )
                )
        self.add_film("film")

    # --- operations -------------------------------------------------------------------------------------------------

    def create_op(self, session, kind, key, request, budget=0, steps=None):
        """A Generation operation of fixture jobs: `budget` video renders, or one local step per name."""
        operation = self.ops.create(
            session,
            kind=kind,
            request=request,
            request_key=key,
            jobs=(
                [
                    JobSpec(
                        "video",
                        "comfy",
                        "fixture",
                        seed=i,
                        graph={"render": {"class_type": "Fixture", "inputs": {}}},
                        snapshot={"knowledge": {"fixture": "no inference"}},
                    )
                    for i in range(1 if request.get("mode") == "draft" else budget)
                ]
                if budget
                else [
                    JobSpec("analysis", "local", "fixture", step=step) for step in (steps or [kind])
                ]
            ),
            budget_limit=None,
            budget=budget,
        )
        if self.park and operation["_created"]:
            for job in operation["jobs"]:
                self.ops.transition(session, job["id"], "claimed")
            operation = self.ops.operation(session, operation["id"]) | {"_created": True}
        return operation

    def move(self, session, job, *states):
        """Move a job through `states`, skipping the ones it has already reached (a parked job is `claimed`)."""
        order = ("queued", "claimed", "running", "collecting")
        current = job["state"]
        for state in states:
            if state in order and current in order and order.index(state) <= order.index(current):
                continue
            self.ops.transition(session, job["id"], state)
            current = state

    def settle(self, session, operation_id, state="succeeded"):
        """End each waiting job of the operation in `state`."""
        for job in self.ops.operation(session, operation_id)["jobs"]:
            if job["state"] not in {"queued", "claimed"}:
                continue
            self.move(
                session,
                job,
                "claimed",
                "running",
                *(("collecting",) if state == "succeeded" else ()),
            )
            self.ops.transition(session, job["id"], state)

    def succeed_with(self, session, job, key, artifact):
        """End a job in success with one output, as collection records it."""
        self.move(session, job, "claimed", "running", "collecting")
        self.ops.record_outputs(session, job["id"], [{"output_key": key, "artifact_id": artifact}])
        self.ops.transition(session, job["id"], "succeeded")

    def settle_target(
        self, asset_id="picture", scores=(0.881,), boxes=None, state="succeeded", raw=None
    ):
        """Run the target face check on a picture to `state`; a succeeded one finds faces with these scores."""
        from gaiavfx.application.target_faces import start_target_report

        with self.db.transaction() as session:
            operation = start_target_report(session, self.library, asset_id)
            if operation["state"] == "succeeded":
                return
            if state != "succeeded":
                self.settle(session, operation["id"], state)
                return
            png = io.BytesIO()
            Image.new("RGB", (768, 1024)).save(png, format="PNG")
            data = io.BytesIO()
            np.savez(
                data,
                frame=np.zeros(len(scores), dtype=int),
                score=np.array(scores),
                box=np.array(
                    boxes if boxes is not None else [[0, 0, 97, 97]] * len(scores)
                ).reshape(-1, 4),
            )
            for job, key, blob in zip(
                operation["jobs"],
                ("normalized", "faces:reports:0"),
                (png.getvalue(), data.getvalue() if raw is None else raw),
            ):
                self.blobs[job["id"]] = blob
                self.succeed_with(session, job, key, job["id"])

    def touch_window(self, operation_id):
        """Give a window analysis a new report artifact: updated evidence is never in-place pixels."""
        from gaiavfx.modules.generation.operations.persistence import JobRecord

        with self.db.transaction() as session:
            job = session.get(JobRecord, self.ops.operation(session, operation_id)["jobs"][0]["id"])
            artifact = job.outputs[0]["artifact_id"] + "-v2"
            self.blobs[artifact] = self.window_reports[operation_id]
            job.outputs = [{"output_key": "people", "artifact_id": artifact}]

    # --- commands ---------------------------------------------------------------------------------------------------

    def invoke(self, function, *args, **kwargs):
        with self.db.transaction() as session:
            return function(session, self.library, *args, **kwargs)

    def create(self, request_key="create", title=None):
        """A recast of the world's film with its film analysis settled."""
        state = self.invoke(
            recast.create_recast, source_asset_id=self.film, title=title, request_key=request_key
        )
        with self.db.transaction() as session:
            self.settle(session, state["analysis"]["operation_id"])
        return self.invoke(recast.recast_state, state["id"])

    def ready(self, request_key="create", target="picture"):
        """A created recast whose first character has a checked target, planned."""
        state = self.create(request_key)
        state = self.invoke(
            recast.set_target,
            state["id"],
            state["characters"][0]["id"],
            expected_revision=state["revision"],
            asset_id=target,
            label="Target",
        )
        self.settle_target(target)
        return self.invoke(
            recast.plan_recast, state["id"], expected_revision=state["revision"], range=None
        )

    def replacement_plan(self, session, request, *, library, renders=None, draft=False):
        """The fixture planner: each window plans with its analysis report's gaps."""
        self.calls["plan"].append(deepcopy(request))
        blob = self.window_reports[request["analysis_operation"]]
        return {"gaps": blob.get("gaps", []), "arm": {}, "crop_preview": {"scale": 0.5}}


def _module(name, adapter, names=None):
    """A module whose functions are the adapter's methods, so tests may patch or delete one."""
    module = ModuleType(name)
    for attribute in names or [n for n in vars(type(adapter)) if not n.startswith("_")]:
        setattr(module, attribute, getattr(adapter, attribute))
    return module


class FilmAnalysis:
    """The fixture film analysis: three local steps, then each film's report as the world holds it."""

    def __init__(self, world):
        self.world = world

    def start_film_analysis(self, session, library, asset_id, *, request_key):
        return self.world.create_op(
            session,
            "film_analysis",
            request_key,
            {"source_asset": asset_id},
            steps=["film_shots", "faces", "film_cast"],
        )

    def film_analysis_report(self, session, library, operation_id):
        operation = self.world.ops.operation(session, operation_id)
        if operation["state"] != "succeeded":
            return None
        return deepcopy(self.world.film_of(operation["request"]["source_asset"]).report)

    def film_faces(self, session, library, operation_id):
        operation = self.world.ops.operation(session, operation_id)
        film = self.world.film_of(operation["request"]["source_asset"])
        tracks = film.report["tracks"]
        count = 1 + max(row for t in tracks for row in t["faces"])
        frame, embedding = np.full(count, -1), np.zeros((count, 4), np.float32)
        for t in tracks:
            for row, value in zip(t["faces"], t["frames"]):
                frame[row] = value
            embedding[t["faces"]] = film.face_embeddings.get(t["id"], (1, 0, 0, 0))
        return {"frame": frame, "embedding": embedding}


class ShotWork:
    """The world's shot work (`recast_take.ShotWork`): window analyses succeed at once with one person per film
    track; renders, bridges and assemblies wait for the tests; an assembly's versions are fixed when it starts."""

    def __init__(self, world):
        self.world = world

    def start_window_analyses(
        self,
        session,
        library,
        *,
        source_asset,
        shot_start,
        shot_end,
        faces_operation,
        concepts=("person",),
        request_key,
    ):
        world, report = self.world, self.world.film_of(source_asset).report
        result = []
        for i, (start, end, _) in enumerate(shot_windows(shot_start, shot_end)):
            op = world.create_op(
                session,
                "person_analysis",
                request_key + str(i),
                {"source_asset": source_asset, "shot_start": start, "shot_end": end},
                steps=["people_report"],
            )
            if op["_created"]:
                world.calls["analysis"].append(op["id"])
                shot = next(s for s in report["shots"] if s["start"] <= start < s["end"])
                tracks = [t for t in report["tracks"] if t["shot"] == shot["index"]]
                blob = {
                    "source_asset": source_asset,
                    "shot_start": start,
                    "shot_end": end,
                    "native": [1920, 1080],
                    "fps": 24,
                    "letterbox": None,
                    "concepts": ["person"],
                    "contacts": {},
                    "tracks": [
                        {
                            "id": f"person{j}",
                            "concept": "person",
                            "box": {"x0": 700, "y0": 100, "x1": 1000, "y1": 950},
                            "identity_margin": 0.5,
                            "presence": ["present"] * (end - start),
                            "identity": {
                                "cluster": "c01",
                                "film_tracks": {t["id"]: 3},
                                "votes": 3,
                                "margin": 0.5,
                            },
                        }
                        for j, t in enumerate(tracks)
                    ],
                }
                world.blobs[op["id"]] = blob
                world.window_reports[op["id"]] = blob
                world.succeed_with(session, op["jobs"][0], "people", op["id"])
            result.append(world.ops.operation(session, op["id"]))
        return result

    def _performers(self, performers, analysis):
        return [
            {"track_id": p["tracks"][analysis], "target_asset": p["target_asset"]}
            for p in performers
        ]

    def start_shot_replacement(
        self,
        session,
        library,
        *,
        source_asset,
        window_analyses,
        performers,
        mode,
        seed,
        request_key,
        budget_limit=None,
        handoff=None,
        faceswap_allowed=True,
    ):
        world, result = self.world, []
        latent = bool(handoff and handoff["kind"] == "latent")
        for i, analysis in enumerate(window_analyses[:1] if latent else window_analyses):
            op = world.create_op(
                session,
                "character_replacement",
                request_key + str(i),
                {
                    "source_asset": source_asset,
                    "analysis": analysis,
                    "analysis_operation": analysis,
                    "facts": {"performers": self._performers(performers, analysis)},
                    "performers": performers,
                    "mode": mode,
                    "seed": seed,
                    "save_latent": latent,
                    "crop_override": (handoff or {}).get("crop"),
                    "faceswap_allowed": faceswap_allowed,
                },
                p.window_renders(mode),
            )
            if op["_created"]:
                world.calls["replacement"].append(op["id"])
            result.append(op)
        return result

    def continue_shot_replacement(
        self,
        session,
        library,
        *,
        source_asset,
        window_analyses,
        window_operations,
        performers,
        mode,
        seed,
        request_key,
        budget_limit=None,
        predecessor_take=None,
        handoff=None,
    ):
        index = len(window_operations)
        analysis = window_analyses[index]
        op = self.world.create_op(
            session,
            "character_replacement",
            request_key + str(index),
            {
                "source_asset": source_asset,
                "analysis": analysis,
                "analysis_operation": analysis,
                "facts": {"performers": self._performers(performers, analysis)},
                "performers": performers,
                "mode": mode,
                "seed": seed,
                "handoff": {
                    "predecessor_operation": window_operations[-1],
                    "candidate_id": predecessor_take["window"]["candidate_id"],
                },
            },
            p.window_renders(mode),
        )
        if op["_created"]:
            self.world.calls["replacement"].append(op["id"])
        return op

    def start_shot_bridge(
        self,
        session,
        library,
        *,
        source_asset,
        predecessor,
        successor,
        join,
        crop,
        seed,
        request_key,
        budget_limit=None,
    ):
        op = self.world.create_op(
            session,
            "character_replacement",
            request_key,
            {
                "source_asset": source_asset,
                "seed": seed,
                "bridge": {"join": join, "windows": [predecessor["window"], successor["window"]]},
            },
            1,
        )
        if op["_created"]:
            self.world.calls.setdefault("bridge", []).append(op["id"])
        return op

    def start_shot_head_pass(
        self,
        session,
        library,
        *,
        source_asset,
        window_analyses,
        performer,
        parent_version,
        seed,
        request_key,
        budget_limit=None,
    ):
        world, result = self.world, []
        for i, analysis in enumerate(window_analyses):
            target = (
                performer["targets"][analysis]
                if "targets" in performer
                else performer["target_asset"]
            )
            op = world.create_op(
                session,
                "character_replacement",
                request_key + str(i),
                {
                    "source_asset": source_asset,
                    "analysis_operation": analysis,
                    "head": {"parent_asset": "parent"},
                    "seed": seed,
                    "facts": {
                        "performers": [
                            {"track_id": performer["tracks"][analysis], "target_asset": target}
                        ]
                    },
                },
                budget=1,
            )
            world.calls["replacement"].append(op["id"])
            result.append(op)
        for bridge in parent_version.get("bridges", []):
            original = world.ops.operation(session, bridge["operation_id"])["request"]["bridge"]
            op = world.create_op(
                session,
                "character_replacement",
                request_key + "bridge" + str(bridge["join"]),
                {
                    "source_asset": source_asset,
                    "head": {"parent_asset": bridge["candidate_id"]},
                    "bridge": original,
                    "facts": {"performers": [{"track_id": "person0", "target_asset": "picture"}]},
                    "seed": seed,
                },
                budget=1,
            )
            world.calls["replacement"].append(op["id"])
            result.append(op)
        return result

    def shared_crop_for_windows(self, session, library, *, requests):
        return {"fixture": "shared_crop", "scale": 0.5}

    def chain_take(self, session, library, operation):
        if operation["state"] != "succeeded":
            return None
        return {
            "window": {
                "candidate_id": operation["id"] + "-candidate",
                "operation_id": operation["id"],
            }
        }

    def window_takes(self, session, library, operation):
        take = self.chain_take(session, library, operation)
        return {0: take} if take else {}

    def start_shot_assembly(
        self,
        session,
        library,
        *,
        source_asset,
        shot_start,
        shot_end,
        window_operations,
        request_key,
        layer=None,
        parent=None,
        stopped=None,
        bridge_operations=None,
        handoff=None,
    ):
        world = self.world
        op = world.create_op(
            session,
            "shot_assembly",
            request_key,
            {
                "source_asset": source_asset,
                "window_operations": window_operations,
                "bridge_operations": bridge_operations or [],
                "handoff": handoff,
                **({"stopped": stopped} if stopped else {}),
                "layer": layer,
                "parent": parent,
            },
        )
        if op["_created"]:
            world.calls["assembly"].append(op["id"])
            available = stopped is None and all(
                world.ops.operation(session, x)["state"] == "succeeded" for x in window_operations
            )
            world.versions[op["id"]] = (
                [
                    self._version(
                        session,
                        op["id"],
                        layer,
                        parent,
                        window_operations,
                        bridge_operations,
                        handoff,
                    )
                ]
                if available
                else []
            )
        return op

    def _version(
        self, session, operation_id, layer, parent, window_operations, bridge_operations, handoff
    ):
        ops = self.world.ops
        return {
            "id": operation_id,
            "take": 0,
            "layer": layer,
            "parent": parent,
            "preview_asset": self.world.previews[
                len(self.world.versions) % len(self.world.previews)
            ],
            "patch_artifact": "patch",
            "checks": [
                {
                    "check": "background_geometry",
                    "verdict": "pass",
                    "calibrated": True,
                    "measured": 0.01,
                    "reason": "Geometry is within tolerance.",
                }
            ],
            "bridges": [
                {
                    "operation_id": key,
                    "candidate_id": key + "-candidate",
                    "join": ops.operation(session, key)["request"]["bridge"]["join"],
                }
                for key in (bridge_operations or [])
                if ops.operation(session, key)["state"] == "succeeded"
            ],
            "joins": [
                {"join": j["join"], "handoff": "bridge" if j["kind"] == "bridge" else "crossfade"}
                for j in (handoff or {}).get("joins", [])
            ],
            "windows": [
                {
                    "operation_id": x,
                    "candidate_id": x + "-candidate",
                    "handoff": "latent"
                    if ops.operation(session, x)["request"].get("handoff")
                    else None,
                    "seed": 123,
                    "treatment": "inverted",
                    "framing": "crop",
                    "round": 0,
                }
                for x in window_operations
            ],
        }

    def shot_versions(self, session, library, assembly_operation_id):
        if self.world.ops.operation(session, assembly_operation_id)["state"] != "succeeded":
            return []
        return deepcopy(self.world.versions[assembly_operation_id])


# --- dialogue analysis ------------------------------------------------------------------------------------------------

# (start, end, text, speaker): A is seen in shots 1-2 (0-6.08 s), B in shots 3-4 (6.08-12.17 s).
LINES = [
    (0.20, 1.00, "Hello there.", "A"),
    (1.50, 2.40, "How are you today?", "A"),
    (3.30, 4.20, "I am fine.", "A"),
    (4.60, 5.60, "Let us go to the market.", "A"),
    (6.30, 7.30, "Not now.", "B"),
    (9.30, 10.30, "Later, then.", "B"),
]
KEYS = [f"{i:05d}-{round(start * 1000)}" for i, (start, *_rest) in enumerate(LINES)]


def embedding(speaker, index):
    vector = [0.01 * math.sin(index * 7 + d) for d in range(192)]
    vector[0 if speaker == "A" else 1] = 1.0
    return vector


def dialogue_report():
    """The six-line report whose embeddings make the two speakers separable."""
    db = [-80.0] * 1217
    segments, speakers = [], {}
    for index, (start, end, text, speaker) in enumerate(LINES):
        words = text.split()
        step = (end - start) / len(words)
        segments.append(
            {
                "key": KEYS[index],
                "index": index,
                "start": start,
                "end": end,
                "text": text,
                "words": [
                    {
                        "start": round(start + k * step, 3),
                        "end": round(start + (k + 1) * step, 3),
                        "word": word,
                    }
                    for k, word in enumerate(words)
                ],
            }
        )
        speakers[KEYS[index]] = {"ecapa": embedding(speaker, index), "median_hz": 100.0 + index}
        db[round(start * 100) : round(end * 100)] = [-20.0] * (
            round(end * 100) - round(start * 100)
        )
    return {
        "version": 1,
        "sample_rate": 48000,
        "offset_samples": 0,
        "samples": 584_000,
        "channels": 2,
        "segments": segments,
        "speakers": speakers,
        "whisper_spans": [[9.2, 10.4]],
        "envelope": {"hop": 480, "db": db},
    }


def install_dialogue(world, patcher):
    """The fixture dialogue analysis: the real module's rules and keys, a shared real operation the tests settle, and
    the six-line report once it succeeds."""
    real = import_module(DIALOGUE_ANALYSIS)
    fake = ModuleType(real.__name__)
    for name in ("NAME", "DIALOGUE_VERSION", "SAMPLE_RATE", "STEPS", "latest", "audio_gap"):
        setattr(fake, name, getattr(real, name))
    fake.dialogue_view = real.dialogue_view
    report = dialogue_report()
    calls = []

    def start_dialogue_analysis(session, library, asset_id, *, retry_failed=False):
        calls.append((asset_id, retry_failed))
        asset = library.get(session, asset_id)
        gap = real.audio_gap(asset)
        if gap:
            raise ApplicationError(gap["message"], 422)
        existing, attempt = real.latest(session, asset)
        if existing:
            settled = existing["state"] in OPERATION_TERMINAL and existing["state"] != "succeeded"
            if not (retry_failed and settled):
                return existing | {"_created": False}
            attempt += 1
        request = {"source_asset": asset_id, "version": real.DIALOGUE_VERSION, "attempt": attempt}
        return world.create_op(
            session, real.NAME, real._key(asset, attempt), request, steps=list(real.STEPS)
        )

    def report_of(session, library, operation_id):
        operation = world.ops.operation(session, operation_id)
        return report if operation["state"] == "succeeded" else None

    fake.start_dialogue_analysis = start_dialogue_analysis
    fake.dialogue_report = report_of
    patcher.setitem(sys.modules, real.__name__, fake)
    return SimpleNamespace(real=real, fake=fake, report=report, calls=calls)


# --- voice builds -----------------------------------------------------------------------------------------------------

# Library audio recordings of the target (seconds); `picture` is the world's image.
RECORDINGS = {
    "rec_a": 12.0,
    "rec_b": 18.0,
    "rec_c": 20.0,
    "rec_d": 25.0,
    "short": 10.0,
    "long": 70.0,
}
STOPPED = frozenset({"failed", "cancelled", "needs_attention"})
TRAINING = "The voice build failed while training the voice. Retry; if it fails again, the app log has the details."
AUDITION_STOPPED = "The voice build failed while converting the audition. Retry; if it fails again, the app log has the details."


def add_recordings(world):
    """The pytest world's recordings and an audition reel, by name, with no files behind them."""
    with world.db.transaction() as session:
        for asset_id, seconds in RECORDINGS.items():
            session.add(
                AssetRecord(
                    id=asset_id,
                    sha256="sha-" + asset_id,
                    name=f"{asset_id}.wav",
                    kind="audio",
                    path=asset_id,
                    mime="audio/wav",
                    info={"duration": seconds, "has_audio": True},
                )
            )
        session.add(
            AssetRecord(
                id="reel",
                sha256="sha-reel",
                name="audition.wav",
                kind="audio",
                path="reel",
                mime="audio/wav",
                info={"duration": 9.0, "has_audio": True},
                provenance=[{"type": "generation_candidate", "operation_id": "audition"}],
            )
        )


def install_voice_builds(world, patcher):
    """The fixture voice build with exactly the real module's interface: the shared build is a real operation found
    by its content key, which the tests settle; a build reads as running at its material stage and fails training."""
    fake = ModuleType(VOICE_BUILD)
    calls = {"start": [], "cancel": [], "retry": [], "audition": []}
    reels = {}  # audition operation id -> reel asset id

    def key(shas, attempt):
        return sha256(json.dumps(["voice_build", list(shas), 1, attempt]).encode()).hexdigest()[:12]

    def latest(session, shas):
        operation, attempt = None, 1
        while (found := world.ops.for_request(session, key(shas, attempt))) is not None:
            operation, attempt = found, attempt + 1
        return (operation, attempt - 1) if operation else (None, 1)

    def start_voice_build(session, library, sample_asset_ids, *, library_scan, attempt):
        calls["start"].append(
            {"ids": list(sample_asset_ids), "attempt": attempt, "digest": library_scan["digest"]}
        )
        shas = [library.get(session, asset_id)["sha256"] for asset_id in sample_asset_ids]
        request = {
            "samples": list(sample_asset_ids),
            "version": 1,
            "attempt": attempt,
            "library": {
                "folder": library_scan["folder"],
                "digest": library_scan["digest"],
                "clips": len(library_scan["clips"]),
                "categories": library_scan["categories"],
            },
        }
        operation = world.ops.create(
            session,
            kind="voice_build",
            request=request,
            request_key=key(shas, attempt),
            jobs=[
                JobSpec("analysis", "local", "fixture", step="voice_samples"),
                JobSpec("analysis", "local", "fixture", step="analysis.transcribe"),
            ],
            budget=0,
            budget_limit=None,
            awaits_review=True,
        )
        if world.park and operation["_created"]:
            for job in operation["jobs"]:
                world.ops.transition(session, job["id"], "claimed")
        return operation

    def cancel_build(session, shas):
        calls["cancel"].append(list(shas))
        operation, _ = latest(session, shas)
        if operation and operation["state"] not in OPERATION_TERMINAL:
            world.ops.request_cancel(session, operation["id"])

    def stopped_jobs(operation):
        if operation["state"] != "awaiting_review":
            return []
        return [j for j in operation["jobs"] if j["state"] in STOPPED]

    def retry_stopped(session, operation):
        calls["retry"].append(operation["id"])
        return operation

    def build_view(operation):
        if operation["state"] == "succeeded":
            state = "succeeded"
        elif operation["state"] in OPERATION_TERMINAL or stopped_jobs(operation):
            state = "failed"
        else:
            state = "running"
        return {
            "state": state,
            "stage": {"running": "material", "failed": "train"}.get(state),
            "done": sum(j["state"] == "succeeded" for j in operation["jobs"]),
            "total": 27,
            "error": TRAINING if state == "failed" else None,
        }

    def audition_view(operation):
        view = build_view(operation)
        return view | {
            "stage": "audition",
            "error": AUDITION_STOPPED if view["state"] == "failed" else None,
        }

    def manifest_artifact(operation):
        if operation["state"] != "succeeded":
            return None
        return {"artifact_id": "hidden-artifact-" + operation["id"], "sha256": "manifest-sha"}

    def voice_manifest(session, library, operation_id):
        return {
            "target_median_hz": 190.0,
            "operation_id": operation_id,
            "weights": {"artifact_id": "hidden-weights", "sha256": "weights-sha"},
            "index": {"artifact_id": "hidden-index", "sha256": "index-sha"},
        }

    def audition_reel(operation):
        if operation["state"] != "succeeded" or operation["id"] not in reels:
            return None
        return {"asset_id": reels[operation["id"]], "output_key": "reel", "kind": "audio"}

    def start_voice_audition(session, library, **kwargs):
        calls["audition"].append(kwargs)
        return world.create_op(
            session,
            "voice_audition",
            kwargs["request_key"],
            {
                "lines": [line["key"] for line in kwargs["lines"]],
                "source_median_hz": kwargs["source_median_hz"],
                "target_median_hz": kwargs["target_median_hz"],
                "voice_model": kwargs.get("voice_model"),
                "attempt": kwargs.get("attempt", 1),
            },
            steps=["voice_audition_cut", "rvc.convert", "voice_reel"],
        )

    for name, value in {
        "NAME": "voice_build",
        "AUDITION": "voice_audition",
        "BUILD_VERSION": 1,
        "STOPPED": STOPPED,
        "key": key,
        "latest": latest,
        "start_voice_build": start_voice_build,
        "cancel_build": cancel_build,
        "stopped_jobs": stopped_jobs,
        "retry_stopped": retry_stopped,
        "build_view": build_view,
        "audition_view": audition_view,
        "manifest_artifact": manifest_artifact,
        "voice_manifest": voice_manifest,
        "audition_reel": audition_reel,
        "start_voice_audition": start_voice_audition,
    }.items():
        setattr(fake, name, value)
    patcher.setitem(sys.modules, fake.__name__, fake)
    return SimpleNamespace(fake=fake, calls=calls, reels=reels, key=key)


# --- fixtures ---------------------------------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def recast_template(tmp_path_factory):
    root = tmp_path_factory.mktemp("recast-template") / "library"
    LibraryService(root, MIGRATIONS).create("Recast tests")
    return root / "library.sqlite3"


@pytest.fixture
def world(tmp_path, monkeypatch, recast_template):
    root = tmp_path / "library"
    root.mkdir()
    shutil.copy2(recast_template, root / "library.sqlite3")
    db = Database(root)
    fixture = RecastWorld(db, MediaLibrary(db, None))
    fixture.add_fixture_assets()
    fixture.install(monkeypatch)
    yield fixture
    db.close()


@pytest.fixture
def voice(world, monkeypatch):
    return install_dialogue(world, monkeypatch)


@pytest.fixture
def builds(world, monkeypatch):
    add_recordings(world)
    return install_voice_builds(world, monkeypatch)


# --- shared commands --------------------------------------------------------------------------------------------------


def checked(state):
    RecastDTO.model_validate(state)
    return state


def edit(world, state, function, *args, **kwargs):
    """Run a Recast command at the state's revision; the result is a valid Recast read."""
    return checked(
        world.invoke(function, state["id"], *args, expected_revision=state["revision"], **kwargs)
    )


def character(state, character_id):
    return next(c for c in state["characters"] if c["id"] == character_id)


def shot_versions(world, recast_id, shot_id):
    """A shot's versions with what the blind state leaves out: windows, seeds, bridges, layer and parent."""

    def versions(session, library):
        row, _report = recast_shell.load(session, library, recast_id)
        shot = next(s for s in row["shots"] if s["shot_id"] == shot_id)
        return [
            {
                "id": v["id"],
                "letter": p.letter(i),
                "windows": v["windows"],
                **({"bridges": v["bridges"], "joins": v["joins"]} if "bridges" in v else {}),
                "layer": v.get("layer"),
                "parent": v.get("parent"),
            }
            for i, v in enumerate(p.versions(session, library, shot))
        ]

    return world.invoke(versions)


def run(world, state, budget=20, mode="draft", frame_range=None):
    return edit(world, state, recast.start_run, mode=mode, range=frame_range, render_budget=budget)


def settle_all(world, kind):
    with world.db.transaction() as session:
        for operation in world.ops.recent(session, kind=kind):
            world.settle(session, operation["id"])


def drain(world, recast_id):
    """Feed and settle until the run finishes with no shot rendering or queued."""
    for _ in range(12):
        world.invoke(recast.feed)
        settle_all(world, "character_replacement")
        world.invoke(recast.feed)
        settle_all(world, "shot_assembly")
        world.invoke(recast.feed)
        state = world.invoke(recast.recast_state, recast_id)
        if state["run"]["state"] == "finished" and all(
            s["status"] not in {"rendering", "queued"} for s in state["shots"]
        ):
            return state
    raise AssertionError("Run did not finish")


def long_shots(world, count=1, length=175):
    """The world's film cut to `count` shots of `length` frames, then a ready recast of it."""
    world.report["shots"] = [
        {"index": i + 1, "start": i * length, "end": (i + 1) * length, "poster_artifact": None}
        for i in range(count)
    ]
    world.report["frame_count"] = count * length
    world.report["tracks"] = world.report["tracks"][:count]
    for i, track in enumerate(world.report["tracks"]):
        track["frames"] = list(range(i * length, (i + 1) * length, 5))
    world.report["clusters"][0]["track_ids"] = [t["id"] for t in world.report["tracks"]]
    return world.ready()


def settle_reference(world, kind, state="succeeded"):
    """Settle fixture jobs through Generation; retain image assets/candidates as collection would."""
    with world.db.transaction() as session:
        operation = world.ops.recent(session, kind=kind)[0]
        for job in operation["jobs"]:
            if state == "succeeded" and job["step"] != "portrait_crop":
                asset_id = job["id"]
                session.add(
                    AssetRecord(
                        id=asset_id,
                        sha256=asset_id,
                        name=asset_id,
                        kind="image",
                        path=asset_id,
                        mime="image/png",
                        info={"width": 768, "height": 1024},
                        provenance=[
                            {"type": "generation_candidate", "operation_id": operation["id"]}
                        ],
                    )
                )
                world.ops.add_candidate(session, job["id"], asset_id, "image", "image")
        world.settle(session, operation["id"], state)
        return world.ops.operation(session, operation["id"])


def waiting_reference(world, state):
    """Feed until the first character's face sheet waits for the user."""
    world.invoke(recast.feed)
    portraits = settle_reference(world, "face_portraits")
    world.invoke(recast.feed)
    sheet = settle_reference(world, "face_sheet")
    world.invoke(recast.feed)
    return world.invoke(recast.recast_state, state["id"]), portraits, sheet


def cast(world):
    """A recast whose film shows A in shots 1-2 and B in shots 3-4."""
    state = world.create()
    state = edit(world, state, recast.move_tracks, track_ids=["t3", "t4"], to="new")
    a, b = (c["id"] for c in state["characters"])
    state = edit(world, state, recast.patch_character, a, label="Mara")
    state = edit(world, state, recast.patch_character, b, label="Tom")
    return state, a, b


def settle_dialogue(world, state):
    with world.db.transaction() as session:
        world.settle(session, state["dialogue"]["operation_id"])
    return checked(world.invoke(recast.recast_state, state["id"]))


def analysed(world):
    """`cast` with its dialogue analysis settled."""
    state, a, b = cast(world)
    state = edit(world, state, recast_lines.start_dialogue_analysis)
    return settle_dialogue(world, state), a, b


def mara(world):
    """A recast whose only character is Mara."""
    state = world.create()
    cid = state["characters"][0]["id"]
    return edit(world, state, recast.patch_character, cid, label="Mara"), cid


def build_operation(world, ids, attempt=None):
    shas = ["sha-" + asset_id for asset_id in ids]
    with world.db.transaction() as session:
        operation, found = sys.modules[VOICE_BUILD].latest(session, shas)
    assert attempt is None or found == attempt
    return operation


def drive(world, operation_id, states):
    """Move the operation's waiting jobs, in order, to these end states."""
    with world.db.transaction() as session:
        for job, state in zip(world.ops.operation(session, operation_id)["jobs"], states):
            if job["state"] not in {"queued", "claimed"}:
                continue
            world.move(
                session,
                job,
                "claimed",
                "running",
                *(("collecting",) if state == "succeeded" else ()),
            )
            failure = {} if state == "succeeded" else {"error": "worker said X:/secret/x.wav"}
            world.ops.transition(session, job["id"], state, **failure)


def succeed(world, operation_id):
    drive(world, operation_id, ["succeeded", "succeeded"])
    with world.db.transaction() as session:
        world.ops.resume(session, operation_id, {"stage": "done"}, jobs=[])
        assert world.ops.operation(session, operation_id)["state"] == "succeeded"


def feed_writes(world, state, character_id, **values):
    """What the voice feed (P5d) writes: feed-owned columns, without a revision."""
    with world.db.transaction() as session:
        p.store.lock(session, state["id"])
        row = p.store.load(session, state["id"])
        next(c for c in row["characters"] if c["id"] == character_id).update(values)
        p.store.save(session, row)


def audition(world, state, character_id, build, *, outcome="succeeded", attempt=1, builds=None):
    """An audition the feed started (P5d), settled; a succeeded one has the reel candidate."""
    with world.db.transaction() as session:
        row = p.store.load(session, state["id"])
        stand = next(c for c in row["characters"] if c["id"] == character_id)
        key = recast_derivatives.audition_key(row, stand, "hidden-artifact-" + build["id"], attempt)
        operation = world.create_op(
            session,
            "voice_audition",
            key,
            {"lines": ["00000-200"], "source_median_hz": 120.0, "target_median_hz": 190.0},
            steps=["voice_audition_cut"],
        )
        world.settle(session, operation["id"], outcome)
    if builds is not None and outcome == "succeeded":
        builds.reels[operation["id"]] = "reel"
    return operation
