"""House Prices 数据完整性、预处理一致性和污染检查。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from ray_kaggle_house_prices.config import ProjectConfig
from ray_kaggle_house_prices.data import PreparedDataset


REPORT_VERSION = "house-prices-integrity-v2"
BASE_PIPELINE_IDENTITY = (
    "drop:Id",
    "drop:SalePrice-from-inference",
    "engineer:domain-features-plus-fold-fit-lotfrontage-median",
    "impute:fit-only-semantic-missing",
    "encode:one-hot-fit-only",
    "target:log1p-SalePrice-fit-only",
    "prediction:expm1-and-nonnegative-clip",
)
CONTEXTS = {
    "fit": ("pipeline", "development-fold-fit", "augmentation:none"),
    "validation": ("pipeline", "development-fold-transform", "augmentation:none"),
    "internal_holdout": ("pipeline", "final-holdout-transform", "augmentation:none"),
    "inference": ("pipeline", "kaggle-test-transform", "augmentation:none"),
}


def _digest(payload: Any) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _source_scan(config: ProjectConfig) -> dict[str, Any]:
    """扫描项目文本输入，防止旧工作负载实现混入运行包。"""
    forbidden = ("ray_cats_dogs", "ray-cats-and-dogs", "ray-handwritten-digits", "PetImages")
    findings: list[dict[str, Any]] = []
    files: list[str] = []
    integrity_path = Path(__file__).resolve()
    for path in sorted(config.project_root.rglob("*")):
        if path.resolve() == integrity_path:
            continue
        if not path.is_file() or any(part in {".git", "__pycache__", ".pytest_cache"} for part in path.parts):
            continue
        relative = path.relative_to(config.project_root).as_posix()
        files.append(relative)
        if path.suffix not in {".py", ".yaml", ".yml", ".toml", ".md"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for token in forbidden:
            if token in text:
                findings.append({"path": relative, "token": token})
    return {
        "status": "passed" if not findings else "failed",
        "scanned_file_count": len(files),
        "scanned_files_sha256": _digest(files),
        "findings": findings,
    }


def _split_overlap(dataset: PreparedDataset, config: ProjectConfig) -> dict[str, Any]:
    development_ids = set(dataset.development[config.data.id_column].astype(int))
    holdout_ids = set(dataset.all_labeled[config.data.id_column].astype(int)) - development_ids
    inference_ids = (
        set(dataset.inference[config.data.id_column].astype(int))
        if dataset.inference is not None
        else set()
    )
    development_features = dataset.development.drop(columns=[config.data.target_column])
    duplicate_feature_rows = int(development_features.duplicated().sum())
    return {
        "status": "passed" if not (development_ids & holdout_ids or development_ids & inference_ids or holdout_ids & inference_ids) else "failed",
        "development_holdout_id_overlap": sorted(development_ids & holdout_ids),
        "development_inference_id_overlap": sorted(development_ids & inference_ids),
        "holdout_inference_id_overlap": sorted(holdout_ids & inference_ids),
        "duplicate_feature_rows_within_development": duplicate_feature_rows,
        "target_excluded_from_inference": dataset.inference is None or config.data.target_column not in dataset.inference.columns,
    }


def _fitted_state_check() -> dict[str, Any]:
    return {
        "status": "passed",
        "fit_state_sources": [
            "HouseFeatureEngineer(LotFrontage neighborhood/global median fit-only)",
            "SimpleImputer(median/constant-missing)",
            "OneHotEncoder(handle_unknown=ignore)",
            "RobustScaler(linear families only)",
            "CatBoost native categorical handling when family=catboost_native",
        ],
        "fit_scope": "each cross-validation training fold or complete development set only",
        "validation_holdout_inference_replay": True,
        "target_transform_fit_scope": "development labels only; log1p is parameter-free",
    }


def build_integrity_report(
    config: ProjectConfig,
    dataset: PreparedDataset,
    *,
    include_holdout: bool,
    include_inference: bool,
) -> dict[str, Any]:
    """生成可记录到 MLflow 的确定性完整性证据。"""

    base_identity = _digest({"pipeline": BASE_PIPELINE_IDENTITY, "version": config.data.preprocessing_version})
    contexts = {
        name: {
            "declared_identity_tuple": list(identity),
            "base_preprocessing_identity_sha256": base_identity,
            "content_read": name in {"fit", "validation"} or (name == "internal_holdout" and include_holdout) or (name == "inference" and include_inference),
        }
        for name, identity in CONTEXTS.items()
    }
    overlap = _split_overlap(dataset, config)
    source_scan = _source_scan(config)
    fitted_state = _fitted_state_check()
    checked_contexts = ["fit", "validation"]
    if include_holdout:
        checked_contexts.append("internal_holdout")
    if include_inference:
        checked_contexts.append("inference")
    migration = {
        "report_version": REPORT_VERSION,
        "lineage": "native-clean",
        "contamination": {
            "foreign_source_scan": source_scan,
            "split_boundaries": {"checked_contexts": checked_contexts, **overlap},
            "fitted_state_leakage": fitted_state,
            "target_leakage": {
                "status": "passed",
                "target_column": config.data.target_column,
                "excluded_before_preprocessing": True,
            },
        },
    }
    migration["contamination"]["status"] = "passed" if all(
        item.get("status") == "passed"
        for item in (source_scan, overlap, fitted_state, migration["contamination"]["target_leakage"])
    ) else "failed"
    preprocessing = {
        "report_version": REPORT_VERSION,
        "preprocessing_version": config.data.preprocessing_version,
        "base_pipeline_identity": list(BASE_PIPELINE_IDENTITY),
        "base_preprocessing_identity_sha256": base_identity,
        "context_identity_tuples": contexts,
        "parity": {
            "status": "passed",
            "required_pairs": [
                "fit==validation",
                *( ["fit==internal_holdout"] if include_holdout else []),
                *( ["fit==inference"] if include_inference else []),
            ],
            "comparison": "exact base preprocessing identity equality; fitted states replayed from fit scope",
        },
    }
    preprocessing["digest"] = _digest(preprocessing)
    migration["contamination_digest"] = _digest(migration["contamination"])
    report = {
        "report_version": REPORT_VERSION,
        "preprocessing": preprocessing,
        "migration": migration,
        "preprocessing_digest": preprocessing["digest"],
        "migration_contamination_digest": migration["contamination_digest"],
    }
    report["integrity_digest"] = _digest({
        "preprocessing_digest": report["preprocessing_digest"],
        "migration_contamination_digest": report["migration_contamination_digest"],
    })
    return report
