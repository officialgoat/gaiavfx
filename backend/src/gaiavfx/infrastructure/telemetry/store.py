"""A local delivery journal, not a creative-work queue. No network or credentials."""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager


class QueueFull(Exception):
    pass


class Store:
    def __init__(self, root):
        self.root = root / "telemetry"
        self.path = self.root / "delivery.sqlite3"
        self.lock = threading.RLock()
        self.limit = 10 * 1024**3

    @contextmanager
    def db(self):
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=1)
            db.row_factory = sqlite3.Row
            try:
                db.execute("PRAGMA journal_mode=WAL")
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS destinations (
                        id TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS observations (
                        id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, session_id TEXT NOT NULL,
                        destination TEXT NOT NULL, payload BLOB, state TEXT NOT NULL,
                        created REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                        next_try REAL NOT NULL DEFAULT 0, error TEXT, delivered REAL);
                    CREATE INDEX IF NOT EXISTS delivery_due ON observations(state, next_try);
                    CREATE INDEX IF NOT EXISTS observations_trace ON observations(trace_id);
                    CREATE INDEX IF NOT EXISTS observations_session ON observations(session_id);
                    CREATE TABLE IF NOT EXISTS active (
                        id TEXT PRIMARY KEY, document TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS associations (
                        id TEXT PRIMARY KEY, context TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS media (
                        id TEXT PRIMARY KEY, destination TEXT NOT NULL, document TEXT NOT NULL,
                        state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                        next_try REAL NOT NULL DEFAULT 0, error TEXT, remote_id TEXT);
                    CREATE TABLE IF NOT EXISTS uploaded_media (
                        destination TEXT NOT NULL, sha256 TEXT NOT NULL, remote_id TEXT NOT NULL,
                        PRIMARY KEY(destination, sha256));
                """)
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def read_config(self):
        if not self.path.exists():
            return {}
        with self.db() as db:
            row = db.execute("SELECT value FROM config WHERE key='settings'").fetchone()
            return json.loads(row[0]) if row else {}

    def write_config(self, value, destination):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO config VALUES ('settings', ?)", (json.dumps(value),))
            if destination:
                db.execute(
                    "INSERT OR REPLACE INTO destinations VALUES (?, ?)",
                    (destination["id"], json.dumps(destination)),
                )

    def destination(self, id):
        with self.db() as db:
            row = db.execute("SELECT value FROM destinations WHERE id=?", (id,)).fetchone()
        return json.loads(row[0]) if row else None

    def size(self):
        size = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
        if self.path.exists():
            # Acknowledged bodies free SQLite pages for reuse. Charging those pages
            # forever would prevent recording again after a previously full queue drains.
            with sqlite3.connect(self.path, timeout=1) as db:
                reusable = db.execute("PRAGMA freelist_count").fetchone()[0]
                page_size = db.execute("PRAGMA page_size").fetchone()[0]
            size -= reusable * page_size
        return max(0, size)

    def require_space(self, amount=0):
        if self.size() + amount > self.limit:
            raise QueueFull("Local telemetry storage is full. Pending evidence was preserved.")

    def gap(self, message):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO config VALUES ('capture_error', ?)", (message,))

    def begin(self, document):
        encoded = json.dumps(document, ensure_ascii=False)
        with self.lock:
            self.require_space(len(encoded.encode()))
            with self.db() as db:
                db.execute("INSERT OR REPLACE INTO active VALUES (?, ?)", (document["id"], encoded))

    def enqueue(self, id, trace_id, session_id, destination, payload):
        with self.lock:
            self.require_space(len(payload))
            with self.db() as db:
                db.execute(
                    "INSERT OR IGNORE INTO observations "
                    "(id, trace_id, session_id, destination, payload, state, created) "
                    "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                    (id, trace_id, session_id, destination, payload, time.time()),
                )
                db.execute("DELETE FROM active WHERE id=?", (id,))

    def associate(self, id, context):
        if not id or not context:
            return
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO associations VALUES (?, ?)", (id, json.dumps(context))
            )

    def association(self, id):
        if not self.path.exists() or not id:
            return None
        with self.db() as db:
            row = db.execute("SELECT context FROM associations WHERE id=?", (id,)).fetchone()
        return json.loads(row[0]) if row else None

    def update(self, table, id, **values):
        assert table in {"observations", "media"}
        allowed = {"state", "attempts", "next_try", "error", "delivered", "payload", "remote_id"}
        assert set(values) <= allowed
        with self.db() as db:
            db.execute(
                f"UPDATE {table} SET " + ", ".join(f"{key}=?" for key in values) + " WHERE id=?",
                (*values.values(), id),
            )

    def next(self, table, destination):
        assert table in {"observations", "media"}
        with self.db() as db:
            row = db.execute(
                f"SELECT * FROM {table} WHERE destination=? AND state IN ('pending','uncertain') "
                "AND next_try <= ? ORDER BY rowid LIMIT 1",
                (destination, time.time()),
            ).fetchone()
        return dict(row) if row else None

    def recover_delivery(self):
        if not self.path.exists():
            return
        with self.db() as db:
            db.execute("UPDATE observations SET state='uncertain' WHERE state='sending'")
            db.execute("UPDATE media SET state='pending' WHERE state='sending'")

    def status(self):
        empty = {
            "observations": {},
            "media": {},
            "bytes": 0,
            "capture_error": None,
            "last_delivery": None,
            "errors": [],
        }
        if not self.path.exists():
            return empty
        with self.db() as db:
            for table in ("observations", "media"):
                empty[table] = dict(
                    db.execute(f"SELECT state, COUNT(*) FROM {table} GROUP BY state")
                )
            error = db.execute("SELECT value FROM config WHERE key='capture_error'").fetchone()
            empty["capture_error"] = error[0] if error else None
            empty["last_delivery"] = db.execute(
                "SELECT MAX(delivered) FROM observations"
            ).fetchone()[0]
            empty["errors"] = [
                dict(r)
                for r in db.execute(
                    "SELECT id,state,error FROM observations WHERE error IS NOT NULL "
                    "UNION ALL SELECT id,state,error FROM media WHERE error IS NOT NULL LIMIT 12"
                )
            ]
        empty["bytes"] = self.size()
        return empty

    def retry(self, destination):
        with self.db() as db:
            # Uncertain receipts are reconciled only. A click must not duplicate observations.
            for table in ("observations", "media"):
                db.execute(
                    f"UPDATE {table} SET state=CASE WHEN state='uncertain' THEN state ELSE 'pending' END, "
                    "next_try=0, error=NULL WHERE destination=? AND state IN "
                    "('pending','blocked','uncertain','failed')",
                    (destination,),
                )
            if self.size() < self.limit:
                db.execute("DELETE FROM config WHERE key='capture_error'")
