"""The browser suite's Recast: the real Recast commands and read, with the Recast test world's adapters in this process.

`install(app)` puts the adapters of backend/tests/recast_world_support.py at Recast's seams in the test server and adds
the test-only routes a film spec seeds, settles, feeds and retires its recast through. Each world's film, pictures,
face crops and posters are small real files in the spec's library, so the page loads them as it would any other.

Fixture work waits for the spec: its jobs are parked at `claimed`, which the live runner never takes; no advancer runs
on an operation of fixture jobs; and the recast feed runs when a spec asks, not on the runner's timer. No route or
switch here ships in the application.
"""

import io
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Literal

import numpy as np
import pytest
from fastapi import HTTPException
from gaiavfx.application import operations, recast
from gaiavfx.infrastructure.database import Base
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.assets import AssetRecord
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field
from recast_world_support import (
    RecastWorld,
    film_report,
    install_dialogue,
    install_voice_builds,
)
from sqlalchemy import delete

MEDIA = Path(__file__).resolve().parents[1] / ".cache" / "recast-world"


class Link(BaseModel):
    """A person the shot's window analysis found with no face, linked to a character: proposed by the analysis with
    its evidence, or then confirmed by the user."""

    shot: int
    character: int
    state: Literal["proposed", "confirmed"] = "proposed"
    track: str = "back"
    evidence: dict = {}


class Scene(BaseModel):
    """The recast a film spec starts from. Characters split the film's shots between them in turn. Each character's
    entry in `targets` is its picture: `confirmed` (the user's portrait), `proposed` (Gaia's portrait), `faceless` (the
    user's picture with no face) or None; `labels` name them, and with `shared` every character's picture is the same
    Library picture. `similar` pairs of characters look alike; `hidden` characters are set aside; `unknown` face tracks
    belong to no character (from Shot 003 on). `sheets` take a character's face sheet to waiting, approved or rejected.
    `faceless` characters have no face crop yet.
    `rendered` shots from the start get a finished Draft run's versions, `takes` of them; a planned shot's `links` join
    a faceless person to a character. `pictures` are more Library portraits, by name."""

    shots: int = Field(8, ge=1, le=2000)
    length: int = Field(48, ge=8)
    rate: tuple[int, int] = (24, 1)
    characters: list[str] = ["Mara"]
    title: str | None = None
    recast: bool = True
    analysed: bool = True
    targets: list[Literal["confirmed", "proposed", "faceless", "unreadable"] | None] = [
        "confirmed"
    ]
    ensemble: int = 0
    labels: list[str | None] = []
    shared: bool = False
    similar: list[tuple[int, int]] = []
    hidden: list[int] = []
    faceless: list[int] = []
    unknown: int = 0
    sheets: list[Literal["waiting", "approved", "rejected"] | None] = []
    planned: bool = False
    rendered: int = 0
    takes: int = Field(1, ge=1, le=4)
    links: list[Link] = []
    pictures: list[str] = []


class Picture(BaseModel):
    """A new picture for one character, set by the user, once its face check has run."""

    character: int
    kind: Literal["confirmed", "faceless", "unreadable"] = "confirmed"


class Settle(BaseModel):
    """End the waiting jobs of the world's operations of one kind (the oldest `count` of them, or all), then feed. A
    head pass's windows are `head` operations."""

    kind: str
    outcome: str = "succeeded"
    count: int | None = None
    head: bool | None = None


def portrait(path: Path, seed: int, face=True, mark=""):
    """A small picture; a portrait has a light face the fixture face scan finds, a faceless one is flat, and an
    unreadable one (face=None) is black. `mark` is drawn in a corner pixel by pixel, so two pictures never share
    their bytes (the Library keeps one asset per content)."""
    image = Image.new(
        "RGB", (192, 256), (40 + seed % 160, 60, 90) if face is not None else (0, 0, 0)
    )
    if face:
        ImageDraw.Draw(image).ellipse((56, 48, 136, 152), fill=(222, 190, 170))
    for i, byte in enumerate(mark.encode()):
        # Dim enough that a flat picture stays flat and a black one stays unreadable for the face scan.
        image.putpixel((i % 16, 250 + i // 16), (byte % 6, 0, 0))
    image.save(path, "PNG" if mark else "JPEG", **({} if mark else {"quality": 85}))
    return path


def faces_in(data: bytes):
    """The fixture face scan: one centred face in a picture that is not flat, none in a flat one, and no reading of a
    black one (its report is unreadable)."""
    with Image.open(io.BytesIO(data)) as image:
        grey = np.asarray(image.convert("L"), dtype=np.float32)
    if grey.max() < 8:
        return None
    if grey.std() < 4:
        return []
    height, width = grey.shape
    return [(0.91, (width * 0.3, height * 0.2, width * 0.7, height * 0.6))]


def face_scan_answer(data: bytes):
    faces = faces_in(data)
    if faces is None:
        return {"faces": {"reports": [{"filename": "faces.npz", "type": "output"}]}}, {
            "faces:reports:0": b"not a report"
        }
    report = io.BytesIO()
    np.savez(
        report,
        frame=np.zeros(len(faces), dtype=int),
        score=np.array([score for score, _ in faces], dtype=np.float64),
        box=np.array([box for _, box in faces], dtype=np.float64).reshape(-1, 4),
    )
    return {"faces": {"reports": [{"filename": "faces.npz", "type": "output"}]}}, {
        "faces:reports:0": report.getvalue()
    }


class BrowserWorld:
    """One Recast world for the whole test server, created on the first seed (the library exists by then)."""

    def __init__(self, app):
        self.app = app
        self.lock = threading.RLock()
        self.patcher = pytest.MonkeyPatch()
        self.world = None
        self.count = 0

    @property
    def workspace(self):
        return self.app.state.workspace

    def get(self):
        with self.lock:
            if self.world is None:
                workspace = self.workspace
                self.world = RecastWorld(workspace.db, workspace.library, park=True)
                self.world.install(self.patcher)
                install_dialogue(self.world, self.patcher)
                install_voice_builds(self.world, self.patcher)
                # Versions show real previews whose names the page must never show (the blind checks); like an
                # assembly's previews they are process files, which the Library leaves out.
                self.world.previews = [
                    self.workspace.import_media(
                        self.preview(i), f"PRIVATE SEED ARM ROUND {i}.mp4"
                    )["asset"]["id"]
                    for i in range(4)
                ]
                with self.world.db.transaction() as session:
                    for asset_id in self.world.previews:
                        session.get(AssetRecord, asset_id).provenance = [
                            {"type": "shot_assembly", "operation_id": "world-preview"}
                        ]
                    # A real patch artifact, so a conform of the world's versions can start.
                    patch = MEDIA / "patch.bin"
                    patch.write_bytes(b"recast world patch")
                    prepared = workspace.library.prepare_blob(
                        patch, "replacement_patch", {}
                    )
                    self.world.patch = workspace.library.register_artifact(
                        session, prepared
                    )["id"]
            return self.world

    def preview(self, index):
        def make(path):
            subprocess.run(
                [
                    str(MediaTools().ffmpeg),
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=320x180:rate=24:duration=2",
                    "-vf",
                    f"drawbox=x={index * 40}:y=140:w=30:h=30:color=white:t=fill",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(path),
                ],
                check=True,
            )

        return self.media(f"preview-{index}.mp4", make)

    def media(self, name, make):
        """A unique file for this world: `make(path)` writes the shared original once, and each copy differs."""
        MEDIA.mkdir(parents=True, exist_ok=True)
        original = MEDIA / name
        if not original.exists():
            # Written whole or not at all: a seed that times out midway leaves no half file for the next one.
            partial = original.with_name(f"partial-{uuid.uuid4().hex[:8]}-{name}")
            make(partial)
            partial.replace(original)
        return original

    def film(self, scene: Scene, tag: str):
        frames = scene.shots * scene.length
        rate = f"{scene.rate[0]}/{scene.rate[1]}"
        tools = MediaTools()

        def make(path):
            subprocess.run(
                [
                    str(tools.ffmpeg),
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    f"testsrc2=size=320x180:rate={rate}",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=220:sample_rate=48000",
                    "-frames:v",
                    str(frames),
                    "-shortest",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    str(path),
                ],
                check=True,
            )

        original = self.media(
            f"film-{frames}-{scene.rate[0]}-{scene.rate[1]}.mp4", make
        )
        unique = MEDIA / f"film-{tag}.mp4"
        subprocess.run(
            [
                str(tools.ffmpeg),
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(original),
                "-c",
                "copy",
                "-metadata",
                f"comment={tag}",
                str(unique),
            ],
            check=True,
        )
        return unique

    def picture(self, name, tag, face=True):
        """A Library picture unique to this world: a portrait, or a flat picture with no face."""
        pictures = MEDIA / "pictures"
        pictures.mkdir(parents=True, exist_ok=True)
        path = portrait(pictures / f"{tag}.png", int(tag, 16), face, mark=tag)
        return self.workspace.import_media(path, name + ".png")["asset"]["id"]

    def artifact(self, session, path):
        library = self.workspace.library
        prepared = library.prepare_blob(path, "visual_evidence", {})
        return library.register_artifact(session, prepared)["id"]

    def seed(self, scene: Scene):
        world = self.get()
        with self.lock:
            self.count += 1
            tag = uuid.uuid4().hex[:8]
        name = f"{scene.title or 'The crossing'} · {tag}"
        imported = self.workspace.import_media(self.film(scene, tag), name + ".mp4")
        film_id = imported["asset"]["id"]
        report = film_report(film_id, scene.shots, scene.length)
        report["fps"] = list(scene.rate)
        # Film analysis numbers shots from 0 (recast_steps.film_shots); the pytest world's report from 1.
        for shot in report["shots"]:
            shot["index"] -= 1
        for track in report["tracks"]:
            track["shot"] -= 1
        count = len(scene.characters)
        # Characters take the shots in turn; in the first `ensemble` shots every character is seen.
        owners = {t["id"]: t["shot"] % count for t in report["tracks"]}
        for shot in report["shots"][: scene.ensemble]:
            base = next(t for t in report["tracks"] if t["shot"] == shot["index"])
            for k in range(count):
                if k == owners[base["id"]]:
                    continue
                n = len(report["tracks"]) + 1
                report["tracks"].append(
                    {
                        **base,
                        "id": f"t{n}",
                        "faces": list(range(15 * (n - 1), 15 * n)),
                    }
                )
                owners[f"t{n}"] = k
        report["clusters"] = [
            {
                "id": f"c{k + 1:02d}",
                "track_ids": [
                    t["id"] for t in report["tracks"] if owners[t["id"]] == k
                ],
                "screen_time_frames": sum(
                    scene.length for t in report["tracks"] if owners[t["id"]] == k
                ),
                "shot_count": sum(1 for t in report["tracks"] if owners[t["id"]] == k),
                "presence": [
                    [s["start"], s["end"]]
                    for s in report["shots"]
                    if any(
                        owners[t["id"]] == k and t["shot"] == s["index"]
                        for t in report["tracks"]
                    )
                ],
                "face_artifact": None,
            }
            for k in range(count)
        ]
        report["suggestions"] = [
            {"a": f"c{a + 1:02d}", "b": f"c{b + 1:02d}", "max_similarity": 0.88}
            for a, b in scene.similar
        ]
        # Unknown faces: a track no cluster holds, in Shot 003 and the shots after it.
        for k in range(scene.unknown):
            shot = report["shots"][min(2 + k, scene.shots - 1)]
            n = len(report["tracks"]) + 1
            report["tracks"].append(
                {
                    **report["tracks"][0],
                    "id": f"t{n}",
                    "shot": shot["index"],
                    "frames": list(range(shot["start"], shot["end"], 5)),
                    "faces": list(range(15 * (n - 1), 15 * n)),
                }
            )
            report["unknown_track_ids"].append(f"t{n}")
        crops = MEDIA / "crops"
        crops.mkdir(parents=True, exist_ok=True)
        with world.db.transaction() as session:
            for track in report["tracks"]:
                index = track["shot"]
                # A `faceless` character's tracks have no face crop yet.
                if owners.get(track["id"]) in scene.faceless:
                    continue
                face = portrait(crops / f"face-{index % 24}.jpg", index * 37)
                track["crop_artifact"] = self.artifact(session, face)
                report["shots"][index]["poster_artifact"] = track["crop_artifact"]
            for cluster in report["clusters"]:
                first = next(
                    t for t in report["tracks"] if t["id"] == cluster["track_ids"][0]
                )
                cluster["face_artifact"] = first["crop_artifact"]
        world.add_film(film_id, report)
        if not scene.recast:
            # A Library film the user has not opened yet: opening it asks before its analysis creates the recast.
            return {
                "id": "",
                "title": name,
                "film": {"asset_id": film_id, "name": name + ".mp4"},
                "shots": [],
                "characters": [],
                "pictures": [],
            }
        state = world.invoke(
            recast.create_recast,
            source_asset_id=film_id,
            title=name,
            request_key=f"world-{tag}",
        )
        if scene.analysed:
            with world.db.transaction() as session:
                world.settle(session, state["analysis"]["operation_id"])
            state = world.invoke(recast.recast_state, state["id"])
            for index, (character, label) in enumerate(
                zip(state["characters"], scene.characters)
            ):
                state = world.invoke(
                    recast.patch_character,
                    state["id"],
                    character["id"],
                    expected_revision=state["revision"],
                    label=label,
                    hidden=index in scene.hidden,
                )
            state = self.cast(state, scene, tag)
            state = self.sheets(state, scene)
            if scene.planned or scene.rendered:
                state = world.invoke(
                    recast.plan_recast,
                    state["id"],
                    expected_revision=state["revision"],
                    range=None,
                )
            if scene.rendered:
                state = self.render(state, scene.rendered, scene.takes)
            state = self.links(state, scene)
            # The scene is where the spec starts, not a history: its cast edits leave no entries to undo.
            edits = Base.metadata.tables["editing_recast_edits"]
            with world.db.transaction() as session:
                session.execute(delete(edits).where(edits.c.recast_id == state["id"]))
        pictures = []
        for index, label in enumerate(scene.pictures):
            named = f"{label} · {tag}-p{index}"
            pictures.append(
                {"id": self.picture(named, f"{tag}{index + 64:02x}"), "label": named}
            )
        return {
            "id": state["id"],
            "title": name,
            "film": {"asset_id": film_id, "name": name + ".mp4"},
            "shots": [s["id"] for s in state["shots"]],
            "characters": [c["id"] for c in state["characters"]],
            "pictures": pictures,
        }

    def cast(self, state, scene, tag):
        """Each character's picture, as the user or Gaia set it; a confirmed picture's face check has run."""
        world = self.get()
        commands = {"proposed": recast.propose_target}
        shared = None
        for index, (character, kind) in enumerate(
            zip(state["characters"], scene.targets)
        ):
            if kind is None:
                continue
            picture = shared or self.picture(
                f"{'Arin' if kind in {'confirmed', 'proposed'} else 'Hallway'} · {tag}-{index}",
                f"{tag}{index:02x}",
                face={"faceless": False, "unreadable": None}.get(kind, True),
            )
            if scene.shared:
                shared = picture
            label = scene.labels[index] if index < len(scene.labels) else None
            state = world.invoke(
                commands.get(kind, recast.set_target),
                state["id"],
                character["id"],
                expected_revision=state["revision"],
                asset_id=picture,
                label=label
                or {"confirmed": "Arin", "proposed": "Arin proposal"}.get(
                    kind, "Hallway"
                ),
            )
        # The live runner checks each picture the user set on the fixture ComfyUI, as the app would.
        for index, kind in enumerate(scene.targets[: len(state["characters"])]):
            if kind not in {None, "proposed"}:
                self.checked(state["id"], index)
        return world.invoke(recast.recast_state, state["id"])

    def sheets(self, state, scene):
        """Each character's face sheet, made from its confirmed picture by the live runner, to the state asked."""
        world = self.get()
        for index, wanted in enumerate(scene.sheets):
            if wanted is None:
                continue
            deadline = time.monotonic() + 60
            while True:
                self.feed()
                state = world.invoke(recast.recast_state, state["id"])
                reference = (state["characters"][index]["target"] or {}).get(
                    "reference"
                )
                if reference and reference["state"] == "waiting":
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError(f"The face sheet did not wait: {reference}")
                time.sleep(0.1)
            command = {
                "approved": recast.approve_reference,
                "rejected": recast.reject_reference,
            }.get(wanted)
            if command:
                state = world.invoke(
                    command,
                    state["id"],
                    state["characters"][index]["id"],
                    expected_revision=state["revision"],
                )
        return state

    def links(self, state, scene):
        """Each link's person: a track with no face in the shot's first window analysis, proposed by the analysis with
        its evidence and, if asked, confirmed."""
        from gaiavfx.application import recast_projection as p

        world = self.get()
        for link in scene.links:
            with world.db.transaction() as session:
                stored = p.store.load(session, state["id"])["shots"][link.shot]
            operation = stored["window_analysis_ops"][0]
            window = world.window_reports[operation]
            window["tracks"].append(
                {
                    **window["tracks"][0],
                    "id": link.track,
                    "identity": {"film_tracks": {}, "margin": None},
                    "identity_margin": None,
                }
            )
            world.touch_window(operation)
            character = state["characters"][link.character]["id"]
            shot = stored["shot_id"]
            state = world.invoke(recast.recast_state, state["id"])
            state = world.invoke(
                recast.propose_appearance,
                state["id"],
                shot,
                operation,
                link.track,
                character,
                expected_revision=state["revision"],
                proposed_by="analysis",
                evidence={
                    **link.evidence,
                    "candidates": [
                        {"character_id": character, **candidate}
                        for candidate in link.evidence.get("candidates", [])
                    ],
                },
            )
            if link.state == "confirmed":
                state = world.invoke(
                    recast.confirm_appearance,
                    state["id"],
                    shot,
                    operation,
                    link.track,
                    character,
                    expected_revision=state["revision"],
                )
        return state

    def target(self, recast_id: str, order: Picture):
        world = self.get()
        tag = uuid.uuid4().hex[:8]
        state = world.invoke(recast.recast_state, recast_id)
        picture = self.picture(
            f"{'Arin' if order.kind == 'confirmed' else 'Hallway'} · {tag}",
            tag,
            face={"faceless": False, "unreadable": None}.get(order.kind, True),
        )
        world.invoke(
            recast.set_target,
            recast_id,
            state["characters"][order.character]["id"],
            expected_revision=state["revision"],
            asset_id=picture,
            label="Arin" if order.kind == "confirmed" else "Hallway",
        )
        self.checked(recast_id, order.character)
        return world.invoke(recast.recast_state, recast_id)

    def checked(self, recast_id, index, timeout=30):
        """Wait until the character's picture has its face check."""
        world = self.get()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            target = world.invoke(recast.recast_state, recast_id)["characters"][index][
                "target"
            ]
            if target and target.get("face") not in {None, "checking"}:
                return
            time.sleep(0.05)
        raise RuntimeError("The target picture's face check did not finish.")

    def render(self, state, shots, takes):
        """A finished Draft run over the first `shots` shots, run `takes` times: each shot has that many versions."""
        world = self.get()
        end = state["shots"][shots - 1]["end"]
        for take in range(takes):
            if take:
                for shot in state["shots"][:shots]:
                    state = world.invoke(
                        recast.retake_shot,
                        state["id"],
                        shot["id"],
                        expected_revision=state["revision"],
                        mode="draft",
                    )
            else:
                state = world.invoke(
                    recast.start_run,
                    state["id"],
                    expected_revision=state["revision"],
                    mode="draft",
                    range={"start": 0, "end": end},
                    render_budget=10 * shots * takes,
                )
            for _ in range(12):
                self.feed()
                for kind in ("character_replacement", "shot_assembly"):
                    with world.db.transaction() as session:
                        for operation in world.ops.recent(
                            session, kind=kind, limit=500
                        ):
                            world.settle(session, operation["id"])
                    self.feed()
                state = world.invoke(recast.recast_state, state["id"])
                if all(
                    s["status"] not in {"rendering", "queued", "analysing", "planned"}
                    for s in state["shots"][:shots]
                ):
                    break
        return state

    def feed(self):
        """Feed every recast until nothing changes, as the runner's timer would."""
        world = self.get()
        for _ in range(8):
            if not world.invoke(recast.feed):
                break

    def settle(self, recast_id: str, order: Settle):
        world = self.get()
        with world.db.transaction() as session:
            waiting = [
                o
                for o in reversed(world.ops.recent(session, kind=order.kind, limit=500))
                if any(j["state"] in {"queued", "claimed"} for j in o["jobs"])
                and (order.head is None or bool(o["request"].get("head")) == order.head)
            ]
            for operation in waiting[: order.count]:
                if order.kind == "recast_conform" and order.outcome == "succeeded":
                    # A finished conform's film: the recast's own source stands in for it.
                    job = operation["jobs"][0]
                    world.move(session, job, "claimed", "running", "collecting")
                    film = operation["request"]["source_asset"]
                    world.ops.record_outputs(
                        session, job["id"], [{"asset_id": film, "progress": 1}]
                    )
                    world.ops.transition(session, job["id"], "succeeded")
                    continue
                world.settle(session, operation["id"], order.outcome)
        self.feed()
        return world.invoke(recast.recast_state, recast_id)

    def retire_film(self, asset_id: str):
        """Retire every recast of a Library film (the one the page created, when the spec seeded none)."""
        recasts = Base.metadata.tables["editing_recasts"]
        with self.get().db.transaction() as session:
            ids = session.execute(
                recasts.select().where(recasts.c.source_asset_id == asset_id)
            ).fetchall()
        for row in ids:
            self.retire(row.id)

    def retire(self, recast_id: str):
        """Remove the recast's Editing rows so later specs' lists and feeds stay small."""
        world = self.get()
        tables = [
            t
            for t in Base.metadata.sorted_tables
            if t.name.startswith("editing_recast_") and "recast_id" in t.c
        ]
        with world.db.transaction() as session:
            for table in reversed(tables):
                session.execute(delete(table).where(table.c.recast_id == recast_id))
            recasts = Base.metadata.tables["editing_recasts"]
            session.execute(delete(recasts).where(recasts.c.id == recast_id))


def held(advance):
    """An advancer that leaves an operation of fixture jobs to the spec."""

    def advance_unless_fixture(session, operation, *, library):
        if operation["jobs"] and all(
            j["integration"] == "fixture" for j in operation["jobs"]
        ):
            return None
        return advance(session, operation, library=library)

    return advance_unless_fixture


def parked(start):
    """A conform start whose local job waits for the spec, as fixture jobs do: a world's versions paste no pixels."""

    def start_and_park(session, library, *args, **kwargs):
        result = start(session, library, *args, **kwargs)
        if result.get("_created", True):
            for job in result["jobs"]:
                if job["state"] == "queued":
                    OperationService().transition(session, job["id"], "claimed")
        return result

    return start_and_park


def install(app):
    browser = BrowserWorld(app)
    recast.start_conform = parked(recast.start_conform)
    for kind, advance in list(operations.ADVANCERS.items()):
        operations.ADVANCERS[kind] = held(advance)
    operations.SWEEPS[:] = [
        s for s in operations.SWEEPS if s is not operations.sweep_recasts
    ]

    @app.post("/api/test/recast-world")
    def seed_world(scene: Scene):
        return browser.seed(scene)

    @app.post("/api/test/recast-world/{recast_id}/settle")
    def settle_world(recast_id: str, order: Settle):
        return browser.settle(recast_id, order)

    @app.post("/api/test/recast-world/{recast_id}/target")
    def target_world(recast_id: str, order: Picture):
        return browser.target(recast_id, order)

    @app.post("/api/test/recast-world/{recast_id}/feed")
    def feed_world(recast_id: str):
        browser.feed()
        return browser.get().invoke(recast.recast_state, recast_id)

    @app.delete("/api/test/recast-world/films/{asset_id}")
    def retire_film(asset_id: str):
        browser.retire_film(asset_id)
        return {"retired": asset_id}

    @app.delete("/api/test/recast-world/{recast_id}")
    def retire_world(recast_id: str):
        if browser.world is None:
            raise HTTPException(404, "No Recast world.")
        browser.retire(recast_id)
        return {"retired": recast_id}

    return browser


def answer(graph, uploads):
    """The fixture ComfyUI's answer to a target face scan, or None for any other graph."""
    if not any(node.get("class_type") == "GaiaFaceScan" for node in graph.values()):
        return None
    name = graph["faces"]["inputs"]["video"]
    return face_scan_answer(uploads[name])
