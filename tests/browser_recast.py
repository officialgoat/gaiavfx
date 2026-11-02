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
import traceback
import uuid
import wave
from pathlib import Path
from typing import Literal

import numpy as np
import pytest
from fastapi import HTTPException
from gaiavfx.application import (
    operations,
    recast,
    recast_lines,
    recast_voice,
)
from gaiavfx.infrastructure.database import Base
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.assets import AssetRecord
from PIL import Image, ImageDraw
from pydantic import BaseModel, Field
from recast_world_support import (
    STOPPED,
    RecastWorld,
    audition,
    drive,
    feed_writes,
    film_report,
    install_dialogue,
    install_voice_builds,
    succeed,
)
from sqlalchemy import delete, select

MEDIA = Path(__file__).resolve().parents[1] / ".cache" / "recast-world"


class Check(BaseModel):
    """One check a version was measured with, as a replacement's checks record it."""

    check: str
    verdict: Literal["pass", "fail", "unsupported"]
    calibrated: bool
    measured: float | None
    reason: str


# What a version's checks say when a scene names none: an uncalibrated identity score and an unsupported seam check.
CHECKS = [
    Check(
        check="identity",
        verdict="pass",
        calibrated=False,
        measured=0.87,
        reason="Identity scoring is not calibrated.",
    ),
    Check(
        check="seam",
        verdict="unsupported",
        calibrated=False,
        measured=None,
        reason="Window seams have no qualified check.",
    ),
]


class Run(BaseModel):
    """A run the scene leaves running, paused or cancelled (idle again), over a range of frames (None: the whole
    film), with its work waiting."""

    state: Literal["running", "paused", "cancelled"] = "running"
    mode: Literal["draft", "final"] = "draft"
    range: dict | None = None
    budget: int = 20


class Recording(BaseModel):
    """A Library recording of a target voice, `seconds` long; the world names it `<name>-<tag>.wav`."""

    name: str
    seconds: float


class Voice(BaseModel):
    """A character's voice from the scene's `recordings` (by index), chosen by the user or proposed by Gaia, and its
    build left `building`, `failed` (it stopped while training) or `waiting` (its audition reel waits for review)."""

    character: int = 0
    recordings: list[int]
    proposed: bool = False
    build: Literal["building", "failed", "waiting"] | None = None


class Link(BaseModel):
    """A person the shot's window analysis found with no face, linked to a character: proposed by the analysis with
    its evidence, or then confirmed by the user."""

    shot: int
    character: int
    state: Literal["proposed", "confirmed"] = "proposed"
    track: str = "back"
    evidence: dict = {}


class Scene(BaseModel):
    """The recast a film spec starts from: `shots` shots of `length` frames, or the shots `cuts` names (first and end
    frame of each, in order). Characters split the film's shots between them in turn. Each character's
    entry in `targets` is its picture: `confirmed` (the user's portrait), `proposed` (Gaia's portrait), `faceless` (the
    user's picture with no face) or None; `labels` name them, and with `shared` every character's picture is the same
    Library picture. `similar` pairs of characters look alike; `hidden` characters are set aside; `unknown` face tracks
    belong to no character (from Shot 003 on). `sheets` take a character's face sheet to waiting, approved or rejected.
    `faceless` characters have no face crop yet.
    `rendered` shots from the start get a finished Draft run's versions, `takes` of them, and `retakes` more on the
    shots it names; `checks` are each take's checks (the last repeats). `states` hold a shot back: a planner `gap`, a
    `failed` render, or one still `rendering` until the spec settles it. A scene's `run` is left running, paused or
    cancelled with its work waiting; with `pending_windows`, every shot planned in the film waits for its window
    analyses (settle `person_analysis`). A planned shot's `links` join a faceless person to a character. `pictures` are more Library portraits, by name.
    The film's `audio` is mono, none or six channels. Its `dialogue` analysis is left running, succeeded or failed;
    the user `confirmed` the proposed lines of the characters it names (journaled, so they stay on the undo stack).
    `recordings` are Library recordings, and `voices` the characters' voices made from them; with `expressive`, an
    expressive recordings folder (clips per category) is set in Settings, and with `expressive_changed` the voices
    were built from an earlier folder."""

    shots: int = Field(8, ge=1, le=2000)
    length: int = Field(48, ge=8)
    cuts: list[tuple[int, int]] = []
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
    takes: int = Field(1, ge=1, le=6)
    retakes: dict[int, int] = {}
    states: dict[int, Literal["gap", "failed", "rendering"]] = {}
    run: Run | None = None
    pending_windows: bool = False
    checks: list[list[Check]] = [CHECKS]
    links: list[Link] = []
    pictures: list[str] = []
    audio: Literal["mono", "none", "six"] = "mono"
    dialogue: Literal["running", "succeeded", "failed"] | None = None
    confirmed: list[int] = []
    recordings: list[Recording] = []
    voices: list[Voice] = []
    expressive: dict[str, int] = {}
    expressive_changed: bool = False


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


def ffmpeg(args):
    """FFmpeg with these arguments, quiet and away from the service's own stdin."""
    subprocess.run(
        [str(MediaTools().ffmpeg), "-nostdin", "-loglevel", "error", *args],
        check=True,
        stdin=subprocess.DEVNULL,
    )


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
        self.voice_builds = None
        # Recasts whose scene set Settings' expressive folder.
        self.expressive_worlds = set()
        # Film asset id -> the Library assets imported for its world, the film among them; and the film importing now.
        self.imports = {}
        self.importing = None
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
                dialogue = install_dialogue(self.world, self.patcher)
                # An audition cuts its lines from the dialogue stem (test_recast_voice's `heard`).
                dialogue.fake.stem_artifact = lambda operation: {
                    "artifact_id": "hidden-stem",
                    "sha256": "stem-sha",
                }
                self.voice_builds = install_voice_builds(self.world, self.patcher)
                self.voice_builds.fake.retry_stopped = resumed(self.world)
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
            ffmpeg(
                [
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
                ]
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
        frames = scene.cuts[-1][1] if scene.cuts else scene.shots * scene.length
        rate = f"{scene.rate[0]}/{scene.rate[1]}"
        # The soundtrack lasts as long as the picture, so neither waits for the other to end.
        seconds = frames * scene.rate[1] / scene.rate[0]
        sine = f"sine=frequency=220:sample_rate=48000:duration={seconds}"
        audio = {
            "mono": ["-f", "lavfi", "-i", sine],
            "six": ["-f", "lavfi", "-i", sine],
            "none": [],
        }[scene.audio]
        codecs = {
            "mono": ["-c:a", "aac"],
            "six": ["-c:a", "aac", "-ac", "6"],
            "none": ["-an"],
        }[scene.audio]

        def make(path):
            ffmpeg(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    f"testsrc2=size=320x180:rate={rate}",
                    *audio,
                    "-frames:v",
                    str(frames),
                    *codecs,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-pix_fmt",
                    "yuv420p",
                    str(path),
                ]
            )

        sounds = "" if scene.audio == "mono" else f"-{scene.audio}"
        original = self.media(
            f"film-{frames}-{scene.rate[0]}-{scene.rate[1]}{sounds}.mp4", make
        )
        unique = MEDIA / f"film-{tag}.mp4"
        ffmpeg(
            [
                "-y",
                "-i",
                str(original),
                "-c",
                "copy",
                "-metadata",
                f"comment={tag}",
                str(unique),
            ]
        )
        return unique

    def imported(self, path, name):
        """A file this world wrote, in the Library, which keeps its own copy: the world's goes. The film's world
        keeps the asset's id, so retiring it leaves the Library as it found it."""
        asset = self.workspace.import_media(path, name)["asset"]
        path.unlink(missing_ok=True)
        if self.importing:
            self.imports.setdefault(self.importing, []).append(asset["id"])
        return asset

    def picture(self, name, tag, face=True):
        """A Library picture unique to this world: a portrait, or a flat picture with no face."""
        pictures = MEDIA / "pictures"
        pictures.mkdir(parents=True, exist_ok=True)
        path = portrait(pictures / f"{tag}.png", int(tag, 16), face, mark=tag)
        return self.imported(path, name + ".png")["id"]

    def wav(self, path, seconds, tag):
        """A 16 kHz tone `seconds` long; the tag's bytes open its samples, so two worlds never share a recording."""
        frames = int(16000 * seconds)
        samples = (
            (np.sin(np.arange(frames) * 2 * np.pi * 180 / 16000) * 8000)
            .astype("<i2")
            .tobytes()
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            out.writeframes(tag.encode() + samples[len(tag.encode()) :])
        return path

    def recording(self, name, seconds, tag):
        """A Library recording unique to this world."""
        named = f"{name}-{tag}.wav"
        path = self.wav(MEDIA / "recordings" / named, seconds, tag)
        asset = self.imported(path, named)
        return {"id": asset["id"], "name": asset["name"]}

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
        film_id = self.imported(self.film(scene, tag), name + ".mp4")["id"]
        self.imports.setdefault(film_id, []).append(film_id)
        self.importing = film_id
        try:
            return self.seeded(scene, world, film_id, name, tag)
        finally:
            self.importing = None

    def seeded(self, scene, world, film_id, name, tag):
        """The rest of a seed, once the film is in the Library: its analysis, cast, plans, renders and the rest."""
        report = film_report(film_id, len(scene.cuts) or scene.shots, scene.length)
        report["fps"] = list(scene.rate)
        for shot, track, (start, end) in zip(
            report["shots"], report["tracks"], scene.cuts
        ):
            shot["start"], shot["end"] = start, end
            track["frames"] = list(range(start, end, 5))
        report["frame_count"] = report["shots"][-1]["end"]
        span = {s["index"] - 1: s["end"] - s["start"] for s in report["shots"]}
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
                    span[t["shot"]] for t in report["tracks"] if owners[t["id"]] == k
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
            shot = report["shots"][min(2 + k, len(report["shots"]) - 1)]
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
            # Two dozen faces, each registered once, however long the film.
            faces = {}
            for track in report["tracks"]:
                index = track["shot"]
                # A `faceless` character's tracks have no face crop yet.
                if owners.get(track["id"]) in scene.faceless:
                    continue
                key = index % 24
                if key not in faces:
                    face = portrait(crops / f"face-{key}.jpg", key * 37)
                    faces[key] = self.artifact(session, face)
                track["crop_artifact"] = faces[key]
                report["shots"][index]["poster_artifact"] = faces[key]
            for cluster in report["clusters"]:
                first = next(
                    t for t in report["tracks"] if t["id"] == cluster["track_ids"][0]
                )
                cluster["face_artifact"] = first["crop_artifact"]
        world.add_film(film_id, report)
        recordings = [
            self.recording(r.name, r.seconds, f"{tag}{i:02x}")
            for i, r in enumerate(scene.recordings)
        ]
        if not scene.recast:
            # A Library film the user has not opened yet: opening it asks before its analysis creates the recast.
            return {
                "id": "",
                "title": name,
                "film": {"asset_id": film_id, "name": name + ".mp4"},
                "shots": [],
                "characters": [],
                "pictures": [],
                "recordings": recordings,
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
            if scene.pending_windows:
                world.held_windows.add(film_id)
            if scene.planned or scene.rendered or scene.run:
                state = world.invoke(
                    recast.plan_recast,
                    state["id"],
                    expected_revision=state["revision"],
                    range=None,
                )
                state = self.gaps(state, scene)
            if scene.rendered:
                state = self.render(state, scene.rendered, scene.takes, scene.states)
                for index, times in scene.retakes.items():
                    state = self.retake(state, index, times)
                self.measure(state, scene.checks)
            if scene.run:
                state = self.run(state, scene.run)
            state = self.links(state, scene)
            state = self.dialogue(state, scene)
            state = self.voices(state, scene, recordings, tag)
            # The scene is where the spec starts, not a history: its cast edits leave no entries to undo.
            edits = Base.metadata.tables["editing_recast_edits"]
            with world.db.transaction() as session:
                session.execute(delete(edits).where(edits.c.recast_id == state["id"]))
            # The user's line decisions stay on the film's one undo stack.
            for index in scene.confirmed:
                state = world.invoke(
                    recast_lines.confirm_lines,
                    state["id"],
                    expected_revision=state["revision"],
                    character_id=state["characters"][index]["id"],
                )
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
            "recordings": recordings,
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

    def dialogue(self, state, scene):
        """The film's dialogue analysis, asked for by the user and left in the scene's state."""
        world = self.get()
        if scene.dialogue is None:
            return state
        state = world.invoke(
            recast_lines.start_dialogue_analysis,
            state["id"],
            expected_revision=state["revision"],
        )
        if scene.dialogue != "running":
            with world.db.transaction() as session:
                world.settle(session, state["dialogue"]["operation_id"], scene.dialogue)
        return world.invoke(recast.recast_state, state["id"])

    def expressive(self, counts, name):
        """An expressive recordings folder: a subfolder per category with `count` clips (the scan reads no audio)."""
        folder = MEDIA / "expressive" / name
        for category, count in counts.items():
            (folder / category).mkdir(parents=True, exist_ok=True)
            for index in range(count):
                (folder / category / f"{category}-{index:02d}.wav").write_bytes(
                    f"{name} {category} {index}".encode()
                )
        return str(folder)

    def use_expressive(self, recast_id, folder):
        """Settings' expressive recordings folder; the world's retirement clears it again."""
        settings = self.workspace.settings()
        self.workspace.save_settings({**settings, "expressive_library": folder})
        self.expressive_worlds.add(recast_id)

    def voices(self, state, scene, recordings, tag):
        """Each voice's recordings, chosen by the user or proposed by Gaia, and its build taken to the state asked:
        building, stopped while training, or built with its audition reel waiting for review."""
        world = self.get()
        folder = ""
        if scene.expressive:
            folder = self.expressive(scene.expressive, f"{tag}")
            if scene.expressive_changed:
                # Built from an earlier folder with one clip fewer; Settings then names the current one.
                first, *_ = scene.expressive
                earlier = {**scene.expressive, first: scene.expressive[first] - 1}
                built = self.expressive(earlier, f"{tag}-earlier")
                self.use_expressive(state["id"], built)
            else:
                self.use_expressive(state["id"], folder)
        for order in scene.voices:
            character = state["characters"][order.character]
            ids = [recordings[i]["id"] for i in order.recordings]
            if order.proposed:
                state = world.invoke(
                    recast_voice.propose_voice,
                    state["id"],
                    character["id"],
                    expected_revision=state["revision"],
                    asset_ids=ids,
                    proposed_by="agent",
                    user_message=f"Use these recordings for {character['label']}.",
                )
            else:
                state = world.invoke(
                    recast_voice.set_voice,
                    state["id"],
                    character["id"],
                    expected_revision=state["revision"],
                    asset_ids=ids,
                )
            if order.build is None:
                continue
            state = world.invoke(
                recast_voice.build_voice,
                state["id"],
                character["id"],
                expected_revision=state["revision"],
                expressive_library=self.workspace.settings()["expressive_library"],
            )
            build = self.build_of(ids)
            if order.build == "failed":
                drive(world, build["id"], ["succeeded", "failed"])
            elif order.build == "waiting":
                # What the voice feed does once the build succeeds: its audition, finished with a reel.
                succeed(world, build["id"])
                heard = audition(world, state, character["id"], build)
                self.voice_builds.reels[heard["id"]] = self.reel(heard["id"], tag)
                feed_writes(
                    world,
                    state,
                    character["id"],
                    voice_model_state="waiting",
                    voice_model_artifact_id="hidden-artifact-" + build["id"],
                )
            state = world.invoke(recast.recast_state, state["id"])
        if scene.expressive_changed:
            self.use_expressive(state["id"], folder)
        return world.invoke(recast.recast_state, state["id"])

    def build_of(self, asset_ids):
        """The voice build of these recordings, found by their content as the build module finds it."""
        world = self.get()
        with world.db.transaction() as session:
            shas = [world.library.get(session, i)["sha256"] for i in asset_ids]
            operation, _ = self.voice_builds.fake.latest(session, shas)
        return operation

    def reel(self, operation_id, tag):
        """An audition reel, a Library candidate of the audition that made it."""
        path = self.wav(MEDIA / "recordings" / f"audition-{tag}.wav", 9.0, tag)
        asset_id = self.imported(path, "audition.wav")["id"]
        with self.get().db.transaction() as session:
            session.get(AssetRecord, asset_id).provenance = [
                {"type": "generation_candidate", "operation_id": operation_id}
            ]
        return asset_id

    def target(self, recast_id: str, order: Picture):
        world = self.get()
        tag = uuid.uuid4().hex[:8]
        state = world.invoke(recast.recast_state, recast_id)
        self.importing = state["film"]["asset_id"]
        try:
            picture = self.picture(
                f"{'Arin' if order.kind == 'confirmed' else 'Hallway'} · {tag}",
                tag,
                face={"faceless": False, "unreadable": None}.get(order.kind, True),
            )
        finally:
            self.importing = None
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

    def run(self, state, order: Run):
        """Start a run and leave it as the scene says; its windows' work waits for the spec."""
        world = self.get()
        state = world.invoke(recast.recast_state, state["id"])
        state = world.invoke(
            recast.start_run,
            state["id"],
            expected_revision=state["revision"],
            mode=order.mode,
            range=order.range,
            render_budget=order.budget,
        )
        self.feed()
        state = world.invoke(recast.recast_state, state["id"])
        command = {"paused": recast.pause_run, "cancelled": recast.cancel_run}.get(
            order.state
        )
        if command:
            state = world.invoke(
                command, state["id"], expected_revision=state["revision"]
            )
        return state

    def gaps(self, state, scene):
        """A `gap` shot's window analysis tells the planner who it cannot tell apart, so its plan has a gap."""
        from gaiavfx.application import recast_projection as p

        world = self.get()
        with world.db.transaction() as session:
            stored = p.store.load(session, state["id"])["shots"]
        for index, kind in scene.states.items():
            if kind != "gap":
                continue
            for operation in stored[index]["window_analysis_ops"]:
                world.window_reports[operation]["gaps"] = [
                    {
                        "code": "identity_conflict",
                        "message": "Confirm who this person is before rendering.",
                    }
                ]
                world.touch_window(operation)
        return world.invoke(recast.recast_state, state["id"])

    def shot_of(self, session, operation):
        """The index of the shot a window's or an assembly's work belongs to."""
        world = self.get()
        request = operation["request"]
        analysis = request.get("analysis")
        if analysis is None:
            window = world.ops.operation(session, request["window_operations"][0])
            analysis = window["request"]["analysis"]
        start = world.window_reports[analysis]["shot_start"]
        shots = world.film_of(request["source_asset"]).report["shots"]
        return next(s["index"] for s in shots if s["start"] <= start < s["end"])

    def render(self, state, shots, takes, states=None):
        """A finished Draft run over the first `shots` shots, run `takes` times: each shot has that many versions. A
        `failed` shot's renders fail and a `rendering` one's wait (`states`)."""
        world = self.get()
        film = state["film"]["asset_id"]
        states = states or {}
        held = {i for i, kind in states.items() if kind in {"rendering", "gap"}}
        end = state["shots"][shots - 1]["end"]
        for take in range(takes):
            if take:
                for index, shot in enumerate(state["shots"][:shots]):
                    # A held, gapped, failed or unmapped shot has no take to repeat.
                    if index in states or not shot["versions"]:
                        continue
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
            # The feed renders two shots at a time: a round settles their windows, then their assemblies, and a
            # shot of two performers takes more rounds.
            for _ in range(12 + 2 * shots):
                self.feed()
                for kind in ("character_replacement", "shot_assembly"):
                    with world.db.transaction() as session:
                        for operation in world.ops.recent(
                            session, kind=kind, limit=500
                        ):
                            # Only this film's renders: earlier specs leave theirs, head passes among them.
                            if operation["request"].get("source_asset") != film:
                                continue
                            index = self.shot_of(session, operation)
                            if states.get(index) == "rendering":
                                continue
                            outcome = (
                                "failed"
                                if states.get(index) == "failed"
                                and kind == "character_replacement"
                                else "succeeded"
                            )
                            world.settle(session, operation["id"], outcome)
                    self.feed()
                state = world.invoke(recast.recast_state, state["id"])
                busy = {
                    s["index"] + 1: s["status"]
                    for i, s in enumerate(state["shots"][:shots])
                    if i not in held
                    and s["status"] in {"rendering", "queued", "analysing", "planned"}
                }
                if not busy:
                    break
            else:
                raise RuntimeError(f"Take {take + 1} did not finish rendering: {busy}")
        return state

    def retake(self, state, index, times):
        """`times` more finished Draft takes of one shot."""
        world = self.get()
        film = state["film"]["asset_id"]
        shot = state["shots"][index]["id"]
        for _ in range(times):
            state = world.invoke(recast.recast_state, state["id"])
            state = world.invoke(
                recast.retake_shot,
                state["id"],
                shot,
                expected_revision=state["revision"],
                mode="draft",
            )
            for _ in range(12):
                self.feed()
                for kind in ("character_replacement", "shot_assembly"):
                    with world.db.transaction() as session:
                        for operation in world.ops.recent(
                            session, kind=kind, limit=500
                        ):
                            if operation["request"].get("source_asset") == film:
                                world.settle(session, operation["id"])
                    self.feed()
                state = world.invoke(recast.recast_state, state["id"])
                if state["shots"][index]["status"] not in {"rendering", "queued"}:
                    break
            else:
                raise RuntimeError(
                    f"Shot {index + 1}'s retake did not finish rendering."
                )
        return state

    def measure(self, state, checks):
        """Each shot's versions carry their take's checks, in the order the shot's takes were made."""
        from gaiavfx.application import recast_projection as p

        world = self.get()
        with world.db.transaction() as session:
            stored = p.store.load(session, state["id"])
            for shot in stored["shots"]:
                for take, assembly in enumerate(p.assemblies(session, shot)):
                    for version in world.versions.get(assembly, []):
                        version["checks"] = [
                            c.model_dump() for c in checks[min(take, len(checks) - 1)]
                        ]

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
                if order.kind == "person_analysis" and order.outcome == "succeeded":
                    # A held window analysis finishes with the report the world wrote for it.
                    world.succeed_with(
                        session, operation["jobs"][0], "people", operation["id"]
                    )
                    continue
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
        self.forget(asset_id)

    def forget(self, film_id):
        """Remove the Library assets a film's world imported, the film among them, and the face portraits and sheets
        the recast feed made from its pictures: nothing but the world's recast, retired first, refers to them."""
        imported = self.imports.pop(film_id, [])
        if not imported:
            return
        operations = Base.metadata.tables["generation_operations"]
        candidates = Base.metadata.tables["generation_candidates"]
        assets = AssetRecord.__table__
        with self.get().db.transaction() as session:
            made = (
                session.execute(
                    select(candidates.c.asset_id)
                    .join(operations, operations.c.id == candidates.c.operation_id)
                    .where(
                        operations.c.request["target_asset"].as_string().in_(imported)
                    )
                )
                .scalars()
                .all()
            )
            session.execute(delete(candidates).where(candidates.c.asset_id.in_(made)))
            session.execute(delete(assets).where(assets.c.id.in_([*imported, *made])))

    def retire(self, recast_id: str):
        """Remove the recast's Editing rows so later specs' lists and feeds stay small, the Library assets its world
        imported, and an expressive folder its scene set."""
        world = self.get()
        if recast_id in self.expressive_worlds:
            self.expressive_worlds.discard(recast_id)
            self.workspace.save_settings(
                {**self.workspace.settings(), "expressive_library": ""}
            )
        tables = [
            t
            for t in Base.metadata.sorted_tables
            if t.name.startswith("editing_recast_") and "recast_id" in t.c
        ]
        recasts = Base.metadata.tables["editing_recasts"]
        with world.db.transaction() as session:
            film = session.execute(
                recasts.select().where(recasts.c.id == recast_id)
            ).first()
            for table in reversed(tables):
                session.execute(delete(table).where(table.c.recast_id == recast_id))
            session.execute(delete(recasts).where(recasts.c.id == recast_id))
        if film:
            self.forget(film.source_asset_id)


def resumed(world):
    """A stopped voice build's retry, as the build module resumes one: a replacement for each stopped job, in the
    same operation, waiting for the spec."""

    def retry_stopped(session, operation):
        stopped = [j for j in operation["jobs"] if j["state"] in STOPPED]
        world.ops.resume(
            session,
            operation["id"],
            {"retry": "stopped"},
            jobs=[
                JobSpec("analysis", "local", "fixture", step=j["step"]) for j in stopped
            ],
        )
        for job in world.ops.operation(session, operation["id"])["jobs"]:
            if job["state"] == "queued":
                world.ops.transition(session, job["id"], "claimed")
        return world.ops.operation(session, operation["id"])

    return retry_stopped


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
        try:
            return browser.seed(scene)
        except Exception as error:
            # Why the seed failed, in the spec's failure: the world's own account, or where the service refused.
            raise HTTPException(
                500, "".join(traceback.format_exception(error)[-4:])
            ) from error

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
