"""House Prices 项目的配置、数据、指标和防泄漏测试。"""

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
from ray_kaggle_house_prices.evaluate import rmsle, rmse_log_target  # noqa: E402
from ray_kaggle_house_prices.integrity import build_integrity_report  # noqa: E402
from ray_kaggle_house_prices.models import HouseFeatureEngineer, optimize_blend_weights  # noqa: E402


class HousePricesProjectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(PROJECT_ROOT / "configs" / "smoke.yaml")

    def test_config_and_dataset_contract(self) -> None:
        dataset = prepare_dataset(
            self.config.data,
            self.config.validation,
            self.config.run.seed,
            include_holdout=False,
            include_inference=False,
        )
        self.assertEqual(len(dataset.all_labeled), 1460)
        self.assertEqual(len(dataset.development), 1168)
        self.assertIsNone(dataset.holdout)
        self.assertIsNone(dataset.inference)
        self.assertEqual(dataset.fold_assignments["fold"].nunique(), 2)

    def test_target_and_id_never_become_features(self) -> None:
        dataset = prepare_dataset(
            self.config.data,
            self.config.validation,
            self.config.run.seed,
            include_holdout=False,
            include_inference=False,
        )
        features, target = split_features_target(dataset.development, self.config.data)
        self.assertNotIn("SalePrice", features.columns)
        self.assertNotIn("Id", features.columns)
        self.assertEqual(len(target), len(features))
        with self.assertRaises(ValueError):
            HouseFeatureEngineer().fit_transform(dataset.development[["Id", "SalePrice"]])

    def test_integrity_and_metric_semantics(self) -> None:
        dataset = prepare_dataset(
            self.config.data,
            self.config.validation,
            self.config.run.seed,
            include_holdout=False,
            include_inference=False,
        )
        report = build_integrity_report(self.config, dataset, include_holdout=False, include_inference=False)
        self.assertEqual(report["preprocessing"]["parity"]["status"], "passed")
        self.assertEqual(report["migration"]["contamination"]["status"], "passed")
        self.assertAlmostEqual(rmsle(np.array([100.0, 200.0]), np.array([100.0, 200.0])), 0.0)
        self.assertAlmostEqual(rmse_log_target(np.log1p([100.0]), np.log1p([100.0])), 0.0)

    def test_blend_weights_are_simplex(self) -> None:
        target = np.array([1.0, 2.0, 3.0])
        matrix = np.column_stack([target, target + 0.2, target - 0.2])
        weights = optimize_blend_weights(matrix, target, 0.005)
        self.assertTrue(np.isfinite(weights).all())
        self.assertTrue((weights >= 0).all())
        self.assertAlmostEqual(float(weights.sum()), 1.0, places=7)


if __name__ == "__main__":
    unittest.main()
