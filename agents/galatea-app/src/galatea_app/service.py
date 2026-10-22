"""Task aggregate and command boundary. IO verification runs outside transactions."""

from __future__ import annotations

from dataclasses import dataclass, asdict, replace
from datetime import datetime, timedelta, timezone
import copy
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import secrets
import time

from jsonschema import Draft202012Validator, ValidationError

from .common import TaskError, canonical, digest, require, timestamp, uid, utc
from .budgets import valid_limits
from .storage import Storage

TERMINAL = {"completed", "failed", "cancelled"}
ACTIVE = {"running", "waiting_external", "verifying"}
SETTLED = {"completed", "skipped", "cancelled", "superseded"}
EXTERNAL_TERMINAL = {"succeeded", "failed", "cancelled"}
STAGE_FIELDS = {
    "id",
    "kind",
    "title",
    "depends_on",
    "required",
    "input_refs",
    "output_slots",
    "acceptance",
    "execution",
    "replaces_stage_id",
}


@dataclass(frozen=True)
class Context:
    principal_id: str
    host_id: str
    session_id: str
    thread_id: str
    workspace_ref: str
    role: str = "agent"
    turn_id: str = ""
    call_id: str = ""
    confirmed: bool = False
    interaction_id: str = ""


class TaskService:
    def __init__(
        self,
        database,
        *,
        workspaces,
        capabilities=None,
        governance=None,
        max_artifact_bytes=8 * 1024 * 1024,
    ):
        self.storage = Storage(database)
        self.workspaces = {k: Path(v).resolve() for k, v in workspaces.items()}
        self.capabilities = capabilities or {
            "data-local/v1": {"training": False, "skills": []},
            "code-local/v1": {"training": False, "skills": []},
            "governed-training/v1": {
                "training": True,
                "skills": ["governed-training-workflow"],
            },
        }
        self.governance = governance or {}
        self.max_artifact_bytes = max_artifact_bytes
        self.validator = Draft202012Validator(
            json.loads(
                (
                    Path(__file__).parent / "contracts/task-snapshot.schema.json"
                ).read_text()
            )
        )

    def close(self):
        self.storage.close()

    def resolve_path(self, workspace, path):
        require(workspace in self.workspaces, "FORBIDDEN")
        relative = Path(path)
        require(
            not relative.is_absolute() and ".." not in relative.parts,
            "FORBIDDEN",
            "Workspace-relative path required",
        )
        root = self.workspaces[workspace]
        resolved = (root / relative).resolve()
        require(
            resolved.is_relative_to(root),
            "FORBIDDEN",
            "Path escapes registered workspace",
        )
        return resolved

    @staticmethod
    def _confirmed(ctx):
        require(
            ctx.role == "app" and ctx.confirmed and ctx.interaction_id,
            "AUTHORIZATION_REQUIRED",
            "A parameter-bound Host interaction is required",
        )

    def _load(self, db, ctx, task_id, *, write=False):
        row = db.execute(
            "SELECT * FROM tasks WHERE id=? AND owner_id=?", (task_id, ctx.principal_id)
        ).fetchone()
        require(row is not None, "NOT_FOUND")
        a = json.loads(row["aggregate_json"])
        b = a["binding"]
        if write or ctx.role == "agent":
            require(
                all(
                    b[k] == getattr(ctx, k)
                    for k in ("host_id", "session_id", "thread_id", "workspace_ref")
                ),
                "FORBIDDEN",
                "Task is bound to another Host session",
            )
        return a

    def _receipt(self, db, ctx, key, value):
        require(isinstance(key, str) and 0 < len(key) <= 128, "INVALID_COMMAND")
        row = db.execute(
            "SELECT * FROM command_receipts WHERE principal_id=? AND command_id=?",
            (ctx.principal_id, key),
        ).fetchone()
        if row:
            require(row["payload_digest"] == digest(value), "IDEMPOTENCY_CONFLICT")
            return json.loads(row["result_json"])

    def _save_receipt(self, db, ctx, key, value, a, result):
        t = a["snapshot"]["task"]
        r = {
            "ok": True,
            "receipt_id": uid("receipt"),
            "task_id": t["id"],
            "version": t["version"],
            "plan_revision": t["plan_revision"],
            "event_seq": a["snapshot"]["last_event_seq"],
            "result": result,
        }
        db.execute(
            "INSERT INTO command_receipts VALUES (?,?,?,?)",
            (ctx.principal_id, key, digest(value), canonical(r)),
        )
        return r

    def get_receipt(self, ctx, command_id):
        with self.storage.lock:
            row = self.storage.db.execute(
                "SELECT result_json FROM command_receipts WHERE principal_id=? AND command_id=?",
                (ctx.principal_id, command_id),
            ).fetchone()
            require(row is not None, "NOT_FOUND")
            value = json.loads(row[0])
            if value.get("task_id"):
                row = self.storage.db.execute(
                    "SELECT aggregate_json FROM tasks WHERE id=? AND owner_id=?",
                    (value["task_id"], ctx.principal_id),
                ).fetchone()
                require(row is not None, "NOT_FOUND")
                binding = json.loads(row[0])["binding"]
                self._load(
                    self.storage.db,
                    replace(ctx, workspace_ref=binding["workspace_ref"]),
                    value["task_id"],
                )
            return value

    def _event(self, db, a, type, data, causation):
        s = a["snapshot"]
        s["last_event_seq"] += 1
        event = {
            "schema_version": "galatea-task-event/v1",
            "task_id": s["task"]["id"],
            "seq": s["last_event_seq"],
            "event_id": uid("evt"),
            "type": type,
            "causation_id": causation,
            "occurred_at": utc(),
            "data": data,
        }
        db.execute(
            "INSERT INTO task_events VALUES (?,?,?)",
            (event["task_id"], event["seq"], canonical(event)),
        )
        return event

    def _save(self, db, a, *, increment=True):
        s = a["snapshot"]
        t = s["task"]
        if t["status"] != "blocked":
            t.pop("blocked_reason", None)
        if increment:
            t["version"] += 1
            t["updated_at"] = utc()
        s["allowed_actions"] = ["explain", "download"]
        if t["status"] not in TERMINAL:
            s["allowed_actions"] += ["cancel"]
            if t["status"] == "draft":
                s["allowed_actions"] += ["start"]
            elif t["status"] == "paused":
                s["allowed_actions"] += ["resume"]
            else:
                s["allowed_actions"] += ["pause"]
            if a.get("requests"):
                s["allowed_actions"] += ["submit_input"]
        self.validator.validate(s)
        require(
            len(canonical(s).encode()) <= 131072,
            "RATE_LIMITED",
            "Snapshot size limit exceeded",
        )
        db.execute(
            "UPDATE tasks SET status=?,version=?,plan_revision=?,control_epoch=?,snapshot_json=?,aggregate_json=? WHERE id=?",
            (
                t["status"],
                t["version"],
                t["plan_revision"],
                t["control_epoch"],
                canonical(s),
                canonical(a),
                t["id"],
            ),
        )
        for kind, records in (
            ("plan", a["plans"]),
            ("attempt", a["attempts"]),
            ("intent", a["intents"]),
            ("proposal", a["proposals"]),
            ("request", a["requests"]),
            ("artifact", a["locators"]),
        ):
            for id, record in records.items():
                db.execute(
                    "INSERT OR REPLACE INTO records VALUES (?,?,?,?)",
                    (t["id"], kind, id, canonical(record)),
                )

    def create(
        self,
        ctx,
        *,
        command_id,
        goal,
        inputs=None,
        constraints=None,
        start=False,
        parent_task_id=None,
        in_current_turn=False,
    ):
        require(
            isinstance(goal, str) and 0 < len(goal.strip()) <= 8000,
            "INVALID_COMMAND",
            "Goal required",
        )
        require(ctx.workspace_ref in self.workspaces, "FORBIDDEN")
        if in_current_turn:
            require(ctx.role == "agent" and ctx.turn_id and ctx.call_id, "FORBIDDEN")
        if start:
            self._confirmed(ctx)
        value = dict(
            command="create_task",
            goal=goal,
            inputs=inputs or [],
            constraints=constraints or {},
            start=start,
            parent_task_id=parent_task_id,
            binding=[ctx.host_id, ctx.session_id, ctx.thread_id, ctx.workspace_ref],
        )
        if in_current_turn:
            value["in_current_turn"] = True
        with self.storage.transaction() as db:
            previous = self._receipt(db, ctx, command_id, value)
            if previous:
                return previous
            require(
                not db.execute(
                    "SELECT 1 FROM tasks WHERE owner_id=? AND session_id=? AND status NOT IN ('completed','failed','cancelled')",
                    (ctx.principal_id, ctx.session_id),
                ).fetchone(),
                "INVALID_TRANSITION",
                "One active task per session",
            )
            if parent_task_id:
                self._load(db, ctx, parent_task_id)
            registered = {}
            for item in inputs or []:
                require(set(item) <= {"path"}, "INVALID_COMMAND")
                input_workspace = ctx.workspace_ref
                relative = Path(item["path"])
                if in_current_turn and relative.is_absolute():
                    resolved = relative.resolve()
                    roots = [(ref, root) for ref, root in self.workspaces.items()
                             if resolved.is_relative_to(root)]
                    require(roots, "FORBIDDEN", "Input is outside registered roots")
                    input_workspace, root = max(roots, key=lambda row: len(str(row[1])))
                    relative = resolved.relative_to(root)
                path = self.resolve_path(input_workspace, str(relative))
                require(path.exists(), "NOT_FOUND", "Input does not exist")
                id = uid("input")
                registered[id] = {
                    "id": id,
                    "path": str(relative),
                    "workspace_ref": input_workspace,
                    "kind": "directory" if path.is_dir() else "file",
                    "sha256": None,
                }
                # Large directory content identity is established by the inspection stage.
                if path.is_file() and path.stat().st_size <= self.max_artifact_bytes:
                    registered[id]["sha256"] = hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
            c = {
                "scope": "data_processing",
                "allow_training": False,
                "max_auto_continuations": 20,
                "deadline_at": (datetime.now(timezone.utc) + timedelta(hours=24))
                .isoformat()
                .replace("+00:00", "Z"),
                **(constraints or {}),
            }
            constraint_schema = self.validator.schema["$defs"]["task"]["properties"]["constraints"]
            require(Draft202012Validator(constraint_schema).is_valid(c),
                    "INVALID_COMMAND", "Invalid task constraints; use the declared scope and budget fields")
            task_id = uid("task")
            binding_id = uid("binding")
            t = dict(
                id=task_id,
                title=goal[:200],
                goal=goal,
                status="planning" if start or in_current_turn else "draft",
                version=1,
                plan_revision=0,
                control_epoch=0,
                binding_id=binding_id,
                workspace_ref=ctx.workspace_ref,
                constraints=c,
                completion_conditions=[
                    {
                        "id": "delivery",
                        "type": "artifact_exists",
                        "parameters": {"artifact_role": "*"},
                        "state": "pending",
                        "evidence_refs": [],
                    }
                ],
                plan_summary="",
                created_at=utc(),
                updated_at=utc(),
            )
            if parent_task_id:
                t["parent_task_id"] = parent_task_id
            s = dict(
                schema_version="galatea-task-snapshot/v1",
                task=t,
                stages=[],
                artifacts=[],
                evidence=[],
                external_operations=[],
                allowed_actions=[],
                last_event_seq=0,
            )
            a = dict(
                snapshot=s,
                binding={
                    "binding_id": binding_id,
                    **{
                        k: getattr(ctx, k)
                        for k in ("host_id", "session_id", "thread_id", "workspace_ref")
                    },
                },
                inputs=registered,
                plans={},
                specs={},
                attempts={},
                intents={},
                locators={},
                proposals={},
                requests={},
                decisions=[],
                reviews=[],
                continuations=0,
                launch_receipt=ctx.call_id or ctx.interaction_id,
                owner_id=ctx.principal_id,
                execution_authorized=bool(start or in_current_turn),
                in_current_turn=in_current_turn,
            )
            db.execute(
                "INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    ctx.principal_id,
                    ctx.session_id,
                    t["status"],
                    1,
                    0,
                    0,
                    canonical(s),
                    canonical(a),
                ),
            )
            self._event(db, a, "task.created", {"goal": goal}, command_id)
            if start:
                self._enqueue_continuation(db, a, "start", command_id)
            self._save(db, a, increment=False)
            return self._save_receipt(
                db, ctx, command_id, value, a, {"binding_id": binding_id}
            )

    def get_task(self, ctx, task_id):
        with self.storage.lock:
            return copy.deepcopy(self._load(self.storage.db, ctx, task_id)["snapshot"])

    def current_task_id(self, ctx):
        with self.storage.lock:
            rows = self.storage.db.execute(
                "SELECT id,aggregate_json FROM tasks WHERE owner_id=? AND session_id=? ORDER BY rowid DESC",
                (ctx.principal_id, ctx.session_id),
            ).fetchall()
            for row in rows:
                binding = json.loads(row["aggregate_json"])["binding"]
                if all(binding[k] == getattr(ctx, k) for k in ("host_id", "thread_id")):
                    return row["id"]
        return None

    def get_context(self, ctx, task_id):
        with self.storage.lock:
            a = self._load(self.storage.db, ctx, task_id)
            return {
                "snapshot": a["snapshot"],
                "stage_specs": list(a["specs"].values()),
                "inputs": list(a["inputs"].values()),
                "capabilities": self.capabilities,
                "pending_requests": list(a["requests"].values()),
                "proposals": list(a["proposals"].values()),
                "training_decisions": self.training_authorizations(a, ctx),
                "external_intents": list(a["intents"].values()),
                "observations": a.get("observations", []),
                "workspace_roots": {
                    k: str(v)
                    for k, v in self.workspaces.items()
                    if k == a["binding"]["workspace_ref"]
                    or any(i.get("workspace_ref") == k for i in a["inputs"].values())
                },
            }

    def list_tasks(self, ctx, *, cursor=0, limit=50):
        limit = max(1, min(int(limit), 100))
        cursor = max(0, int(cursor))
        with self.storage.lock:
            rows = self.storage.db.execute(
                "SELECT snapshot_json FROM tasks WHERE owner_id=? ORDER BY rowid LIMIT ? OFFSET ?",
                (ctx.principal_id, limit + 1, cursor),
            ).fetchall()
        return {
            "items": [json.loads(r[0])["task"] for r in rows[:limit]],
            "next_cursor": cursor + min(len(rows), limit),
            "has_more": len(rows) > limit,
        }

    def events(self, ctx, task_id, *, after_seq=0, limit=100):
        with self.storage.lock:
            self._load(self.storage.db, ctx, task_id)
            rows = self.storage.db.execute(
                "SELECT event_json FROM task_events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?",
                (task_id, int(after_seq), max(1, min(int(limit), 100)) + 1),
            ).fetchall()
            items = [json.loads(r[0]) for r in rows[:limit]]
            return {
                "events": items,
                "next_seq": items[-1]["seq"] if items else after_seq,
                "has_more": len(rows) > limit,
            }

    def command(
        self,
        ctx,
        task_id,
        command,
        payload,
        *,
        command_id,
        expected_plan_revision=None,
        expected_version=None,
    ):
        value = dict(
            task_id=task_id,
            command=command,
            payload=payload,
            expected_plan_revision=expected_plan_revision,
            expected_version=expected_version,
        )
        file_snapshot = None
        if command in {"begin_stage", "report_result"}:
            with self.storage.lock:
                current = self._load(self.storage.db, ctx, task_id, write=True)
                stage = self._stage(current, payload["stage_id"])
                is_code = stage["kind"] == "code.prepare"
            if is_code:
                from .files import snapshot

                file_snapshot = snapshot(self.workspaces[ctx.workspace_ref])
        with self.storage.transaction() as db:
            a = self._load(db, ctx, task_id, write=True)
            previous = self._receipt(db, ctx, command_id, value)
            if previous:
                return previous
            require(
                not db.execute(
                    "SELECT 1 FROM metadata WHERE key='recovery_mode'"
                ).fetchone(),
                "UNAVAILABLE",
                "Restore requires read-only reconciliation",
            )
            t = a["snapshot"]["task"]
            require(
                t["status"] not in TERMINAL
                or command in {"bind_external", "record_review"},
                "INVALID_TRANSITION",
                "Task is sealed",
            )
            if expected_version is not None:
                require(t["version"] == expected_version, "VERSION_CONFLICT")
            if expected_plan_revision is not None:
                require(t["plan_revision"] == expected_plan_revision, "PLAN_CONFLICT")
            if command in {
                "replace_plan",
                "begin_stage",
                "propose_training",
                "complete_task",
            }:
                require(
                    expected_plan_revision is not None,
                    "PLAN_CONFLICT",
                    "Plan revision required",
                )
            if command in {"request_control", "submit_input"}:
                self._confirmed(ctx)
                require(
                    expected_version is not None,
                    "VERSION_CONFLICT",
                    "Task version required",
                )
            elif command == "record_review":
                self._confirmed(ctx)
            else:
                require(ctx.role == "agent", "FORBIDDEN")
            method = getattr(self, "_cmd_" + command, None)
            require(method is not None, "INVALID_COMMAND")
            result = method(db, a, ctx, payload, command_id) or {}
            if file_snapshot is not None:
                if command == "begin_stage":
                    a["attempts"][result["attempt_id"]]["file_baseline"] = file_snapshot
                elif command == "report_result":
                    from .files import changes

                    attempt = a["attempts"][payload["attempt_id"]]
                    for change in changes(
                        attempt.get(
                            "file_baseline", {"files": {}, "coverage": "incomplete"}
                        ),
                        file_snapshot,
                        payload["stage_id"],
                        payload["attempt_id"],
                    ):
                        db.execute(
                            "INSERT INTO records VALUES (?,?,?,?)",
                            (
                                task_id,
                                "file_change",
                                change["change_id"],
                                canonical(change),
                            ),
                        )
                        self._event(
                            db,
                            a,
                            "file.changed",
                            {
                                k: change[k]
                                for k in (
                                    "change_id",
                                    "stage_id",
                                    "attempt_id",
                                    "relative_path",
                                    "source",
                                    "coverage",
                                )
                            },
                            command_id,
                        )
            self._save(db, a)
            return self._save_receipt(db, ctx, command_id, value, a, result)

    def _stage(self, a, id):
        stages = [s for s in a["snapshot"]["stages"] if s["id"] == id]
        require(bool(stages), "NOT_FOUND")
        return stages[0]

    def _cmd_replace_plan(self, db, a, ctx, p, key):
        stages = p.get("stages")
        if isinstance(stages, list):
            parsed = []
            for stage in stages:
                if isinstance(stage, str):
                    try:
                        stage = json.loads(stage)
                    except json.JSONDecodeError:
                        raise TaskError("INVALID_PLAN", "Stage must be an object")
                parsed.append(stage)
            stages = parsed
        require(
            isinstance(stages, list)
            and 0 < len(stages) <= 50
            and all(isinstance(stage, dict) for stage in stages),
            "INVALID_PLAN",
        )
        require(
            isinstance(p.get("reason"), str) and p["reason"].strip(),
            "INVALID_PLAN",
            "Revision reason required",
        )
        ids = [s.get("id") for s in stages]
        require(
            len(set(ids)) == len(ids)
            and all(isinstance(i, str) and 0 < len(i) <= 128 for i in ids),
            "INVALID_PLAN",
        )
        specs = {s["id"]: copy.deepcopy(s) for s in stages}
        for s in stages:
            require(
                set(s) <= STAGE_FIELDS
                and STAGE_FIELDS - {"replaces_stage_id"} <= set(s),
                "INVALID_PLAN",
                "Use StageSpec, not StageView",
            )
            require(
                isinstance(s["required"], bool)
                and s["title"]
                and len(s["title"]) <= 200,
                "INVALID_PLAN",
            )
            require(
                isinstance(s["depends_on"], list)
                and len(set(s["depends_on"])) == len(s["depends_on"])
                and all(x in ids and x != s["id"] for x in s["depends_on"]),
                "INVALID_PLAN",
            )
            cap = self.capabilities.get(s["execution"].get("capability_ref"))
            require(cap is not None, "CAPABILITY_UNAVAILABLE")
            require(
                set(s["execution"].get("skill_refs", [])) <= set(cap["skills"]),
                "CAPABILITY_UNAVAILABLE",
            )
            require(
                not cap["training"]
                or a["snapshot"]["task"]["constraints"]["allow_training"],
                "AUTHORIZATION_REQUIRED",
            )
            require(
                s["kind"] != "training.run" or cap["training"], "CAPABILITY_UNAVAILABLE"
            )
            require(
                s["acceptance"]
                and all(
                    set(r) == {"id", "type", "parameters"} for r in s["acceptance"]
                ),
                "INVALID_PLAN",
            )
            for r in s["acceptance"]:
                self._validate_rule(r)
            slots = s["output_slots"]
            require(
                isinstance(slots, list)
                and len({v["name"] for v in slots}) == len(slots),
                "INVALID_PLAN",
            )
            for ref in s["input_refs"]:
                if ref.startswith("output:"):
                    parts = ref.split(":")
                    require(
                        len(parts) == 3 and parts[1] in s["depends_on"], "INVALID_PLAN"
                    )
                    require(
                        any(
                            x["name"] == parts[2]
                            for x in specs[parts[1]]["output_slots"]
                        ),
                        "INVALID_PLAN",
                    )
                else:
                    require(
                        ref in a["inputs"] or ref in a["locators"],
                        "INVALID_PLAN",
                        "Unknown input reference",
                    )
        order = []
        pending = list(ids)
        while pending:
            ready = [
                i for i in pending if all(d in order for d in specs[i]["depends_on"])
            ]
            require(ready, "INVALID_PLAN", "Dependency cycle")
            for i in ready:
                order.append(i)
                pending.remove(i)
        old = {s["id"]: s for s in a["snapshot"]["stages"]}
        replacements = {}
        for id, spec in specs.items():
            target = spec.get("replaces_stage_id")
            if target is None:
                continue
            require(
                target in old and target != id and target not in replacements,
                "PLAN_CONFLICT",
                "Replacement must identify one existing stage without branching",
            )
            previous = a["specs"][target]
            require(
                spec["kind"] == previous["kind"]
                and spec["execution"]["capability_ref"]
                == previous["execution"]["capability_ref"]
                and spec["execution"]["mode"] == previous["execution"]["mode"]
                and set(spec["execution"]["skill_refs"])
                >= set(previous["execution"]["skill_refs"])
                and (not previous["required"] or spec["required"])
                and all(rule in spec["acceptance"] for rule in previous["acceptance"])
                and all(slot in spec["output_slots"] for slot in previous["output_slots"]),
                "PLAN_CONFLICT",
                "Replacement must preserve the delivery and execution contract",
            )
            if id not in old or spec != a["specs"][id]:
                require(
                    old[target]["status"] == "failed"
                    and not any(
                        operation["stage_id"] == target
                        and operation["status"] not in EXTERNAL_TERMINAL
                        for operation in a["snapshot"]["external_operations"]
                    )
                    and not any(
                        intent["stage_id"] == target
                        and intent["state"] in {"prepared", "unknown"}
                        for intent in a["intents"].values()
                    ),
                    "PLAN_CONFLICT",
                    "Only reconciled failed work can be replaced",
                )
            replacements[target] = id
        for target in replacements:
            visited = set()
            while target in replacements:
                require(target not in visited, "INVALID_PLAN", "Replacement cycle")
                visited.add(target)
                target = replacements[target]
        for id, s in old.items():
            if s["status"] in SETTLED | ACTIVE | {
                "failed",
                "waiting_input",
                "waiting_approval",
            }:
                require(
                    id in specs and specs[id] == a["specs"][id],
                    "PLAN_CONFLICT",
                    "Executed or waiting stage is immutable",
                )
            elif id in specs:
                require(
                    not s["required"] or specs[id]["required"],
                    "PLAN_CONFLICT",
                    "Cannot weaken required work",
                )
            else:
                require(
                    not s["required"],
                    "PLAN_CONFLICT",
                    "Required stages must remain in history",
                )
        views = []
        for id in order:
            s = specs[id]
            if id in old and s == a["specs"][id]:
                views.append(old[id])
                continue
            views.append(
                {
                    k: copy.deepcopy(s[k])
                    for k in (
                        "id",
                        "kind",
                        "title",
                        "depends_on",
                        "required",
                        "input_refs",
                    )
                }
                | {
                    "status": "pending",
                    "acceptance": [
                        {**r, "state": "pending", "evidence_refs": []}
                        for r in s["acceptance"]
                    ],
                    "evidence_refs": [],
                    "artifact_refs": [],
                    "current_attempt": None,
                }
            )
        for id, s in old.items():
            if id not in specs:
                s["status"] = "superseded"
                s["reason"] = p["reason"]
                views.append(s)
                specs[id] = a["specs"][id]
        for view in views:
            if "replaces_stage_id" in specs[view["id"]]:
                view["replaces_stage_id"] = specs[view["id"]]["replaces_stage_id"]
        a["snapshot"]["stages"] = views
        a["specs"] = specs
        t = a["snapshot"]["task"]
        t["plan_revision"] += 1
        t["plan_summary"] = p["reason"]
        if not any(s["status"] in ACTIVE for s in views) and t["status"] != "draft":
            t["status"] = "running"
        a["plans"][str(t["plan_revision"])] = dict(
            revision=t["plan_revision"],
            reason=p["reason"],
            actor=ctx.principal_id,
            created_at=utc(),
            stage_specs=copy.deepcopy(list(specs.values())),
        )
        self._ready(a)
        difference = {
            "added": [i for i in ids if i not in old],
            "replacements": replacements,
            "changed": [i for i in ids if i in old and old[i] != self._stage(a, i)],
            "reason": p["reason"],
            "revision": t["plan_revision"],
        }
        self._event(db, a, "plan.revised", difference, key)
        return difference

    @staticmethod
    def _validate_rule(r):
        allowed = {
            "artifact_exists": {"artifact_role"},
            "schema_matches": {"artifact_role", "schema_ref"},
            "metric_threshold": {"metric", "operator", "value", "split"},
            "external_operation_terminal": set(),
            "user_review_recorded": set(),
        }
        require(
            r["type"] in allowed and set(r["parameters"]) == allowed[r["type"]],
            "INVALID_PLAN",
            "Unregistered acceptance parameters",
        )
        if r["type"] == "metric_threshold":
            require(
                r["parameters"]["split"] in {"train", "validation"}
                and r["parameters"]["operator"] in {">=", "<="},
                "INVALID_PLAN",
            )

    def _ready(self, a):
        states = {s["id"]: s["status"] for s in a["snapshot"]["stages"]}
        for s in a["snapshot"]["stages"]:
            if s["status"] in {"pending", "ready"}:
                s["status"] = (
                    "ready"
                    if all(
                        states[d] in {"completed", "skipped"} for d in s["depends_on"]
                    )
                    else "pending"
                )

    def _resolved(self, a, s):
        resolved = []
        for ref in s["input_refs"]:
            if ref.startswith("output:"):
                _, parent, slot = ref.split(":")
                ids = self._stage(a, parent)["artifact_refs"]
                values = [
                    a["locators"][id]
                    for id in ids
                    if a["locators"][id].get("slot") == slot
                ]
                require(
                    values and self._stage(a, parent)["status"] == "completed",
                    "INVALID_TRANSITION",
                    "Required output unavailable",
                )
                resolved.extend(values)
            elif ref in a["inputs"]:
                item = a["inputs"][ref]
                path = self.resolve_path(
                    item.get("workspace_ref", a["binding"]["workspace_ref"]),
                    item["path"],
                )
                if item["sha256"]:
                    require(
                        path.is_file()
                        and hashlib.sha256(path.read_bytes()).hexdigest()
                        == item["sha256"],
                        "PLAN_CONFLICT",
                        "Input changed; register a new input version",
                    )
                resolved.append(item)
            else:
                resolved.append(a["locators"][ref])
        return resolved

    def _cmd_begin_stage(self, db, a, ctx, p, key):
        t = a["snapshot"]["task"]
        require(
            a.get("execution_authorized", False),
            "AUTHORIZATION_REQUIRED",
            "Task has not been started by a trusted user",
        )
        require(
            t["status"] in {"running", "planning"} and not a.get("control_requested"),
            "INVALID_TRANSITION",
        )
        require(
            timestamp(t["constraints"]["deadline_at"]) > time.time(),
            "AUTHORIZATION_REQUIRED",
            "Task authorization expired",
        )
        s = self._stage(a, p["stage_id"])
        require(
            s["status"] == "ready"
            and not any(v["status"] in ACTIVE for v in a["snapshot"]["stages"]),
            "INVALID_TRANSITION",
            "Stages execute serially after dependencies",
        )
        if self.capabilities[a["specs"][s["id"]]["execution"]["capability_ref"]][
            "training"
        ]:
            proposals = [v for v in a["proposals"].values() if v["stage_id"] == s["id"]]
            require(proposals, "AUTHORIZATION_REQUIRED")
            proposal = max(proposals, key=lambda p: p["revision"])
            self._authorized(a, proposal)
            self._training_ready(proposal)
            require(
                any(
                    d["subject_digest"] == proposal["digest"]
                    and d["decision"] == "confirm"
                    for d in a["decisions"]
                ),
                "AUTHORIZATION_REQUIRED",
            )
        resolved = self._resolved(a, s)
        n = sum(1 for x in a["attempts"].values() if x["stage_id"] == s["id"]) + 1
        require(
            n
            <= a["specs"][s["id"]]["execution"]
            .get("retry_policy", {})
            .get("max_attempts", 1),
            "AUTHORIZATION_REQUIRED",
            "Attempt budget exhausted",
        )
        if self.capabilities[a["specs"][s["id"]]["execution"]["capability_ref"]][
            "training"
        ]:
            require(
                n <= proposal["hard_limits"]["attempts"]
                and n - 1 <= proposal["hard_limits"]["retries"],
                "AUTHORIZATION_REQUIRED",
                "Confirmed training budget exhausted",
            )
        attempt = {
            "id": uid("attempt"),
            "number": n,
            "status": "running",
            "execution_scope_id": uid("scope"),
            "started_at": utc(),
        }
        token = attempt[
            "execution_scope_id"
        ]  # Opaque capability reference; the Provider exposes its scope identity.
        a["attempts"][attempt["id"]] = {
            **attempt,
            "stage_id": s["id"],
            "token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "control_epoch": t["control_epoch"],
            "plan_revision": t["plan_revision"],
            "turn_id": ctx.turn_id,
            "journal_call_id": ctx.call_id,
            "input_digest": digest(resolved),
            "lease_until": time.time() + 3600,
        }
        s["current_attempt"] = attempt
        s["status"] = "running"
        t["status"] = "running"
        self._event(
            db,
            a,
            "stage.started",
            {
                "stage_id": s["id"],
                "attempt_id": attempt["id"],
                "execution_scope_id": attempt["execution_scope_id"],
                "turn_id": ctx.turn_id,
                "journal_call_id": ctx.call_id,
            },
            key,
        )
        return {
            "stage_id": s["id"],
            "attempt_id": attempt["id"],
            "execution_scope_id": attempt["execution_scope_id"],
            "execution_token": token,
            "resolved_inputs": resolved,
            "output_workspace_ref": a["binding"]["workspace_ref"],
            "reporting_instruction": "Use execution_scope_id as execution_token when reporting through the native Provider.",
        }

    def _attempt(self, a, p, *, draining=False):
        attempt = a["attempts"].get(p.get("attempt_id"))
        require(attempt is not None, "NOT_FOUND")
        t = a["snapshot"]["task"]
        require(
            secrets.compare_digest(
                attempt["token_hash"],
                hashlib.sha256(p.get("execution_token", "").encode()).hexdigest(),
            )
            and (
                attempt["control_epoch"] == t["control_epoch"]
                or draining
                and a.get("control_requested") in {"pause", "interrupt_agent"}
                and attempt["id"] in a.get("draining_attempts", [])
            )
            and attempt["lease_until"] > time.time(),
            "STALE_EXECUTION",
        )
        require(
            attempt["status"] in {"running", "waiting_external"}, "INVALID_TRANSITION"
        )
        return attempt, self._stage(a, attempt["stage_id"])

    def _cmd_report_result(self, db, a, ctx, p, key):
        attempt, s = self._attempt(a, p, draining=True)
        require(s["id"] == p["stage_id"], "FORBIDDEN")
        candidates = p.get("artifact_candidates", [])
        require(
            isinstance(candidates, list) and len(candidates) <= 32, "INVALID_COMMAND"
        )
        for c in candidates:
            require(
                set(c)
                <= {"path", "role", "slot", "media_type", "sha256", "external_ref"}
                and "role" in c,
                "INVALID_COMMAND",
            )
            require(
                any(
                    slot["name"] == c.get("slot") and slot["role"] == c["role"]
                    for slot in a["specs"][s["id"]]["output_slots"]
                ),
                "INVALID_COMMAND",
                "Output slot mismatch",
            )
            if "path" in c:
                self.resolve_path(a["binding"]["workspace_ref"], c["path"])
            else:
                require("external_ref" in c, "INVALID_COMMAND")
                ref = c["external_ref"]
                require(
                    ref.get("route") == "project_governance"
                    or ref.get("route") == "governance_mcp",
                    "FORBIDDEN",
                )
                require(
                    ref.get("operation_id")
                    in {o["id"] for o in a["snapshot"]["external_operations"]},
                    "FORBIDDEN",
                    "Artifact operation is not bound to this attempt",
                )
                require(
                    any(
                        o["id"] == ref.get("operation_id")
                        and o["attempt_id"] == attempt["id"]
                        for o in a["snapshot"]["external_operations"]
                    ),
                    "FORBIDDEN",
                )
        attempt["candidates"] = copy.deepcopy(candidates)
        attempt["result"] = copy.deepcopy(p.get("result", {}))
        attempt["status"] = "verifying"
        s["status"] = "verifying"
        s["current_attempt"]["status"] = "verifying"
        self._event(
            db,
            a,
            "stage.result_reported",
            {"stage_id": s["id"], "attempt_id": attempt["id"]},
            key,
        )
        self.storage.enqueue(
            db,
            a["snapshot"]["task"]["id"],
            "verify",
            {"attempt_id": attempt["id"]},
            "verify:" + attempt["id"],
        )
        return {"state": "verifying"}

    def _read_candidate(self, a, c):
        if "external_ref" in c:
            ref = c["external_ref"]
            adapter = self.governance.get(ref.get("route"))
            require(adapter is not None, "CAPABILITY_UNAVAILABLE")
            return adapter.read_artifact(ref, self.max_artifact_bytes)
        path = self.resolve_path(a["binding"]["workspace_ref"], c["path"])
        require(path.is_file(), "NOT_FOUND", "Artifact unavailable")
        # Reject last-component symlink races; inspect the opened descriptor's actual target too.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as f:
            require(
                Path(f"/proc/self/fd/{f.fileno()}")
                .resolve()
                .is_relative_to(self.workspaces[a["binding"]["workspace_ref"]]),
                "FORBIDDEN",
            )
            data = f.read(self.max_artifact_bytes + 1)
        require(
            len(data) <= self.max_artifact_bytes,
            "RATE_LIMITED",
            "Artifact exceeds preview limit",
        )
        return data

    def verify_pending(self):
        with self.storage.lock:
            rows = self.storage.db.execute(
                "SELECT aggregate_json FROM tasks"
            ).fetchall()
            jobs = [
                (a, attempt)
                for row in rows
                for a in [json.loads(row[0])]
                for attempt in a["attempts"].values()
                if attempt["status"] == "verifying"
            ]
        for original, attempt in jobs:
            checked = []
            failure = None
            try:
                for c in attempt.get("candidates", []):
                    data = self._read_candidate(original, c)
                    sha = hashlib.sha256(data).hexdigest()
                    require(
                        not c.get("sha256") or c["sha256"] == sha,
                        "EVIDENCE_REJECTED",
                        "Artifact digest mismatch",
                    )
                    if c.get("media_type") == "application/json":
                        json.loads(data)
                    checked.append((c, data, sha))
                self._check_acceptance(
                    original, self._stage(original, attempt["stage_id"]), checked
                )
            except (TaskError, OSError, ValueError, ValidationError) as exc:
                failure = (
                    exc.code if isinstance(exc, TaskError) else "EVIDENCE_REJECTED"
                )
            with self.storage.transaction() as db:
                row = db.execute(
                    "SELECT aggregate_json FROM tasks WHERE id=?",
                    (original["snapshot"]["task"]["id"],),
                ).fetchone()
                a = json.loads(row[0])
                current = a["attempts"][attempt["id"]]
                s = self._stage(a, attempt["stage_id"])
                if current["status"] != "verifying":
                    continue
                if a["snapshot"]["task"]["control_epoch"] != attempt[
                    "control_epoch"
                ] and not (
                    a.get("control_requested") in {"pause", "interrupt_agent"}
                    and attempt["id"] in a.get("draining_attempts", [])
                ):
                    self._event(
                        db,
                        a,
                        "verification.quarantined",
                        {"attempt_id": attempt["id"]},
                        attempt["id"],
                    )
                    self._save(db, a)
                    continue
                ids = []
                if not failure:
                    for c, data, sha in checked:
                        id = uid("artifact")
                        ids.append(id)
                        artifact = dict(
                            id=id,
                            kind=c["role"],
                            display_name=Path(
                                c.get(
                                    "path",
                                    c.get("external_ref", {}).get("path", "artifact"),
                                )
                            ).name,
                            media_type=c.get("media_type", "application/octet-stream"),
                            size_bytes=len(data),
                            sha256=sha,
                            locator_ref=uid("locator"),
                            created_at=utc(),
                        )
                        a["snapshot"]["artifacts"].append(artifact)
                        a["locators"][id] = {
                            **c,
                            "sha256": sha,
                            "size_bytes": len(data),
                            "content_hex": data.hex(),
                        }
                evidence = dict(
                    id=uid("evidence"),
                    source="artifact_verifier",
                    verification="rejected" if failure else "verified",
                    sha256=digest([v[2] for v in checked]),
                    observed_at=utc(),
                    artifact_refs=ids,
                    operation_refs=[
                        o["id"]
                        for o in a["snapshot"]["external_operations"]
                        if o["attempt_id"] == attempt["id"]
                    ],
                    summary=failure or "Declared acceptance rules verified",
                    verifier_version="artifact-verifier/v1",
                )
                a["snapshot"]["evidence"].append(evidence)
                s["evidence_refs"].append(evidence["id"])
                s["artifact_refs"].extend(ids)
                s["status"] = "failed" if failure else "completed"
                current["status"] = "failed" if failure else "succeeded"
                current["ended_at"] = utc()
                s["current_attempt"].update(
                    status=current["status"], ended_at=current["ended_at"]
                )
                s["result"] = current.get("result", {})
                for rule in s["acceptance"]:
                    rule.update(
                        state="failed" if failure else "passed",
                        evidence_refs=[evidence["id"]],
                    )
                if a["snapshot"]["task"]["status"] not in TERMINAL | {
                    "cancelling",
                    "paused",
                }:
                    a["snapshot"]["task"]["status"] = (
                        "blocked" if failure else "running"
                    )
                if a.get("control_requested") in {
                    "pause",
                    "interrupt_agent",
                } and not any(v["status"] in ACTIVE for v in a["snapshot"]["stages"]):
                    a["snapshot"]["task"]["status"] = "paused"
                self._ready(a)
                self._event(
                    db,
                    a,
                    "evidence.verified",
                    {
                        "stage_id": s["id"],
                        "evidence_id": evidence["id"],
                        "verification": evidence["verification"],
                    },
                    attempt["id"],
                )
                if not failure:
                    self._event(
                        db,
                        a,
                        "stage.completed",
                        {"stage_id": s["id"], "attempt_id": attempt["id"]},
                        attempt["id"],
                    )
                self._enqueue_continuation(db, a, "verification", attempt["id"])
                self._save(db, a)

    def _check_acceptance(self, a, s, checked):
        for r in s["acceptance"]:
            p = r["parameters"]
            kind = r["type"]
            if kind in {"artifact_exists", "schema_matches"}:
                values = [
                    (c, d)
                    for c, d, _ in checked
                    if c["role"] == p["artifact_role"] or p["artifact_role"] == "*"
                ]
                require(values, "EVIDENCE_REJECTED", "Required artifact missing")
                if kind == "schema_matches":
                    require(
                        p["schema_ref"] in {"object/v1"},
                        "CAPABILITY_UNAVAILABLE",
                        "Schema not registered",
                    )
                    for _, data in values:
                        Draft202012Validator({"type": "object"}).validate(
                            json.loads(data)
                        )
            elif kind == "external_operation_terminal":
                ops = [
                    o
                    for o in a["snapshot"]["external_operations"]
                    if o["attempt_id"] == s["current_attempt"]["id"]
                ]
                require(
                    ops and all(o["status"] == "succeeded" for o in ops),
                    "EVIDENCE_REJECTED",
                )
            elif kind == "metric_threshold":
                facts = [
                    v
                    for v in a.get("observations", [])
                    if v.get("attempt_id") == s["current_attempt"]["id"]
                ]
                values = [
                    m["value"]
                    for fact in facts
                    for m in fact.get("metrics", [])
                    if m["name"] == p["metric"] and m["split"] == p["split"]
                ]
                require(
                    values
                    and (
                        values[-1] >= p["value"]
                        if p["operator"] == ">="
                        else values[-1] <= p["value"]
                    ),
                    "EVIDENCE_REJECTED",
                )
            elif kind == "user_review_recorded":
                require(
                    any(
                        v["decision"] == "accept"
                        and v["result_digest"] == digest([sha for _, _, sha in checked])
                        for v in a["reviews"]
                    ),
                    "EVIDENCE_REJECTED",
                )

    def artifact(self, ctx, task_id, artifact_id):
        from .projections import safe_text

        with self.storage.lock:
            a = self._load(self.storage.db, ctx, task_id)
            require(artifact_id in a["locators"], "NOT_FOUND")
            c = a["locators"][artifact_id]
            data = bytes.fromhex(c["content_hex"])
            require(
                hashlib.sha256(data).hexdigest() == c["sha256"], "EVIDENCE_REJECTED"
            )
            media = c.get("media_type", "")
            return {
                "artifact_id": artifact_id,
                "sha256": c["sha256"],
                "media_type": media,
                "size_bytes": len(data),
                "content": (
                    safe_text(data[:65536].decode("utf-8", errors="replace"))
                    if media.startswith("text/") or media == "application/json"
                    else None
                ),
                "truncated": len(data) > 65536,
                "untrusted": True,
            }

    def _cmd_skip_stage(self, db, a, ctx, p, key):
        s = self._stage(a, p["stage_id"])
        require(
            not s["required"]
            and s["status"] in {"pending", "ready"}
            and p.get("reason"),
            "INVALID_TRANSITION",
        )
        s.update(status="skipped", reason=p["reason"])
        self._ready(a)
        self._event(db, a, "stage.skipped", p, key)

    def _cmd_retry_stage(self, db, a, ctx, p, key):
        s = self._stage(a, p["stage_id"])
        require(s["status"] == "failed", "INVALID_TRANSITION")
        require(
            not any(spec.get("replaces_stage_id") == s["id"] for spec in a["specs"].values()),
            "INVALID_TRANSITION",
            "Failed stage already has a replacement",
        )
        require(
            not any(
                o["status"] not in EXTERNAL_TERMINAL
                for o in a["snapshot"]["external_operations"]
                if o["stage_id"] == s["id"]
            ),
            "EXECUTION_UNKNOWN",
        )
        s["status"] = "ready"
        a["snapshot"]["task"]["status"] = "running"
        self._event(db, a, "stage.retry_requested", p, key)

    def _cmd_request_replan(self, db, a, ctx, p, key):
        require(
            not any(s["status"] in ACTIVE for s in a["snapshot"]["stages"]),
            "INVALID_TRANSITION",
        )
        a["snapshot"]["task"]["status"] = "planning"
        self._event(db, a, "plan.requested", p, key)

    def _cmd_complete_task(self, db, a, ctx, p, key):
        s = a["snapshot"]
        stages = {stage["id"]: stage for stage in s["stages"]}
        replacements = {
            spec["replaces_stage_id"]: id
            for id, spec in a["specs"].items()
            if "replaces_stage_id" in spec
        }

        def delivered(stage):
            # Preserve failed Attempts as history; only verified replacement delivery
            # satisfies the original obligation. A new proposal alone never does.
            visited = set()
            while stage["status"] == "failed" and stage["id"] in replacements:
                if stage["id"] in visited:
                    return False
                visited.add(stage["id"])
                stage = stages[replacements[stage["id"]]]
            return (
                stage["status"] == "completed"
                if stage["required"]
                else stage["status"] in {"completed", "skipped", "superseded"}
            )

        require(
            s["stages"]
            and all(delivered(stage) for stage in s["stages"]),
            "INVALID_TRANSITION",
        )
        require(
            not any(
                v["status"] not in EXTERNAL_TERMINAL for v in s["external_operations"]
            ),
            "EXECUTION_UNKNOWN",
        )
        require(
            not any(
                v["state"] in {"prepared", "unknown"} for v in a["intents"].values()
            ),
            "EXECUTION_UNKNOWN",
        )
        require(
            p.get("summary")
            and p.get("artifact_refs")
            and all(id in a["locators"] for id in p["artifact_refs"]),
            "EVIDENCE_REJECTED",
        )
        for r in s["task"]["completion_conditions"]:
            r.update(
                state="passed",
                evidence_refs=[
                    e["id"] for e in s["evidence"] if e["verification"] == "verified"
                ],
            )
        s["task"]["status"] = "completed"
        a["final_summary"] = p["summary"]
        self._event(db, a, "task.completed", p, key)
        db.execute(
            "UPDATE outbox SET state='cancelled' WHERE task_id=? AND kind='continuation' AND state IN ('pending','leased')",
            (s["task"]["id"],),
        )
        if a.get("host_continuation_binding"):
            self.storage.enqueue(
                db,
                s["task"]["id"],
                "revoke_continuation",
                {"binding_id": a["host_continuation_binding"]},
                "seal:" + s["task"]["id"],
            )

    def _cmd_fail_task(self, db, a, ctx, p, key):
        require(
            not any(
                i["state"] in {"prepared", "unknown"} for i in a["intents"].values()
            ),
            "EXECUTION_UNKNOWN",
        )
        require(
            not any(s["status"] in ACTIVE for s in a["snapshot"]["stages"]),
            "INVALID_TRANSITION",
        )
        require(
            not any(
                o["status"] not in EXTERNAL_TERMINAL
                for o in a["snapshot"]["external_operations"]
            ),
            "EXECUTION_UNKNOWN",
        )
        a["snapshot"]["task"]["status"] = "failed"
        self._event(db, a, "task.failed", p, key)

    def _cmd_request_control(self, db, a, ctx, p, key):
        t = a["snapshot"]["task"]
        action = p.get("action")
        require(
            action in {"start", "pause", "interrupt_agent", "resume", "cancel"},
            "INVALID_COMMAND",
        )
        if action in {"start", "resume"}:
            require(
                t["status"] == ("draft" if action == "start" else "paused"),
                "INVALID_TRANSITION",
            )
            require(
                timestamp(t["constraints"]["deadline_at"]) > time.time(),
                "AUTHORIZATION_REQUIRED",
            )
            a.pop("control_requested", None)
            a.pop("host_continuation_binding", None)
            a["execution_authorized"] = True
            a["in_current_turn"] = False
            a["launch_receipt"] = ctx.call_id or ctx.interaction_id
            t["status"] = "running" if a["specs"] else "planning"
            self._enqueue_continuation(db, a, action, key)
        else:
            if a.get("in_current_turn"):
                a["launch_receipt"] = ctx.call_id or ctx.interaction_id
            t["control_epoch"] += 1
            a["control_requested"] = action
            a["draining_attempts"] = [
                v["id"] for v in a["attempts"].values() if v["status"] in ACTIVE
            ]
            db.execute(
                "UPDATE outbox SET state='cancelled' WHERE task_id=? AND kind='continuation' AND state IN ('pending','leased')",
                (t["id"],),
            )
            active = any(s["status"] in ACTIVE for s in a["snapshot"]["stages"]) or any(
                i["state"] in {"prepared", "unknown"} for i in a["intents"].values()
            )
            t["status"] = (
                ("cancelling" if active else "cancelled")
                if action == "cancel"
                else (t["status"] if active else "paused")
            )
            self.storage.enqueue(
                db,
                t["id"],
                "control",
                {"action": action, "control_epoch": t["control_epoch"]},
                "control:" + key,
            )
        self._event(
            db,
            a,
            "task.control_requested",
            {"action": action, "control_epoch": t["control_epoch"]},
            key,
        )
        return {
            "state": t["status"],
            "draining": a.get("control_requested") is not None,
        }

    def reconcile_idle_task(self, task_id, runtime, *, reconciled_intents=()):
        """Close an ended native turn through the existing, revocable Host binding.

        A finished turn is not a finished task. Only durable business changes reset
        the recovery budget; repeated reads, timestamps and turn events do not.
        """
        with self.storage.transaction() as db:
            row = db.execute(
                "SELECT aggregate_json FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if row is None:
                return
            a = json.loads(row[0])
            t = a["snapshot"]["task"]
            turn_id = runtime.get("last_turn_id")
            if (
                t["status"] not in {"running", "planning", "waiting_external"}
                or a.get("in_current_turn")
                or not a.get("execution_authorized")
                or not a.get("launch_receipt")
                or a.get("control_requested")
                or runtime.get("binding_id") != a.get("host_continuation_binding")
                or runtime.get("state") != "active"
                or runtime.get("session_id") != a["binding"]["session_id"]
                or runtime.get("thread_id") != a["binding"]["thread_id"]
                or runtime.get("session_status") != "ready"
                or runtime.get("active_turn_id")
                or runtime.get("pending_continuation")
                or not turn_id
            ):
                return
            previous = a.get("idle_recovery", {})
            if previous.get("turn_id") == turn_id and previous.get("control_epoch") == t["control_epoch"]:
                return
            # Existing deliveries own the next turn. Unknown external submissions
            # must be reconciled, never converted into another submission attempt.
            if db.execute(
                "SELECT 1 FROM outbox WHERE task_id=? AND kind='continuation' "
                "AND state IN ('pending','leased')", (task_id,),
            ).fetchone():
                return
            if any(o["status"] not in EXTERNAL_TERMINAL for o in a["snapshot"]["external_operations"]):
                return
            if any(s["status"] == "verifying" for s in a["snapshot"]["stages"]):
                return
            if any(r["status"] in {"open", "deferred"} for r in a["requests"].values()):
                return
            if any(i["state"] == "unknown" or (
                i["state"] == "prepared" and i["id"] not in reconciled_intents
            ) for i in a["intents"].values()):
                return
            fingerprint = digest({
                "plan_revision": t["plan_revision"],
                "control_epoch": t["control_epoch"],
                "stages": [{
                    "id": s["id"], "status": s["status"],
                    "attempt_id": (s.get("current_attempt") or {}).get("id"),
                    "acceptance": s.get("acceptance"),
                } for s in a["snapshot"]["stages"]],
                "intents": sorted((i["id"], i["state"]) for i in a["intents"].values()),
                "operations": [(o["id"], o["status"]) for o in a["snapshot"]["external_operations"]],
                "artifacts": [v["id"] for v in a["snapshot"]["artifacts"]],
                "evidence": [v["id"] for v in a["snapshot"]["evidence"]],
            })
            stagnant = previous.get("stagnant_turns", 0) + 1 if previous.get("fingerprint") == fingerprint else 0
            a["idle_recovery"] = {
                "turn_id": turn_id, "fingerprint": fingerprint,
                "stagnant_turns": stagnant, "control_epoch": t["control_epoch"],
            }
            if runtime.get("last_turn_status") != "completed":
                t["status"] = "blocked"
                t["blocked_reason"] = "Agent 回合被中断或失败；请在原会话检查原因后继续。"
            elif stagnant >= 2:
                t["status"] = "blocked"
                t["blocked_reason"] = "连续两次自动接续没有推进任务；请在原会话检查阻塞原因。"
            else:
                self._enqueue_continuation(db, a, "idle_turn", turn_id)
            self._event(db, a, "task.recovery_blocked" if t["status"] == "blocked" else "task.recovery_requested", {
                "turn_id": turn_id, "stagnant_turns": stagnant,
                "reason": t.get("blocked_reason", "native_turn_completed_with_unfinished_task"),
            }, "idle:" + turn_id)
            self._save(db, a)

    def _enqueue_continuation(self, db, a, reason, identity):
        # The current native turn already owns execution. A background continuation
        # needs its own trusted App decision receipt; opening a view grants none.
        if a.get("in_current_turn"):
            return
        t = a["snapshot"]["task"]
        if t["status"] in TERMINAL | {"paused", "cancelling"} or a.get(
            "control_requested"
        ):
            return
        dedupe = f"{t['id']}:{reason}:{identity}:{t['control_epoch']}"
        if db.execute("SELECT 1 FROM outbox WHERE dedupe_key=?", (dedupe,)).fetchone():
            return
        if (
            a["continuations"] >= t["constraints"]["max_auto_continuations"]
            or timestamp(t["constraints"]["deadline_at"]) <= time.time()
        ):
            t["status"] = "blocked"
            t["blocked_reason"] = "自动接续次数已用完或任务期限已到；请检查任务后调整计划。"
            return
        a["continuations"] += 1
        self.storage.enqueue(
            db,
            t["id"],
            "continuation",
            {
                "reason": reason,
                "binding": a["binding"],
                "event_seq": a["snapshot"]["last_event_seq"],
                "control_epoch": t["control_epoch"],
                "expires_at": t["constraints"]["deadline_at"],
                "launch_receipt": a["launch_receipt"],
            },
            dedupe,
        )

    def _authorized(self, a, proposal):
        require(
            timestamp(proposal["expires_at"]) > time.time(),
            "AUTHORIZATION_REQUIRED",
            "Proposal expired",
        )
        require(
            proposal["plan_revision"] == a["snapshot"]["task"]["plan_revision"],
            "PLAN_CONFLICT",
        )
        adapter = self.governance.get(proposal["immutable_inputs"]["route"])
        require(
            adapter is not None,
            "AUTHORIZATION_REQUIRED",
            "Governance adapter unavailable",
        )
        require(
            adapter.authorized(proposal) or self._user_grant_valid(a, proposal, adapter),
            "AUTHORIZATION_REQUIRED",
            "Governance authorization missing, expired or mismatched",
        )

    def _user_grant_valid(self, a, proposal, adapter):
        can_authorize = getattr(adapter, "can_authorize", lambda *_: False)
        return any(
            g["proposal_digest"] == proposal["digest"]
            and g["principal_id"] == a["owner_id"]
            and g["binding_id"] == a["binding"]["binding_id"]
            and g["plan_revision"] == proposal["plan_revision"]
            and can_authorize(g["principal_id"], proposal)
            for g in a.get("training_grants", [])
        )

    @staticmethod
    def _training_ready(proposal):
        require(
            all(check.get("status") == "passed" for check in proposal["readiness"]),
            "AUTHORIZATION_REQUIRED",
            "Training readiness checks remain unresolved; update the proposal after verification",
        )

    def training_authorizations(self, a, ctx):
        """Read-only availability; the command always repeats these checks."""
        result = {}
        task = a["snapshot"]["task"]
        for proposal in a["proposals"].values():
            adapter = self.governance.get(proposal["immutable_inputs"]["route"])
            stage = self._stage(a, proposal["stage_id"])
            request = max(
                (r for r in a["requests"].values()
                 if r["proposal_id"] == proposal["proposal_id"]),
                key=lambda r: (r.get("parent_request_id") is not None, r["created_at"]),
                default=None,
            )
            latest = max(
                (p for p in a["proposals"].values() if p["stage_id"] == stage["id"]),
                key=lambda p: p["revision"],
            )
            current = bool(
                request and request["status"] in {"open", "deferred"}
                and latest["proposal_id"] == proposal["proposal_id"]
                and task["status"] not in TERMINAL | {"cancelling"}
                and (not a.get("control_requested") or request["status"] == "deferred")
                and proposal["plan_revision"] == task["plan_revision"]
                and time.time() < timestamp(proposal["expires_at"])
                and time.time() < timestamp(task["constraints"]["deadline_at"])
                and stage["current_attempt"] is None
                and not any(i["stage_id"] == stage["id"] for i in a["intents"].values())
            )
            unresolved = [
                str(check.get("label") or check.get("id") or "准备检查")
                for check in proposal["readiness"] if check.get("status") != "passed"
            ]
            try:
                self._authorized(a, proposal)
                authorized = True
            except TaskError:
                authorized = False
            can_authorize = bool(
                current and not authorized and ctx.principal_id == a["owner_id"]
                and request["status"] == "open" and task["status"] != "paused"
                and adapter and getattr(adapter, "can_authorize", lambda *_: False)(
                    ctx.principal_id, proposal
                )
            )
            if not current:
                reason = "方案已过期、被替换、已处理或任务已暂停；请在聊天中要求更新方案。"
            elif authorized:
                reason = (
                    "本次方案已有授权；仍须完成准备检查：" + "、".join(unresolved)
                    if unresolved else "本次方案已有授权；确认执行后才会开始训练。"
                )
            elif not adapter:
                reason = "尚未配置此项目的治理入口；请在聊天中要求配置，重复点击不会启动训练。"
            elif can_authorize:
                reason = (
                    "本次方案可授权。需要手动审批时，请点击“授权本次方案”并允许 Host 授权请求。"
                    "此操作不会启动训练。"
                )
            else:
                reason = "当前用户没有此范围的授权权限，或方案超出授权范围；请在聊天中要求调整或配置权限。"
            if unresolved and not authorized:
                reason += "执行前仍须完成准备检查：" + "、".join(unresolved) + "。"
            confirm_reason = reason
            if current and not authorized:
                confirm_reason = "尚未授权，请先完成上方“授权本次方案”。"
            if current and unresolved and not authorized:
                confirm_reason += "另外尚未通过：" + "、".join(unresolved) + "。"
            result[proposal["proposal_id"]] = {
                "authorized": authorized, "can_authorize": can_authorize,
                "can_confirm": current and authorized and not unresolved,
                "reason": reason, "confirm_reason": confirm_reason,
            }
        return result

    def _cmd_propose_training(self, db, a, ctx, p, key):
        s = self._stage(a, p["stage_id"])
        require(
            s["status"] in {"ready", "waiting_input", "waiting_approval"},
            "INVALID_TRANSITION",
        )
        require(
            a["snapshot"]["task"]["constraints"]["allow_training"],
            "AUTHORIZATION_REQUIRED",
        )
        proposal = copy.deepcopy(p["proposal"])
        require(
            set(proposal)
            == {
                "immutable_inputs",
                "resources",
                "hard_limits",
                "estimate",
                "readiness",
                "expires_at",
                "objective",
            },
            "INVALID_COMMAND",
        )
        require(
            set(proposal["immutable_inputs"])
            >= {
                "route",
                "project_id",
                "release_digest",
                "config_digest",
                "data_digest",
                "split_digest",
                "role",
                "profile",
                "final_test",
            },
            "INVALID_COMMAND",
        )
        require(
            proposal["immutable_inputs"]["route"]
            in {"governance_mcp", "project_governance"},
            "INVALID_COMMAND",
        )
        require(
            valid_limits(proposal["hard_limits"]),
            "INVALID_COMMAND",
        )
        if proposal["hard_limits"]["memory_bytes"] is None:
            memory = proposal["resources"].get("memory")
            explanation = proposal["estimate"].get("memory_limit_enforcement")
            require(
                isinstance(memory, dict)
                and memory.get("enforcement") == "scheduling_only"
                and isinstance(explanation, str) and bool(explanation.strip()),
                "INVALID_COMMAND",
                "An uncapped memory proposal must explicitly disclose scheduling-only enforcement",
            )
        proposal.update(
            proposal_id=uid("proposal"),
            revision=1 + sum(x["stage_id"] == s["id"] for x in a["proposals"].values()),
            task_id=a["snapshot"]["task"]["id"],
            stage_id=s["id"],
            plan_revision=a["snapshot"]["task"]["plan_revision"],
        )
        proposal["digest"] = digest(proposal)
        for request in a["requests"].values():
            if request["stage_id"] == s["id"] and request["status"] == "open":
                request["status"] = "superseded"
        request = {
            "request_id": uid("request"),
            "kind": "training_proposal",
            "stage_id": s["id"],
            "proposal_id": proposal["proposal_id"],
            "subject_digest": proposal["digest"],
            "status": "open",
            "allowed_decisions": ["authorize", "confirm", "defer"],
            "created_at": utc(),
            "expires_at": proposal["expires_at"],
        }
        a["proposals"][proposal["proposal_id"]] = proposal
        a["requests"][request["request_id"]] = request
        try:
            self._authorized(a, proposal)
            state = "waiting_input"
        except TaskError:
            state = "waiting_approval"
        s["status"] = state
        a["snapshot"]["task"]["status"] = state
        self._event(
            db,
            a,
            "training.proposed",
            {
                "stage_id": s["id"],
                "proposal_id": proposal["proposal_id"],
                "digest": proposal["digest"],
            },
            key,
        )
        return {"proposal": proposal, "request": request,
                "decision_availability": self.training_authorizations(a, ctx)[proposal["proposal_id"]]}

    def _cmd_submit_input(self, db, a, ctx, p, key):
        request = a["requests"].get(p.get("request_id"))
        require(request is not None, "NOT_FOUND")
        require(request["status"] in {"open", "deferred"}, "VERSION_CONFLICT")
        values = p.get("values", {})
        require(set(values) == {"proposal_digest", "decision"}, "INVALID_COMMAND")
        require(values["proposal_digest"] == request["subject_digest"], "PLAN_CONFLICT")
        proposal = a["proposals"][request["proposal_id"]]
        s = self._stage(a, request["stage_id"])
        require(
            s["current_attempt"] is None
            and not any(i["stage_id"] == s["id"] for i in a["intents"].values()),
            "INVALID_TRANSITION",
            "Submission may already have started",
        )
        require(
            timestamp(proposal["expires_at"]) > time.time(), "AUTHORIZATION_REQUIRED"
        )
        decision = values["decision"]
        require(decision in {"authorize", "confirm", "defer"}, "INVALID_COMMAND")
        if decision == "authorize":
            availability = self.training_authorizations(a, ctx)[proposal["proposal_id"]]
            require(availability["can_authorize"], "AUTHORIZATION_REQUIRED",
                    availability["reason"])
            grant = {
                "id": uid("grant"),
                "proposal_digest": proposal["digest"],
                "plan_revision": proposal["plan_revision"],
                "binding_id": a["binding"]["binding_id"],
                "principal_id": ctx.principal_id,
                "interaction_receipt": ctx.interaction_id,
                "command_id": key,
                "expires_at": proposal["expires_at"],
                "granted_at": utc(),
            }
            a.setdefault("training_grants", []).append(grant)
            s["status"] = "waiting_input"
            a["snapshot"]["task"]["status"] = "waiting_input"
            self._event(db, a, "training.authorized", {
                "proposal_id": proposal["proposal_id"], **grant,
            }, key)
            return {"request_id": request["request_id"], "state": "authorized",
                    "grant_id": grant["id"]}
        if decision == "confirm":
            require(
                self.training_authorizations(a, ctx)[proposal["proposal_id"]]["can_confirm"],
                "AUTHORIZATION_REQUIRED", "Proposal is no longer confirmable",
            )
            self._authorized(a, proposal)
            if a.get("in_current_turn"):
                a["in_current_turn"] = False
                a["launch_receipt"] = ctx.call_id or ctx.interaction_id
            if request["status"] == "deferred":
                request = {
                    **request,
                    "request_id": uid("request"),
                    "parent_request_id": request["request_id"],
                    "status": "open",
                }
                a["requests"][request["request_id"]] = request
            request["status"] = "accepted"
            s["status"] = "ready"
            a["snapshot"]["task"]["status"] = "running"
            a.pop("control_requested", None)
            self._enqueue_continuation(db, a, "decision", request["request_id"])
        else:
            request["status"] = "deferred"
            a["snapshot"]["task"]["status"] = "paused"
            db.execute(
                "UPDATE outbox SET state='cancelled' WHERE task_id=? AND kind='continuation' AND state IN ('pending','leased')",
                (proposal["task_id"],),
            )
        a["decisions"].append(
            {
                "request_id": request["request_id"],
                "decision": decision,
                "subject_digest": proposal["digest"],
                "interaction_receipt": ctx.interaction_id,
                "command_id": key,
                "decided_at": utc(),
            }
        )
        self._event(
            db,
            a,
            "input.recorded",
            {"request_id": request["request_id"], "decision": decision},
            key,
        )
        return {"request_id": request["request_id"], "state": request["status"]}

    def _cmd_record_review(self, db, a, ctx, p, key):
        require(
            p.get("decision") in {"accept", "reject"}
            and len(p.get("result_digest", "")) == 64,
            "INVALID_COMMAND",
        )
        a["reviews"].append(
            {**p, "interaction_receipt": ctx.interaction_id, "observed_at": utc()}
        )
        self._event(db, a, "review.recorded", p, key)

    def _cmd_prepare_external(self, db, a, ctx, p, key):
        attempt, s = self._attempt(a, p)
        require(not a.get("control_requested"), "STALE_EXECUTION")
        require(
            not any(i["attempt_id"] == attempt["id"] for i in a["intents"].values()),
            "EXECUTION_UNKNOWN",
            "Reconcile existing intent",
        )
        route = p.get("route")
        require(route in self.governance, "CAPABILITY_UNAVAILABLE")
        if self.capabilities[a["specs"][s["id"]]["execution"]["capability_ref"]][
            "training"
        ]:
            proposals = [v for v in a["proposals"].values() if v["stage_id"] == s["id"]]
            require(proposals, "AUTHORIZATION_REQUIRED")
            proposal = max(proposals, key=lambda p: p["revision"])
            self._authorized(a, proposal)
            self._training_ready(proposal)
            require(
                p["immutable_inputs"] == proposal["immutable_inputs"]
                and route == proposal["immutable_inputs"]["route"],
                "PLAN_CONFLICT",
            )
        intent = {
            "id": uid("intent"),
            "attempt_id": attempt["id"],
            "stage_id": s["id"],
            "route": route,
            "immutable_inputs": p["immutable_inputs"],
            "idempotency_key": uid("execution"),
            "state": "prepared",
            "control_epoch": a["snapshot"]["task"]["control_epoch"],
        }
        a["intents"][intent["id"]] = intent
        self._event(
            db,
            a,
            "external.prepared",
            {"intent_id": intent["id"], "attempt_id": attempt["id"]},
            key,
        )
        return {"intent_id": intent["id"], "idempotency_key": intent["idempotency_key"]}

    def _cmd_bind_external(self, db, a, ctx, p, key):
        intent = a["intents"].get(p["intent_id"])
        require(intent is not None, "NOT_FOUND")
        operation = p["operation_ref"]
        require(
            self.governance[intent["route"]].owns(intent, operation),
            "FORBIDDEN",
            "Operation does not match durable intent",
        )
        if intent.get("operation_id"):
            require(intent["operation_id"] == operation["id"], "IDEMPOTENCY_CONFLICT")
            return {"operation_id": operation["id"]}
        o = {
            "id": operation["id"],
            "provider": intent["route"],
            "governance_route": intent["route"],
            "stage_id": intent["stage_id"],
            "attempt_id": intent["attempt_id"],
            "status": "accepted",
            "last_observed_at": utc(),
        }
        for k in ("job_id", "run_id", "campaign_id"):
            if operation.get(k):
                o[k] = operation[k]
        deadline = timestamp(a["snapshot"]["task"]["constraints"]["deadline_at"])
        proposals = [
            v for v in a["proposals"].values() if v["stage_id"] == intent["stage_id"]
        ]
        if proposals:
            started_at = timestamp(a["attempts"][intent["attempt_id"]]["started_at"])
            adapter = self.governance[intent["route"]]
            if hasattr(adapter, "execution_started_at"):
                try:
                    started_at = adapter.execution_started_at(operation)
                except Exception:
                    # Missing authoritative time keeps the conservative Attempt
                    # deadline; it never grants an unbounded execution window.
                    pass
            deadline = min(
                deadline,
                started_at
                + max(proposals, key=lambda p: p["revision"])["hard_limits"]["wall_seconds"],
            )
        a.setdefault("external_deadlines", {})[o["id"]] = deadline
        a["snapshot"]["external_operations"].append(o)
        intent.update(state="bound", operation_id=o["id"])
        s = self._stage(a, intent["stage_id"])
        s["status"] = "waiting_external"
        s["current_attempt"]["status"] = "waiting_external"
        a["attempts"][intent["attempt_id"]]["status"] = "waiting_external"
        if a.get("control_requested"):
            if a["control_requested"] == "cancel":
                a["snapshot"]["task"]["status"] = "cancelling"
                self.storage.enqueue(
                    db,
                    a["snapshot"]["task"]["id"],
                    "stop_external",
                    {"operation_id": o["id"], "route": intent["route"]},
                    "stop:" + o["id"],
                )
        else:
            a["snapshot"]["task"]["status"] = "waiting_external"
        self._event(db, a, "external.bound", o, key)
        return {"operation_id": o["id"]}

    def _cmd_wait(self, db, a, ctx, p, key):
        attempt, s = self._attempt(a, p)
        wait = p["wait_spec"]
        require(
            wait.get("kind") in {"external", "input", "approval"}, "INVALID_COMMAND"
        )
        if wait["kind"] == "external":
            require(
                any(
                    o["id"] == wait.get("operation_id")
                    and o["attempt_id"] == attempt["id"]
                    for o in a["snapshot"]["external_operations"]
                ),
                "INVALID_COMMAND",
            )
        state = "waiting_" + wait["kind"]
        s["status"] = state
        a["snapshot"]["task"]["status"] = state
        s["wait"] = {
            "kind": wait["kind"],
            "id": uid("wait"),
            "summary": wait.get("summary", "Waiting for verified external facts"),
            "deadline_at": wait.get(
                "deadline_at", a["snapshot"]["task"]["constraints"]["deadline_at"]
            ),
        }
        if wait.get("operation_id"):
            s["wait"]["operation_id"] = wait["operation_id"]
        self._event(
            db, a, "stage.waiting", {"stage_id": s["id"], "wait": s["wait"]}, key
        )

    def enforce_deadlines(self, now=None):
        now = time.time() if now is None else now
        with self.storage.transaction() as db:
            for row in db.execute(
                "SELECT aggregate_json FROM tasks WHERE status NOT IN ('completed','failed','cancelled')"
            ).fetchall():
                a = json.loads(row[0])
                expired = any(
                    o["status"] not in EXTERNAL_TERMINAL
                    and a.get("external_deadlines", {}).get(o["id"], float("inf"))
                    <= now
                    for o in a["snapshot"]["external_operations"]
                )
                if expired and a.get("control_requested") != "cancel":
                    self._cmd_request_control(
                        db,
                        a,
                        None,
                        {"action": "cancel"},
                        "budget-expired:" + a["snapshot"]["task"]["id"],
                    )
                    self._event(
                        db,
                        a,
                        "budget.expired",
                        {"reason": "External wall-time budget exhausted"},
                        "budget-expired",
                    )
                    self._save(db, a)

    def reconcile_external(self, task_id, intent_id, operation):
        """Worker-only read reconciliation; never submits or creates authorization."""
        with self.storage.transaction() as db:
            row = db.execute(
                "SELECT aggregate_json FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            require(row is not None, "NOT_FOUND")
            a = json.loads(row[0])
            intent = a["intents"][intent_id]
            if intent["state"] not in {"prepared", "unknown"}:
                return
            self._cmd_bind_external(
                db,
                a,
                None,
                {"intent_id": intent_id, "operation_ref": operation},
                "reconcile:" + intent_id,
            )
            self._save(db, a)

    def control_ack(self, task_id, control_epoch):
        with self.storage.transaction() as db:
            a = json.loads(
                db.execute(
                    "SELECT aggregate_json FROM tasks WHERE id=?", (task_id,)
                ).fetchone()[0]
            )
            t = a["snapshot"]["task"]
            if t["control_epoch"] != control_epoch or a.get(
                "control_requested"
            ) not in {
                "cancel",
                "interrupt_agent",
            }:
                return
            external = {i["attempt_id"] for i in a["intents"].values()}
            for attempt in a["attempts"].values():
                if attempt["id"] in external or attempt["status"] not in ACTIVE:
                    continue
                stage = self._stage(a, attempt["stage_id"])
                attempt["status"] = "cancelled"
                stage["status"] = "cancelled"
                stage["current_attempt"]["status"] = "cancelled"
            if not any(
                s["status"] in ACTIVE for s in a["snapshot"]["stages"]
            ) and not any(
                i["state"] in {"prepared", "unknown"} for i in a["intents"].values()
            ):
                t["status"] = (
                    "cancelled" if a["control_requested"] == "cancel" else "paused"
                )
            self._event(
                db,
                a,
                "task.control_acknowledged",
                {"control_epoch": control_epoch},
                "control-ack:" + str(control_epoch),
            )
            self._save(db, a)

    def observe(self, task_id, operation_id, fact, *, source, message_id):
        """Trusted worker only. HTTP authentication selects the source; payload cannot."""
        with self.storage.transaction() as db:
            row = db.execute(
                "SELECT aggregate_json FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            require(row is not None, "NOT_FOUND")
            a = json.loads(row[0])
            ops = [
                o
                for o in a["snapshot"]["external_operations"]
                if o["id"] == operation_id and o["provider"] == source
            ]
            require(ops, "FORBIDDEN")
            existing = db.execute(
                "SELECT digest FROM inbox WHERE source=? AND message_id=?",
                (source, message_id),
            ).fetchone()
            d = digest([task_id, operation_id, fact])
            if existing:
                require(existing[0] == d, "IDEMPOTENCY_CONFLICT")
                ops[0]["last_observed_at"] = utc()
                self._save(db, a, increment=False)
                return
            fingerprints = a.setdefault("observation_fingerprints", {})
            if fingerprints.get(operation_id) == d:
                ops[0]["last_observed_at"] = utc()
                self._save(db, a, increment=False)
                return
            db.execute("INSERT INTO inbox VALUES (?,?,?)", (source, message_id, d))
            fingerprints[operation_id] = d
            o = ops[0]
            old = o["status"]
            status = fact.get("status", "unknown")
            require(
                status
                in {
                    "accepted",
                    "queued",
                    "running",
                    "succeeded",
                    "failed",
                    "cancelled",
                    "unknown",
                },
                "INVALID_COMMAND",
            )
            if o["status"] in EXTERNAL_TERMINAL and status not in EXTERNAL_TERMINAL:
                return
            o["status"] = status
            o["last_observed_at"] = utc()
            if fact.get("run_id"):
                o["run_id"] = fact["run_id"]
            a.setdefault("observations", []).append(
                {
                    **fact,
                    "operation_id": operation_id,
                    "attempt_id": o["attempt_id"],
                    "observed_at": o["last_observed_at"],
                }
            )
            a["observations"] = a["observations"][-100:]
            self._event(
                db,
                a,
                "external.observed",
                {"operation_id": operation_id, "status": status},
                message_id,
            )
            t = a["snapshot"]["task"]
            s = self._stage(a, o["stage_id"])
            if a.get("control_requested"):
                if status in EXTERNAL_TERMINAL:
                    s["status"] = "cancelled"
                    s["current_attempt"]["status"] = "cancelled"
                    a["attempts"][o["attempt_id"]]["status"] = "cancelled"
                    if all(
                        v["status"] in EXTERNAL_TERMINAL
                        for v in a["snapshot"]["external_operations"]
                    ) and not any(
                        i["state"] in {"prepared", "unknown"}
                        for i in a["intents"].values()
                    ):
                        t["status"] = (
                            "cancelled"
                            if a["control_requested"] == "cancel"
                            else "paused"
                        )
            elif t["status"] not in TERMINAL:
                if status == "unknown":
                    t["status"] = "blocked"
                    if (
                        fact.get("ray_status") in {"SUCCEEDED", "FAILED", "STOPPED"}
                        and fact.get("reason")
                    ):
                        # A terminal scheduler with unverifiable model evidence
                        # needs Agent diagnosis, not endless silent observation.
                        self._enqueue_continuation(
                            db, a, "external_evidence_inconsistent", operation_id
                        )
                elif status in EXTERNAL_TERMINAL:
                    t["status"] = "running"
                    if status == "succeeded":
                        a["attempts"][o["attempt_id"]]["lease_until"] = (
                            time.time() + 3600
                        )
                    if status != "succeeded":
                        s["status"] = "failed"
                        s["current_attempt"]["status"] = "failed"
                        a["attempts"][o["attempt_id"]]["status"] = "failed"
                    if old != status:
                        self._enqueue_continuation(
                            db, a, "external", operation_id + ":" + status
                        )
                else:
                    t["status"] = "waiting_external"
            self._save(db, a)
