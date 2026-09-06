#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from llm_lora_playground.config import load_training_config, validate_training_config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--variant", choices=("base", "prompt-only", "lora"), default="base")
    parser.add_argument("--split", choices=("train", "validation", "test"), default="validation")
    parser.add_argument("--build-split", action="store_true")
    parser.add_argument("--freeze-candidate", action="store_true")
    parser.add_argument("--candidate-run")
    parser.add_argument("--test-once", action="store_true")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    config = load_training_config(args.config)
    errors = validate_training_config(config)
    if errors:
        print(json.dumps({"status": "blocked", "errors": errors}, sort_keys=True))
        return 2
    if args.run:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "reason": (
                        "evaluation evidence must run through the immutable Release, "
                        "Galatea authorization, and fixed Ray Driver"
                    ),
                    "execution_backend": "ray_job",
                    "will_create_mlflow_run": False,
                },
                sort_keys=True,
            )
        )
        return 2
    if args.split == "test" and not args.test_once:
        print(json.dumps({"status": "blocked", "reason": "test requires --test-once"}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "status": "planned",
                "variant": args.variant,
                "split": args.split,
                "run": False,
                "will_create_mlflow_run": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
