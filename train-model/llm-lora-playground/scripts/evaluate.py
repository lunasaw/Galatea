#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
        print({"status": "blocked", "errors": errors})
        return 2
    if args.split == "test" and not args.test_once:
        print({"status": "blocked", "reason": "test requires --test-once"})
        return 2
    print({"status": "planned", "variant": args.variant, "split": args.split, "run": args.run})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
