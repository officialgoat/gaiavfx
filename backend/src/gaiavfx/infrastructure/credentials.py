"""Portable passphrase-encrypted credentials; keys never enter task records."""

import base64
import json
import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from filelock import FileLock

from gaiavfx.shared.errors import ApplicationError


class CredentialStore:
    def __init__(self, root: Path):
        self.path = root / "credentials" / "assistant.json.enc"
        self.cipher = None
        self.session_values = {}
        self.lock = threading.RLock()

    def status(self):
        with self.lock:
            return {
                "exists": self.path.exists(),
                "unlocked": self.cipher is not None,
                "credential_ids": sorted(
                    set(self._read() if self.cipher else {}) | set(self.session_values)
                ),
                "session_credential_ids": sorted(self.session_values),
            }

    def sensitive_values(self):
        """In-memory redaction values only; never serialize this method's result."""
        with self.lock:
            values = list(self.session_values.values())
            if self.cipher is not None:
                try:
                    values.extend(self._read().values())
                except ApplicationError:
                    pass
            return tuple(values)

    def _read(self):
        if self.cipher is None:
            raise ApplicationError("Unlock the assistant credential store in Settings.", 422)
        try:
            record = json.loads(self.path.read_text(encoding="utf-8"))
            return json.loads(self.cipher.decrypt(record["encrypted"].encode()))
        except (InvalidToken, ValueError, KeyError, OSError) as exc:
            raise ApplicationError(
                "The credential store could not be unlocked. Check the passphrase and file.", 422
            ) from exc

    def unlock(self, passphrase):
        if len(passphrase) < 8:
            raise ApplicationError("Use a passphrase of at least eight characters.", 422)
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(str(self.path) + ".lock"):
                record = (
                    json.loads(self.path.read_text(encoding="utf-8"))
                    if self.path.exists()
                    else {"version": 1, "salt": base64.b64encode(os.urandom(16)).decode()}
                )
                if record.get("version") != 1:
                    raise ApplicationError(
                        "This credential store requires a newer GAIAVFX version.", 422
                    )
                derived = PBKDF2HMAC(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=base64.b64decode(record["salt"]),
                    iterations=600000,
                ).derive(passphrase.encode())
                self.cipher = Fernet(base64.urlsafe_b64encode(derived))
                try:
                    if "encrypted" in record:
                        self._read()
                    else:
                        self._write(record, {})
                except Exception:
                    self.cipher = None
                    raise
        return self.status()

    def _write(self, record, values):
        record["encrypted"] = self.cipher.encrypt(json.dumps(values).encode()).decode()
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record), encoding="utf-8")
        temporary.replace(self.path)

    def save(self, id, value, storage="portable"):
        if not id or len(id) > 120:
            raise ApplicationError("Choose a credential name of 1–120 characters.", 422)
        if storage == "session":
            with self.lock:
                if value:
                    self.session_values[id] = value
                else:
                    self.session_values.pop(id, None)
            return self.status()
        if storage != "portable":
            raise ApplicationError("Choose session or portable credential storage.", 422)
        if self.cipher is None:
            raise ApplicationError("Unlock the assistant credential store in Settings.", 422)
        with self.lock, FileLock(str(self.path) + ".lock"):
            values = self._read()
            if value:
                values[id] = value
            else:
                values.pop(id, None)
            self._write(json.loads(self.path.read_text(encoding="utf-8")), values)
        return self.status()

    def get(self, id):
        if id is None:
            return None
        with self.lock:
            if id in self.session_values:
                return self.session_values[id]
            if self.cipher is None and not self.path.exists():
                raise ApplicationError(
                    "Re-enter the session API key in Settings before retrying.", 422
                )
            value = self._read().get(id)
        if not value:
            raise ApplicationError(
                "The selected API credential is missing. Add it in Settings.", 422
            )
        return value

    def close(self):
        with self.lock:
            self.cipher = None
            self.session_values.clear()
