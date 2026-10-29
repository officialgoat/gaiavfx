import numpy as np

from gaiavfx.application.replacement_steps import window_identities


def test_face_rows_vote_by_sam_mask_and_use_cluster_centroids():
    faces = {
        "frame": np.array([10, 11, 12, 10, 11]),
        "box": np.array([[1, 1, 3, 3]] * 3 + [[6, 1, 8, 3]] * 2),
        "embedding": np.array([[1, 0], [1, 0], [0, 1], [0, 1], [0, 1]], dtype=np.float16),
    }
    report = {
        "tracks": [{"id": "a", "faces": [0, 1]}, {"id": "b", "faces": [2, 3, 4]}],
        "clusters": [{"id": "c01", "track_ids": ["a"]}, {"id": "c02", "track_ids": ["b"]}],
    }
    masks = {
        "left": np.zeros((3, 10, 10), bool),
        "right": np.zeros((3, 10, 10), bool),
        "empty": np.zeros((3, 10, 10), bool),
    }
    masks["left"][:, :5, :5] = True
    masks["right"][:, :5, 5:] = True
    result = window_identities(faces, report, masks, 10)
    assert result["left"]["cluster"] == "c01" and result["left"]["votes"] == 2
    assert result["left"]["film_tracks"] == {"a": 2, "b": 1}
    np.testing.assert_allclose(result["left"]["margin"], 1 / np.sqrt(5))
    assert result["right"] == {
        "cluster": "c02",
        "film_tracks": {"b": 2},
        "votes": 2,
        "margin": None,
    }
    assert result["empty"] == {"cluster": None, "film_tracks": {}, "votes": 0, "margin": None}


def test_unclustered_film_tracks_remain_available_for_user_overrides():
    faces = {
        "frame": np.array([3]),
        "box": np.array([[0, 0, 2, 2]]),
        "embedding": np.array([[1.0, 0.0]]),
    }
    report = {"tracks": [{"id": "unknown-film-track", "faces": [0]}], "clusters": []}
    result = window_identities(faces, report, {"sam": np.ones((1, 4, 4), bool)}, 3)
    assert result["sam"] == {
        "cluster": None,
        "film_tracks": {"unknown-film-track": 1},
        "votes": 0,
        "margin": None,
    }


def test_single_cluster_margin_and_npz_arrays_are_read_once_per_window():
    class Once(dict):
        def __getitem__(self, key):
            assert key not in self.read
            self.read.add(key)
            return super().__getitem__(key)

    faces = Once(
        frame=np.array([0, 10, 11, 12, 90]),
        box=np.tile([0, 0, 2, 2], (5, 1)),
        embedding=np.tile(np.array([1, 0], np.float16), (5, 1)),
    )
    faces.read = set()
    report = {
        "tracks": [{"id": "film", "faces": list(range(5))}],
        "clusters": [{"id": "only", "track_ids": ["film"]}],
    }
    masks = {"three": np.ones((3, 4, 4), bool), "two": np.ones((3, 4, 4), bool)}
    masks["two"][2] = False
    result = window_identities(faces, report, masks, 10)
    assert result["three"] == {
        "cluster": "only",
        "film_tracks": {"film": 3},
        "votes": 3,
        "margin": 1.0,
    }
    assert result["two"]["margin"] is None and result["two"]["votes"] == 2


def test_user_confirmation_bypasses_missing_margin_in_the_real_planner():
    from dataclasses import replace

    from test_replacement_contract import mug_facts

    from gaiavfx.application.replace_character import _plan
    from gaiavfx.modules.direction.application import replacement_policy_text

    facts = mug_facts()
    identity = replace(
        facts, tracks=tuple(replace(t, selected_by="identity") for t in facts.tracks)
    )
    assert _plan(identity, replacement_policy_text()).gaps[0].code == "ambiguous_identity"
    assert _plan(facts, replacement_policy_text()).arm is not None
