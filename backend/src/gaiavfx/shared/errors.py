class ApplicationError(Exception):
    """A refusal a caller can act on: its message, its HTTP status and any facts it carries (`payload`), which the
    API returns beside `detail`."""

    def __init__(self, message: str, status: int = 409, payload: dict | None = None):
        super().__init__(message)
        self.status = status
        self.payload = payload or {}


class StaleRevision(ApplicationError):
    """A compare-and-set lost: the record moved on since the caller read it. It names the revision the record is at
    now, so a caller can word its retry without reading the record again."""

    def __init__(self, kind: str, record_id: str, revision: int):
        stale = {"kind": kind, "id": record_id, "revision": revision}
        super().__init__(f"{kind} revision is stale.", 409, {"stale": stale})
        self.kind, self.record_id, self.revision = kind, record_id, revision


class ObservationReadError(ApplicationError):
    """A valid observation range unexpectedly yielded no decoded frames."""
