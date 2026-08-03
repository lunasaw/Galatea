from __future__ import annotations

import sys
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_cats_dogs.worker import (  # noqa: E402
    _augment_batch,
    _global_epoch_metrics,
    _is_better,
    _prepare_images,
    _run_epoch,
)


class ObjectiveTest(unittest.TestCase):
    def test_objective_direction_is_explicit(self) -> None:
        self.assertTrue(_is_better(0.9, 0.8, "max"))
        self.assertFalse(_is_better(0.7, 0.8, "max"))
        self.assertTrue(_is_better(0.2, 0.3, "min"))
        self.assertFalse(_is_better(0.4, 0.3, "min"))


class EpochMetricsTest(unittest.TestCase):
    def test_uint8_images_are_normalized_and_augmented_as_a_batch(self) -> None:
        import torch

        images = torch.full((4, 3, 12, 10), 255, dtype=torch.uint8)
        normalized = _prepare_images(
            images,
            torch.device("cpu"),
            augmentation=False,
        )
        self.assertEqual(torch.float32, normalized.dtype)
        self.assertTrue(torch.allclose(normalized, torch.ones_like(normalized)))

        torch.manual_seed(42)
        augmented = _augment_batch(normalized)
        self.assertEqual(normalized.shape, augmented.shape)
        self.assertGreaterEqual(float(augmented.min()), 0.0)
        self.assertLessEqual(float(augmented.max()), 1.0)

    def test_global_epoch_metrics_include_binary_and_macro_scores(self) -> None:
        import torch

        metrics = _global_epoch_metrics(
            loss_sum=20.0,
            confusion=torch.tensor(
                [[8.0, 2.0], [1.0, 9.0]], dtype=torch.float64
            ),
            device=torch.device("cpu"),
        )

        self.assertAlmostEqual(0.85, metrics["accuracy"])
        self.assertAlmostEqual(9 / 11, metrics["precision"])
        self.assertAlmostEqual(0.9, metrics["recall"])
        self.assertAlmostEqual(8 / 9, metrics["cat_precision"])
        self.assertAlmostEqual(9 / 11, metrics["dog_precision"])
        self.assertIn("macro_f1", metrics)
        self.assertEqual(20.0, metrics["examples"])

    def test_run_epoch_renders_batch_progress(self) -> None:
        import torch

        model = torch.nn.Linear(2, 2)
        batches = [
            (
                torch.tensor([[1.0, 0.0], [0.0, 1.0]]),
                torch.tensor([0, 1]),
            ),
            (
                torch.tensor([[0.5, 0.5], [0.25, 0.75]]),
                torch.tensor([0, 1]),
            ),
        ]
        output = StringIO()
        with redirect_stderr(output):
            metrics = _run_epoch(
                model,
                batches,
                torch.nn.CrossEntropyLoss(),
                torch.device("cpu"),
                torch.optim.SGD(model.parameters(), lr=0.1),
                show_progress=True,
                total_batches=2,
                progress_description="epoch 1/1 train",
            )

        self.assertEqual(4.0, metrics["examples"])
        self.assertIn("data_wait_seconds", metrics)
        self.assertIn("epoch 1/1 train", output.getvalue())
        self.assertIn("2/2", output.getvalue())


if __name__ == "__main__":
    unittest.main()
