"""Deterministic reference preparation and typed notes; never calls a model."""

import hashlib
import json
from pathlib import Path
from typing import Annotated

from pydantic import Field, ValidationError, model_validator

from gaiavfx.application.harness.actions import _detail, _enforced, inlined
from gaiavfx.application.harness.board import write_entry
from gaiavfx.application.harness.knowledge import load
from gaiavfx.infrastructure.assistant import image_part
from gaiavfx.infrastructure.assistant_media import sample_video
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError

PACK = load()["cinematography"]
TEXT = load()["prompts"]["reading"]
LIMITS = PACK["reading"]


class ImageNotes(StrictModel):
    subject: str = Field(max_length=300)
    place: str = Field(max_length=300)
    lighting: str = Field(max_length=200)
    shot_size: str
    camera_height: str
    mood: str = Field(max_length=120)
    notable: list[Annotated[str, Field(max_length=120)]] = Field(max_length=5)


class ShotNotes(StrictModel):
    from_frame: int = Field(ge=0, strict=True)
    to_frame: int = Field(ge=0, strict=True)
    shot_size: str
    camera_move: str
    description: str = Field(max_length=200)

    @model_validator(mode="after")
    def ordered(self):
        if self.to_frame < self.from_frame:
            raise ValueError("to_frame precedes from_frame")
        return self


class VideoNotes(StrictModel):
    shots: list[ShotNotes] = Field(min_length=1, max_length=6)
    continuity: str = Field(max_length=300)
    lighting: str = Field(max_length=200)
    suggested_techniques: list[str] = Field(max_length=3)


def notes_schema(kind, frames=6):
    schema = inlined((ImageNotes if kind == "image" else VideoNotes).model_json_schema())
    vocabulary = PACK["vocabulary"]

    def pin(node):
        if isinstance(node, dict):
            for name, rule in node.get("properties", {}).items():
                key = {
                    "shot_size": "shot_sizes",
                    "camera_move": "camera_moves",
                    "camera_height": "camera_heights",
                }.get(name)
                if key:
                    rule["enum"] = vocabulary[key]
                elif name == "suggested_techniques":
                    rule["items"]["enum"] = vocabulary["techniques"]
                elif name in {"from_frame", "to_frame"}:
                    rule["maximum"] = frames - 1
            for item in node.values():
                pin(item)
        elif isinstance(node, list):
            for item in node:
                pin(item)

    pin(schema)
    return schema


def validate_notes(raw, kind, frames):
    try:
        notes = (ImageNotes if kind == "image" else VideoNotes).model_validate(raw)
        problems = _enforced(notes_schema(kind, frames), raw)
        if kind == "video" and any(shot.to_frame >= frames for shot in notes.shots):
            problems.append("shot frames must name supplied samples")
        if problems:
            raise ValueError("; ".join(problems))
        return notes.model_dump()
    except (ValidationError, ValueError) as exc:
        detail = _detail(exc, "invalid notes") if isinstance(exc, ValidationError) else str(exc)
        raise ApplicationError(
            TEXT["refusals"]["invalid_notes"].format(detail=detail)[:2000], 422
        ) from exc


def evenly_spaced(frames, maximum):
    if len(frames) <= maximum:
        return frames
    first, last = frames[0]["source_us"], frames[-1]["source_us"]
    chosen = {
        min(range(len(frames)), key=lambda i: abs(frames[i]["source_us"] - target))
        for target in (first + (last - first) * i / (maximum - 1) for i in range(maximum))
    }
    return [frames[i] for i in sorted(chosen)]


def prepare_reference(library, asset_id, kind, question, cancelled):
    # Close the read transaction before decoding or writing any cached media.
    with library.database.transaction() as session:
        asset = library.asset_info(session, asset_id)
        source = library.path(session, asset_id)
    if asset["kind"] != kind:
        raise ApplicationError(TEXT["refusals"]["wrong_kind"].format(kind=kind), 422)
    key = hashlib.sha256(
        json.dumps(["reading-1", asset["sha256"], kind, LIMITS], sort_keys=True).encode()
    ).hexdigest()
    folder = library.root / "cache" / "assistant" / "reading" / key
    manifest = folder / "manifest.json"
    if cancelled():
        raise ApplicationError("cancelled by the user", 409)
    cached = None
    if manifest.is_file():
        try:
            cached = json.loads(manifest.read_text("utf-8"))
            load_parts(library.root, cached["parts"])
        except (OSError, ValueError, KeyError, TypeError, ApplicationError):
            cached = None
    if cached is None:
        folder.mkdir(parents=True, exist_ok=True)
        seconds, truncated = 0, False
        if kind == "image":
            parts = [image_part(source)]
        else:
            if library.media is None:
                raise ApplicationError(TEXT["refusals"]["media_unavailable"], 422)
            duration = round(asset["info"].get("duration", 0) * 1_000_000)
            stop = min(duration, LIMITS["video_seconds_max"] * 1_000_000)
            seconds, truncated = stop / 1_000_000, stop < duration
            frames = evenly_spaced(
                sample_video(library.media, source, folder, 0, stop, cancelled),
                LIMITS["frames_max"],
            )
            parts = []
            for index, frame in enumerate(frames):
                parts.extend(
                    [
                        {
                            "type": "text",
                            "text": TEXT["frame"].format(
                                index=index, seconds=frame["source_us"] / 1_000_000
                            ),
                        },
                        image_part(Path(frame["path"])),
                    ]
                )
            if truncated:
                parts.append({"type": "text", "text": TEXT["truncated"].format(seconds=seconds)})
        paths = []
        for index, part in enumerate(parts):
            target = folder / f"part-{index}.json"
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(part), encoding="utf-8")
            temporary.replace(target)
            paths.append(target.relative_to(library.root).as_posix())
        cached = {
            "parts": paths,
            "frames": sum(p["type"] == "image_url" for p in parts),
            "seconds": seconds,
            "truncated": truncated,
        }
        temporary = manifest.with_suffix(".tmp")
        temporary.write_text(json.dumps(cached), encoding="utf-8")
        temporary.replace(manifest)
    payload = {"asset_id": asset_id, "kind": kind, **{k: cached[k] for k in ("frames", "seconds")}}
    if cached["truncated"]:
        payload["notice"] = TEXT["truncated"].format(seconds=cached["seconds"])
    return payload, {
        "asset_id": asset_id,
        "kind": kind,
        "parts": cached["parts"],
        "question": question,
    }


def load_parts(root, paths):
    allowed = (root / "cache" / "assistant" / "reading").resolve()
    try:
        parts = []
        for name in paths:
            path = (root / name).resolve()
            if Path(name).is_absolute() or not path.is_relative_to(allowed):
                raise ValueError("Non-portable reading path")
            parts.append(json.loads(path.read_text("utf-8")))
        images = sum(part["type"] == "image_url" for part in parts)
        if not 1 <= images <= LIMITS["frames_max"]:
            raise ValueError("Invalid frame count")
        return parts
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ApplicationError(TEXT["refusals"]["missing_parts"], 422) from exc


def retain_notes(document, notes, turn):
    asset_id = document.focus["asset_id"]
    # The paragraph indexes the full typed notes retained on the turn. Its summary
    # keeps identity and observations visible within the bounded whiteboard.
    paragraph = "; ".join(
        f"{key}: {json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value}"
        for key, value in notes.items()
    )
    entry = write_entry(
        document.board,
        "references",
        paragraph if len(paragraph) <= 2000 else paragraph[:1999] + "?",
        turn,
    )
    entry.summary = (TEXT["reference"].format(asset_id=asset_id) + ": " + paragraph)[:200]
    document.focus = None
