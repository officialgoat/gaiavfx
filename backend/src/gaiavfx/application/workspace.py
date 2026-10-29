from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

from gaiavfx.infrastructure.comfy import ComfyGateway
from gaiavfx.infrastructure.credentials import CredentialStore
from gaiavfx.infrastructure.database import Database
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.infrastructure.records import SettingRecord
from gaiavfx.infrastructure.requests import Requests
from gaiavfx.infrastructure.telemetry import Telemetry
from gaiavfx.modules.catalog.application import Catalog
from gaiavfx.modules.library.application import MediaLibrary
from gaiavfx.setup.manifest import connection_defaults
from gaiavfx.shared.errors import ApplicationError

DEFAULT_SETTINGS = {
    "comfy_url": "http://127.0.0.1:8188",
    "assistant_executable": "",
    "assistant_model": "",
    "assistant_projector": "",
    "assistant_context": 8192,
    "assistant_agent_context": 32768,
    "assistant_connections": [],
    "assistant_agent_connection": "local",
    "assistant_agent_thinking": "inherit",
    "agent_autonomy": "off",
    "agent_budget_jobs": 6,
    "agent_keep_warm_seconds": 180,
    "engines_root": "",
    # The expressive recordings library (ADR 0016 P5): one folder per library, a subfolder per category.
    "expressive_library": "",
}


class Workspace:
    def __init__(self, root: Path, media: MediaTools | None = None):
        self.db = Database(root)
        self.credentials = CredentialStore(root)
        self.telemetry = Telemetry(root, self.credentials)
        self.media = media or MediaTools()
        self.library = MediaLibrary(self.db, self.media)
        self.catalog = Catalog()
        self.comfy = ComfyGateway()
        self.requests = Requests(self.db)

    def settings(self) -> dict:
        if not self.db.path.exists():
            return {**deepcopy(DEFAULT_SETTINGS), **connection_defaults()}
        with self.db.transaction() as session:
            row = session.get(SettingRecord, "connections")
            return {
                key: (row.value if row else {}).get(key, deepcopy(default))
                for key, default in DEFAULT_SETTINGS.items()
            }

    def initialize_settings(self):
        """Seed only a newly created library. Existing libraries keep their decisions."""
        with self.db.transaction() as session:
            if session.get(SettingRecord, "connections") is None:
                session.add(
                    SettingRecord(
                        key="connections",
                        value={**deepcopy(DEFAULT_SETTINGS), **connection_defaults()},
                    )
                )

    def save_settings(self, value: dict):
        from gaiavfx.shared.assistant import AssistantConnection, connection_for

        value = deepcopy(value)
        value["assistant_connections"] = [
            AssistantConnection.model_validate(c).model_dump()
            for c in value.get("assistant_connections", [])
        ]
        if len({c["id"] for c in value["assistant_connections"]}) != len(
            value["assistant_connections"]
        ):
            raise ApplicationError("Assistant connection IDs must be unique.", 422)
        connection_for(value)
        parsed = urlparse(value["comfy_url"])
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ApplicationError(
                "Enter a ComfyUI HTTP(S) address without embedded credentials.", 422
            )
        value["comfy_url"] = value["comfy_url"].rstrip("/")
        engines_root = str(value.get("engines_root") or "").strip()
        if engines_root and not Path(engines_root).is_absolute():
            raise ApplicationError(
                "Enter an absolute folder for the local engines, or leave it empty.", 422
            )
        value["engines_root"] = engines_root
        expressive_library = str(value.get("expressive_library") or "").strip()
        if expressive_library and not Path(expressive_library).is_absolute():
            raise ApplicationError(
                "Enter an absolute folder for the expressive recordings, or leave it empty.", 422
            )
        value["expressive_library"] = expressive_library
        with self.db.transaction() as session:
            row = session.get(SettingRecord, "connections")
            if row:
                row.value = deepcopy(value)
            else:
                session.add(SettingRecord(key="connections", value=deepcopy(value)))
        return value

    def import_media(
        self, source: Path, name: str, collection_id=None, entity_id=None, role="reference"
    ):
        """Prepare outside the transaction; register and associate atomically."""
        prepared = self.library.prepare(source, name)
        with self.db.transaction() as session:
            result = self.library.register(
                session,
                prepared,
                {"type": "import", "original_name": name},
                collection_id,
                report_disposition=True,
            )
            if entity_id:
                self.catalog.assign(session, entity_id, result["asset"]["id"], role)
            return result
