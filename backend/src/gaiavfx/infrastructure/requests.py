"""Idempotent intent reservations, persisted before preflight and external IO."""

import hashlib
import json
import threading
from contextlib import contextmanager
from weakref import WeakValueDictionary

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from gaiavfx.shared.errors import ApplicationError

from .database import Base, new_id, timestamp


class RequestRecord(Base):
    __tablename__ = "execution_requests"
    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    operation: Mapped[str] = mapped_column(String(160))
    fingerprint: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class Requests:
    def __init__(self, db):
        self.db = db
        self.guard = threading.Lock()
        self.locks = WeakValueDictionary()

    @contextmanager
    def reserve(self, key, operation, body):
        if not key:
            yield new_id()
            return
        if len(key) > 120 or not key.replace("-", "").replace("_", "").isalnum():
            raise ApplicationError(
                "Use a stable request identifier of at most 120 letters, digits, underscores or hyphens.",
                422,
            )
        with self.guard:
            lock = self.locks.setdefault(key, threading.RLock())
        with lock:
            digest = hashlib.sha256(
                json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            with self.db.transaction() as session:
                row = session.get(RequestRecord, key)
                if row:
                    if row.operation != operation or row.fingerprint != digest:
                        raise ApplicationError(
                            "This request identifier already belongs to a different intent."
                        )
                    resource_id = row.resource_id
                else:
                    resource_id = new_id()
                    session.add(
                        RequestRecord(
                            key=key,
                            operation=operation,
                            fingerprint=digest,
                            resource_id=resource_id,
                        )
                    )
            yield resource_id
