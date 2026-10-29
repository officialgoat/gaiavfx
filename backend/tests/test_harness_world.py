"""Bounded readers filter the relevant records before applying their limit."""

import harness_support

from gaiavfx.application.harness.shots import shots, targeted_shots
from gaiavfx.application.harness.world import world_state
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.media_application import MediaLibrary, PreparedAsset

kernel = harness_support.kernel


def test_imported_sources_filter_before_limit(kernel):
    library = MediaLibrary(kernel.db, None)
    with kernel.db.transaction() as session:
        for index in range(30):
            library.register(
                session,
                PreparedAsset(
                    str(index),
                    f"Asset {index}",
                    f"media/{index}.png",
                    "image/png",
                    {"kind": "image"},
                ),
                {"type": "import" if index < 2 else "generated"},
            )
        sources = MediaLibrary.imported_sources(session, limit=1)
        # Both imports can share a coarse clock tick; the limit must apply after filtering.
        assert len(sources) == 1 and sources[0]["name"] in {"Asset 0", "Asset 1"}
        assert set(sources[0]) == {"id", "kind", "name"}


def test_other_movie_operations_cannot_hide_scope_review(kernel):
    service = OperationService()
    with kernel.db.transaction() as session:
        place = DirectionService().create_place(session, "Here")
        own = service.create(
            session,
            kind="survey",
            request={"place_id": place["id"]},
            jobs=[JobSpec("image", "local", "test", step="test")],
            budget_limit=None,
        )
        for _ in range(25):
            service.create(
                session,
                kind="survey",
                request={"place_id": "another-movie-place"},
                jobs=[JobSpec("image", "local", "test", step="test")],
                budget_limit=None,
            )
        state = world_state(session, kernel.movie["id"], "movie", kernel.movie["id"], "off")
        assert [o["id"] for o in state.operations] == [own["id"]]
        assert service.for_request(session, "unknown") is None


def test_cut_scope_filters_before_the_movie_list_limit(kernel):
    from test_direction_store import make_segment

    direction = DirectionService()
    with kernel.db.transaction() as session:
        segment = make_segment(direction, session, kernel.movie["id"])
        place = direction.create_place(session, "Here")
        angle = direction.add_angle(session, place["id"], "Opening")
        target_angle = direction.add_angle(session, place["id"], "Closer view")
        revision = segment["revision"]
        for frame in range(1, 26):
            cut = direction.create_cut(
                session,
                segment["id"],
                revision,
                frame=frame,
                from_angle_id=angle["id"] if frame % 2 else target_angle["id"],
                to_angle_id=target_angle["id"] if frame % 2 else angle["id"],
                relation="same_direction",
                crosses_barrier=False,
                relation_source="authored",
            )
            revision += 1
        state = world_state(session, kernel.movie["id"], "cut", cut["id"], "off")
        assert [row["id"] for row in state.cuts] == [cut["id"]]
        assert cut["id"] in state.plans


def test_shots_are_each_segments_first_shot_then_its_cuts_and_stay_out_of_the_payload(kernel):
    """ADR 0019 §1, §7 (Fable and Astra, round 6): the harness works on shots. A first shot's id is its segment's; it
    shows its opening angle and its survey starts there. Target matching keeps preparing cuts only."""
    from test_direction_store import make_segment

    direction = DirectionService()
    with kernel.db.transaction() as session:
        place = direction.create_place(session, "Courtyard")
        door, stove = (
            direction.add_angle(session, place["id"], name) for name in ("Door", "Stove")
        )
        cut_segment = make_segment(direction, session, kernel.movie["id"])
        cut = direction.create_cut(
            session,
            cut_segment["id"],
            cut_segment["revision"],
            frame=30,
            from_angle_id=door["id"],
            to_angle_id=stove["id"],
            relation="same_direction",
            crosses_barrier=False,
            relation_source="authored",
        )
        assert direction.read_segment(session, cut_segment["id"])["opening_angle_id"] == door["id"]
        movie = direction.read_movie(session, kernel.movie["id"])
        still = direction.add_segment(
            session,
            movie["id"],
            movie["revision"],
            content=cut_segment["content"],
            delivered_frames=48,
            continues=False,
            phases=[(0, {"description": "Arin waits.", "screen_direction": "", "sound": ""})],
        )
        direction.update_segment(
            session, still["id"], still["revision"], opening_angle_id=stove["id"]
        )
        state = world_state(session, kernel.movie["id"], "movie", kernel.movie["id"], "off")
        found = {shot["id"]: shot for shot in shots(state)}
        assert list(found) == [cut_segment["id"], cut["id"], still["id"]]
        assert {k: v for k, v in found[cut_segment["id"]].items()} == {
            "id": cut_segment["id"],
            "kind": "opening",
            "segment_id": cut_segment["id"],
            "place_id": place["id"],
            "angle_id": door["id"],
            "plate_angle_id": door["id"],
            "angle": "Door",
            "plate_angle": "Door",
        }
        assert (found[cut["id"]]["angle_id"], found[cut["id"]]["plate_angle_id"]) == (
            stove["id"],
            door["id"],
        )
        assert (
            found[still["id"]]["kind"] == "opening"
            and found[still["id"]]["angle_id"] == stove["id"]
        )
        # A place, a segment or an angle names the shots that show it. These first shots are told in text alone,
        # so they have nothing to prepare (test_harness_first_shot prepares one that takes the set's views).
        assert [s["id"] for s in targeted_shots(state, place["id"])] == [cut["id"]]
        assert [s["id"] for s in targeted_shots(state, cut_segment["id"])] == [cut["id"]]
        assert targeted_shots(state, still["id"]) == []
        payload = state.payload()
        assert "shots" not in payload and "scope_kind" not in payload
        # A cut scope holds its cut alone, not its segment's first shot.
        scoped = world_state(session, kernel.movie["id"], "cut", cut["id"], "off")
        assert [s["id"] for s in shots(scoped)] == [cut["id"]]
