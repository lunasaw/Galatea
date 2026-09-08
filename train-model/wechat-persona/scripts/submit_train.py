#!/usr/bin/env python3
"""Read-only planner and sole MCP-authorized Ray Driver entrypoint."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona.binding import verify_execution_binding  # noqa: E402
from wechat_persona.driver import execute  # noqa: E402
from wechat_persona.runtime import load_project_config, validate_project_config  # noqa: E402
from wechat_persona.training import (  # noqa: E402
    build_training_plan,
    load_bound_config,
    run_training,
)


def _run_driver() -> dict[str, object]:
    import boto3
    import mlflow
    import ray

    public_key = (ROOT / "release" / "execution-public.pem").read_bytes()
    raw_binding = os.environ.get("GALATEA_EXECUTION_BINDING")
    signature = os.environ.get("GALATEA_EXECUTION_SIGNATURE")
    binding = verify_execution_binding(raw_binding, signature, public_key)
    ray.init(address="auto")

    def official(verified: dict[str, object], admission: object) -> dict[str, object]:
        config = load_bound_config(ROOT / str(verified["config_path"]), verified)
        mlflow.set_tracking_uri(str(verified["tracking_uri"]))
        client = mlflow.MlflowClient()
        s3 = boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT_URL"))
        return run_training(
            config,
            binding=verified,
            admission=admission,
            s3_client=s3,
            mlflow_client=client,
        )

    return execute(
        public_key,
        ray.get_runtime_context(),
        fit=official,
        raw_binding=raw_binding,
        signature=signature,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-config", action="store_true")
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.run:
        if args.config is not None:
            parser.error("--run consumes only the MCP-signed embedded config")
        payload = _run_driver()
    else:
        if args.config is None:
            parser.error("--check-config/--plan require --config")
        config = load_project_config(args.config)
        if args.check_config:
            errors = validate_project_config(config)
            payload = {
                "status": "ok" if not errors else "blocked",
                "errors": errors,
                "will_create_mlflow_run": False,
            }
        else:
            payload = build_training_plan(config)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload.get("status") in {"ok", "planned", "succeeded"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
