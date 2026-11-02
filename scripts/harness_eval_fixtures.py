"""Every eval family's fixture: the libraries the scripted and live campaigns run in, built through public services.

The DZ courtyard (movie-scope tasks), the replacement study, the recast study, the film's seeded library and the
stage scene. Each builder returns a fresh library under the task's path; no fixture touches a user's library or runs
inference. Generation always runs on a fake: FakeComfy for the courtyard, SyntheticReplacement for the replacement
study and set_scene's fakes for the stage.
"""

import io
import json
import struct
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from threading import Lock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "tests"))

from fake_comfy import FakeComfy
from gaiavfx.application.authored import import_segment
from gaiavfx.application.operations import OperationRunner
from gaiavfx.application.workspace import Workspace
from gaiavfx.modules.direction.service import DirectionService, last_angle
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.application import LibraryService
from gaiavfx.modules.library.media_application import MediaLibrary
from PIL import Image, ImageDraw

AUTHORED = ROOT / "backend" / "tests" / "fixtures" / "parity" / "authored"
MEDIA = ROOT / "backend" / "tests" / "fixtures" / "harness"
MIGRATIONS = ROOT / "backend" / "migrations"
direction = DirectionService()
_MIGRATION_LOCK = (
    Lock()
)  # Alembic's context proxy is process-global, even for distinct DBs.
FIXTURE_DEFAULTS = {
    "relation": "reverse",
    "place_text": True,
    "plate": True,
    "insertion": True,
    "autonomy": "prepare",
    "budget": 6,
    "second_segment": False,
    "empty_movie": False,
}


def authored(name):
    return json.loads((AUTHORED / (name + ".json")).read_text("utf-8"))


def png(color):
    stream = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(stream, "PNG")
    return stream.getvalue()


def context_bytes():
    header = json.dumps(
        {
            "__metadata__": {"format": "h3_motion_context_av_v1"},
            "video": {"shape": [1, 1, 1, 1, 1]},
            "audio": {"shape": [1, 1, 1, 1]},
        }
    ).encode()
    return struct.pack("<Q", len(header)) + header


def close(work):
    work.telemetry.close()
    work.credentials.close()
    work.db.close()


def run_idle(work, fake):
    """Drain the fixture's fake producer queue."""
    if isinstance(fake, SyntheticReplacement):
        return fake.drain(work)
    runner = OperationRunner(
        work.db, fake, work.library, "http://fake-comfy", poll_seconds=0
    )
    for _ in range(60):
        if not runner.run_once():
            return
    raise AssertionError("Fake operation queue did not drain")


# ---------------------------------------------------------------- the DZ courtyard (movie scope)


def dz_library(
    tmp_path,
    *,
    relation="reverse",
    place_text=True,
    plate=True,
    insertion=True,
    autonomy="prepare",
    budget=6,
    second_segment=False,
    empty_movie=False,
    plan_mode=False,
    attachments=(),
    no_cast=False,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "library"
    with _MIGRATION_LOCK:
        LibraryService(root, MIGRATIONS).create("Harness eval")
    work = Workspace(root)
    settings = {
        **work.settings(),
        "agent_autonomy": autonomy,
        "agent_budget_jobs": budget,
    }
    work.save_settings(settings)

    def image_asset(name, color):
        path = tmp_path / (name + ".png")
        path.write_bytes(png(color))
        prepared = work.library.prepare(path, path.name)
        with work.db.transaction() as session:
            return work.library.register(session, prepared, {"type": "import"})["id"]

    identity_path = tmp_path / "Walker.jpg"
    identity_path.write_bytes((MEDIA / "Walker.jpg").read_bytes())
    prepared = work.library.prepare(identity_path, identity_path.name)
    with work.db.transaction() as session:
        identity = work.library.register(session, prepared, {"type": "import"})["id"]
    plate_asset = image_asset("Empty courtyard", (80, 100, 120))
    video = tmp_path / "take.mp4"
    video.write_bytes((MEDIA / "take.mp4").read_bytes())
    prepared = work.library.prepare(video, video.name)
    with work.db.transaction() as session:
        take = work.library.register(session, prepared, {"type": "import"})["id"]
    # Fake producer output must cover the survey's extraction offsets (through
    # frame 102). Reference reading still receives the original three-second clip.
    produced_video = tmp_path / "fake-take.mp4"
    subprocess.run(
        [
            str(work.media.ffmpeg),
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-vf",
            "tpad=stop_mode=clone:stop_duration=3",
            "-frames:v",
            "124",
            "-c:v",
            "libx264",
            "-crf",
            "29",
            str(produced_video),
        ],
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
    )
    latent = tmp_path / "context.safetensors"
    latent.write_bytes(context_bytes())
    prepared = work.library.prepare_artifact(latent)
    with work.db.transaction() as session:
        artifact = work.library.register_artifact(session, prepared)["id"]

    def outputs_for(graph):
        classes = {node["class_type"] for node in graph.values()}
        if "TextEncodeQwenImageEditPlus" in classes:
            seed = graph["sample"]["inputs"]["seed"]
            return {"save": {"images": [{"filename": "edit.png", "type": "output"}]}}, {
                "save:images:0": png((seed % 256, 40, 60))
            }
        return {
            "save_video": {
                "images": [{"filename": "take.mp4", "type": "output"}],
                "animated": [True],
            }
        }, {
            "save_video:images:0": produced_video.read_bytes(),
            "context": context_bytes(),
        }

    fake = FakeComfy(outputs_for, polls_to_complete=1)
    survey_text = authored("SV-C2B")
    wording = {
        key: value
        for key, value in authored("IN-DZ").items()
        if key not in {"seed", "view", "subject"} and value is not None
    }
    with work.db.transaction() as session:
        movie = direction.create_movie(session, "Courtyard")
        place = direction.create_place(session, "Courtyard")
        angles = [
            direction.add_angle(session, place["id"], name)
            for name in ("gate", "along", "reverse")
        ]
        if place_text:
            direction.update_place(
                session,
                place["id"],
                1,
                description=survey_text["what"],
                sound=survey_text["sound"],
                overhead=survey_text["overhead"],
            )
            for angle, description in zip(
                angles,
                [survey_text["first_view"], *[v[1] for v in survey_text["views"]]],
                strict=False,
            ):
                direction.update_angle(session, angle["id"], 1, description=description)
        if plate:
            direction.set_plate(
                session, place["id"], asset_id=plate_asset, angle_id=angles[0]["id"]
            )
        if empty_movie and not no_cast:
            direction.add_subject(
                session,
                movie["id"],
                1,
                name="Arin",
                description="A walker",
                keeps=["plaid shirt"],
                identity_asset_id=identity,
            )
        if empty_movie:
            segment, cut = None, None
        else:
            segment_doc = deepcopy(authored("DZ")["segment"])
            segment_doc["subjects"][0]["identity_asset"] = identity
            segment = import_segment(session, movie["id"], 1, segment_doc)
            cut = direction.create_cut(
                session,
                segment["id"],
                segment["revision"],
                frame=36,
                from_angle_id=angles[0]["id"],
                to_angle_id=angles[2]["id"],
                relation=relation,
                crosses_barrier=(relation == "reverse") if relation else None,
                relation_source="authored" if relation else "unknown",
                receives_predecessor=True,
            )
            if insertion:
                cut = direction.update_cut(
                    session, cut["id"], cut["revision"], insertion=wording
                )
        second, second_cut = None, None
        if second_segment:
            second_doc = deepcopy(authored("TC")["segment"])
            second_doc["subjects"][0]["identity_asset"] = identity
            revision = direction.read_movie(session, movie["id"])["revision"]
            second = import_segment(session, movie["id"], revision, second_doc)
            second_cut = direction.create_cut(
                session,
                second["id"],
                second["revision"],
                frame=36,
                from_angle_id=angles[0]["id"],
                to_angle_id=angles[1]["id"],
                relation="same_direction",
                crosses_barrier=False,
                relation_source="authored",
                receives_predecessor=True,
            )
    return work, {
        "plan_mode": plan_mode,
        "attachments": [
            {"Walker": identity, "Empty courtyard": plate_asset, "take": take}[name]
            for name in attachments
        ],
        "movie_id": movie["id"],
        "segment_id": segment["id"] if segment else None,
        "cut_id": cut["id"] if cut else None,
        "place_id": place["id"],
        "angles": [a["id"] for a in angles],
        "take": take,
        "identity": identity,
        "plate": plate_asset,
        "artifact": artifact,
        "wording": wording,
        "fake": fake,
        "second_segment_id": second["id"] if second else None,
        "second_cut_id": second_cut["id"] if second_cut else None,
        "settings": settings,
    }


def prepare_fixture(task, work, setup):
    """Real public flows build predecessor takes and stale sheets on the fake."""
    from gaiavfx.application.candidate_review import select_take
    from gaiavfx.application.prepare_references import (
        assemble_sheet,
        sheet_tiles_for_place,
    )
    from gaiavfx.application.render_cut import open_takes_review, start_render

    with work.db.transaction() as session:
        if task.fixture.get("second_segment"):
            source_cut = setup["cut_id"]
            setup["first_segment_id"] = setup["segment_id"]
            setup["first_cut_id"] = source_cut
            setup["cut_id"] = setup["second_cut_id"]
            setup["segment_id"] = setup["second_segment_id"]
        else:
            doc = deepcopy(authored("TC")["segment"])
            doc["subjects"][0]["identity_asset"] = setup["identity"]
            movie = direction.read_movie(session, setup["movie_id"])
            first = import_segment(session, movie["id"], movie["revision"], doc, at=0)
            setup["first_segment_id"] = first["id"]
            source = direction.create_cut(
                session,
                first["id"],
                first["revision"],
                frame=36,
                from_angle_id=setup["angles"][0],
                to_angle_id=setup["angles"][1],
                relation="same_direction",
                crosses_barrier=False,
                relation_source="authored",
                receives_predecessor=True,
            )
            source_cut = source["id"]
        rendered = start_render(
            session,
            cut_id=source_cut,
            seeds=[901, 902],
            context_artifact=setup["artifact"],
        )
        setup["fixture_render"] = rendered["id"]
    run_idle(work, setup["fake"])
    with work.db.transaction() as session:
        review = open_takes_review(session, rendered["id"])
        if task.criteria.get("preselect", True):
            select_take(
                session,
                review_set_id=review["id"],
                candidate_id=review["members"][0]["candidate_id"],
                authority="user",
                expected_revision=0,
            )
        if task.criteria.get("stale_sheet"):
            evidence = []
            for angle in setup["angles"][1:]:
                row = direction.record_evidence(
                    session, angle, "imported", setup["plate"]
                )
                evidence.append(direction.approve_evidence(session, row["id"]))
            pin = direction.record_keyframe(
                session, setup["cut_id"], setup["plate"], evidence[-1]["id"]
            )
            direction.approve_keyframe(session, pin["id"])
            assemble_sheet(
                session,
                place_id=setup["place_id"],
                tiles=sheet_tiles_for_place(session, setup["place_id"]),
                origin="survey",
            )
    if task.criteria.get("stale_sheet"):
        run_idle(work, setup["fake"])
        with work.db.transaction() as session:
            row = direction.record_evidence(
                session, setup["angles"][0], "imported", setup["plate"]
            )
            direction.approve_evidence(session, row["id"])


def uncut_second_segment(work, setup):
    """Undo the second segment's authored cut, so a hard cut is the agent's to add."""
    with work.db.transaction() as session:
        receipt = next(
            r
            for r in direction.receipts(session, setup["movie_id"])
            if r["command"] == "create_cut"
            and r["after"]["record"]["id"] == setup["second_cut_id"]
        )
        direction.undo(session, receipt["id"])


def prepare_segment_fixture(task, work, setup):
    with work.db.transaction() as session:
        segment = direction.read_segment(session, setup["segment_id"])
        if task.criteria["segment_cuts"] == 0:
            receipt = next(
                r
                for r in direction.receipts(session, setup["movie_id"])
                if r["command"] == "create_cut"
                and r["after"]["record"]["id"] == setup["cut_id"]
            )
            direction.undo(session, receipt["id"])
            # An uncut continuation of the same shot opens where its predecessor ends; a new angle at the join
            # is a cut, which the rule hands over by paired latents (docs/CONTINUATION.md).
            segment = direction.read_segment(session, segment["id"])
            direction.update_segment(
                session,
                segment["id"],
                segment["revision"],
                opening_angle_id=last_angle(direction.predecessor(session, segment)),
            )
        else:
            direction.add_phase(
                session,
                segment["id"],
                segment["revision"],
                start_frame=72,
                content={
                    "description": "The walker pauses.",
                    "screen_direction": "",
                    "sound": "Wind.",
                },
            )
            segment = direction.read_segment(session, segment["id"])
            direction.create_cut(
                session,
                segment["id"],
                segment["revision"],
                frame=72,
                from_angle_id=segment["cuts"][-1]["to_angle_id"],
                to_angle_id=setup["angles"][0],
                relation="same_direction",
                crosses_barrier=False,
                relation_source="authored",
                receives_predecessor=True,
            )

            # Both pins and the one shared sheet travel through the product path.
            for cut in direction.read_segment(session, segment["id"])["cuts"]:
                direction.update_cut(
                    session,
                    cut["id"],
                    cut["revision"],
                    override_technique="views_and_pin",
                )
                view = direction.record_evidence(
                    session, cut["to_angle_id"], "imported", setup["plate"]
                )
                direction.approve_evidence(session, view["id"])
                pin = direction.record_keyframe(
                    session, cut["id"], setup["plate"], view["id"]
                )
                direction.approve_keyframe(session, pin["id"])
            from gaiavfx.application.prepare_references import (
                assemble_sheet,
                sheet_tiles_for_place,
            )

            assemble_sheet(
                session,
                place_id=setup["place_id"],
                tiles=sheet_tiles_for_place(session, setup["place_id"]),
                origin="survey",
            )
    run_idle(work, setup["fake"])


# ---------------------------------------------------------------- the replacement study


def finish_job(service, session, job, outputs=()):
    service.transition(session, job["id"], "running")
    service.transition(session, job["id"], "collecting")
    service.transition(session, job["id"], "succeeded", outputs=list(outputs))


class SyntheticReplacement:
    """Publish synthetic candidates using public services, without rendering pixels."""

    def drain(self, work):
        service = OperationService()
        with work.db.transaction() as session:
            while job := service.claim_next(session):
                operation = service.operation(session, job["operation_id"])
                if operation["kind"] == "person_analysis":
                    # A model may ask for another range or more concepts; serve the fixture's
                    # synthetic report again so the conversation can continue.
                    report = [
                        d
                        for o in service.recent(session, kind="person_analysis")
                        if o["state"] == "succeeded"
                        for j in o["jobs"]
                        if j["step"] == "people_report"
                        for d in j["outputs"]
                    ][-1:]
                    if not report:
                        service.transition(
                            session,
                            job["id"],
                            "failed",
                            error="No synthetic report for this clip.",
                        )
                        continue
                    finish_job(
                        service,
                        session,
                        job,
                        report if job["step"] == "people_report" else (),
                    )
                    continue
                if operation["kind"] != "character_replacement":
                    raise AssertionError("Unexpected producer in replacement eval")
                source = operation["request"]["source_asset"]
                outputs = []
                if job["step"] == "replacement_composite":
                    outputs = [{"output_key": "composite", "asset_id": source}]
                finish_job(service, session, job, outputs)
                if outputs:
                    service.add_candidate(
                        session,
                        job_id=job["id"],
                        asset_id=source,
                        output_key="composite",
                        kind="video",
                    )
            for operation in service.recent(session, kind="character_replacement"):
                if operation["state"] == "awaiting_review":
                    service.resume(
                        session,
                        operation["id"],
                        decision={
                            "decided_by": "advancer",
                            "round": 1,
                            "attempts": [],
                            "arm": None,
                            "gaps": [],
                            "reasons": ["Synthetic eval stops after first round."],
                            "seeds": [],
                        },
                        jobs=[],
                        awaits_review=False,
                    )


def replacement_library(path, *, count, name, mode):
    """A clip of numbered performers, the cast (unless the mode leaves it out) and a synthetic people report."""
    path.mkdir(parents=True, exist_ok=True)
    root = path / "library"
    with _MIGRATION_LOCK:
        LibraryService(root, MIGRATIONS).create("Replacement eval")
    work = Workspace(root)
    settings = {**work.settings(), "agent_autonomy": "off", "agent_budget_jobs": 4}
    work.save_settings(settings)
    image = Image.new("RGB", (320, 240), (38, 61, 86))
    draw = ImageDraw.Draw(image)
    for i in range(count):
        x = 45 + i * 90
        draw.rectangle((x, 60, x + 35, 210), fill=(120 + i * 20, 130, 110))
        draw.text((x, 45), str(i + 1), fill="white")
    still = path / "numbered-performers.png"
    image.save(still)
    clip = path / "source.mp4"
    frames = 240 if mode == "untested_length" else 24
    work.media.run(
        work.media.ffmpeg,
        [
            "-y",
            "-loop",
            "1",
            "-i",
            str(still),
            "-r",
            "24",
            "-frames:v",
            str(frames),
            *(
                ["-vf", "negate=enable='between(t,2,4)+between(t,6,8)'"]
                if mode == "untested_length"
                else []
            ),
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
    )
    identity = path / "identity.png"
    Image.new("RGB", (40, 60), (150, 60, 90)).save(identity)
    ids = []
    for item in (clip, identity):
        prepared = work.library.prepare(item, item.name)
        with work.db.transaction() as session:
            ids.append(
                work.library.register(session, prepared, {"type": "import"})["id"]
            )
    service = OperationService()
    subjects = []
    with work.db.transaction() as session:
        movie = direction.create_movie(session, "Replacement study")
        if mode != "cast":
            for cast_name in [name, "Oren"] if mode == "pair" else [name]:
                revision = direction.read_movie(session, movie["id"])["revision"]
                subject = direction.add_subject(
                    session,
                    movie["id"],
                    revision,
                    name=cast_name,
                    description="A cast performer",
                    keeps=[],
                    identity_asset_id=ids[1],
                )
                subjects.append(subject["id"])
    analysis_id = None
    if mode != "untested_length":
        report = {
            "source_asset": ids[0],
            "shot_start": 0,
            "shot_end": frames,
            "native": [320, 240],
            "fps": 24,
            "letterbox": None,
            "concepts": ["person"],
            "contacts": {},
            "tracks": [
                {
                    "id": f"track-{i + 1}",
                    "concept": "person",
                    "box": {"x0": 45 + i * 90, "y0": 60, "x1": 80 + i * 90, "y1": 210},
                    "presence": ["present"] * frames,
                }
                for i in range(count)
            ],
        }
        report_path = path / "people.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        prepared = work.library.prepare_blob(
            report_path, "analysis_report", {"synthetic_eval": True}
        )
        with work.db.transaction() as session:
            artifact = work.library.register_artifact(session, prepared)["id"]
            operation = service.create(
                session,
                kind="person_analysis",
                request={
                    "source_asset": ids[0],
                    "start": 0,
                    "end": frames,
                    "concepts": ["person"],
                },
                jobs=[
                    JobSpec("analysis", "local", "synthetic_eval", step="people_report")
                ],
                budget_limit=None,
                budget=0,
            )
            analysis_id = operation["id"]
            job = service.claim_next(session)
            finish_job(
                service,
                session,
                job,
                [{"output_key": "people", "artifact_id": artifact}],
            )
    return work, {
        "movie_id": movie["id"],
        "plan_mode": True,
        "attachments": [ids[0]],
        "take": ids[0],
        "identity": ids[1],
        "subjects": subjects,
        "analysis_id": analysis_id,
        "replacement_mode": mode,
        "fake": SyntheticReplacement(),
        "settings": settings,
    }


# ---------------------------------------------------------------- the recast study


def recast_library(path, *, budget):
    """A movie with one cast member and their portrait; the recast itself is synthetic (recast_study)."""
    path.mkdir(parents=True, exist_ok=True)
    with _MIGRATION_LOCK:
        LibraryService(path / "library", MIGRATIONS).create("Recast scripted eval")
    work = Workspace(path / "library")
    work.save_settings(
        {**work.settings(), "agent_autonomy": "off", "agent_budget_jobs": budget}
    )
    identity = path / "identity.png"
    Image.new("RGB", (32, 48), (33, 104, 166)).save(identity)
    prepared = work.library.prepare(identity, "Target portrait")
    with work.db.transaction() as session:
        asset = work.library.register(session, prepared, {"type": "import"})
        movie = direction.create_movie(session, "Recast conversation")
        direction.add_subject(
            session,
            movie["id"],
            movie["revision"],
            name="Target performer",
            description="A performer",
            keeps=[],
            identity_asset_id=asset["id"],
        )
    return work, movie, asset


def recast_study(mode, identity):
    """The synthetic recast a scenario starts from: four planned shots, one or two look-alike characters."""
    character = {
        "id": "c01",
        "label": "First guide" if mode == "lookalikes" else "Lead",
        "hidden": False,
        "screen_time_frames": 200,
        "target": None,
    }
    if mode == "over_budget":
        character["target"] = {
            "asset_id": identity,
            "label": "Osvin",
            "state": "confirmed",
            "proposed_by": "user",
        }
    return {
        "id": "fixture-recast",
        "title": "Recast study",
        "revision": 1,
        "film": {"frame_count": 200, "asset_id": "fixture-film"},
        "characters": [
            character,
            *(
                [{**character, "id": "c02", "label": "Second guide"}]
                if mode == "lookalikes"
                else []
            ),
        ],
        "shots": [
            {
                "id": f"s{i}",
                "start": i * 50,
                "end": (i + 1) * 50,
                "included": True,
                "status": "planned",
                "choice": None,
                "versions": [],
                "plan": {
                    "performers": ["c01"],
                    "gaps": [],
                    "renders_per_window": 2,
                    "windows": [{"start": i * 50, "end": (i + 1) * 50, "length": 56}],
                },
            }
            for i in range(4)
        ],
        "run": {"state": "idle", "mode": "draft", "renders_used": 0},
        "estimate": {"renders": 8},
    }


# ---------------------------------------------------------------- the film's seeded library


def film_workspace(path):
    """An empty library for the seeded or held-out film; the recast itself is patched in (harness_eval_film)."""
    with _MIGRATION_LOCK:
        LibraryService(path / "library", MIGRATIONS).create("Film scripted eval")
    work = Workspace(path / "library")
    work.save_settings(
        {**work.settings(), "agent_autonomy": "off", "agent_budget_jobs": 6}
    )
    return work


def import_image(work, name):
    prepared = work.library.prepare(MEDIA / "Walker.jpg", name)
    with work.db.transaction() as session:
        asset = work.library.register(session, prepared, {"type": "import"})
    return {"id": asset["id"], "name": name}


def library_image(work):
    with work.db.transaction() as session:
        images = [
            a
            for a in MediaLibrary.imported_sources(session, 24)
            if a["kind"] == "image"
        ]
    return images[0] if images else None


# ---------------------------------------------------------------- the stage scene

STAGE = "Box room stage"


def built_stage(scene):
    """A stage built from scene 1's first shot, without filling, its v1 accepted: the place is built as it."""
    from gaiavfx.application import set_seed, sets
    from set_scene import OPERATIONS

    work = scene.workspace
    with work.db.transaction() as session:
        started = set_seed.start_seed(
            session,
            work.library,
            segment_id=scene.segment["id"],
            shot=0,
            name=STAGE,
            use_for_place=True,
            fill_unseen=False,
        )
    for _ in range(600):
        if not scene.run.run_once():
            break
    with work.db.transaction() as session:
        set_id = OPERATIONS.operation(session, started["id"])["review"][-1]["set_id"]
    with work.db.transaction() as session:
        first = sets.STORE.get(session, set_id)["versions"][0]
        sets.accept_version(
            session, work.library, set_id, first["id"], expected_current_id=None
        )
    return set_id


def stage_facts(scene, set_id):
    """The ids and names a stage task's messages and scripted controls use."""
    from gaiavfx.application import sets

    facts = {
        "movie": scene.movie["id"],
        "first": scene.segment["id"],
        "second": scene.second["id"],
        "corner": scene.angles["corner"]["id"],
        "place": scene.place["name"],
        "place_id": scene.place["id"],
        "stage": set_id,
        "stage_name": STAGE,
        "camera": None,
    }
    if set_id:
        work = scene.workspace
        with work.db.transaction() as session:
            state = sets.set_state(session, work.library, set_id)
        cameras = [
            c["label"] for c in (state["version"]["map_meta"] or {}).get("cameras", [])
        ]
        facts["camera"] = cameras[min(1, len(cameras) - 1)] if cameras else None
    return facts


def stage_scene(path):
    """set_scene's two scenes sharing a place, with fakes for every build stage (built_stage adds a stage)."""
    from set_scene import build_scene

    return build_scene(path, second_scene=True)
