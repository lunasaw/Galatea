"""Source-identified public messages and immutable file changes, with update cursors."""

from __future__ import annotations
import difflib
import hashlib
import json
import re
from .common import canonical, digest, require, uid, utc


def safe_text(value):
    text = str(value)[:65536]
    return re.sub(
        r"(?i)(bearer\s+|(?:api[_-]?key|token|password|secret)[\"']?\s*[:=]\s*[\"']?)[^\s,;\"'}]+",
        r"\1[REDACTED]",
        text,
    )


class Workbench:
    def __init__(self, service):
        self.service = service
        self.storage = service.storage

    def ingest(self, task_id, event):
        with self.storage.transaction() as db:
            row = db.execute(
                "SELECT aggregate_json FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            require(row is not None, "NOT_FOUND")
            a = json.loads(row[0])
            b = a["binding"]
            require(
                all(
                    event.get(k) == b[k] for k in ("host_id", "session_id", "thread_id")
                ),
                "FORBIDDEN",
            )
            source = f"host:{event['host_id']}:{event['epoch']}"
            message_id = str(event["seq"])
            d = digest(event)
            previous = db.execute(
                "SELECT digest FROM inbox WHERE source=? AND message_id=?",
                (source, message_id),
            ).fetchone()
            if previous:
                require(previous[0] == d, "IDEMPOTENCY_CONFLICT")
                return
            db.execute("INSERT INTO inbox VALUES (?,?,?)", (source, message_id, d))
            cursor = a.setdefault("source_cursors", {}).setdefault(
                "host", {"epoch": event["epoch"], "seq": 0, "coverage": "complete"}
            )
            if event["epoch"] != cursor["epoch"] or (
                event.get("sequence_scope") != "host_global_filtered"
                and int(event["seq"]) > cursor["seq"] + 1
            ):
                cursor["coverage"] = "incomplete"
            cursor.update(
                epoch=event["epoch"],
                seq=max(cursor["seq"], int(event["seq"])),
                observed_at=utc(),
            )
            kind = event.get("type")
            if kind == "public_message" and event.get("role") in {"user", "assistant"}:
                id = f"message:{event['host_id']}:{event['message_id']}"
                old = db.execute(
                    "SELECT * FROM projections WHERE task_id=? AND id=?", (task_id, id)
                ).fetchone()
                revision = int(event.get("content_revision", 1))
                if not old or revision > old["revision"]:
                    seq = (
                        old["seq"]
                        if old
                        else db.execute(
                            "SELECT COALESCE(MAX(seq),0)+1 FROM projections WHERE task_id=?",
                            (task_id,),
                        ).fetchone()[0]
                    )
                    item = {
                        "id": id,
                        "seq": seq,
                        "kind": "public_message",
                        "message_id": event["message_id"],
                        "turn_id": event.get("turn_id"),
                        "role": event["role"],
                        "safe_content": safe_text(event.get("safe_content", "")),
                        "delivery_state": event.get("delivery_state", "completed"),
                        "content_revision": revision,
                        "source_ref": source + ":" + message_id,
                        "occurred_at": event.get("occurred_at", utc()),
                    }
                    db.execute(
                        "INSERT OR REPLACE INTO projections VALUES (?,?,?,?,?)",
                        (task_id, id, seq, revision, canonical(item)),
                    )
                    db.execute(
                        "INSERT INTO projection_updates(task_id,data_json) VALUES (?,?)",
                        (task_id, canonical({"op": "upsert", "item": item})),
                    )
            elif kind in {
                "tool",
                "turn/completed",
                "turn/plan/updated",
                "skill",
                "file_change",
            }:
                # No guessed stage attribution: an explicit scope is required.
                scope = event.get("execution_scope_id")
                attempts = [
                    v
                    for v in a["attempts"].values()
                    if v["execution_scope_id"] == scope
                ]
                data = {
                    "source_ref": source + ":" + message_id,
                    "type": kind,
                    "summary": safe_text(event.get("summary", "")),
                    "turn_id": event.get("turn_id"),
                    "status": event.get("status", "unknown"),
                }
                if attempts:
                    data.update(
                        stage_id=attempts[0]["stage_id"], attempt_id=attempts[0]["id"]
                    )
                self.service._event(db, a, "runtime.operation", data, message_id)
            self.service._save(db, a, increment=False)

    def capture_change(
        self,
        ctx,
        task_id,
        *,
        stage_id,
        attempt_id,
        relative_path,
        before,
        after,
        checks=None,
    ):
        require(
            isinstance(before, str)
            and isinstance(after, str)
            and len(before.encode()) + len(after.encode()) <= 262144,
            "RATE_LIMITED",
        )
        self.service.resolve_path(ctx.workspace_ref, relative_path)
        with self.storage.transaction() as db:
            a = self.service._load(db, ctx, task_id, write=True)
            require(
                attempt_id in a["attempts"]
                and a["attempts"][attempt_id]["stage_id"] == stage_id,
                "FORBIDDEN",
            )
            id = uid("change")
            item = dict(
                change_id=id,
                relative_path=relative_path,
                operation="add" if not before else "delete" if not after else "modify",
                base_digest=hashlib.sha256(before.encode()).hexdigest(),
                current_digest=hashlib.sha256(after.encode()).hexdigest(),
                stage_id=stage_id,
                attempt_id=attempt_id,
                hunks=list(
                    difflib.unified_diff(
                        before.splitlines(),
                        after.splitlines(),
                        fromfile="a/" + relative_path,
                        tofile="b/" + relative_path,
                        lineterm="",
                    )
                ),
                checks=checks or [],
                observed_at=utc(),
            )
            db.execute(
                "INSERT INTO records VALUES (?,?,?,?)",
                (task_id, "file_change", id, canonical(item)),
            )
            self.service._event(
                db,
                a,
                "file.changed",
                {
                    "change_id": id,
                    "stage_id": stage_id,
                    "attempt_id": attempt_id,
                    "relative_path": relative_path,
                },
                id,
            )
            self.service._save(db, a, increment=False)
            return item

    def file_change(self, ctx, task_id, change_id, *, cursor=0, limit=200):
        with self.storage.lock:
            self.service._load(self.storage.db, ctx, task_id)
            row = self.storage.db.execute(
                "SELECT data_json FROM records WHERE task_id=? AND kind='file_change' AND id=?",
                (task_id, change_id),
            ).fetchone()
            require(row is not None, "NOT_FOUND")
            data = json.loads(row[0])
            lines = data["hunks"]
            cursor = max(0, int(cursor))
            limit = max(1, min(int(limit), 200))
            return {
                **data,
                "hunks": [safe_text(line) for line in lines[cursor : cursor + limit]],
                "next_cursor": min(cursor + limit, len(lines)),
                "has_more": cursor + limit < len(lines),
            }

    def events(self, ctx, task_id, *, after_cursor=0, limit=100):
        limit = max(1, min(int(limit), 100))
        with self.storage.lock:
            a = self.service._load(self.storage.db, ctx, task_id)
            rows = self.storage.db.execute(
                "SELECT cursor,data_json FROM projection_updates WHERE task_id=? AND cursor>? ORDER BY cursor LIMIT ?",
                (task_id, int(after_cursor), limit + 1),
            ).fetchall()
            return {
                "items": [json.loads(r["data_json"]) for r in rows[:limit]],
                "next_cursor": (
                    rows[min(len(rows), limit) - 1]["cursor"] if rows else after_cursor
                ),
                "has_more": len(rows) > limit,
                "coverage": a.get("source_cursors", {})
                .get("host", {})
                .get("coverage", "pending"),
            }

    def view(self, ctx, task_id):
        with self.storage.lock:
            a = self.service._load(self.storage.db, ctx, task_id)
            rows = self.storage.db.execute(
                "SELECT data_json FROM projections WHERE task_id=? ORDER BY seq DESC LIMIT 101",
                (task_id,),
            ).fetchall()
            cursor = self.storage.db.execute(
                "SELECT COALESCE(MAX(cursor),0) FROM projection_updates WHERE task_id=?",
                (task_id,),
            ).fetchone()[0]
            b = a["binding"]
            t = a["snapshot"]["task"]
            project = a.get(
                "project",
                {
                    "project_id": t["workspace_ref"],
                    "name": t["title"],
                    "slug": t["workspace_ref"],
                    "workspace_display_path": str(
                        self.service.workspaces[t["workspace_ref"]]
                    ),
                    "initialization_state": "ready",
                },
            )
            return {
                "schema_version": "galatea-workbench-view/v1",
                "project": project,
                "binding": {**b, "task_id": task_id, "activation_state": "ready"},
                "task_snapshot": a["snapshot"],
                "timeline_page": {
                    "items": [json.loads(r[0]) for r in reversed(rows[:100])],
                    "has_earlier": len(rows) > 100,
                    "next_cursor": cursor,
                    "has_more": False,
                },
                "pending_requests": list(a["requests"].values()),
                "proposals": list(a["proposals"].values()),
                "training_decisions": self.service.training_authorizations(a, ctx),
                "inputs": list(a["inputs"].values()),
                "capabilities": {
                    "raw_logs": False,
                    "uploads": False,
                    "promotion": False,
                    "interrupt_agent": True,
                    "training": bool(self.service.governance),
                },
                "source_cursors": a.get(
                    "source_cursors", {"host": {"coverage": "pending"}}
                ),
                "observed_at": utc(),
                "final_summary": a.get("final_summary"),
            }

    def stage_details(self, ctx, task_id, stage_id, *, cursor=0):
        with self.storage.lock:
            a = self.service._load(self.storage.db, ctx, task_id)
            s = self.service._stage(a, stage_id)
            files = [
                json.loads(r[0])
                for r in self.storage.db.execute(
                    "SELECT data_json FROM records WHERE task_id=? AND kind='file_change'",
                    (task_id,),
                ).fetchall()
            ]
            events = self.service.events(ctx, task_id, after_seq=cursor)
            return {
                "stage": s,
                "spec": a["specs"][stage_id],
                "artifacts": [
                    v
                    for v in a["snapshot"]["artifacts"]
                    if v["id"] in s["artifact_refs"]
                ],
                "evidence": [
                    v
                    for v in a["snapshot"]["evidence"]
                    if v["id"] in s["evidence_refs"]
                ],
                "file_changes": [
                    {k: v for k, v in f.items() if k != "hunks"}
                    for f in files
                    if f["stage_id"] == stage_id
                ],
                "operations": [
                    o
                    for o in a["snapshot"]["external_operations"]
                    if o["stage_id"] == stage_id
                ],
                "metrics": [
                    m
                    for o in a.get("observations", [])
                    if o.get("attempt_id") == (s["current_attempt"] or {}).get("id")
                    for m in o.get("metrics", [])
                ],
                "logs": [
                    e for e in events["events"] if e["data"].get("stage_id") == stage_id
                ],
                "next_seq": events["next_seq"],
                "has_more": events["has_more"],
                "raw_logs": False,
                "raw_logs_reason": "当前接口未提供原始训练日志",
                "binding": a["binding"],
            }
