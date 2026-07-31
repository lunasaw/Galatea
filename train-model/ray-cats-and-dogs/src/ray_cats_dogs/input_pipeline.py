"""PyTorch image batches backed by Ray Dataset shards or local manifests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pandas as pd


def _image_transform(
    image_size: tuple[int, int],
    *,
    training: bool,
    augmentation: bool,
) -> Any:
    from torchvision import transforms

    operations: list[Any] = [transforms.Resize(image_size)]
    if training and augmentation:
        operations.extend(
            [
                transforms.RandomHorizontalFlip(),
                transforms.RandomRotation(20),
                transforms.RandomAffine(degrees=0, translate=(0.1, 0.1), fill=0),
            ]
        )
    operations.append(transforms.ToTensor())
    return transforms.Compose(operations)


def _decode_batch(frame: pd.DataFrame, transform: Any) -> tuple[Any, Any]:
    import torch
    from PIL import Image

    images = []
    for path in frame["path"]:
        with Image.open(path) as image:
            images.append(transform(image.convert("RGB")))
    labels = torch.as_tensor(frame["label"].to_numpy(), dtype=torch.long)
    return torch.stack(images), labels


class _RayImageBatches:
    def __init__(
        self,
        dataset_shard: Any,
        *,
        image_size: tuple[int, int],
        batch_size: int,
        training: bool,
        augmentation: bool,
        seed: int,
    ) -> None:
        self.dataset_shard = dataset_shard
        self.image_size = image_size
        self.batch_size = batch_size
        self.training = training
        self.augmentation = augmentation
        self.seed = seed

    def __iter__(self) -> Iterator[tuple[Any, Any]]:
        import torch

        torch.manual_seed(self.seed)
        transform = _image_transform(
            self.image_size,
            training=self.training,
            augmentation=self.augmentation,
        )
        kwargs: dict[str, Any] = {
            "batch_size": self.batch_size,
            "batch_format": "pandas",
            "drop_last": False,
        }
        if self.training:
            kwargs.update(
                {
                    "local_shuffle_buffer_size": max(self.batch_size * 8, 256),
                    "local_shuffle_seed": self.seed,
                }
            )
        for frame in self.dataset_shard.iter_batches(**kwargs):
            yield _decode_batch(frame, transform)


def make_worker_dataset(
    dataset_shard: Any,
    *,
    image_size: tuple[int, int],
    batch_size: int,
    training: bool,
    augmentation: bool,
    seed: int,
) -> Any:
    return _RayImageBatches(
        dataset_shard,
        image_size=image_size,
        batch_size=batch_size,
        training=training,
        augmentation=augmentation,
        seed=seed,
    )


def make_local_dataset(
    frame: pd.DataFrame,
    pet_images_root: Path,
    *,
    image_size: tuple[int, int],
    batch_size: int,
) -> Any:
    from torch.utils.data import DataLoader, Dataset
    from PIL import Image

    transform = _image_transform(
        image_size,
        training=False,
        augmentation=False,
    )

    class ManifestDataset(Dataset):
        def __len__(self) -> int:
            return len(frame)

        def __getitem__(self, index: int) -> tuple[Any, int]:
            record = frame.iloc[index]
            path = pet_images_root / str(record["relative_path"])
            with Image.open(path) as image:
                tensor = transform(image.convert("RGB"))
            return tensor, int(record["label"])

    return DataLoader(
        ManifestDataset(),
        batch_size=batch_size,
        shuffle=False,
        num_workers=0,
        pin_memory=False,
    )
