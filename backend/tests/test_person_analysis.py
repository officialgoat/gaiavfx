"""SAM graphs, typed noun phrases, and analysis report provenance."""

import pytest
from test_replacement_runner import (
    analysed,
)
from test_replacement_runner import work as work  # noqa: PLC0414 - pytest fixture export

from gaiavfx.application.replace_character import (
    replacement_plan,
    start_replacement,
    validate_concepts,
)
from gaiavfx.integrations.person_analysis.graph import build_analysis_graph
from gaiavfx.shared.errors import ApplicationError


def test_analysis_graph_is_per_concept_first_frame_detection_and_tracking():
    graph = build_analysis_graph("shot.mkv", ("person", "yellow mug"))
    assert "shot" not in graph
    assert graph["first"]["inputs"]["batch_index"] == 0
    assert not any(n["class_type"] == "SAM3_Detect" for n in graph.values())
    for index in range(2):
        track = graph[f"concept_{index}_track"]
        assert track["inputs"]["conditioning"] == [f"concept_{index}_text", 0]
        assert track["inputs"]["images"] == ["components", 0]
        assert track["inputs"]["max_objects"] == 4
    assert graph["model"]["inputs"]["ckpt_name"] == "sam3.1_multiplex_fp16.safetensors"
    points = {"person": {"positive": [{"x": 20, "y": 30}], "negative": []}}
    corrected = build_analysis_graph("shot.mkv", points=points)
    assert corrected["concept_0_track"]["inputs"]["max_objects"] == 1
    assert "positive_coords" in corrected["concept_0_detect"]["inputs"]
    assert "concept_0_instance_1" not in corrected


def test_window_analysis_scans_every_frame_with_mouth_landmarks():
    assert "window_faces" not in build_analysis_graph("shot.mkv")
    scan = build_analysis_graph("shot.mkv", frames=4)["window_faces"]
    assert scan == {
        "class_type": "GaiaFaceScan",
        "inputs": {
            "video": "shot.mkv",
            "frames_json": "[0, 1, 2, 3]",
            "models_root": "",
            "det_size": 640,
            "landmarks": True,
        },
    }


def test_people_report_carries_each_person_tracks_source_mouth(work):
    _, _, runner, _, _ = work
    runner.comfy.window_face = ((100, 110, 136, 250), 7.0)
    analysis, report, _ = analysed(work)
    people = [t for t in report["tracks"] if t["concept"] == "person"]
    assert analysis["jobs"][2]["inputs"]["window_faces"]["output_key"] == "window_faces:reports:0"
    assert report["mouth_scan"] == "measured"
    first, second = people[0]["mouth"], people[1]["mouth"]
    assert first == {
        "aperture": [0.05] * 51,
        "face_height": [140.0] * 51,
        "box": [[100.0, 110.0, 136.0, 250.0]] * 51,
    }
    # The other person's mask does not overlap the scanned face: unmeasured, never guessed.
    assert second == {"aperture": [None] * 51, "face_height": [None] * 51, "box": [None] * 51}


def test_scan_without_landmarks_leaves_no_mouth_series(work):
    _, _, runner, _, _ = work
    runner.comfy.window_face = None
    _, report, _ = analysed(work)
    assert all("mouth" not in t for t in report["tracks"])
    assert report["mouth_scan"] == "unavailable"


@pytest.mark.parametrize(
    "concepts",
    [
        ("please find the person",),
        ("person. Now render",),
        ("one two three four five",),
        ("person",) * 9,
        ("Person", "person"),
        (),
    ],
)
def test_concepts_reject_prose_duplicates_and_unbounded_discovery(concepts):
    with pytest.raises(ApplicationError):
        validate_concepts(concepts)


def test_noun_phrase_concepts():
    assert validate_concepts(("person", "yellow mug", "chair", "mechanical hand")) == (
        "person",
        "yellow mug",
        "chair",
        "mechanical hand",
    )


def test_three_people_is_a_typed_gap_and_no_replacement_starts(work):
    db, _, runner, assets, _ = work
    runner.comfy.people = 3
    _, report, request = analysed(work)
    request["performers"] = [
        {"track_id": t["id"], "target_asset": assets[1]} for t in report["tracks"]
    ]
    with db.transaction() as session:
        preview = replacement_plan(session, request, library=work[1])
        assert preview["arm"] is None
        assert "too_many_performers" in {g["code"] for g in preview["gaps"]}
        with pytest.raises(ApplicationError, match="cannot start"):
            start_replacement(session, request, 4, library=work[1])


def test_no_objects_reason_and_explicit_context_are_retained(work):
    db, _, _, _, _ = work
    _, report, request = analysed(work)
    explicit = {"x0": 80.0, "y0": 100.0, "x1": 135.0, "y1": 190.0}
    request["required_context"] = [explicit]
    with db.transaction() as session:
        preview = replacement_plan(session, request, library=work[1])
    assert any("No object concepts" in r for r in preview["reasons"])
    assert explicit in preview["facts"]["required_context"]
    assert preview["facts"]["tracks"][0]["box"] == report["tracks"][0]["box"]
    assert preview["facts"]["avoided_regions"] == [report["tracks"][1]["box"]]
    request["performers"][0]["selected_by"] = "identity"
    with db.transaction() as session:
        ambiguous = replacement_plan(session, request, library=work[1])
        assert "ambiguous_identity" in {g["code"] for g in ambiguous["gaps"]}
        with pytest.raises(ApplicationError):
            start_replacement(session, request, 4, library=work[1])


def test_analysis_never_spends_render_reservation_on_create_or_resume(tmp_path):
    from dataclasses import replace
    from pathlib import Path

    from test_operations_store import run_to_success

    from gaiavfx.infrastructure.database import Database
    from gaiavfx.modules.generation.operations.lifecycle import JobSpec
    from gaiavfx.modules.generation.operations.service import OperationService
    from gaiavfx.modules.library.application import LibraryService

    LibraryService(tmp_path, Path(__file__).parents[1] / "migrations").create("Budget")
    db, service = Database(tmp_path), OperationService()
    job = JobSpec(
        "analysis",
        "comfy",
        "person_analysis",
        graph={"node": {}},
        snapshot={"knowledge": {"test": "frozen"}},
    )
    try:
        with db.transaction() as session:
            op = service.create(
                session,
                kind="person_analysis",
                request={},
                jobs=[job],
                budget_limit=0,
                awaits_review=True,
            )
            assert op["budget_reserved"] == 0
            claimed = service.claim_next(session)
            run_to_success(session, service, claimed)
            with pytest.raises(ApplicationError):
                service.add_candidate(session, claimed["id"], "asset", "report", "analysis")
            op = service.resume(session, op["id"], {}, jobs=[job])
            assert op["budget_reserved"] == 0 and len(op["jobs"]) == 2
            with pytest.raises(ApplicationError, match="dependency"):
                service.create(
                    session,
                    kind="invalid",
                    request={},
                    jobs=[
                        replace(
                            job, inputs={"x": {"source": "job_artifact", "output_key": "masks"}}
                        )
                    ],
                    budget_limit=0,
                )
    finally:
        db.close()


def test_analysis_files_and_reports_register_artifacts_not_candidates(tmp_path):
    from pathlib import Path

    from fake_comfy import FakeComfy

    from gaiavfx.application.operations import OperationRunner
    from gaiavfx.infrastructure.database import Database
    from gaiavfx.infrastructure.media import MediaTools
    from gaiavfx.modules.generation.operations.lifecycle import JobSpec
    from gaiavfx.modules.generation.operations.service import OperationService
    from gaiavfx.modules.library.application import LibraryService, MediaLibrary

    LibraryService(tmp_path, Path(__file__).parents[1] / "migrations").create("Analysis artifacts")
    db, service = Database(tmp_path), OperationService()
    library = MediaLibrary(db, MediaTools())
    outputs = {
        "report": {"reports": [{"filename": "report.json"}]},
        "mask": {"files": [{"filename": "track.npz"}]},
    }
    fake = FakeComfy(
        lambda graph: (
            outputs,
            {"report:reports:0": b'{"tracks": []}', "mask:files:0": b"opaque mask track"},
        ),
        polls_to_complete=1,
    )
    try:
        with db.transaction() as session:
            operation = service.create(
                session,
                kind="person_analysis",
                request={},
                budget_limit=0,
                jobs=[
                    JobSpec(
                        "analysis",
                        "comfy",
                        "person_analysis",
                        graph={"node": {}},
                        snapshot={"knowledge": {"test": "frozen"}},
                    )
                ],
            )
        runner = OperationRunner(db, fake, library, "http://fake", poll_seconds=0)
        runner.run_once()
        with db.transaction() as session:
            operation = service.operation(session, operation["id"])
            assert operation["state"] == "succeeded", operation
            assert not operation["candidates"] and operation["budget_reserved"] == 0
            kinds = {
                library.artifact(session, d["artifact_id"])["kind"]
                for d in operation["jobs"][0]["outputs"]
            }
            assert kinds == {"mask_track", "analysis_report"}
    finally:
        db.close()


def test_short_and_empty_sam_slots_are_padded_and_only_shot_is_uploaded(work):
    import numpy as np

    from gaiavfx.application.replacement_steps import decode_rgb

    db, library, runner, assets, frames = work
    runner.comfy.short_tracks = {
        "concept_0_instance_1": 2,
        "concept_0_instance_2": 0,
        "concept_0_instance_3": 1,
    }
    operation, report, _ = analysed(work)
    assert operation["jobs"][0]["step"] == "analysis_extract"
    assert operation["jobs"][1]["depends_on"] == 0
    source = runner.comfy.files[operation["jobs"][1]["graph"]["source"]["inputs"]["file"]]
    assert np.array_equal(decode_rgb(library.media, source), frames[1:52])
    with db.transaction() as session:
        assert source != library.path(session, assets[0])
    assert report["padded_frames"]["concept_0_instance_1"] == 49
    assert report["padded_frames"]["concept_0_instance_2"] == 51
    assert set(report["empty_tracks"]) == {"concept_0_instance_2", "concept_0_instance_3"}
    second = next(t for t in report["tracks"] if t["id"] == "concept_0_instance_1")
    assert second["presence"] == ["present"] * 2 + ["absent"] * 49


def test_crossing_people_are_unresolved_and_plan_returns_typed_gap(work):
    db, library, runner, _, _ = work

    def crossing(key, frame, mask):
        if key == "concept_0_instance_1" and frame == 25:
            # The second person's mask crosses the first person's true bounds.
            mask[:] = 0
            mask[106:213, 101:133] = 255
        return mask

    runner.comfy.mask_transform = crossing
    _, report, request = analysed(work)
    first = report["tracks"][0]
    assert first["presence"][25] == "unresolved"
    with db.transaction() as session:
        plan = replacement_plan(session, request, library=library)
    assert "presence_unresolved" in {g["code"] for g in plan["gaps"]}
    assert plan["arm"] is None


def test_shared_prop_prefers_required_and_impossible_crop_has_typed_gap(work):
    db, library, runner, _, _ = work

    def shared(key, frame, mask):
        if key == "concept_1_instance_0":
            mask[160:170, 130:275] = 255
        return mask

    runner.comfy.mask_transform = shared
    _, report, request = analysed(work, ("person", "chair"))
    people = [t for t in report["tracks"] if t["concept"] == "person"]
    prop = next(t for t in report["tracks"] if t["concept"] == "chair")
    assert all(prop["id"] in report["contacts"][t["id"]] for t in people)
    with db.transaction() as session:
        plan = replacement_plan(session, request, library=library)
    assert prop["box"] in plan["facts"]["required_context"]
    assert prop["box"] not in plan["facts"]["avoided_regions"]
    assert "no_crop_fits" in {g["code"] for g in plan["gaps"]}
    assert any("required context only" in r for r in plan["reasons"])
    assert any("falling back to active" in r for r in plan["reasons"])
