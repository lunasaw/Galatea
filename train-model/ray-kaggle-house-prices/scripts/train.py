#!/usr/bin/env python3
"""House Prices 正式训练、配置检查和只读计划入口。"""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_kaggle_house_prices.config import load_config  # noqa: E402


def _handle_termination(signum: int, frame: object) -> None:
    raise KeyboardInterrupt(f"收到终止信号: {signal.Signals(signum).name}")


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_termination)
    parser = argparse.ArgumentParser(description="训练 Kaggle House Prices 表格回归模型")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "configs" / "baseline.yaml")
    parser.add_argument("--set", dest="overrides", action="append", default=[], metavar="PATH=VALUE")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-config", action="store_true", help="只验证配置，不访问数据或服务")
    mode.add_argument("--plan", action="store_true", help="运行只读数据、完整性和 MLflow 预检")
    parser.add_argument("--force", action="store_true", help="忽略相同身份的已完成运行")
    arguments = parser.parse_args()
    config = load_config(arguments.config, tuple(arguments.overrides))
    from ray_kaggle_house_prices.train import config_plan, read_only_plan, run_training

    if arguments.check_config:
        result = config_plan(config)
    elif arguments.plan:
        result = read_only_plan(config)
    else:
        result = run_training(config, force=arguments.force)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
