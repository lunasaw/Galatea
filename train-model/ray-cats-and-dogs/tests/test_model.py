from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

import numpy as np


os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_cats_dogs.models import build_model  # noqa: E402


class ModelTest(unittest.TestCase):
    def test_custom_cnn_accepts_raw_rgb_batches(self) -> None:
        import tensorflow as tf

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

        outputs = model(np.zeros((2, 64, 64, 3), dtype="float32"), training=False)
        self.assertEqual((2, 2), tuple(outputs.shape))
        self.assertTrue(np.allclose(outputs.numpy().sum(axis=1), 1.0))
        tf.keras.backend.clear_session()


if __name__ == "__main__":
    unittest.main()
