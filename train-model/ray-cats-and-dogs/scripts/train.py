#!/usr/bin/env python3
"""Formal command-line entry point for Ray cats-and-dogs training."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_cats_dogs.config import load_config  # noqa: E402
from ray_cats_dogs.train import config_plan, read_only_plan, run_training  # noqa: E402


def _handle_termination(signum, frame) -> None:
    raise KeyboardInterrupt(f"received {signal.Signals(signum).name}")


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_termination)
    parser = argparse.ArgumentParser(
        description="Train cats-vs-dogs with Ray Train and MLflow."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "baseline.yaml",
        help="YAML workload configuration",
    )
    parser.add_argument(
        "--set",
        dest="overrides",
        action="append",
        default=[],
        metavar="PATH=VALUE",
        help="override an existing YAML key; may be repeated",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check-config",
        action="store_true",
        help="validate and print configuration without external API or data access",
    )
    mode.add_argument(
        "--plan",
        action="store_true",
        help="validate MLflow and dataset identity without starting training",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="start a new attempt even if the same idempotency key already succeeded",
    )
    arguments = parser.parse_args()
    config = load_config(arguments.config, tuple(arguments.overrides))
    if arguments.check_config:
        result = config_plan(config)
    elif arguments.plan:
        result = read_only_plan(config)
    else:
        result = run_training(config, force=arguments.force)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
