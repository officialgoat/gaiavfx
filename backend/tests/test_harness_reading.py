"""Durable vision turns, portable caches and strict notes without any model runtime."""

import json
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import harness_support
import pytest
from harness_eval_scripts import IMAGE_NOTES, VIDEO_NOTES
from harness_support import ScriptedClient, action

from gaiavfx.application.harness.actions import parse_action
from gaiavfx.application.harness.context import IMAGE_TOKENS, assemble_reading
from gaiavfx.application.harness.driver import AgentRunner
from gaiavfx.application.harness.loop import Harness
from gaiavfx.application.harness.model import AssistantModel
from gaiavfx.application.harness.prompt import SYSTEM
from gaiavfx.application.harness.reading import (
    TEXT,
    evenly_spaced,
    load_parts,
    notes_schema,
    prepare_reference,
    validate_notes,
)
from gaiavfx.application.harness.session import AgentSession
from gaiavfx.application.harness.tools.reading import AnalyzeReferenceVideo, DescribeReference
from gaiavfx.infrastructure.assistant import redacted_request
from gaiavfx.modules.agent.service import AgentStore
from gaiavfx.shared.errors import ApplicationError

world = harness_support.world


def test_reading_turn_committed_before_call_and_atomic_notes(world):
    work, setup = world
    model = ScriptedClient()
    harness = Harness(work.db, AgentStore(), model, work.settings, library=work.library)
    row = harness.open(setup["movie_id"])

    def read(messages, schema):
        turns = harness.turns(row["id"])
        assert turns[-1]["state"] == "requested"
        assert harness.read(row["id"])["document"]["focus"]
        assert len(messages) == 2 and messages[0] == {"role": "system", "content": SYSTEM}
        assert all(
            word not in json.dumps(messages)
            for word in ("Whiteboard:", "Current view:", "Available tools:")
        )
        assert schema == notes_schema("image", 1)
        return IMAGE_NOTES

    def finish(messages, schema):
        saved = harness.read(row["id"])["document"]
        assert saved["focus"] is None and len(saved["board"]["references"]) == 1
        assert all(isinstance(m["content"], str) for m in messages)
        assert "image_url" not in json.dumps(harness.turns(row["id"]))
        return action("say", text="A wide shot followed by a close-up.")

    model.replies = iter([action("describe_reference", asset_id=setup["identity"]), read, finish])
    harness.post_message(row["id"], "Discuss this", "image", [setup["identity"]])
    AgentRunner(work.db, harness).run_once()
    saved = harness.read(row["id"])
    assert saved["state"] == "idle"
    assert setup["identity"] in saved["document"]["summary"]
    notes = next(
        t for t in harness.turns(row["id"]) if (t["action"] or {}).get("tool") == "reference_notes"
    )
    assert notes["provenance"]["context"]["images"] == 1
    assert notes["provenance"]["context"]["state"] == "movie/plan/reading"


def test_reading_scope_vision_and_kind_are_enforced_before_preparation(world, monkeypatch):
    work, setup = world
    harness = Harness(work.db, AgentStore(), None, work.settings, library=work.library)
    row = harness.open(setup["movie_id"])
    harness.post_message(row["id"], "Read", "read", [setup["identity"]])
    row = harness.read(row["id"])
    context = harness.context(
        row, AgentSession.model_validate(row["document"]), harness.turns(row["id"])
    )
    tool = DescribeReference()
    # Attachments survive falling out of the recent-import projection.
    context.state.assets = []
    assert tool.narrow(context)["properties"]["asset_id"]["enum"] == [setup["identity"]]
    assert AnalyzeReferenceVideo().narrow(context) is None
    assert tool.narrow(replace(context, attachments=[])) is None
    prepared = Mock(side_effect=AssertionError("Preparation must not run"))
    monkeypatch.setattr("gaiavfx.application.harness.tools.reading.prepare_reference", prepared)
    remote = {
        "id": "text",
        "name": "Text",
        "base_url": "https://example.invalid",
        "model": "text",
        "vision": False,
    }
    text_context = replace(
        context, settings={"assistant_agent_connection": "text", "assistant_connections": [remote]}
    )
    result = tool.run(text_context, tool.Input(asset_id=setup["identity"]))
    assert not result.ok and "vision_unavailable" in result.discrepancy
    assert not tool.run(context, tool.Input(asset_id="invented")).ok
    with pytest.raises(ApplicationError, match="asset_id"):
        parse_action(
            action("describe_reference", asset_id="invented"), [(tool, tool.narrow(context))]
        )
    prepared.assert_not_called()


def test_video_cache_time_bounds_portability_and_no_transaction_during_sampling(
    world, monkeypatch, tmp_path
):
    work, setup = world
    original = work.library.asset_info
    monkeypatch.setattr(
        work.library,
        "asset_info",
        lambda session, aid: {**original(session, aid), "info": {"duration": 95}},
    )
    source = work.library.root / "fixture.jpg"
    from PIL import Image

    Image.new("RGB", (20, 10)).save(source)
    calls = []

    def sample(media, path, folder, start_us, end_us, cancelled):
        assert start_us == 0 and end_us == 60_000_000
        calls.append(end_us)
        frames = []
        for i in range(61):
            target = folder / f"raw-{i}.jpg"
            shutil.copyfile(source, target)
            frames.append({"source_us": min(i * 1_000_000, end_us - 1), "path": str(target)})
        return frames

    monkeypatch.setattr("gaiavfx.application.harness.reading.sample_video", sample)
    payload, focus = prepare_reference(
        work.library, setup["take"], "video", "Camera?", lambda: False
    )
    assert payload["frames"] == 6 and payload["seconds"] == 60 and "first 60" in payload["notice"]
    parts = load_parts(work.db.root, focus["parts"])
    assert sum(p["type"] == "image_url" for p in parts) == 6
    assert any("frame 5 at t=60.0s" in p.get("text", "") for p in parts)
    assert all(not Path(p).is_absolute() for p in focus["parts"])
    second, second_focus = prepare_reference(
        work.library, setup["take"], "video", "Different question", lambda: False
    )
    assert len(calls) == 1 and second == payload
    assert second_focus["parts"] == focus["parts"]
    relocated = tmp_path / "relocated"
    shutil.copytree(work.db.root / "cache", relocated / "cache")
    assert load_parts(relocated, focus["parts"]) == parts
    with pytest.raises(ApplicationError, match="cached reference"):
        load_parts(work.db.root, ["../outside.json"])
    with pytest.raises(ApplicationError, match="cancelled"):
        prepare_reference(work.library, setup["take"], "video", "", lambda: True)


@pytest.mark.parametrize("bad_again", [False, True])
def test_invalid_notes_retry_once_and_keep_focus_until_accepted(world, bad_again):
    work, setup = world
    invalid = {**IMAGE_NOTES, "shot_size": "invented"}
    model = ScriptedClient(
        [
            action("describe_reference", asset_id=setup["identity"]),
            invalid,
            invalid if bad_again else IMAGE_NOTES,
            action("say", text="Read"),
        ]
    )
    harness = Harness(work.db, AgentStore(), model, work.settings, library=work.library)
    row = harness.open(setup["movie_id"])
    harness.post_message(row["id"], "Read", "retry", [setup["identity"]])
    AgentRunner(work.db, harness).run_once()
    saved = harness.read(row["id"])
    assert saved["state"] == ("failed" if bad_again else "idle")
    assert saved["document"]["model_retries"] == 1
    assert bool(saved["document"]["focus"]) == bad_again
    assert len(saved["document"]["board"]["references"]) == (0 if bad_again else 1)
    assert TEXT["retry"] in json.dumps(model.calls[2][0])
    assert all(t["state"] != "requested" for t in harness.turns(row["id"]))


def test_notes_frame_bounds_enums_lengths_and_image_budget():
    assert validate_notes(VIDEO_NOTES, "video", 6) == VIDEO_NOTES
    for invalid in (
        {**VIDEO_NOTES, "suggested_techniques": ["invented"]},
        {**VIDEO_NOTES, "shots": [{**VIDEO_NOTES["shots"][0], "camera_move": "teleport"}]},
        {**VIDEO_NOTES, "shots": [{**VIDEO_NOTES["shots"][0], "to_frame": 6}]},
        {**VIDEO_NOTES, "shots": [{**VIDEO_NOTES["shots"][0], "from_frame": 3, "to_frame": 2}]},
        {**VIDEO_NOTES, "lighting": "x" * 201},
        {**VIDEO_NOTES, "extra": "no"},
    ):
        with pytest.raises(ApplicationError, match="Invalid reference notes"):
            validate_notes(invalid, "video", 6)
    parts = [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,bytes"}}] * 6
    args = (SYSTEM, TEXT["video"], "Camera?", parts, notes_schema("video"), 32768, 2400)
    report = assemble_reading(*args, schema_billed=True).report
    assert report["images"] == 6 and report["image_reserve"] == 6 * IMAGE_TOKENS
    assert report["fixed"] < 32768 and report["board"] == report["view"] == report["turns"] == 0
    with pytest.raises(ApplicationError, match="image tokens"):
        assemble_reading(*args[:5], 8192, 2400)
    frames = [{"source_us": t} for t in [0, 1, 2, 3, 20, 40, 60]]
    chosen = evenly_spaced(frames, 4)
    assert [f["source_us"] for f in chosen] == [0, 20, 40, 60]


def test_gateway_notes_survive_adapter_metadata_and_logs_redact_without_mutating(tmp_path):
    gateway = SimpleNamespace(
        run=lambda *a, **k: {
            **IMAGE_NOTES,
            "metrics": {"usage": {"prompt_tokens": 42}},
            "request": "private",
        }
    )
    reply = AssistantModel(gateway, None).call(
        {}, [], notes_schema("image"), lambda: False, tmp_path / "call.log"
    )
    assert reply.raw == IMAGE_NOTES and reply.input_tokens == 42
    url = "data:image/jpeg;base64,PRIVATE"
    body = {
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}
        ]
    }
    logged = redacted_request(body)
    assert (
        "PRIVATE" not in json.dumps(logged)
        and logged["messages"][0]["content"][0]["image_url"]["sha256"]
    )
    assert body["messages"][0]["content"][0]["image_url"]["url"] == url


def test_local_wire_retains_images_while_request_file_omits_them(tmp_path, monkeypatch):
    import threading

    import httpx

    from gaiavfx.infrastructure.assistant import LocalAssistant

    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,PRIVATE"}}
                ],
            }
        ]
    }
    sent = []

    def respond(request):
        path = request.url.path
        if path == "/apply-template":
            return httpx.Response(200, json={"prompt": "Read"})
        if path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1, 2]})
        if path == "/v1/chat/completions":
            sent.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(IMAGE_NOTES)}}
                    ]
                },
            )
        return httpx.Response(200, json={})

    client = httpx.Client
    monkeypatch.setattr(
        "gaiavfx.infrastructure.assistant.httpx.Client",
        lambda **kw: client(transport=httpx.MockTransport(respond), **kw),
    )
    monkeypatch.setattr("gaiavfx.infrastructure.assistant.resource_identity", lambda settings: {})
    monkeypatch.setattr(
        "gaiavfx.infrastructure.assistant.subprocess.Popen",
        lambda *a, **k: pytest.fail("No process may start"),
    )
    assistant = LocalAssistant()
    assistant.child = SimpleNamespace(poll=lambda: None)
    assistant.runtime = (json.dumps([{}, 32768]), 9999)
    settings = {
        "assistant_context": 32768,
        "assistant_executable": "unused",
        "assistant_model": "unused",
        "assistant_projector": "unused",
    }
    log = tmp_path / "local.log"
    result = assistant.run(
        settings,
        None,
        threading.Event(),
        log,
        notes_schema("image"),
        keep_alive=True,
        messages=body["messages"],
    )
    assert sent[0]["messages"] == body["messages"]
    logged = log.with_suffix(".request.json").read_text()
    assert "PRIVATE" not in logged and "[image bytes omitted]" in logged and "sha256" in logged
    assert "PRIVATE" not in json.dumps(result["request"])
    assert result["metrics"]["budget"]["visual_token_reserve"] == 2048
