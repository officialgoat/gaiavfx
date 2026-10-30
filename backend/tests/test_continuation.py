"""Per-segment continuation (docs/CONTINUATION.md): the grid's context lengths, the research rule, the undoable
choice and its refusals, the plan's hint and both lowerings."""

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from wording_fixtures import GOLDEN, GRID, compile_fixture

from gaiavfx.application import direction_plans
from gaiavfx.application.continuation import set_continuation
from gaiavfx.application.render_cut import start_render
from gaiavfx.integrations.minimax_h3 import continuation as continuity
from gaiavfx.integrations.minimax_h3.continuation import MASKED, PAIRED, ContinuationFacts
from gaiavfx.integrations.minimax_h3.graph import build_graph, render_presets
from gaiavfx.integrations.minimax_h3.prompt import compile_prompt
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.direction.timebase import FrameGrid
from gaiavfx.modules.library.application import LibraryService
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.errors import ApplicationError

MIGRATIONS = Path(__file__).parents[1] / "migrations"
GRAPHS = GOLDEN / "graphs"
AUDIO = render_presets().conditioning["audio_context_frames"]


def facts(**changes):
    return ContinuationFacts(**{"continues": True, "cuts": 0, "guides": 0, **changes})


def test_h3_offers_four_context_lengths_on_its_window_grid():
    assert GRID.context_default == 22
    assert GRID.context_lengths() == (5, 22, 39, 56)
    # The context is the end of the source take's window: a 39-frame take cannot hand over 56.
    assert GRID.context_lengths(39) == (5, 22, 39)
    assert GRID.context_lengths(4) == ()
    assert [n for n in range(80) if GRID.valid_context(n)] == [5, 22, 39, 56]
    assert not GRID.valid_context(True)
    assert FrameGrid(24, 5, 17, 22).context_lengths() == (22,)
    with pytest.raises(ValueError, match="longest context"):
        FrameGrid(24, 5, 17, 22, context_max=50)
    with pytest.raises(ValueError, match="longest context"):
        FrameGrid(24, 5, 17, 39, context_max=22)


def test_a_saved_paired_latent_names_its_window():
    assert continuity.source_frames({"video_shape": [1, 24, 22, 14, 24]}, GRID) == 73
    assert continuity.source_frames({"video_shape": [1, 24, 12, 14, 24]}, GRID) == 39
    for info in (None, {}, {"video_shape": [1, 24, 21, 14, 24]}, {"video_shape": [1, 24]}):
        assert continuity.source_frames(info, GRID) is None


def test_the_grid_converts_windows_to_latent_steps_and_back():
    for steps, frames in ((2, 5), (7, 22), (12, 39), (17, 56), (22, 73)):
        assert GRID.latent_steps(frames) == steps
        assert GRID.window_of_latent(steps) == frames
    assert GRID.window_of_latent(21) is None and GRID.window_of_latent(1) is None
    with pytest.raises(ValueError, match="whole window"):
        GRID.latent_steps(30)
    with pytest.raises(ValueError, match="no latent layout"):
        FrameGrid(24, 5, 17, 22).latent_steps(22)
    with pytest.raises(ValueError, match="latent layout"):
        FrameGrid(24, 5, 17, 22, latent_base=2)


def test_the_rule_prefers_masked_on_the_same_shot_and_paired_across_cuts():
    assert continuity.recommend(facts()) == (MASKED, continuity.SAME_SHOT)
    assert continuity.recommend(facts(cuts=1)) == (PAIRED, continuity.ACROSS_CUTS)
    # A new angle at the join is a cut too: the masked prefix would carry the old direction across it.
    assert continuity.recommend(facts(angle_changes=True)) == (PAIRED, continuity.ACROSS_CUTS)
    assert "9 of 11" in continuity.ACROSS_CUTS and "6 of 11" in continuity.ACROSS_CUTS
    method, reason = continuity.recommend(facts(guides=1))
    assert method == PAIRED and "pinned keyframe" in reason and reason.endswith("recommendation.")
    assert continuity.masked_problem(facts()) is None
    assert "2 pinned keyframes" in continuity.masked_problem(facts(guides=2))
    assert continuity.masked_problem(facts(continues=False)).startswith("Only a continuing")


def test_the_handoff_makes_the_audio_coupling_explicit():
    rule = continuity.resolve(facts(source_frames=73), method=None, context_frames=None, grid=GRID)
    assert (rule.method, rule.explicit_method, rule.recommended) == (MASKED, None, MASKED)
    assert (rule.context_frames, rule.explicit_context, rule.default_context) == (22, None, 22)
    # Masked re-encodes exactly the picture context's audio; paired keeps its own audio window.
    assert (rule.audio_context_frames, rule.audio_follows_picture) == (22, True)
    paired = continuity.resolve(facts(), method=PAIRED, context_frames=39, grid=GRID)
    assert (paired.method, paired.context_frames) == (PAIRED, 39)
    assert (paired.audio_context_frames, paired.audio_follows_picture) == (AUDIO, False)
    masked = continuity.resolve(facts(cuts=1), method=MASKED, context_frames=39, grid=GRID)
    assert (masked.method, masked.recommended, masked.audio_context_frames) == (MASKED, PAIRED, 39)
    assert masked.refusals == ()
    assert continuity.resolve(
        facts(source_frames=39), method=None, context_frames=None, grid=GRID
    ).context_choices == (5, 22, 39)


def test_stored_choices_the_segment_can_no_longer_render_are_refused_with_reasons():
    pinned = continuity.resolve(facts(guides=1), method=MASKED, context_frames=None, grid=GRID)
    assert pinned.refusals[0][0] == "continuation_method"
    assert "pinned keyframe" in pinned.refusals[0][1]
    long = continuity.resolve(facts(source_frames=39), method=None, context_frames=56, grid=GRID)
    [(code, message)] = long.refusals
    assert code == "continuation_context"
    assert message == (
        "The selected take's window holds 39 frames; choose a motion context of at most 39 frames."
    )
    odd = continuity.resolve(facts(), method="levelled", context_frames=30, grid=GRID)
    assert [code for code, _ in odd.refusals] == ["continuation_method", "continuation_context"]
    assert "5, 22, 39, 56" in odd.refusals[1][1]


def test_a_choice_is_judged_only_on_the_fields_it_changes():
    def problem(facts_, **choice):
        return continuity.choice_problem(
            facts_,
            GRID,
            method=choice.get("method"),
            context_frames=choice.get("context_frames"),
            changing=frozenset(choice),
        )

    assert problem(facts(), method=MASKED) is None
    assert problem(facts(cuts=2), method=MASKED) is None  # not recommended, still renderable
    assert "pinned keyframe" in problem(facts(guides=1), method=MASKED)
    assert problem(facts(guides=1), method=PAIRED) is None
    assert problem(facts(), method="silver") == "Choose Paired latents or Masked handoff."
    assert "5, 22, 39, 56" in problem(facts(), context_frames=21)
    assert "at most 39" in problem(facts(source_frames=39), context_frames=56)
    assert problem(facts(continues=False), context_frames=None).startswith("Only a continuing")
    # A later pin already makes the plan refuse a stored masked choice; a context edit is not refused for it.
    assert (
        continuity.choice_problem(
            facts(guides=1),
            GRID,
            method=MASKED,
            context_frames=39,
            changing=frozenset({"context_frames"}),
        )
        is None
    )


def two_segments(service, session):
    movie = service.create_movie(session, "Movie")
    subject = service.add_subject(
        session,
        movie["id"],
        movie["revision"],
        name="Arin",
        description="A walker",
        keeps="coat",
        identity_asset_id=None,
    )
    revision = subject["receipt"]["after"]["revision"]
    segments = []
    for continues in (False, True):
        added = service.add_segment(
            session,
            movie["id"],
            revision,
            content={
                "subjects": [subject["id"]],
                "place": {"noun": "courtyard", "description": "A courtyard.", "landmarks": ""},
            },
            delivered_frames=96,
            continues=continues,
            phases=[
                (0, {"description": "Walk.", "screen_direction": "left", "sound": "steps"}),
                (48, {"description": "Turn.", "screen_direction": "left", "sound": "steps"}),
            ],
        )
        revision = added["receipt"]["after"]["revision"]
        segments.append(added)
    return segments


def test_the_handoff_is_a_revision_checked_edit_that_undo_returns_to_the_rule(store):
    db, service = store
    with db.transaction() as session:
        _, second = two_segments(service, session)
        chosen = set_continuation(
            session, second["id"], second["revision"], method=MASKED, context_frames=39
        )
        assert (chosen["continuation_method"], chosen["context_frames"]) == (MASKED, 39)
        assert chosen["revision"] == second["revision"] + 1
        assert chosen["receipt"]["before"] == {
            "revision": second["revision"],
            "continuation_method": None,
            "context_frames": None,
        }
        with pytest.raises(ApplicationError) as stale:
            set_continuation(session, second["id"], second["revision"], method=PAIRED)
        assert stale.value.status == 409
        undone = service.undo(session, chosen["receipt"]["id"])
        row = service.read_segment(session, second["id"])
        assert (row["continuation_method"], row["context_frames"]) == (None, None)
        service.undo(session, undone["id"])  # undoing the undo is redo
        row = service.read_segment(session, second["id"])
        assert (row["continuation_method"], row["context_frames"]) == (MASKED, 39)
        only_context = set_continuation(session, second["id"], row["revision"], context_frames=None)
        assert (only_context["continuation_method"], only_context["context_frames"]) == (
            MASKED,
            None,
        )


def pin_a_cut(service, session, segment):
    """A cut at the segment's second phase whose shot lowers an approved pinned keyframe."""
    place = service.create_place(session, "Courtyard")
    forward = service.add_angle(session, place["id"], "Forward")
    closer = service.add_angle(session, place["id"], "Closer view")
    cut = service.create_cut(
        session,
        segment["id"],
        segment["revision"],
        frame=48,
        from_angle_id=forward["id"],
        to_angle_id=closer["id"],
        relation="same_direction",
        crosses_barrier=False,
        relation_source="authored",
        receives_predecessor=True,
    )
    service.update_cut(session, cut["id"], cut["revision"], override_technique="views_and_pin")
    evidence = service.record_evidence(session, closer["id"], "imported", "view")
    service.approve_evidence(session, evidence["id"])
    pin = service.record_keyframe(session, cut["id"], "pin", evidence["id"])
    service.approve_keyframe(session, pin["id"])
    return service.read_segment(session, segment["id"])


def test_choices_the_rule_cannot_render_are_refused_with_the_reason(store):
    db, service = store
    with db.transaction() as session:
        first, second = two_segments(service, session)
        with pytest.raises(ApplicationError, match="Only a continuing segment") as error:
            set_continuation(session, first["id"], first["revision"], method=MASKED)
        assert error.value.status == 422
        with pytest.raises(ApplicationError, match="5, 22, 39, 56"):
            set_continuation(session, second["id"], second["revision"], context_frames=30)
        with pytest.raises(ApplicationError, match="Choose a continuation method"):
            set_continuation(session, second["id"], second["revision"])
        pinned = pin_a_cut(service, session, second)
        with pytest.raises(ApplicationError, match="pinned keyframe"):
            set_continuation(session, second["id"], pinned["revision"], method=MASKED)
        assert set_continuation(session, second["id"], pinned["revision"], method=PAIRED)
        assert service.read_segment(session, second["id"])["revision"] == pinned["revision"] + 1


def test_direction_keeps_the_shape_and_drops_the_handoff_with_continuation(store):
    db, service = store
    with db.transaction() as session:
        first, second = two_segments(service, session)
        for bad in ({"continuation_method": "Masked!"}, {"context_frames": 0}):
            with pytest.raises(ApplicationError):
                service.update_segment(session, second["id"], second["revision"], **bad)
        with pytest.raises(ApplicationError, match="Only a continuing segment"):
            service.update_segment(session, first["id"], first["revision"], context_frames=39)
        chosen = service.update_segment(
            session,
            second["id"],
            second["revision"],
            continuation_method=MASKED,
            context_frames=56,
        )
        stopped = service.update_segment(session, second["id"], chosen["revision"], continues=False)
        assert (stopped["continuation_method"], stopped["context_frames"]) == (None, None)
        assert stopped["receipt"]["before"] == {
            "revision": chosen["revision"],
            "continues": True,
            "continuation_method": MASKED,
            "context_frames": 56,
        }
        service.undo(session, stopped["receipt"]["id"])
        row = service.read_segment(session, second["id"])
        assert (row["continues"], row["continuation_method"], row["context_frames"]) == (
            True,
            MASKED,
            56,
        )
        movie = service.read_movie(session, row["movie_id"])
        removed = service.remove_segment(session, second["id"], movie["revision"])
        service.undo(session, removed["receipt"]["id"])
        restored = service.read_segment(session, second["id"])
        assert (restored["continuation_method"], restored["context_frames"]) == (MASKED, 56)
        with pytest.raises(ApplicationError, match="Only a continuing segment"):
            service.add_segment(
                session,
                movie["id"],
                service.read_movie(session, movie["id"])["revision"],
                content=restored["content"],
                delivered_frames=48,
                continues=False,
                context_frames=22,
            )


def test_a_split_join_follows_the_rule_while_the_left_half_keeps_its_choice(store):
    db, service = store
    with db.transaction() as session:
        _, second = two_segments(service, session)
        chosen = service.update_segment(
            session, second["id"], second["revision"], continuation_method=PAIRED, context_frames=5
        )
        result = service.split_segment(session, second["id"], chosen["revision"], 48)
        left, right = result["segments"]
        assert (left["continuation_method"], left["context_frames"]) == (PAIRED, 5)
        assert right["continues"] is True
        assert (right["continuation_method"], right["context_frames"]) == (None, None)


def test_the_default_context_is_stored_as_null_and_an_unchanged_choice_writes_nothing(store):
    db, service = store
    with db.transaction() as session:
        _, second = two_segments(service, session)
        before = len(service.receipts(session, second["movie_id"]))
        # The default already applies: no receipt records a change that did not happen.
        assert (
            set_continuation(session, second["id"], second["revision"], context_frames=22) is None
        )
        chosen = set_continuation(session, second["id"], second["revision"], context_frames=39)
        back = set_continuation(session, second["id"], chosen["revision"], context_frames=22)
        assert back["context_frames"] is None
        assert back["receipt"]["before"]["context_frames"] == 39
        unchanged = set_continuation(
            session, second["id"], back["revision"], method=None, context_frames=None
        )
        assert unchanged is None
        assert len(service.receipts(session, second["movie_id"])) == before + 2


def test_a_new_angle_at_the_join_is_a_cut(store):
    db, service = store
    with db.transaction() as session:
        first, second = two_segments(service, session)
        place = service.create_place(session, "Courtyard")
        wide, close = (service.add_angle(session, place["id"], n)["id"] for n in ("Wide", "Close"))

        def opens_on(segment, angle):
            row = service.read_segment(session, segment["id"])
            service.update_segment(session, row["id"], row["revision"], opening_angle_id=angle)

        def rule():
            handoff = direction_plans.plan_for_segment(session, second["id"]).continuation
            return handoff.recommended, handoff.reason

        assert rule() == (MASKED, continuity.SAME_SHOT)  # neither names an angle
        # As coverage reads it, a named opening after a predecessor that names none is another angle.
        opens_on(second, close)
        assert rule() == (PAIRED, continuity.ACROSS_CUTS)
        opens_on(first, close)
        assert rule() == (MASKED, continuity.SAME_SHOT)
        opens_on(first, wide)
        assert rule() == (PAIRED, continuity.ACROSS_CUTS)
        row = service.read_segment(session, second["id"])
        assert service.predecessor(session, row)["id"] == first["id"]
        assert service.predecessor(session, service.read_segment(session, first["id"])) is None


def test_the_plan_shows_the_handoff_and_its_recommendation(store):
    db, service = store
    with db.transaction() as session:
        first, second = two_segments(service, session)
        assert direction_plans.plan_for_segment(session, first["id"]).continuation is None
        plan = direction_plans.plan_for_segment(session, second["id"])
        handoff = plan.continuation
        assert (handoff.method, handoff.recommended, handoff.reason) == (
            MASKED,
            MASKED,
            continuity.SAME_SHOT,
        )
        assert handoff.context_choices == (5, 22, 39, 56)  # no selected take yet
        # Rendering still needs the predecessor's take; the hint is there before it.
        assert [r.code for r in plan.refusals] == ["continuation_source"]
        as_dict = direction_plans.plan_to_dict(plan)["continuation"]
        assert as_dict["audio_follows_picture"] is True and as_dict["audio_context_frames"] == 22


def test_the_api_sets_refuses_and_reports_the_handoff(tmp_path):
    from fastapi.testclient import TestClient
    from test_direction_api import AUTH

    from gaiavfx.api.app import create_app

    LibraryService(tmp_path, MIGRATIONS).create("Continuation API")
    app = create_app(tmp_path, MIGRATIONS, "test-token", enable_runner=False)
    with TestClient(app, base_url="http://localhost", headers=AUTH) as client:
        with app.state.workspace.db.transaction() as session:
            first, second = two_segments(DirectionService(), session)
        url = f"/api/direction/segments/{second['id']}/continuation"
        chosen = client.patch(
            url, json={"expected_revision": second["revision"], "method": "masked"}
        )
        assert chosen.status_code == 200, chosen.text
        body = chosen.json()
        assert (body["continuation_method"], body["context_frames"]) == ("masked", None)
        assert body["receipt"]["command"] == "update_segment"
        stale = client.patch(url, json={"expected_revision": second["revision"], "method": None})
        assert stale.status_code == 409
        refused = client.patch(
            url, json={"expected_revision": body["revision"], "context_frames": 30}
        )
        assert refused.status_code == 422
        assert "5, 22, 39, 56" in refused.json()["detail"]
        assert (
            client.patch(
                url, json={"expected_revision": body["revision"], "method": "levelled"}
            ).status_code
            == 422
        )
        not_continuing = client.patch(
            f"/api/direction/segments/{first['id']}/continuation",
            json={"expected_revision": first["revision"], "method": "masked"},
        )
        assert not_continuing.status_code == 422
        assert "Only a continuing segment" in not_continuing.json()["detail"]
        unchanged = client.patch(
            url, json={"expected_revision": body["revision"], "method": "masked"}
        )
        assert unchanged.status_code == 422
        assert unchanged.json()["detail"] == "The segment already hands over that way."
        plan = client.get(f"/api/direction/segments/{second['id']}/plan").json()
        assert plan["continuation"]["method"] == "masked"
        assert plan["continuation"]["explicit_method"] == "masked"
        assert plan["continuation"]["recommended"] == "masked"
        movie = client.get(f"/api/direction/movies/{body['movie_id']}").json()
        assert movie["segments"][1]["continuation_method"] == "masked"
        undone = client.post(f"/api/direction/receipts/{body['receipt']['id']}/undo")
        assert undone.status_code == 200, undone.text
        segment = client.get(f"/api/direction/segments/{second['id']}").json()
        assert segment["continuation_method"] is None


def snapshot(path, graph):
    text = json.dumps(graph, indent=1, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_GRAPH_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    assert path.exists(), f"No snapshot at {path}; run with UPDATE_GRAPH_GOLDEN=1."
    assert path.read_bytes() == text.encode("utf-8")


def lowered(method, context, *, keyframes=(), handoff=None):
    _, segment, pictures, _ = compile_fixture(GOLDEN / "inputs" / "single-continuation.json")
    compiled = compile_prompt(segment, pictures, (), GRID, context)
    compiled = replace(compiled, keyframes=keyframes)
    return compiled, build_graph(
        compiled,
        files={asset: f"{asset}.png" for asset in [*compiled.slots, *(a for a, _ in keyframes)]},
        seed=42,
        width=768,
        height=448,
        fps=GRID.fps,
        context_latent="test/context.safetensors",
        output_prefix="test/golden",
        handoff=handoff
        or continuity.resolve(facts(), method=method, context_frames=context, grid=GRID),
    )


@pytest.mark.parametrize("method,context", [(PAIRED, 22), (PAIRED, 39), (MASKED, 22), (MASKED, 39)])
def test_both_handoffs_lower_to_reviewed_graphs(method, context):
    compiled, graph = lowered(method, context)
    snapshot(GRAPHS / f"{method}-{context}.graph.json", graph)
    assert compiled.window_frames == GRID.window_for(120, context)
    assert graph["conditioning"]["inputs"]["length"] == compiled.window_frames
    assert graph["load_context"]["inputs"]["latent_path"] == "test/context.safetensors"
    assert graph["save_context"]["inputs"]["latent"] == ["sample", 0]  # chains continue either way
    if method == MASKED:
        assert "motion_context" not in graph
        node = graph["masked_context"]
        assert node["class_type"] == "GaiaH3ContinuityMasked"
        assert node["inputs"] | {"context_latent": None} == {
            "model": ["model", 0],
            "positive": ["conditioning", 0],
            "latent": ["conditioning", 1],
            "context_latent": None,
            "audio_vae": ["audio_vae_guard", 0],
            "context_length": context,
            "fps": 24.0,
        }
        assert graph["shift"]["inputs"]["model"] == ["masked_context", 0]
        assert graph["guider"]["inputs"]["conditioning"] == ["masked_context", 1]
        assert graph["sample"]["inputs"]["latent_image"] == ["masked_context", 2]
        assert graph["trim"]["inputs"]["trim_frames"] == ["masked_context", 3]
    else:
        assert "masked_context" not in graph
        motion = graph["motion_context"]["inputs"]
        assert (motion["context_length"], motion["audio_context_length"]) == (str(context), AUDIO)
        assert graph["trim"]["inputs"]["trim_frames"] == context


def test_paired_22_is_the_default_continuation_graph():
    *_, compiled = compile_fixture(GOLDEN / "inputs" / "single-continuation.json")
    default = build_graph(
        compiled,
        files={asset: f"{asset}.png" for asset in compiled.slots},
        seed=42,
        width=768,
        height=448,
        fps=GRID.fps,
        context_latent="test/context.safetensors",
        output_prefix="test/golden",
    )
    assert default == lowered(PAIRED, 22)[1]


def test_lowering_refuses_a_handoff_the_window_cannot_take():
    with pytest.raises(ValueError, match="pinned keyframes"):
        lowered(MASKED, 22, keyframes=(("pin", 40),))
    with pytest.raises(ValueError, match="differs"):
        lowered(
            PAIRED,
            22,
            handoff=continuity.resolve(facts(), method=PAIRED, context_frames=39, grid=GRID),
        )
    rule = continuity.resolve(facts(), method=MASKED, context_frames=22, grid=GRID)
    with pytest.raises(ValueError, match="unknown"):
        lowered(MASKED, 22, handoff=replace(rule, method="levelled"))
    with pytest.raises(ValueError, match="ties the audio"):
        lowered(MASKED, 22, handoff=replace(rule, audio_context_frames=24))


def test_a_render_records_its_handoff_and_lowers_it(store, monkeypatch):
    db, service = store
    shape = {"video_shape": [1, 24, 22, 14, 24]}  # a 73-frame source take
    monkeypatch.setattr(
        MediaLibrary,
        "artifact",
        lambda session, artifact: {
            "id": artifact,
            "sha256": "c",
            "kind": "h3_context",
            "info": shape,
        },
    )
    with db.transaction() as session:
        _, second = two_segments(service, session)
        row = service.update_segment(session, second["id"], second["revision"], context_frames=39)
        operation = start_render(
            session, segment_id=second["id"], seeds=[7], context_artifact="context"
        )
        [job] = operation["jobs"]
    evidence = {
        "method": MASKED,
        "chosen": False,
        "recommended": MASKED,
        "context_frames": 39,
        "audio_context_frames": 39,
        "audio_follows_picture": True,
    }
    assert job["snapshot"]["continuation"] == operation["request"]["continuation"] == evidence
    assert job["snapshot"]["context_frames"] == 39
    length = job["graph"]["conditioning"]["inputs"]["length"]
    assert job["snapshot"]["expect"]["frames"] == length - 39
    assert job["graph"]["masked_context"]["inputs"]["context_length"] == 39
    assert "motion_context" not in job["graph"]
    with db.transaction() as session:
        set_continuation(session, second["id"], row["revision"], method=PAIRED)
        operation = start_render(
            session, segment_id=second["id"], seeds=[7], context_artifact="context"
        )
        [job] = operation["jobs"]
    assert job["snapshot"]["continuation"]["audio_context_frames"] == AUDIO
    assert job["graph"]["motion_context"]["inputs"]["context_length"] == "39"
