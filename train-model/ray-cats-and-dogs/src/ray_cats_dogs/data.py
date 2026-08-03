"""Read-only source validation and deterministic dataset manifests."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from PIL import Image, UnidentifiedImageError

from ray_cats_dogs.config import DatasetSettings, ProjectConfig


CLASS_NAMES = ("Cat", "Dog")
CLASS_DIRECTORIES = (("Cat", 0), ("Dog", 1))
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


@dataclass(frozen=True)
class PreparedDataset:
    manifest: pd.DataFrame
    invalid_files: tuple[dict[str, str], ...]
    dataset_version: str
    content_digest: str
    split_digest: str
    source_uri: str
    manifest_path: Path
    profile: dict[str, Any]

    def split_frame(self, split_name: str) -> pd.DataFrame:
        return self.manifest.loc[self.manifest["split"] == split_name].copy()


def _file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _split_names(count: int, config: DatasetSettings) -> tuple[str, ...]:
    train_count = int(config.train_fraction * count)
    holdout_count = count - train_count
    validation_share = config.validation_fraction / (
        config.validation_fraction + config.test_fraction
    )
    validation_count = int(round(holdout_count * validation_share))
    test_count = holdout_count - validation_count
    if min(train_count, validation_count, test_count) < 1:
        raise ValueError(
            "Each class needs enough valid images to populate train, validation, and test"
        )
    return (
        *("training" for _ in range(train_count)),
        *("validation" for _ in range(validation_count)),
        *("test" for _ in range(test_count)),
    )


def _validated_class_records(
    pet_images_root: Path,
    class_name: str,
    label: int,
    config: DatasetSettings,
    seed: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
    source_dir = pet_images_root / class_name
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Missing dataset class directory: {source_dir}")
    candidates = [
        path
        for path in sorted(source_dir.iterdir())
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    ]
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    for path in candidates:
        relative_path = path.relative_to(pet_images_root).as_posix()
        try:
            if path.stat().st_size == 0:
                raise OSError("empty image")
            with Image.open(path) as image:
                image.verify()
            valid.append(
                {
                    "relative_path": relative_path,
                    "class_name": class_name,
                    "label": label,
                    "bytes": path.stat().st_size,
                    "sha256": _file_sha256(path),
                }
            )
        except (OSError, UnidentifiedImageError) as error:
            invalid.append(
                {
                    "relative_path": relative_path,
                    "error_type": type(error).__name__,
                }
            )

    shuffled = list(valid)
    random.Random(seed + label).shuffle(shuffled)
    split_names = _split_names(len(shuffled), config)
    for record, split_name in zip(shuffled, split_names, strict=True):
        record["split"] = split_name
    return shuffled, invalid, len(candidates)


def _digest_manifest(manifest: pd.DataFrame) -> tuple[str, str]:
    content = hashlib.sha256()
    split = hashlib.sha256()
    for record in manifest.sort_values("relative_path").to_dict(orient="records"):
        content.update(
            (
                f"{record['relative_path']}|{record['bytes']}|{record['sha256']}\n"
            ).encode()
        )
        split.update(
            f"{record['relative_path']}|{record['label']}|{record['split']}\n".encode()
        )
    return content.hexdigest(), split.hexdigest()


def _persist_manifest(
    manifest: pd.DataFrame,
    cache_dir: Path,
    content_digest: str,
    split_digest: str,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"manifest-{content_digest[:16]}-{split_digest[:16]}.csv"
    serialized = manifest.to_csv(index=False, lineterminator="\n")
    try:
        with path.open("x", encoding="utf-8", newline="") as file_handle:
            file_handle.write(serialized)
    except FileExistsError:
        if path.read_text(encoding="utf-8") != serialized:
            raise RuntimeError(f"Cached manifest content mismatch: {path}")
    return path


def prepare_dataset(config: DatasetSettings, seed: int) -> PreparedDataset:
    """Validate source files without mutation and build a deterministic split."""

    pet_images_root = config.root / "PetImages"
    all_records: list[dict[str, Any]] = []
    invalid_files: list[dict[str, str]] = []
    source_counts: dict[str, int] = {}
    for class_name, label in CLASS_DIRECTORIES:
        records, invalid, source_count = _validated_class_records(
            pet_images_root, class_name, label, config, seed
        )
        all_records.extend(records)
        invalid_files.extend(invalid)
        source_counts[class_name] = source_count

    if config.expected_images_per_class is not None and any(
        count != config.expected_images_per_class for count in source_counts.values()
    ):
        details = ", ".join(
            f"{class_name}={count}" for class_name, count in source_counts.items()
        )
        raise RuntimeError(
            f"Expected {config.expected_images_per_class} source images per class; {details}"
        )

    manifest = pd.DataFrame(all_records).sort_values(
        ["relative_path", "split"]
    ).reset_index(drop=True)
    expected_columns = {
        "relative_path",
        "class_name",
        "label",
        "bytes",
        "sha256",
        "split",
    }
    if set(manifest.columns) != expected_columns or manifest.empty:
        raise RuntimeError("Dataset manifest is empty or malformed")
    if manifest["relative_path"].duplicated().any():
        raise RuntimeError("Dataset manifest contains duplicate source images")
    if set(manifest["split"]) != {"training", "validation", "test"}:
        raise RuntimeError("Dataset manifest is missing a required split")
    if config.expected_valid_images is not None and len(manifest) != config.expected_valid_images:
        raise RuntimeError(
            f"Expected {config.expected_valid_images} valid images; found {len(manifest)}"
        )
    split_counts = manifest.groupby(["split", "class_name"]).size().unstack(fill_value=0)
    if split_counts.empty or (split_counts <= 0).any().any():
        raise RuntimeError("At least one dataset split has an empty class")

    content_digest, split_digest = _digest_manifest(manifest)
    dataset_version = f"sha256-{content_digest[:16]}"
    manifest_path = _persist_manifest(
        manifest,
        config.cache_dir,
        content_digest,
        split_digest,
    )
    source_uri = config.source_uri or pet_images_root.resolve().as_uri()
    profile = {
        "dataset_name": "microsoft-cats-vs-dogs",
        "dataset_version": dataset_version,
        "source_uri": source_uri,
        "content_sha256": content_digest,
        "split_sha256": split_digest,
        "preprocessing_version": config.preprocessing_version,
        "source_counts": source_counts,
        "valid_images": len(manifest),
        "invalid_images": invalid_files,
        "total_bytes": int(manifest["bytes"].sum()),
        "split_counts": {
            split_name: {
                class_name: int(count)
                for class_name, count in class_counts.items()
            }
            for split_name, class_counts in split_counts.to_dict(orient="index").items()
        },
    }
    return PreparedDataset(
        manifest=manifest,
        invalid_files=tuple(invalid_files),
        dataset_version=dataset_version,
        content_digest=content_digest,
        split_digest=split_digest,
        source_uri=source_uri,
        manifest_path=manifest_path,
        profile=json.loads(json.dumps(profile)),
    )


def build_ray_datasets(
    dataset: PreparedDataset,
    config: ProjectConfig,
) -> dict[str, Any]:
    """Build shuffled, parallel-decoded Ray Datasets for the Train workers."""

    import ray
    from ray.data import TaskPoolStrategy

    from ray_cats_dogs.input_pipeline import decode_image_batch

    datasets = {}
    pet_images_root = config.data.root / "PetImages"
    for split_name in ("training", "validation"):
        frame = dataset.split_frame(split_name)[["relative_path", "label"]]
        frame.insert(
            0,
            "path",
            frame["relative_path"].map(
                lambda relative: str(pet_images_root / str(relative))
            ),
        )
        requested_blocks = max(
            config.ray.data_decode_workers,
            (len(frame) + config.ray.data_decode_batch_size - 1)
            // config.ray.data_decode_batch_size,
        )
        num_blocks = max(
            1,
            min(len(frame), config.ray.data_num_blocks, requested_blocks),
        )
        ray_dataset = ray.data.from_pandas(
            frame,
            override_num_blocks=num_blocks,
        ).random_shuffle(
            seed=config.run.seed + (0 if split_name == "training" else 1),
        )
        ray_dataset = ray_dataset.map_batches(
            decode_image_batch,
            batch_size=config.ray.data_decode_batch_size,
            batch_format="pandas",
            zero_copy_batch=True,
            fn_kwargs={"image_size": config.image_size},
            num_cpus=1,
            compute=TaskPoolStrategy(size=config.ray.data_decode_workers),
        )
        if config.ray.data_cache_decoded:
            ray_dataset = ray_dataset.materialize()
        datasets[split_name] = ray_dataset
    return datasets


def validate_equal_shards(dataset: PreparedDataset, num_workers: int) -> None:
    """Prevent Ray's equal streaming shards from silently dropping records."""

    incompatible = {
        split_name: len(dataset.split_frame(split_name))
        for split_name in ("training", "validation")
        if len(dataset.split_frame(split_name)) % num_workers != 0
    }
    if incompatible:
        raise ValueError(
            "Training and validation row counts must divide evenly across Ray workers; "
            f"num_workers={num_workers}, incompatible={incompatible}"
        )
