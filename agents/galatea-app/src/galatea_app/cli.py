"""Explicit configuration, loopback service, contracts and consistent backups."""

import argparse
import json
import os
from pathlib import Path
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from .api import AppAPI
from .service import TaskService
from .trust import ProofVerifier
from .workers import HostClient, Worker
from .projects import Projects


def secret(path):
    path = Path(path)
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError("Credential files require mode 0600")
    return path.read_text().strip()


def build(config):
    if config.get("schema_version") != "galatea-app-config/v1":
        raise ValueError("Invalid service configuration")
    keys = {
        k: {
            **v,
            "public_key": load_pem_public_key(Path(v["public_key_file"]).read_bytes()),
        }
        for k, v in config["trusted_hosts"].items()
    }
    credentials = {
        k: {**v, "token": secret(v["credential_file"])}
        for k, v in config["credentials"].items()
    }
    governance = {}
    if config.get("project_governance"):
        from .integrations.project_governance import ProjectGovernance

        governance["project_governance"] = ProjectGovernance(
            **config["project_governance"]
        )
    if config.get("governance_mcp"):
        import time
        from .common import timestamp
        from .integrations.governance_mcp import GovernanceMCP, MCPTransport

        settings = dict(config["governance_mcp"])
        grants = settings.pop("grants", [])
        transport = MCPTransport(
            settings.pop("url"), secret(settings.pop("credential_file"))
        )

        def authorized(proposal):
            return any(
                g.get("immutable_inputs") == proposal["immutable_inputs"]
                and timestamp(g["expires_at"]) > time.time()
                and all(
                    proposal["hard_limits"][k] <= g["hard_limits"][k]
                    for k in ("attempts", "retries", "wall_seconds", "memory_bytes")
                )
                for g in grants
            )

        governance["governance_mcp"] = GovernanceMCP(
            transport, authorization_check=authorized, **settings
        )
    service = TaskService(
        config["database_path"],
        workspaces=config["workspaces"],
        governance=governance,
        capabilities=config.get("capabilities"),
    )
    host = HostClient(config["host"]["url"], secret(config["host"]["credential_file"]))
    projects = Projects(service, host, workspace_ref=config["project_workspace_ref"])
    worker = Worker(
        service,
        host,
        projects=projects,
        interval=config.get("worker_interval_seconds", 5),
    )
    return AppAPI(
        service,
        verifier=ProofVerifier(keys, config["audience"]),
        credentials=credentials,
        worker=worker,
        projects=projects,
        example=config.get("example"),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18886)
    parser.add_argument("--backup")
    args = parser.parse_args()
    config = json.loads(os.path.expandvars(Path(args.config).read_text()))
    app = build(config)
    if args.backup:
        app.service.storage.backup(args.backup)
        app.service.close()
        return
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
