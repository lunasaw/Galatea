"""Durable project initialization; native sessions are always created by Host."""

import json
from pathlib import Path
import re
from dataclasses import replace
from .common import canonical, digest, require, uid, utc


class Projects:
    def __init__(self, service, host, *, workspace_ref):
        self.service, self.host, self.workspace_ref = service, host, workspace_ref
        with service.storage.lock:
            for row in service.storage.db.execute(
                "SELECT data_json FROM projects"
            ).fetchall():
                project = json.loads(row[0])
                service.workspaces[project["workspace_ref"]] = Path(
                    project["workspace_display_path"]
                )

    async def call(self, ctx, name, args):
        if name == "list_projects":
            with self.service.storage.lock:
                rows = self.service.storage.db.execute(
                    "SELECT data_json FROM projects WHERE owner_id=? ORDER BY rowid LIMIT 100",
                    (ctx.principal_id,),
                ).fetchall()
            return {"items": [json.loads(r[0]) for r in rows]}
        if name == "get_activation":
            with self.service.storage.lock:
                row = self.service.storage.db.execute(
                    "SELECT data_json FROM activations WHERE id=? AND owner_id=?",
                    (args["activation_id"], ctx.principal_id),
                ).fetchone()
                require(row is not None, "NOT_FOUND")
                return json.loads(row[0])
        self.service._confirmed(ctx)
        if name == "launch_project":
            command_id = args["command_id"]
            activation_id = "activation_" + digest([ctx.principal_id, command_id])[:32]
            with self.service.storage.transaction() as db:
                row = db.execute(
                    "SELECT data_json FROM activations WHERE id=?", (activation_id,)
                ).fetchone()
                if row:
                    saved = json.loads(row[0])
                    require(saved["digest"] == digest(args), "IDEMPOTENCY_CONFLICT")
                    return saved
                require(
                    args.get("inputs"), "INVALID_COMMAND", "Data directory required"
                )
                inputs = []
                for item in args["inputs"]:
                    path = Path(item["path"])
                    if path.is_absolute():
                        path = path.resolve()
                        roots = [
                            (ref, root)
                            for ref, root in self.service.workspaces.items()
                            if path.is_relative_to(root)
                        ]
                        require(
                            roots,
                            "FORBIDDEN",
                            "Data directory is outside registered roots",
                        )
                        ref, root = max(roots, key=lambda x: len(str(x[1])))
                        inputs.append(
                            {"workspace_ref": ref, "path": str(path.relative_to(root))}
                        )
                    else:
                        self.service.resolve_path(ctx.workspace_ref, str(path))
                        inputs.append(
                            {"workspace_ref": ctx.workspace_ref, "path": str(path)}
                        )
                project_id = uid("project")
                slug = "project-" + project_id[-10:]
                relative = slug
                directory = self.service.resolve_path(self.workspace_ref, relative)
                project = {
                    "project_id": project_id,
                    "name": args["goal"][:80],
                    "slug": slug,
                    "workspace_ref": project_id,
                    "workspace_display_path": str(directory),
                    "initialization_state": "allocating",
                    "task_sessions": [],
                }
                saved = {
                    "activation_id": activation_id,
                    "command_id": command_id,
                    "digest": digest(args),
                    "state": "allocating",
                    "project_id": project_id,
                    "project": project,
                    "goal": args["goal"],
                    "inputs": inputs,
                    "constraints": args.get("constraints", {}),
                    "launch_receipt": ctx.call_id,
                    "owner_id": ctx.principal_id,
                    "context": {
                        k: getattr(ctx, k)
                        for k in (
                            "principal_id",
                            "host_id",
                            "session_id",
                            "thread_id",
                            "workspace_ref",
                        )
                    },
                    "parent_workspace_ref": self.workspace_ref,
                    "workspace_relative_path": relative,
                }
                db.execute(
                    "INSERT INTO projects VALUES (?,?,?)",
                    (project_id, ctx.principal_id, canonical(project)),
                )
                db.execute(
                    "INSERT INTO activations VALUES (?,?,?)",
                    (activation_id, ctx.principal_id, canonical(saved)),
                )
            return saved
        if name == "activate_project_task":
            id = "activation_" + digest([ctx.principal_id, args["command_id"]])[:32]
            with self.service.storage.transaction() as db:
                prior = db.execute(
                    "SELECT data_json FROM activations WHERE id=?", (id,)
                ).fetchone()
                if prior:
                    saved = json.loads(prior[0])
                    require(saved["digest"] == digest(args), "IDEMPOTENCY_CONFLICT")
                    return saved
                aggregate = self.service._load(db, ctx, args["task_id"])
                project = aggregate.get("project")
                require(
                    project and project["project_id"] == args["project_id"], "NOT_FOUND"
                )
                saved = {
                    "activation_id": id,
                    "digest": digest(args),
                    "state": "binding",
                    "mode": "switch",
                    "owner_id": ctx.principal_id,
                    "project_id": project["project_id"],
                    "project": project,
                    "task_id": args["task_id"],
                    "host_request": {
                        "activation_id": id,
                        "launch_receipt": ctx.call_id,
                        "workspace_ref": aggregate["binding"]["workspace_ref"],
                        "existing_session_ref": aggregate["binding"]["session_id"],
                        "source_command_id": args["command_id"],
                    },
                }
                db.execute(
                    "INSERT INTO activations VALUES (?,?,?)",
                    (id, ctx.principal_id, canonical(saved)),
                )
            return saved
        raise ValueError(name)

    async def tick(self):
        with self.service.storage.lock:
            rows = self.service.storage.db.execute(
                "SELECT data_json FROM activations"
            ).fetchall()
        for row in rows:
            a = json.loads(row[0])
            if a["state"] not in {"allocating", "binding"}:
                continue
            project = a["project"]
            directory = Path(project["workspace_display_path"])
            try:
                if a.get("mode") == "switch":
                    receipt = await self.host.activate(a["host_request"])
                    a.update(state=receipt["state"], binding=receipt)
                    self._save(a)
                    continue
                if a["state"] == "allocating":
                    directory.mkdir(mode=0o750, exist_ok=True)
                    marker = directory / ".galatea-project"
                    if marker.exists():
                        require(
                            marker.read_text() == project["project_id"],
                            "FORBIDDEN",
                            "Existing directory belongs to another project",
                        )
                    else:
                        require(
                            not any(directory.iterdir()),
                            "FORBIDDEN",
                            "Directory is not empty",
                        )
                        marker.write_text(project["project_id"])
                    self.service.workspaces[project["workspace_ref"]] = directory
                    a["state"] = "binding"
                    self._save(a)
                receipt = await self.host.activate(
                    {
                        "activation_id": a["activation_id"],
                        "launch_receipt": a["launch_receipt"],
                        "workspace_ref": project["workspace_ref"],
                        "parent_workspace_ref": a["parent_workspace_ref"],
                        "workspace_relative_path": a["workspace_relative_path"],
                        "source_command_id": a["command_id"],
                        "create_session": True,
                    }
                )
                if receipt["state"] != "ready":
                    a["state"] = receipt["state"]
                    self._save(a)
                    continue
                from .service import Context

                ctx = Context(
                    a["owner_id"],
                    a["context"]["host_id"],
                    receipt["session_id"],
                    receipt["thread_id"],
                    project["workspace_ref"],
                    role="app",
                    confirmed=True,
                    interaction_id=a["activation_id"],
                    call_id=a["activation_id"],
                )
                result = self.service.create(
                    ctx,
                    command_id=a["command_id"] + ":task",
                    goal=a["goal"],
                    inputs=[],
                    constraints=a["constraints"],
                    start=True,
                )
                task_id = result["task_id"]
                project.update(
                    initialization_state="ready",
                    task_sessions=[
                        {
                            "id": task_id,
                            "title": a["goal"][:80],
                            "status": "planning",
                            "session_id": receipt["session_id"],
                        }
                    ],
                )
                with self.service.storage.transaction() as db:
                    aggregate = self.service._load(db, ctx, task_id)
                    aggregate["project"] = project
                    # Inputs retain their original workspace and are read-only references.
                    for item in a["inputs"]:
                        id = uid("input")
                        aggregate["inputs"][id] = {
                            "id": id,
                            **item,
                            "kind": "directory",
                            "sha256": None,
                        }
                    self.service._save(db, aggregate, increment=False)
                a.update(state="ready", task_id=task_id, binding=receipt)
                self._save(a)
            except Exception as exc:
                a["error"] = getattr(exc, "code", "HOST_UNAVAILABLE")
                self._save(a)

    def _save(self, a):
        with self.service.storage.transaction() as db:
            db.execute(
                "UPDATE activations SET data_json=? WHERE id=?",
                (canonical(a), a["activation_id"]),
            )
            db.execute(
                "UPDATE projects SET data_json=? WHERE id=?",
                (canonical(a["project"]), a["project_id"]),
            )
