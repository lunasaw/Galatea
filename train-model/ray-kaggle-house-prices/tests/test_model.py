"""模型管道的最小训练回归测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_kaggle_house_prices.config import load_config  # noqa: E402
from ray_kaggle_house_prices.data import prepare_dataset, split_features_target  # noqa: E402
from ray_kaggle_house_prices.input_pipeline import fit_oof_family  # noqa: E402


class HousePricesModelTest(unittest.TestCase):
    def test_small_elastic_net_oof(self) -> None:
        config = load_config(PROJECT_ROOT / "configs" / "smoke.yaml")
        dataset = prepare_dataset(
            config.data,
            config.validation,
            config.run.seed,
            include_holdout=False,
            include_inference=False,
        )
        result = fit_oof_family(
            "elastic_net",
            {"alpha": 0.001, "l1_ratio": 0.5, "max_iter": 200},
            dataset,
            config,
        )
        self.assertEqual(result.predictions_log.shape, (len(dataset.development),))
        self.assertTrue(np.isfinite(result.predictions_log).all())
        self.assertGreater(result.mean_rmsle, 0.0)


if __name__ == "__main__":
    unittest.main()
