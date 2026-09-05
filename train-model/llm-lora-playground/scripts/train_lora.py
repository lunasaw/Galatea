#!/usr/bin/env python3
"""Config-first training entrypoint; model execution is intentionally opt-in."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from llm_lora_playground.config import load_training_config, validate_training_config, canonical_training_config_digest
from llm_lora_playground.training import TrainingContractError, train as _train


def train(config, runtime=None, resume_from=None, data=None):
    return _train(config, runtime=runtime, resume_from=resume_from, data=data)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--resume-from")
    args = parser.parse_args()
    config = load_training_config(args.config)
    errors = validate_training_config(config)
    result = {"status": "ok" if not errors else "blocked", "errors": errors, "config_digest": canonical_training_config_digest(config), "will_create_mlflow_run": bool(args.run and not errors)}
    print(result)
    if errors:
        return 2
    if args.run:
        try:
            train(config, resume_from=args.resume_from, data=args.data)
        except (TrainingContractError, RuntimeError, FileNotFoundError) as exc:
            print({"status": "blocked", "reason": str(exc)})
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
