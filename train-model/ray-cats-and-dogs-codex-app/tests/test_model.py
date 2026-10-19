from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import torch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_cats_dogs.models import build_model  # noqa: E402


class ModelTest(unittest.TestCase):
    def test_custom_cnn_accepts_raw_rgb_batches(self) -> None:
        model = build_model(
            {
                "family": "custom_cnn",
                "dense_units": 16,
                "dropout": 0.0,
                "augmentation": False,
                "pretrained_weights": None,
            },
            {"optimizer": "adam", "learning_rate": 0.001},
            (64, 64),
            seed=42,
        )

        outputs = model(torch.zeros((2, 3, 64, 64), dtype=torch.float32))
        probabilities = torch.softmax(outputs, dim=1)
        self.assertEqual((2, 2), tuple(outputs.shape))
        self.assertTrue(torch.allclose(probabilities.sum(dim=1), torch.ones(2)))
        self.assertGreater(model.count_params(), 0)

    def test_model_has_a_stable_serialization_identity(self) -> None:
        model = build_model(
            {
                "family": "custom_cnn",
                "dense_units": 8,
                "dropout": 0.0,
                "augmentation": False,
                "pretrained_weights": None,
            },
            {"optimizer": "adam", "learning_rate": 0.001},
            (32, 32),
            seed=7,
        ).eval()
        inputs = torch.zeros((1, 3, 32, 32), dtype=torch.float32)
        with tempfile.NamedTemporaryFile(suffix=".pt") as checkpoint:
            torch.save(model, checkpoint.name)
            restored = torch.load(checkpoint.name, map_location="cpu", weights_only=False)
        self.assertEqual(type(model), type(restored))
        with torch.no_grad():
            self.assertTrue(torch.allclose(model(inputs), restored(inputs)))


if __name__ == "__main__":
    unittest.main()
