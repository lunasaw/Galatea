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
    unit.add_argument("--environment-file", type=Path, required=True)
    unit.add_argument("--conflicts")
    serve = subparsers.add_parser("serve")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--dev", action="store_true", help="run repository source without deployment release artifacts")
    args = parser.parse_args(argv)
    config, raw = load_config(args.config)
    if args.command == "render-unit":
        from .deployment import render_unit
        print(render_unit(config, executable=args.executable, user=args.user,
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
    if not args.dev:
        check(args.config)
    from .app import build_host
    listen_host = args.host if args.host is not None else config.console_host
    listen_port = args.port if args.port is not None else config.console_port
    import re
    if not re.fullmatch(r'[A-Za-z0-9_.:-]+', listen_host) or not 1 <= listen_port <= 65535:
        raise ValueError('invalid Console listen address or port')
    if args.dev and not config.raw['galatea'].get('platform'):
        from .local import build_local_service
        service, principal = build_local_service(config)
    else:
        service, principal = build_service(config)
    import uvicorn
    try:
        host, application = build_host(config, service, principal, development=args.dev)
        uvicorn.run(application, host=listen_host, port=listen_port, log_level="info", lifespan="on",
                    limit_concurrency=64, timeout_keep_alive=10, ws="none",
                    timeout_graceful_shutdown=10 if args.dev else None)
    finally:
        store = getattr(service, "store", None)
        if store:
            store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
