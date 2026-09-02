"""将手写数字图片转换为单通道 PyTorch NCHW 批次。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pandas as pd


def decode_image_batch(frame: pd.DataFrame, *, image_size: tuple[int, int]) -> dict[str, Any]:
    """读取图片，保持笔画的深色前景并返回 uint8 NCHW。"""
    import numpy as np
    from PIL import Image
    from torchvision import transforms

    transform = transforms.Compose(
        [
            transforms.Resize(image_size),
            transforms.Grayscale(num_output_channels=1),
            transforms.PILToTensor(),
        ]
    )
    images = []
    for path in frame["path"]:
        with Image.open(path) as image:
            images.append(transform(image.convert("L")).numpy())
    return {
        "image": np.stack(images).astype(np.uint8, copy=False),
        "label": frame["label"].to_numpy(dtype=np.int64, copy=True),
    }


class _RayImageBatches:
    def __init__(self, dataset_shard: Any, *, batch_size: int, prefetch_batches: int) -> None:
        self.dataset_shard = dataset_shard
        self.batch_size = batch_size
        self.prefetch_batches = prefetch_batches

    def __iter__(self) -> Iterator[tuple[Any, Any]]:
        import torch

        batches = self.dataset_shard.iter_torch_batches(
            batch_size=self.batch_size,
            dtypes={"image": torch.uint8, "label": torch.int64},
            device="auto",
            drop_last=False,
            prefetch_batches=self.prefetch_batches,
            pin_memory=torch.cuda.is_available(),
        )
        for batch in batches:
            images = batch["image"]
            labels = batch["label"]
            if isinstance(images, list):
                images = torch.cat(images)
            if isinstance(labels, list):
                labels = torch.cat(labels)
            yield images, labels


def make_worker_dataset(dataset_shard: Any, *, batch_size: int, prefetch_batches: int) -> Any:
    return _RayImageBatches(
        dataset_shard,
        batch_size=batch_size,
        prefetch_batches=prefetch_batches,
    )


def make_local_dataset(
    frame: pd.DataFrame,
    dataset_root: Path,
    *,
    image_size: tuple[int, int],
    batch_size: int,
) -> Any:
    from PIL import Image
    from torch.utils.data import DataLoader, Dataset
    from torchvision import transforms

    transform = transforms.Compose(
        [
            transforms.Resize(image_size),
            transforms.Grayscale(num_output_channels=1),
            transforms.ToTensor(),
        ]
    )

    class ManifestDataset(Dataset):
        def __len__(self) -> int:
            return len(frame)

        def __getitem__(self, index: int) -> tuple[Any, int]:
            record = frame.iloc[index]
            with Image.open(dataset_root / str(record["relative_path"])) as image:
                return transform(image.convert("L")), int(record["label"])

    return DataLoader(
        ManifestDataset(),
        batch_size=batch_size,
        shuffle=False,
        num_workers=0,
        pin_memory=False,
    )
