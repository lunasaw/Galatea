"""House Prices 官方 CSV 的验证、身份计算和确定性切分。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.model_selection import StratifiedKFold, train_test_split

from ray_kaggle_house_prices.config import DataSettings, ValidationSettings


@dataclass(frozen=True)
class PreparedDataset:
    development: pd.DataFrame
    holdout: pd.DataFrame | None
    inference: pd.DataFrame | None
    all_labeled: pd.DataFrame
    fold_assignments: pd.DataFrame
    content_digest: str
    split_digest: str
    dataset_version: str
    profile: dict[str, Any]
    manifest_path: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for block in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stratification_labels(target: pd.Series, bins: int) -> np.ndarray:
    ranked = target.rank(method="first")
    effective_bins = min(bins, max(2, len(target) // 20))
    return pd.qcut(ranked, q=effective_bins, labels=False, duplicates="drop").to_numpy()


def _validate_frames(
    train: pd.DataFrame,
    test: pd.DataFrame | None,
    settings: DataSettings,
) -> None:
    if len(train) != settings.expected_train_rows:
        raise ValueError(f"训练集行数应为 {settings.expected_train_rows}，实际为 {len(train)}")
    if settings.id_column not in train or settings.target_column not in train:
        raise ValueError("训练集缺少 Id 或 SalePrice")
    if not train[settings.id_column].is_unique:
        raise ValueError("训练集 Id 不唯一")
    if train[settings.target_column].isna().any() or (train[settings.target_column] < 0).any():
        raise ValueError("训练目标包含缺失值或负数")
    if test is None:
        return
    if len(test) != settings.expected_test_rows:
        raise ValueError(f"推理集行数应为 {settings.expected_test_rows}，实际为 {len(test)}")
    if settings.id_column not in test or settings.target_column in test:
        raise ValueError("推理集 Id/目标列契约不成立")
    if settings.sample_submission_csv.is_file():
        sample = pd.read_csv(settings.sample_submission_csv)
        expected_ids = test[settings.id_column].astype("int64").reset_index(drop=True)
        if list(sample.columns) != [settings.id_column, settings.target_column]:
            raise ValueError("sample_submission 列契约不成立")
        if len(sample) != len(test) or not sample[settings.id_column].astype("int64").reset_index(drop=True).equals(expected_ids):
            raise ValueError("sample_submission Id 顺序与推理集不一致")
    if not test[settings.id_column].is_unique:
        raise ValueError("推理集 Id 不唯一")
    if set(train[settings.id_column]) & set(test[settings.id_column]):
        raise ValueError("训练集与推理集 Id 存在重叠")
    expected_features = set(train.columns) - {settings.target_column}
    if set(test.columns) != expected_features:
        raise ValueError("训练和推理特征架构不一致")


def prepare_dataset(
    settings: DataSettings,
    validation: ValidationSettings,
    seed: int,
    *,
    include_holdout: bool,
    include_inference: bool,
) -> PreparedDataset:
    """读取官方数据并隔离开发、最终留出和无标签推理人群。"""

    if not settings.train_csv.is_file():
        raise FileNotFoundError(f"训练 CSV 不存在: {settings.train_csv}")
    train = pd.read_csv(settings.train_csv)
    test = None
    if include_inference:
        if not settings.test_csv.is_file():
            raise FileNotFoundError(f"推理 CSV 不存在: {settings.test_csv}")
        test = pd.read_csv(settings.test_csv)
    _validate_frames(train, test, settings)

    stratify = _stratification_labels(train[settings.target_column], validation.stratification_bins)
    if settings.holdout_fraction == 0.0:
        development_index = np.arange(len(train))
        holdout_index = np.asarray([], dtype="int64")
    else:
        development_index, holdout_index = train_test_split(
            np.arange(len(train)),
            test_size=settings.holdout_fraction,
            random_state=seed,
            shuffle=True,
            stratify=stratify,
        )
    development = train.iloc[np.sort(development_index)].reset_index(drop=True)
    hidden_holdout = train.iloc[np.sort(holdout_index)].reset_index(drop=True)

    development_strata = _stratification_labels(
        development[settings.target_column], validation.stratification_bins
    )
    splitter = StratifiedKFold(
        n_splits=validation.folds,
        shuffle=True,
        random_state=seed,
    )
    fold = np.full(len(development), -1, dtype="int64")
    for fold_index, (_, validation_index) in enumerate(
        splitter.split(np.zeros(len(development)), development_strata)
    ):
        fold[validation_index] = fold_index
    if (fold < 0).any():
        raise RuntimeError("存在未分配交叉验证折的开发样本")
    fold_assignments = pd.DataFrame(
        {settings.id_column: development[settings.id_column].astype("int64"), "fold": fold}
    ).sort_values(settings.id_column)

    train_sha256 = _sha256(settings.train_csv)
    test_sha256 = _sha256(settings.test_csv) if test is not None and settings.test_csv.is_file() else None
    sample_sha256 = (
        _sha256(settings.sample_submission_csv)
        if settings.sample_submission_csv.is_file()
        else None
    )
    schema = [(str(name), str(dtype)) for name, dtype in train.dtypes.items()]
    content_identity = {
        "source_uri": settings.source_uri,
        "train_sha256": train_sha256,
        "test_sha256": test_sha256,
        "sample_submission_sha256": sample_sha256,
        "schema_version": settings.schema_version,
        "schema": schema,
        "train_rows": len(train),
    }
    content_digest = _canonical_digest(content_identity)
    split_identity = {
        "seed": seed,
        "holdout_fraction": settings.holdout_fraction,
        "development_ids": sorted(int(value) for value in development[settings.id_column]),
        "holdout_ids": sorted(int(value) for value in hidden_holdout[settings.id_column]),
        "folds": fold_assignments.to_dict(orient="records"),
    }
    split_digest = _canonical_digest(split_identity)
    dataset_version = f"house-prices-{train_sha256[:12]}-{split_digest[:12]}"

    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = settings.cache_dir / f"manifest-{content_digest[:16]}-{split_digest[:16]}.json"
    manifest = {
        "dataset_version": dataset_version,
        "content_sha256": content_digest,
        "split_sha256": split_digest,
        "content_identity": content_identity,
        "split_counts": {
            "development": len(development),
            "holdout": len(hidden_holdout),
            "inference": len(test) if test is not None else 0,
        },
        "fold_counts": {
            str(key): int(value) for key, value in fold_assignments["fold"].value_counts().sort_index().items()
        },
        "split_identity": split_identity,
    }
    serialized = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if manifest_path.exists() and manifest_path.read_text(encoding="utf-8") != serialized:
        raise RuntimeError(f"拒绝覆盖内容不同的数据清单: {manifest_path}")
    manifest_path.write_text(serialized, encoding="utf-8")

    return PreparedDataset(
        development=development,
        holdout=hidden_holdout if include_holdout else None,
        inference=test,
        all_labeled=train,
        fold_assignments=fold_assignments,
        content_digest=content_digest,
        split_digest=split_digest,
        dataset_version=dataset_version,
        profile={
            "split_counts": manifest["split_counts"],
            "fold_counts": manifest["fold_counts"],
            "train_sha256": train_sha256,
            "test_sha256": test_sha256,
            "schema_sha256": _canonical_digest(schema),
        },
        manifest_path=manifest_path,
    )


def split_features_target(frame: pd.DataFrame, settings: DataSettings) -> tuple[pd.DataFrame, np.ndarray]:
    """分离 Id、特征和目标，并返回 log1p 目标。"""

    if settings.target_column not in frame:
        raise ValueError("标注数据缺少目标列")
    features = frame.drop(columns=[settings.target_column, settings.id_column])
    if settings.target_column in features or settings.id_column in features:
        raise RuntimeError("目标或 Id 泄漏到模型特征")
    target_log = np.log1p(frame[settings.target_column].to_numpy(dtype="float64"))
    return features, target_log


def inference_features(frame: pd.DataFrame, settings: DataSettings) -> pd.DataFrame:
    """生成推理特征并强制排除 Id 与目标列。"""

    if settings.target_column in frame:
        raise ValueError("推理输入不得包含 SalePrice")
    return frame.drop(columns=[settings.id_column])
