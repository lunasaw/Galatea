#!/usr/bin/env python3
"""Read-only planner and fixed Galatea Ray Driver boundary."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wechat_persona.runtime import load_project_config, validate_project_config
from wechat_persona.training import build_training_plan, run_training


def _runtime_from_environment() -> dict[str, object]:
    promotable = os.environ.get("GALATEA_PROMOTABLE", "").casefold() == "true"
    return {
        "execution_mode": os.environ.get("GALATEA_EXECUTION_MODE"),
        "release_id": os.environ.get("GALATEA_RELEASE_ID"),
        "readiness_digest": os.environ.get("GALATEA_READINESS_DIGEST"),
        "execution_identity": os.environ.get("GALATEA_EXECUTION_IDENTITY"),
        "attempt_id": os.environ.get("GALATEA_ATTEMPT_ID"),
        "ray_submission_id": os.environ.get("RAY_JOB_SUBMISSION_ID"),
        "ray_job_id": os.environ.get("RAY_JOB_ID"),
        "galatea_project": os.environ.get("GALATEA_PROJECT"),
        "role": os.environ.get("GALATEA_RUN_ROLE"),
        "promotable": promotable,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-config", action="store_true")
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--run", action="store_true")
    args = parser.parse_args()
    config = load_project_config(args.config)
    if args.check_config:
        errors = validate_project_config(config)
        payload = {"status": "ok" if not errors else "blocked", "errors": errors, "will_create_mlflow_run": False}
    elif args.plan:
        payload = build_training_plan(config)
    else:
        payload = run_training(config, runtime=_runtime_from_environment())
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload.get("status") in {"ok", "planned"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
