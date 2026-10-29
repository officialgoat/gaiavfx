"""Provider-neutral assistant connection contracts."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from .contracts import StrictModel


class AssistantConnection(StrictModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=120)
    provider: Literal["openai_compatible"] = "openai_compatible"
    base_url: str
    model: str = Field(min_length=1, max_length=240)
    credential_id: str | None = None
    vision: bool = True
    video_input: Literal["none", "video_url"] = "none"
    structured_output: Literal["json_schema", "json_object"] = "json_schema"
    reasoning: Literal["none", "reasoning_effort", "openrouter"] = "none"
    sampling: bool = True
    streaming: bool = False
    context_tokens: int = Field(default=32768, ge=4096, le=2000000)
    max_output_tokens: int = Field(default=4096, ge=512, le=65536)
    input_cost_per_million: float | None = Field(default=None, ge=0)
    output_cost_per_million: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_endpoint(self):
        parsed = urlsplit(self.base_url)
        if (
            self.id == "local"
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Use an HTTP(S) API base URL without embedded credentials and a connection ID other than local."
            )
        self.base_url = self.base_url.rstrip("/")
        return self


def connection_for(settings, phase="agent"):
    selected = settings.get("assistant_agent_connection", "local")
    if selected == "local":
        return {
            "id": "local",
            "name": "Local model",
            "provider": "managed_local",
            "vision": True,
            "model": settings.get("assistant_model", "").replace("\\", "/").rsplit("/", 1)[-1],
        }
    from .errors import ApplicationError

    candidate = next(
        (c for c in settings.get("assistant_connections", []) if c["id"] == selected), None
    )
    if candidate is None:
        raise ApplicationError(
            "The selected assistant connection no longer exists. Choose a connection in Settings.",
            422,
        )
    return AssistantConnection.model_validate(candidate).model_dump()
