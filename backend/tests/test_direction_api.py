"""The read-only plan endpoint exposes a typed OpenAPI response."""

from pathlib import Path

from fastapi.testclient import TestClient
from test_direction_store import make_segment
from wording_fixtures import GOLDEN, compile_fixture

from gaiavfx.api.app import create_app
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.library.application import LibraryService

MIGRATIONS = Path(__file__).parents[1] / "migrations"
AUTH = {"Authorization": "Bearer test-token"}


def test_cut_plan_endpoint_and_missing_cut(tmp_path):
    LibraryService(tmp_path, MIGRATIONS).create("Direction API")
    app = create_app(tmp_path, MIGRATIONS, "test-token", enable_runner=False)
    with TestClient(app, base_url="http://localhost") as client:
        with app.state.workspace.db.transaction() as session:
            service = DirectionService()
            place = service.create_place(session, "Courtyard")
            source = service.add_angle(session, place["id"], "Gate")
            target = service.add_angle(session, place["id"], "Arcade")
            segment = make_segment(service, session)
            cut = service.create_cut(
                session,
                segment_id=segment["id"],
                expected_revision=segment["revision"],
                frame=36,
                from_angle_id=source["id"],
                to_angle_id=target["id"],
                relation="reverse",
                crosses_barrier=True,
                relation_source="authored",
            )
        path = f"/api/direction/cuts/{cut['id']}/plan"
        response = client.get(path, headers=AUTH)
        assert response.status_code == 200
        assert response.json()["assessment"]["coverage"] == "new"
        assert response.json()["recommended"]["technique_id"] == "views_and_pin"
        assert response.json()["recommended"]["actions"][0]["action"] == "view_angle"
        assert client.get(path).status_code == 401
        assert client.get("/api/direction/cuts/missing/plan", headers=AUTH).status_code == 404
        assert client.get(path, headers=AUTH, params={"integration": "other"}).status_code == 422
        operation = app.openapi()["paths"]["/api/direction/cuts/{cut_id}/plan"]["get"]
        assert "200" in operation["responses"]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith("/CutPlanDTO")
        remove_path = f"/api/direction/cuts/{cut['id']}/remove"
        assert client.post(remove_path, json={"expected_revision": 2}).status_code == 401
        assert (
            client.post(remove_path, headers=AUTH, json={"expected_revision": 1}).status_code == 409
        )
        response = client.post(remove_path, headers=AUTH, json={"expected_revision": 2})
        assert response.status_code == 200, response.text
        removed = response.json()
        assert removed["removed"] and removed["receipt"]["command"] == "remove_cut"
        assert client.get(path, headers=AUTH).status_code == 404
        inverse = client.post(
            f"/api/direction/receipts/{removed['receipt']['id']}/undo", headers=AUTH
        )
        assert inverse.status_code == 200, inverse.text
        assert client.get(path, headers=AUTH).status_code == 200


def test_persisted_authoring_receipts_and_undo_over_http(tmp_path):
    LibraryService(tmp_path, MIGRATIONS).create("Authoring API")
    app = create_app(tmp_path, MIGRATIONS, "test-token", enable_runner=False)
    with TestClient(app, base_url="http://localhost", headers=AUTH) as client:

        def command(path, data=None, method="post", status=201):
            response = getattr(client, method)(f"/api/direction/{path}", json=data)
            assert response.status_code == status, response.text
            result = response.json()
            if "receipt" in result:
                receipt = result["receipt"]
                assert receipt["before"]["revision"] + 1 == receipt["after"]["revision"]
            return result

        def read(path):
            response = client.get(f"/api/direction/{path}")
            assert response.status_code == 200, response.text
            return response.json()

        movie = command("movies", {"title": "Cast"})
        mid = movie["id"]
        assert read("movies")[0]["id"] == mid
        movie = command(
            f"movies/{mid}", {"expected_revision": 1, "title": "Courtyard"}, "patch", 200
        )
        subject = command(
            f"movies/{mid}/subjects",
            {
                "expected_revision": 2,
                "id": "arin",
                "name": "Arin",
                "description": "A walker.",
                "keeps": ["grey coat"],
                "identity_asset_id": None,
            },
        )
        assert subject["receipt"]["target_kind"] == "movie"
        place = {"noun": "courtyard", "description": "A courtyard.", "landmarks": ""}
        shot = {"description": "Walk.", "screen_direction": "forward", "sound": "steps"}
        data = {
            "content": {"subjects": ["arin"], "place": place},
            "delivered_frames": 102,
            "continues": True,
            "phases": [{"start_frame": frame, "content": shot} for frame in (0, 36)],
        }
        segment = command(f"movies/{mid}/segments", {**data, "expected_revision": 3})
        sid = segment["id"]
        continuous = read(f"segments/{sid}/assembled")
        assert continuous["shots"] == [shot]
        assert continuous["cut_frames"] == []
        geometry = command("places", {"name": "Courtyard"})
        angle = command(f"places/{geometry['id']}/angles", {"name": "Gate"})
        target = command(f"places/{geometry['id']}/angles", {"name": "Closer view"})
        cut = command(
            "cuts",
            {
                "segment_id": sid,
                "expected_revision": 1,
                "frame": 36,
                "from_angle_id": angle["id"],
                "to_angle_id": target["id"],
            },
        )
        assert cut["movie_id"] == mid and cut["receipt"]["target_id"] == sid
        assembled = read(f"segments/{sid}/assembled")
        assert assembled == {
            "subjects": [
                {
                    "id": "arin",
                    "name": "Arin",
                    "description": "A walker.",
                    "keeps": ["grey coat"],
                    "identity_asset": None,
                }
            ],
            "place": place,
            "shots": [shot, shot],
            "cut_frames": [36],
            "delivered_frames": 102,
            "continues": True,
        }
        phase = segment["phases"][0]
        changed = command(
            f"phases/{phase['id']}",
            {"expected_revision": 1, "content": {**shot, "description": "Wait."}},
            "patch",
            200,
        )
        command(f"phases/{phase['id']}", {"expected_revision": 1, "start_frame": 12}, "patch", 409)
        undone = command(f"receipts/{changed['receipt']['id']}/undo", status=200)
        assert undone["command"] == "undo" and undone["undoes"] == changed["receipt"]["id"]
        assert read(f"segments/{sid}/assembled") == assembled
        receipts = read(f"movies/{mid}/receipts?limit=2")
        assert [r["id"] for r in receipts] == [undone["id"], changed["receipt"]["id"]]
        assert receipts[1]["undone_at"]
        command("subjects/arin/remove", {"expected_revision": 4}, status=409)
        changed_subject = command(
            "subjects/arin", {"expected_revision": 1, "identity_asset_id": "image"}, "patch", 200
        )
        assert changed_subject["identity_asset_id"] == "image"
        cleared = command(
            "subjects/arin", {"expected_revision": 2, "identity_asset_id": None}, "patch", 200
        )
        assert cleared["identity_asset_id"] is None
        assert read(f"movies/{mid}/subjects")[0]["revision"] == 3
        assert read(f"movies/{mid}")["segments"][0]["cuts"][0]["movie_id"] == mid
        extra = command(f"movies/{mid}/segments", {**data, "expected_revision": 4})
        moved = command(
            f"segments/{extra['id']}/move", {"expected_revision": 5, "ordinal": 0}, status=200
        )
        assert moved["ordinal"] == 0
        command(
            f"segments/{extra['id']}", {"expected_revision": 1, "continues": False}, "patch", 200
        )
        new_phase = command(
            f"segments/{extra['id']}/phases",
            {"expected_revision": 2, "start_frame": 72, "content": shot},
        )
        new_phase = command(
            f"phases/{new_phase['id']}", {"expected_revision": 1, "start_frame": 84}, "patch", 200
        )
        command(f"phases/{new_phase['id']}/remove", {"expected_revision": 3}, status=200)
        command(f"segments/{extra['id']}/remove", {"expected_revision": 6}, status=200)
        assert len(read(f"movies/{mid}")["segments"]) == 1
        assert client.get(f"/api/direction/movies/{mid}/receipts?limit=0").status_code == 422
        assert client.get("/api/direction/segments/missing").status_code == 404
        command("receipts/missing/undo", status=404)


def test_schema_two_typed_content_survives_api_writes_and_reads(tmp_path):
    """HTTP must retain casts, timing and protected dialogue through every write route."""
    LibraryService(tmp_path, MIGRATIONS).create("Schema two API")
    app = create_app(tmp_path, MIGRATIONS, "test-token", enable_runner=False)
    fixture, _, _, _ = compile_fixture(GOLDEN / "inputs" / "dialogue.json")
    authored = fixture["segment"]
    with TestClient(app, base_url="http://localhost", headers=AUTH) as client:

        def command(path, data, method="post", status=201):
            response = getattr(client, method)(f"/api/direction/{path}", json=data)
            assert response.status_code == status, response.text
            return response.json()

        movie = command("movies", {"title": "Dialogue"})
        for revision, subject in enumerate(authored["subjects"], start=1):
            command(
                f"movies/{movie['id']}/subjects",
                {
                    **{key: value for key, value in subject.items() if key != "identity_asset"},
                    "identity_asset_id": None,
                    "expected_revision": revision,
                },
            )
        speech = [dict(item) for item in authored["speech"]]
        speech[0]["text"] = "  @Tomas, you came back.  "
        content = {
            "subjects": ["mara", "tomas"],
            "place": authored["place"],
            "style": "Handheld super 16.",
            "events": [{"at_frame": 48, "description": "A gull lands."}],
            "speech": speech,
            "states": [{"at_frame": 60, "subject_id": "mara", "attribute": "hood", "value": "up"}],
        }
        created = command(
            f"movies/{movie['id']}/segments",
            {
                "expected_revision": 3,
                "content": content,
                "delivered_frames": 120,
                "continues": False,
                "phases": [{"start_frame": 0, "content": authored["shots"][0]}],
            },
        )
        sid = created["id"]
        assert created["content"] == content
        assert created["content_schema"] == 2
        assert created["phases"][0]["content"]["cast"] == ["mara", "tomas"]
        assert created["phases"][0]["content_schema"] == 2
        second = command(
            f"segments/{sid}/phases",
            {"expected_revision": 1, "start_frame": 60, "content": authored["shots"][1]},
        )
        assert second["content"] == authored["shots"][1]
        place = command("places", {"name": "Cafe"})
        angle = command(f"places/{place['id']}/angles", {"name": "Window"})
        target = command(f"places/{place['id']}/angles", {"name": "Tomas"})
        command(
            "cuts",
            {
                "expected_revision": 2,
                "segment_id": sid,
                "frame": 60,
                "from_angle_id": angle["id"],
                "to_angle_id": target["id"],
            },
        )
        later = {"description": "Tomas looks away.", "screen_direction": "Left.", "sound": "Steps."}
        command(
            f"segments/{sid}/phases",
            {"expected_revision": 3, "start_frame": 90, "content": later},
        )
        response = client.get(f"/api/direction/segments/{sid}/assembled")
        assert response.status_code == 200, response.text
        assembled = response.json()
        assert assembled == {
            **authored,
            **{key: content[key] for key in ("style", "events", "speech", "states")},
            "phases": [{"start_frame": 90, **later}],
        }
        changed_shot = {**authored["shots"][1], "cast": []}
        patched = command(
            f"phases/{second['id']}",
            {"expected_revision": 1, "content": changed_shot},
            "patch",
            200,
        )
        assert patched["content"]["cast"] == []
        cleared = {
            "subjects": content["subjects"],
            "place": content["place"],
            "style": "",
            "events": [],
            "speech": [],
            "states": [],
        }
        updated = command(
            f"segments/{sid}",
            {"expected_revision": 4, "content": cleared},
            "patch",
            200,
        )
        assert updated["content"] == cleared
        assert client.get(f"/api/direction/segments/{sid}").json()["content"] == cleared

        changed = command(
            "subjects/mara", {"expected_revision": 1, "keeps": ["coat", "bag"]}, "patch", 200
        )
        assert changed["keeps"] == ["coat", "bag"]
        assert client.get(f"/api/direction/segments/{sid}/assembled").json()["subjects"][0][
            "keeps"
        ] == ["coat", "bag"]
        for keeps in (["coat", "coat"], [" coat"], [""], "coat", [1]):
            command("subjects/mara", {"expected_revision": 2, "keeps": keeps}, "patch", 422)
        cleared_subject = command(
            "subjects/mara", {"expected_revision": 2, "keeps": []}, "patch", 200
        )
        assert cleared_subject["keeps"] == []
        assert (
            client.get(f"/api/direction/movies/{movie['id']}").json()["segments"][0]["content"]
            == cleared
        )
        assert client.get(f"/api/direction/segments/{sid}/assembled").json() == {
            **{
                key: value
                for key, value in assembled.items()
                if key not in {"style", "events", "speech", "states"}
            },
            "shots": [authored["shots"][0], changed_shot],
        }
        # Unknown fields and coercion must not disappear inside typed nested DTOs.
        for changes in (
            {"extra": True},
            {"style": None},
            {"events": [{"at_frame": True, "description": "Bell."}]},
            {"speech": [{**speech[0], "off_screen": "false"}]},
            {"speech": [{**speech[0], "extra": 1}]},
            {"states": [{**content["states"][0], "subject_id": "unknown"}]},
        ):
            command(
                f"segments/{sid}",
                {"expected_revision": 5, "content": {**content, **changes}},
                "patch",
                422,
            )
        for changes in ({"extra": 1}, {"cast": None}, {"cast": [True]}):
            command(
                f"phases/{second['id']}",
                {"expected_revision": 2, "content": {**changed_shot, **changes}},
                "patch",
                422,
            )
        assert client.get(f"/api/direction/segments/{sid}").json()["revision"] == 5
        assert client.get(f"/api/direction/segments/{sid}").json()["content"] == cleared


def test_direction_openapi_exposes_schema_two_content(tmp_path):
    schemas = create_app(tmp_path, MIGRATIONS, "test-token", enable_runner=False).openapi()[
        "components"
    ]["schemas"]
    assembled = schemas["AssembledSegmentDTO"]["properties"]
    for key, dto in (
        ("phases", "LaterPhaseDTO"),
        ("events", "EventDTO"),
        ("speech", "SpeechDTO"),
        ("states", "StateDTO"),
    ):
        assert assembled[key]["items"]["$ref"].endswith(f"/{dto}")
    assert assembled["style"]["type"] == "string"
    assert "cast" in schemas["ShotDTO"]["properties"]
    assert schemas["SegmentCreate"]["properties"]["content"]["$ref"].endswith("/SegmentContentDTO")
    assert schemas["PhaseCreate"]["properties"]["content"]["$ref"].endswith("/ShotDTO")
