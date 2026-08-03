from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_cats_dogs.config import load_config  # noqa: E402


class ConfigTest(unittest.TestCase):
    def load(self, name: str, *overrides: str):
        with patch.dict(os.environ, {}, clear=True):
            return load_config(PROJECT_ROOT / "configs" / name, overrides)

    def test_smoke_inherits_baseline_and_accepts_typed_overrides(self) -> None:
        config = self.load(
            "smoke.yaml",
            "training.epochs=2",
            "ray.address=local",
        )

        self.assertEqual("smoke", config.run.role)
        self.assertEqual(2, config.training.epochs)
        self.assertIsNone(config.ray.address)
        self.assertEqual("val_accuracy", config.training.objective_metric)
        self.assertEqual("max", config.training.objective_mode)
        self.assertEqual("bf16", config.training.mixed_precision)
        self.assertEqual(16, config.ray.data_decode_workers)
        self.assertTrue(config.ray.data_cache_decoded)

    def test_distributed_config_declares_worker_resources(self) -> None:
        config = self.load(
            "distributed.yaml",
            "ray.storage_path=s3://training/ray-results",
        )

        self.assertEqual(2, config.ray.num_workers)
        self.assertEqual("SPREAD", config.ray.placement_strategy)
        self.assertEqual("s3://training/ray-results", config.ray.storage_path)
        self.assertGreater(config.ray.memory_per_worker_bytes, 0)

    def test_task_timeline_recording_is_enabled_by_default(self) -> None:
        config = self.load("baseline.yaml")

        self.assertTrue(config.ray.record_task_timeline)

    def test_ray_data_parallelism_is_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least data_decode_workers"):
            self.load(
                "baseline.yaml",
                "ray.data_num_blocks=4",
                "ray.data_decode_workers=8",
            )

        with self.assertRaisesRegex(ValueError, "mixed_precision"):
            self.load("baseline.yaml", "training.mixed_precision=fp8")

    def test_only_champion_may_evaluate_test_holdout(self) -> None:
        with self.assertRaisesRegex(ValueError, "Only a champion"):
            self.load("baseline.yaml", "evaluation.evaluate_test=true")

    def test_test_metric_cannot_be_an_optimization_objective(self) -> None:
        with self.assertRaisesRegex(ValueError, "objective_metric"):
            self.load(
                "baseline.yaml",
                "training.objective_metric=test_accuracy",
            )

    def test_unknown_override_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.load("baseline.yaml", "training.typo=1")

    def test_tracking_uri_can_be_set_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {"MLFLOW_TRACKING_URI": "https://tracking.example.test"},
            clear=True,
        ):
            config = load_config(PROJECT_ROOT / "configs" / "smoke.yaml")

        self.assertEqual(
            "https://tracking.example.test",
            config.mlflow.tracking_uri,
        )


if __name__ == "__main__":
    unittest.main()
