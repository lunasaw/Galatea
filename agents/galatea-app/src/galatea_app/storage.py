"""Single-writer SQLite aggregate, append-only history, receipts and leased outbox."""

from __future__ import annotations

from contextlib import contextmanager, closing
import json
from pathlib import Path
import sqlite3
import threading
import time

from .common import canonical, require, uid

MIGRATION = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, session_id TEXT NOT NULL,
 status TEXT NOT NULL, version INTEGER NOT NULL, plan_revision INTEGER NOT NULL,
 control_epoch INTEGER NOT NULL, snapshot_json TEXT NOT NULL, aggregate_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS task_owner ON tasks(owner_id, session_id);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_session ON tasks(owner_id,session_id)
 WHERE status NOT IN ('completed','failed','cancelled');
CREATE TABLE IF NOT EXISTS command_receipts (
 principal_id TEXT NOT NULL, command_id TEXT NOT NULL, payload_digest TEXT NOT NULL,
 result_json TEXT NOT NULL, PRIMARY KEY(principal_id,command_id));
CREATE TABLE IF NOT EXISTS task_events (
 task_id TEXT NOT NULL REFERENCES tasks(id), seq INTEGER NOT NULL, event_json TEXT NOT NULL,
 PRIMARY KEY(task_id,seq));
CREATE TABLE IF NOT EXISTS records (
 task_id TEXT NOT NULL REFERENCES tasks(id), kind TEXT NOT NULL, id TEXT NOT NULL,
 data_json TEXT NOT NULL, PRIMARY KEY(task_id,kind,id));
CREATE TABLE IF NOT EXISTS outbox (
 id TEXT PRIMARY KEY, dedupe_key TEXT NOT NULL UNIQUE, task_id TEXT NOT NULL REFERENCES tasks(id),
 kind TEXT NOT NULL, payload_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
 lease_owner TEXT, lease_until REAL NOT NULL DEFAULT 0, fencing_token INTEGER NOT NULL DEFAULT 0,
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL DEFAULT 0, last_error TEXT);
CREATE TABLE IF NOT EXISTS inbox (
 source TEXT NOT NULL, message_id TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(source,message_id));
CREATE TABLE IF NOT EXISTS projections (
 task_id TEXT NOT NULL REFERENCES tasks(id), id TEXT NOT NULL, seq INTEGER NOT NULL,
 revision INTEGER NOT NULL, data_json TEXT NOT NULL, PRIMARY KEY(task_id,id));
CREATE TABLE IF NOT EXISTS projection_updates (
 cursor INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, data_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, data_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS activations (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, data_json TEXT NOT NULL);
"""


class Storage:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(
            path, check_same_thread=False, isolation_level=None, timeout=10
        )
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(MIGRATION)
        self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('schema_version','1')")
        require(
            self.db.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()[0]
            == "1",
            "UNAVAILABLE",
            "Incompatible database schema",
        )

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                require(
                    not self.db.execute(
                        "SELECT 1 FROM metadata WHERE key='recovery_mode'"
                    ).fetchone(),
                    "UNAVAILABLE",
                    "Restored database requires read-only reconciliation",
                )
                yield self.db
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def enqueue(self, db, task_id, kind, payload, dedupe_key):
        db.execute(
            "INSERT OR IGNORE INTO outbox(id,dedupe_key,task_id,kind,payload_json) VALUES (?,?,?,?,?)",
            (uid("out"), dedupe_key, task_id, kind, canonical(payload)),
        )

    def claim(self, owner, *, now=None, lease_seconds=30):
        now = time.time() if now is None else now
        if self.health()["recovery_mode"]:
            return None
        with self.transaction() as db:
            if db.execute(
                "SELECT value FROM metadata WHERE key='recovery_mode'"
            ).fetchone():
                return None
            row = db.execute(
                "SELECT * FROM outbox WHERE state IN ('pending','leased') AND lease_until<=? AND next_attempt_at<=? ORDER BY rowid LIMIT 1",
                (now, now),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE outbox SET state='leased',lease_owner=?,lease_until=?,fencing_token=fencing_token+1,attempts=attempts+1 WHERE id=?",
                (owner, now + lease_seconds, row["id"]),
            )
            result = dict(
                db.execute("SELECT * FROM outbox WHERE id=?", (row["id"],)).fetchone()
            )
            result["payload"] = json.loads(result.pop("payload_json"))
            return result

    def ack(self, id, token, *, error=None, now=None):
        with self.transaction() as db:
            row = db.execute("SELECT * FROM outbox WHERE id=?", (id,)).fetchone()
            require(
                row is not None
                and row["fencing_token"] == token
                and row["state"] == "leased",
                "STALE_EXECUTION",
            )
            state = (
                ("dead_letter" if row["attempts"] >= 5 else "pending")
                if error
                else "delivered"
            )
            retry = (
                (time.time() if now is None else now)
                + min(60, 2 ** min(row["attempts"], 6))
                if error
                else 0
            )
            db.execute(
                "UPDATE outbox SET state=?,lease_until=0,next_attempt_at=?,last_error=? WHERE id=?",
                (state, retry, error, id),
            )

    def backup(self, path):
        with self.lock, closing(sqlite3.connect(path)) as destination:
            self.db.backup(destination)
        with closing(sqlite3.connect(path)) as destination:
            destination.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('recovery_mode','read_only_reconciliation')"
            )
            destination.commit()

    def health(self):
        with self.lock:
            states = dict(
                self.db.execute(
                    "SELECT state,count(*) FROM outbox GROUP BY state"
                ).fetchall()
            )
            return {
                "storage": "sqlite-wal",
                "schema_version": 1,
                "recovery_mode": bool(
                    self.db.execute(
                        "SELECT 1 FROM metadata WHERE key='recovery_mode'"
                    ).fetchone()
                ),
                "dead_letters": states.get("dead_letter", 0),
                "pending_deliveries": states.get("pending", 0),
                "leased_deliveries": states.get("leased", 0),
                "active_tasks": self.db.execute(
                    "SELECT count(*) FROM tasks WHERE status NOT IN ('completed','cancelled','failed')"
                ).fetchone()[0],
            }

    def close(self):
        self.db.close()
