#!/usr/bin/env python3
"""手写数字 Ray Train 正式命令行入口。"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_handwritten_digits.config import load_config  # noqa: E402


def _handle_termination(signum, frame) -> None:
    raise KeyboardInterrupt(f"received {signal.Signals(signum).name}")


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_termination)
    parser = argparse.ArgumentParser(
        description="使用 Ray Train 和 MLflow 训练十分类手写数字模型。"
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
        from ray_handwritten_digits.train import config_plan

        result = config_plan(config)
    elif arguments.plan:
        from ray_handwritten_digits.train import read_only_plan

        result = read_only_plan(config)
    else:
        import mlflow

        mlflow.set_tracking_uri(config.mlflow.tracking_uri)
        mlflow.set_experiment(config.mlflow.experiment_name)
        mlflow.autolog(log_models=False, silent=True)

        from ray_handwritten_digits.train import run_training

        result = run_training(config, force=arguments.force)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
