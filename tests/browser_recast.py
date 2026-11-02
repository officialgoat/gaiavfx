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


class Scene(BaseModel):
    """The recast a film spec starts from. Characters split the film's shots between them in turn. Each character's
    entry in `targets` is its picture: `confirmed` (the user's portrait), `proposed` (Gaia's portrait), `faceless` (the
    user's picture with no face) or None. `similar` pairs of characters look alike; `rendered` shots from the start get
    a finished Draft run's versions, `takes` of them."""

    shots: int = Field(8, ge=1, le=2000)
    length: int = Field(48, ge=8)
    rate: tuple[int, int] = (24, 1)
    characters: list[str] = ["Mara"]
    title: str | None = None
    analysed: bool = True
    targets: list[Literal["confirmed", "proposed", "faceless"] | None] = ["confirmed"]
    similar: list[tuple[int, int]] = []
    planned: bool = False
    rendered: int = 0
    takes: int = Field(1, ge=1, le=4)


class Settle(BaseModel):
    """End the waiting jobs of the world's operations of one kind, then feed."""

    kind: str
    outcome: str = "succeeded"


def portrait(path: Path, seed: int, face=True):
    """A small picture; a portrait has a light face the fixture face scan finds, a faceless one is flat."""
    image = Image.new("RGB", (192, 256), (40 + seed % 160, 60, 90))
    if face:
        ImageDraw.Draw(image).ellipse((56, 48, 136, 152), fill=(222, 190, 170))
    image.save(path, "JPEG", quality=85)
    return path


def faces_in(data: bytes):
    """The fixture face scan: one centred face in a picture that is not flat, none in a flat one."""
    with Image.open(io.BytesIO(data)) as image:
        grey = np.asarray(image.convert("L"), dtype=np.float32)
    if grey.std() < 4:
        return []
    height, width = grey.shape
    return [(0.91, (width * 0.3, height * 0.2, width * 0.7, height * 0.6))]


def face_scan_answer(data: bytes):
    faces = faces_in(data)
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
                # Versions show real previews whose names the page must never show (the blind checks).
                self.world.previews = [
                    self.workspace.import_media(
                        self.preview(i), f"PRIVATE SEED ARM ROUND {i}.mp4"
                    )["asset"]["id"]
                    for i in range(4)
                ]
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
            make(original)
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
        path = portrait(pictures / f"{tag}.jpg", int(tag, 16), face)
        return self.workspace.import_media(path, name + ".jpg")["asset"]["id"]

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
        report["clusters"] = [
            {
                "id": f"c{k + 1:02d}",
                "track_ids": [
                    t["id"] for t in report["tracks"] if t["shot"] % count == k
                ],
                "screen_time_frames": sum(
                    scene.length for t in report["tracks"] if t["shot"] % count == k
                ),
                "shot_count": sum(
                    1 for t in report["tracks"] if t["shot"] % count == k
                ),
                "presence": [
                    [s["start"], s["end"]]
                    for s in report["shots"]
                    if s["index"] % count == k
                ],
                "face_artifact": None,
            }
            for k in range(count)
        ]
        report["suggestions"] = [
            {"a": f"c{a + 1:02d}", "b": f"c{b + 1:02d}", "max_similarity": 0.88}
            for a, b in scene.similar
        ]
        crops = MEDIA / "crops"
        crops.mkdir(parents=True, exist_ok=True)
        with world.db.transaction() as session:
            for track in report["tracks"]:
                index = track["shot"]
                face = portrait(crops / f"face-{index % 24}.jpg", index * 37)
                track["crop_artifact"] = self.artifact(session, face)
                report["shots"][index]["poster_artifact"] = track["crop_artifact"]
            for cluster in report["clusters"]:
                first = next(
                    t for t in report["tracks"] if t["id"] == cluster["track_ids"][0]
                )
                cluster["face_artifact"] = first["crop_artifact"]
        world.add_film(film_id, report)
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
            for character, label in zip(state["characters"], scene.characters):
                state = world.invoke(
                    recast.patch_character,
                    state["id"],
                    character["id"],
                    expected_revision=state["revision"],
                    label=label,
                    hidden=False,
                )
            state = self.cast(state, scene, tag)
            if scene.planned or scene.rendered:
                state = world.invoke(
                    recast.plan_recast,
                    state["id"],
                    expected_revision=state["revision"],
                    range=None,
                )
            if scene.rendered:
                state = self.render(state, scene.rendered, scene.takes)
            # The scene is where the spec starts, not a history: its cast edits leave no entries to undo.
            edits = Base.metadata.tables["editing_recast_edits"]
            with world.db.transaction() as session:
                session.execute(delete(edits).where(edits.c.recast_id == state["id"]))
        return {
            "id": state["id"],
            "title": name,
            "film": {"asset_id": film_id, "name": name + ".mp4"},
            "shots": [s["id"] for s in state["shots"]],
            "characters": [c["id"] for c in state["characters"]],
        }

    def cast(self, state, scene, tag):
        """Each character's picture, as the user or Gaia set it; a confirmed picture's face check has run."""
        world = self.get()
        commands = {"proposed": recast.propose_target}
        for index, (character, kind) in enumerate(
            zip(state["characters"], scene.targets)
        ):
            if kind is None:
                continue
            picture = self.picture(
                f"{'Arin' if kind != 'faceless' else 'Hallway'} · {tag}-{index}",
                f"{tag}{index:02x}",
                face=kind != "faceless",
            )
            state = world.invoke(
                commands.get(kind, recast.set_target),
                state["id"],
                character["id"],
                expected_revision=state["revision"],
                asset_id=picture,
                label="Arin"
                if kind == "confirmed"
                else "Arin proposal"
                if kind == "proposed"
                else "Hallway",
            )
        # The live runner checks each picture the user set on the fixture ComfyUI, as the app would.
        for index, kind in enumerate(scene.targets[: len(state["characters"])]):
            if kind not in {None, "proposed"}:
                self.checked(state["id"], index)
        return world.invoke(recast.recast_state, state["id"])

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
            for operation in world.ops.recent(session, kind=order.kind, limit=500):
                world.settle(session, operation["id"], order.outcome)
        self.feed()
        return world.invoke(recast.recast_state, recast_id)

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


def install(app):
    browser = BrowserWorld(app)
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

    @app.post("/api/test/recast-world/{recast_id}/feed")
    def feed_world(recast_id: str):
        browser.feed()
        return browser.get().invoke(recast.recast_state, recast_id)

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
