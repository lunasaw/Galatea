"""Read-only evidence viewer using the real AppBridge. No execution or user authority."""

import argparse
import json
from pathlib import Path
from .common import canonical
from .projections import Workbench
from .service import Context, TaskService


class Preview:
    def __init__(self, service, ctx, task_id, *, fixture=False):
        self.service, self.ctx, self.task_id, self.fixture = (
            service,
            ctx,
            task_id,
            fixture,
        )
        self.workbench = Workbench(service)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return

        async def response(status, value, mime="application/json"):
            data = value if isinstance(value, bytes) else canonical(value).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [
                        (b"content-type", mime.encode()),
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": data})

        if scope["path"] in {"/", "/preview/app"}:
            file = "preview-host.html" if scope["path"] == "/" else "workbench.html"
            return await response(
                200,
                (Path(__file__).parent / "static" / file).read_bytes(),
                "text/html; charset=utf-8",
            )
        if scope["path"] != "/preview/tool" or scope["method"] != "POST":
            return await response(404, {})
        # This loopback-only viewer has no authenticated mutation routes.
        chunks = []
        size = 0
        while True:
            event = await receive()
            chunks.append(event.get("body", b""))
            size += len(chunks[-1])
            if size > 16384:
                return await response(413, {})
            if not event.get("more_body"):
                break
        body = json.loads(b"".join(chunks))
        name = body["name"]
        args = body.get("arguments") or {}
        ctx = self.ctx
        task_id = args.get("task_id", self.task_id)
        try:
            if name == "get_workbench":
                value = self.workbench.view(ctx, task_id)
                value.update(
                    read_only=True,
                    fixture=self.fixture,
                    reference_project="ray-cats-and-dogs",
                )
            elif name == "list_projects":
                value = {
                    "items": [
                        {
                            **(v := self.workbench.view(ctx, t["id"]))["project"],
                            "task_sessions": [v["task_snapshot"]["task"]],
                        }
                        for t in self.service.list_tasks(ctx)["items"]
                    ]
                }
            elif name == "get_stage_details":
                value = self.workbench.stage_details(
                    ctx, task_id, args["stage_id"], cursor=args.get("cursor", 0)
                )
            elif name == "get_file_change":
                value = self.workbench.file_change(
                    ctx, task_id, args["change_id"], cursor=args.get("cursor", 0)
                )
            elif name == "get_artifact":
                value = self.service.artifact(ctx, task_id, args["artifact_id"])
            elif name == "get_training_proposal":
                v = self.service.get_context(ctx, self.task_id)
                value = {
                    "proposal": next(
                        p
                        for p in v["proposals"]
                        if p["proposal_id"] == args["proposal_id"]
                    )
                }
            else:
                return await response(
                    200,
                    {
                        "isError": True,
                        "content": [{"type": "text", "text": "只读证据展示不执行操作"}],
                    },
                )
            return await response(
                200,
                {
                    "content": [{"type": "text", "text": canonical(value)}],
                    "structuredContent": value,
                },
            )
        except Exception as exc:
            return await response(
                200, {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
            )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--state-dir", required=True)
    p.add_argument("--port", type=int, default=18887)
    a = p.parse_args()
    root = Path(a.state_dir)
    metadata = json.loads((root / "viewer.json").read_text())
    service = TaskService(root / "tasks.sqlite3", workspaces=metadata["workspaces"])
    ctx = Context(**metadata["context"])
    app = Preview(
        service, ctx, metadata["task_id"], fixture=metadata.get("fixture", False)
    )
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=a.port, lifespan="off")


if __name__ == "__main__":
    main()
