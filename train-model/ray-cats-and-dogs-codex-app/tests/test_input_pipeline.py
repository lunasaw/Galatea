from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_cats_dogs.input_pipeline import (  # noqa: E402
    decode_image_batch,
    make_worker_dataset,
)


class FakeShard:
    def __init__(self, batch: dict[str, np.ndarray]) -> None:
        self.batch = batch
        self.iteration_options = None

    def iter_torch_batches(self, **kwargs):
        import torch

        self.iteration_options = kwargs
        yield {
            "image": torch.from_numpy(self.batch["image"]),
            "label": torch.from_numpy(self.batch["label"]),
        }


class InputPipelineTest(unittest.TestCase):
    def test_ray_decoder_returns_compact_nchw_tensor_batches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            for index, color in enumerate(((255, 0, 0), (0, 255, 0))):
                path = root / f"image-{index}.png"
                Image.new("RGB", (9, 7), color).save(path)
                paths.append(str(path))

            decoded = decode_image_batch(
                pd.DataFrame({"path": paths, "label": [0, 1]}),
                image_size=(12, 10),
            )

        self.assertEqual((2, 3, 12, 10), decoded["image"].shape)
        self.assertEqual(np.uint8, decoded["image"].dtype)
        self.assertEqual(np.int64, decoded["label"].dtype)
        self.assertEqual([0, 1], decoded["label"].tolist())

    def test_worker_iterator_uses_prefetched_pinned_torch_batches(self) -> None:
        shard = FakeShard(
            {
                "image": np.zeros((2, 3, 12, 10), dtype=np.uint8),
                "label": np.array([0, 1], dtype=np.int64),
            }
        )
        dataset = make_worker_dataset(
            shard,
            batch_size=128,
            prefetch_batches=4,
        )

        with patch("torch.cuda.is_available", return_value=False):
            images, labels = next(iter(dataset))

        self.assertEqual((2, 3, 12, 10), tuple(images.shape))
        self.assertEqual([0, 1], labels.tolist())
        self.assertEqual(128, shard.iteration_options["batch_size"])
        self.assertEqual(4, shard.iteration_options["prefetch_batches"])
        self.assertEqual("auto", shard.iteration_options["device"])
        self.assertFalse(shard.iteration_options["pin_memory"])


if __name__ == "__main__":
    unittest.main()
