"""Read-only source validation and deterministic digit manifests."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from PIL import Image, UnidentifiedImageError

from ray_handwritten_digits.config import DatasetSettings, ProjectConfig

CLASS_NAMES = tuple(f"digit_{index}" for index in range(10))
CLASS_DIRECTORIES = tuple((name, index) for index, name in enumerate(CLASS_NAMES))
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


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
    validation_share = config.validation_fraction / (config.validation_fraction + config.test_fraction)
    validation_count = int(round(holdout_count * validation_share))
    test_count = holdout_count - validation_count
    if min(train_count, validation_count, test_count) < 1:
        raise ValueError("每个数字类别都需要训练、验证和测试样本")
    return (*(("training",) * train_count), *(("validation",) * validation_count), *(("test",) * test_count))


def _validated_class_records(root: Path, class_name: str, label: int, config: DatasetSettings, seed: int) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
    source_dir = root / class_name
    if not source_dir.is_dir():
        raise FileNotFoundError(f"缺少数字目录: {source_dir}")
    candidates = [path for path in sorted(source_dir.iterdir()) if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES]
    valid: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    for path in candidates:
        relative_path = path.relative_to(root).as_posix()
        try:
            if path.stat().st_size == 0:
                raise OSError("empty image")
            with Image.open(path) as image:
                image.verify()
            valid.append({"relative_path": relative_path, "class_name": class_name, "label": label, "bytes": path.stat().st_size, "sha256": _file_sha256(path)})
        except (OSError, UnidentifiedImageError) as error:
            invalid.append({"relative_path": relative_path, "error_type": type(error).__name__})
    shuffled = list(valid)
    random.Random(seed + label).shuffle(shuffled)
    return shuffled, invalid, len(candidates)


def _assign_grouped_splits(
    records: list[dict[str, Any]], config: DatasetSettings, seed: int
) -> None:
    """将相同内容的记录放入同一切分，并尽量保持按类别的比例。"""

    target_counts: dict[tuple[str, int], int] = {}
    by_class: dict[int, list[dict[str, Any]]] = {}
    for record in records:
        by_class.setdefault(int(record["label"]), []).append(record)
    for label, class_records in by_class.items():
        split_names = _split_names(len(class_records), config)
        for split_name in ("training", "validation", "test"):
            target_counts[(split_name, label)] = split_names.count(split_name)

    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(str(record["sha256"]), []).append(record)
    ordered_groups = list(groups.values())
    random.Random(seed).shuffle(ordered_groups)
    ordered_groups.sort(key=len, reverse=True)
    current: dict[tuple[str, int], int] = {}
    split_order = ("training", "validation", "test")
    for group in ordered_groups:
        class_sizes: dict[int, int] = {}
        for record in group:
            label = int(record["label"])
            class_sizes[label] = class_sizes.get(label, 0) + 1

        def score(split_name: str) -> tuple[int, int, int]:
            overflow = sum(
                max(
                    0,
                    current.get((split_name, label), 0)
                    + count
                    - target_counts[(split_name, label)],
                )
                for label, count in class_sizes.items()
            )
            remaining_capacity = sum(
                max(
                    0,
                    target_counts[(split_name, label)]
                    - current.get((split_name, label), 0),
                )
                for label in class_sizes
            )
            return overflow, -remaining_capacity, split_order.index(split_name)

        selected = min(split_order, key=score)
        for record in group:
            record["split"] = selected
            key = (selected, int(record["label"]))
            current[key] = current.get(key, 0) + 1


def _digest_manifest(manifest: pd.DataFrame) -> tuple[str, str]:
    content = hashlib.sha256()
    split = hashlib.sha256()
    for record in manifest.sort_values("relative_path").to_dict(orient="records"):
        content.update(f"{record['relative_path']}|{record['bytes']}|{record['sha256']}\n".encode())
        split.update(f"{record['relative_path']}|{record['label']}|{record['split']}\n".encode())
    return content.hexdigest(), split.hexdigest()


def _persist_manifest(manifest: pd.DataFrame, cache_dir: Path, content_digest: str, split_digest: str) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"manifest-{content_digest[:16]}-{split_digest[:16]}.csv"
    serialized = manifest.to_csv(index=False, lineterminator="\n")
    try:
        with path.open("x", encoding="utf-8", newline="") as file_handle:
            file_handle.write(serialized)
    except FileExistsError:
        if path.read_text(encoding="utf-8") != serialized:
            raise RuntimeError(f"清单内容不一致: {path}")
    return path


def prepare_dataset(config: DatasetSettings, seed: int) -> PreparedDataset:
    all_records: list[dict[str, Any]] = []
    invalid_files: list[dict[str, str]] = []
    source_counts: dict[str, int] = {}
    for class_name, label in CLASS_DIRECTORIES:
        records, invalid, source_count = _validated_class_records(config.root, class_name, label, config, seed)
        all_records.extend(records)
        invalid_files.extend(invalid)
        source_counts[class_name] = source_count
    if config.expected_images_per_class is not None and any(count != config.expected_images_per_class for count in source_counts.values()):
        raise RuntimeError(f"每类期望 {config.expected_images_per_class} 个文件，实际为 {source_counts}")
    _assign_grouped_splits(all_records, config, seed)
    manifest = pd.DataFrame(all_records).sort_values(["relative_path", "split"]).reset_index(drop=True)
    if manifest.empty or (config.expected_valid_images is not None and len(manifest) != config.expected_valid_images):
        raise RuntimeError(f"有效图片数不符合配置: {len(manifest)}")
    if set(manifest["split"]) != {"training", "validation", "test"} or manifest["relative_path"].duplicated().any():
        raise RuntimeError("清单切分或路径无效")
    split_counts = manifest.groupby(["split", "class_name"]).size().unstack(fill_value=0)
    if (split_counts <= 0).any().any():
        raise RuntimeError("每个切分都必须包含十个数字类别")
    content_digest, split_digest = _digest_manifest(manifest)
    source_uri = config.source_uri or config.root.resolve().as_uri()
    profile = {"dataset_name": "kaggle-handwritten-digits-0-9", "dataset_version": f"sha256-{content_digest[:16]}", "source_uri": source_uri, "content_sha256": content_digest, "split_sha256": split_digest, "preprocessing_version": config.preprocessing_version, "class_names": list(CLASS_NAMES), "source_counts": source_counts, "valid_images": len(manifest), "invalid_images": invalid_files, "total_bytes": int(manifest["bytes"].sum()), "split_counts": {name: {key: int(value) for key, value in values.items()} for name, values in split_counts.to_dict(orient="index").items()}}
    return PreparedDataset(manifest, tuple(invalid_files), profile["dataset_version"], content_digest, split_digest, source_uri, _persist_manifest(manifest, config.cache_dir, content_digest, split_digest), json.loads(json.dumps(profile)))


def build_ray_datasets(dataset: PreparedDataset, config: ProjectConfig) -> dict[str, Any]:
    import ray
    from ray.data import TaskPoolStrategy
    from ray_handwritten_digits.input_pipeline import decode_image_batch
    datasets = {}
    for split_name in ("training", "validation"):
        frame = dataset.split_frame(split_name)[["relative_path", "label"]].copy()
        frame.insert(0, "path", frame["relative_path"].map(lambda relative: str(config.data.root / str(relative))))
        ray_dataset = ray.data.from_pandas(frame, override_num_blocks=max(1, min(len(frame), config.ray.data_num_blocks)))
        ray_dataset = ray_dataset.random_shuffle(seed=config.run.seed + (0 if split_name == "training" else 1)).map_batches(decode_image_batch, batch_size=config.ray.data_decode_batch_size, batch_format="pandas", zero_copy_batch=True, fn_kwargs={"image_size": config.image_size}, num_cpus=1, compute=TaskPoolStrategy(size=config.ray.data_decode_workers))
        if config.ray.data_cache_decoded:
            ray_dataset = ray_dataset.materialize()
        datasets[split_name] = ray_dataset
    return datasets


def validate_equal_shards(dataset: PreparedDataset, num_workers: int) -> None:
    incompatible = {name: len(dataset.split_frame(name)) for name in ("training", "validation") if len(dataset.split_frame(name)) % num_workers}
    if incompatible:
        raise ValueError(f"训练和验证样本数必须能被 Ray worker 数整除: {incompatible}")
