from __future__ import annotations

import argparse
import json
from pathlib import Path

from .catalog import Catalog
from .config import AgentConfig
from .stage0 import GateError
from .release import verify_release, load_json, measure


def load_config(path: Path):
    raw = load_json(path)
    return AgentConfig.from_dict(raw, path.parent), raw


def check(config_path: Path):
    config, raw = load_config(config_path)
    manifest = verify_release(config)
    return {"status": "accepted-release", "catalog_digest": manifest["dynamic_tool_catalog_sha256"],
            "runtime_version": manifest["codex_version"], "training_started": False}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="codex-agent")
    parser.add_argument("--config", type=Path, required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check")
    subparsers.add_parser("inspect-runtime")
    preflight_parser = subparsers.add_parser("preflight")
    preflight_parser.add_argument("--artifact-index", action="store_true")
    unit = subparsers.add_parser("render-unit")
    unit.add_argument("--executable", type=Path, required=True)
    unit.add_argument("--user", required=True)
    unit.add_argument("--token-file", type=Path, required=True)
    unit.add_argument("--environment-file", type=Path, required=True)
    unit.add_argument("--conflicts")
    serve = subparsers.add_parser("serve")
    serve.add_argument("--token-file", type=Path, required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=18880)
    args = parser.parse_args(argv)
    config, raw = load_config(args.config)
    if args.command == "render-unit":
        from .deployment import render_unit
        print(render_unit(config, executable=args.executable, user=args.user, token_file=args.token_file,
                          environment_file=args.environment_file, conflicts=args.conflicts), end="")
        return 0
    if args.command == "check":
        print(json.dumps(check(args.config), indent=2))
        return 0
    if args.command == "inspect-runtime":
        import asyncio
        from .process import ManagedProcess
        from .catalog import digest
        manifest = measure(config)
        async def inspect_runtime():
            process = ManagedProcess(config)
            try:
                client = await process.start()
                observed = await client.read_config(str(config.workspace_dir))
                manifest["effective_config_sha256"] = digest(observed)
            finally:
                await process.stop()
        asyncio.run(inspect_runtime())
        print(json.dumps(manifest, indent=2))
        return 0
    from .platform import build_service, preflight
    if args.command == "preflight":
        # Read-only evidence gathering precedes release acceptance and never takes
        # the production Service's writer lock.
        result = preflight(config, artifact_index=args.artifact_index)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "platform-reachable" else 2
    check(args.config)
    from .app import build_host
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise GateError("Console must bind loopback")
    if args.token_file.is_symlink() or args.token_file.stat().st_mode & 0o077:
        raise GateError("Console token file must be protected (0600)")
    token = args.token_file.read_text(encoding="utf-8").strip()
    if len(token) < 16:
        raise GateError("strong Console token required")
    origins = set(raw.get("console", {}).get("allowed_origins", []))
    if not origins:
        raise GateError("Console origins must be configured")
    service, principal = build_service(config)
    import uvicorn
    try:
        host, application = build_host(config, service, principal, console_token=token, allowed_origins=origins)
        uvicorn.run(application, host=args.host, port=args.port, log_level="info", lifespan="on",
                    limit_concurrency=64, timeout_keep_alive=10, ws="none")
    finally:
        store = getattr(service, "store", None)
        if store:
            store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
