"""Ray Job 可调用的单任务训练辅助函数。"""

from __future__ import annotations

from typing import Any

from ray_kaggle_house_prices.config import ProjectConfig
from ray_kaggle_house_prices.data import PreparedDataset
from ray_kaggle_house_prices.input_pipeline import OofResult, fit_oof_family


def train_family_worker(
    family: str,
    parameters: dict[str, Any],
    dataset: PreparedDataset,
    config: ProjectConfig,
) -> OofResult:
    """为 Ray 远程任务提供确定性的模型族训练入口。"""

    return fit_oof_family(family, parameters, dataset, config)
