"""Authenticated assistant connection checks and write-only credential input."""

import json
import threading
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, SecretStr

from gaiavfx.shared.errors import ApplicationError


class CredentialState(BaseModel):
    exists: bool
    unlocked: bool
    credential_ids: list[str]
    session_credential_ids: list[str] = Field(default_factory=list)


class UnlockCredentials(BaseModel):
    passphrase: SecretStr


class SaveCredential(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    value: SecretStr
    storage: Literal["portable", "session"] = "portable"


class ConnectionTest(BaseModel):
    ok: bool
    model: str
    vision_input_tested: bool
    video_input_tested: bool = False
    structured_output_tested: bool = True
    modality: str = "image"


class ConnectionProbe(BaseModel):
    modality: Literal["text", "image", "video"] | None = None


def assistant_connection_router(w, gateway):
    router = APIRouter(prefix="/settings/assistant")

    @router.get("/credentials", response_model=CredentialState)
    def credentials():
        return w.credentials.status()

    @router.post("/credentials/unlock", response_model=CredentialState)
    def unlock(body: UnlockCredentials):
        return w.credentials.unlock(body.passphrase.get_secret_value())

    @router.post("/credentials/lock", response_model=CredentialState)
    def lock():
        w.credentials.close()
        return w.credentials.status()

    @router.put("/credentials", response_model=CredentialState)
    def save(body: SaveCredential):
        return w.credentials.save(body.id, body.value.get_secret_value(), body.storage)

    @router.post("/connections/{connection_id}/test", response_model=ConnectionTest)
    def test(connection_id: str, body: ConnectionProbe | None = None):
        settings = {
            **w.settings(),
            "assistant_agent_connection": connection_id,
            "assistant_max_tokens": 512,
        }
        from gaiavfx.shared.assistant import connection_for

        connection = connection_for(settings)
        modality = (body.modality if body else None) or (
            "image" if connection["vision"] else "text"
        )
        if connection["provider"] == "managed_local":
            raise ApplicationError(
                "Use a reference description to verify the managed local model.", 422
            )
        content = [
            {
                "type": "text",
                "text": 'Return a JSON object with ok=true and color="none". If an image is supplied, report its main color instead.',
            }
        ]
        if modality == "image":
            import base64
            import io

            from PIL import Image

            buffer = io.BytesIO()
            Image.new("RGB", (64, 64), (0, 180, 0)).save(buffer, "PNG")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,"
                        + base64.b64encode(buffer.getvalue()).decode()
                    },
                }
            )
        if modality == "video":
            from gaiavfx.infrastructure.assistant_media import native_video_parts

            if connection["video_input"] != "video_url":
                raise ApplicationError(
                    "This connection does not declare native video support.", 422
                )
            folder = w.db.root / "cache" / "assistant-probes" / str(uuid.uuid4())
            folder.mkdir(parents=True)
            source = folder / "red-blue.mp4"
            w.media.run(
                w.media.ffmpeg,
                [
                    "-v",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=red:s=128x96:r=8:d=1",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=blue:s=128x96:r=8:d=1",
                    "-filter_complex",
                    "[0:v][1:v]concat=n=2:v=1:a=0[v]",
                    "-map",
                    "[v]",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(source),
                ],
            )
            part = next(
                native_video_parts(w.media, source, folder, 0, 2000000, None, lambda: False)
            )
            content = [
                part,
                {
                    "type": "text",
                    "text": 'Describe the two successive solid colors in this silent video. Return JSON with ok=true, color="none", colors=[first_color,last_color]. Use plain English color names.',
                },
            ]
        result = gateway.run(
            settings,
            content,
            threading.Event(),
            Path(),
            response_schema={
                "type": "object",
                "properties": {
                    "ok": {"type": "boolean"},
                    "color": {"type": "string"},
                    "colors": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["ok", "color"],
                "additionalProperties": False,
            },
        )
        if (
            result.get("ok") is not True
            or (modality == "image" and "green" not in str(result.get("color", "")).lower())
            or (
                modality == "video"
                and [str(v).lower() for v in result.get("colors", [])] != ["red", "blue"]
            )
        ):
            raise ApplicationError(
                "The model did not complete the structured connection test. Check the selected capabilities.",
                422,
            )
        folder = w.db.root / "cache" / "assistant-probes"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{connection_id}-{modality}.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        return {
            "ok": True,
            "model": result["model"],
            "vision_input_tested": modality == "image",
            "video_input_tested": modality == "video",
            "modality": modality,
        }

    return router
