#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from llm_lora_playground.config import load_training_config, validate_training_config


def run_worker(config, runtime=None, resume_from=None):
    from train_lora import train
    return train(config, runtime=runtime, resume_from=resume_from)


def run_driver(config_path: Path, data: Path | None = None, resume_from: str | None = None):
    config = load_training_config(config_path)
    errors = validate_training_config(config)
    if errors:
        raise ValueError("blocked config: " + "; ".join(errors))
    return run_worker(config, resume_from=resume_from)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--resume-from")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.run:
        run_driver(args.config, args.data, args.resume_from)
    else:
        print({"status": "planned", "config": str(args.config), "run": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
