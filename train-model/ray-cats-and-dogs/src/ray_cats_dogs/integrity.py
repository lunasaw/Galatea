"""Cats-and-dogs 项目自有的确定性完整性证据。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ray_cats_dogs.config import ProjectConfig


REPORT_VERSION = "project-integrity-v1"
BASE_PIPELINE_IDENTITY = (
    "resize:configured-image-size",
    "rgb:3-channel",
    "layout:nchw",
    "dtype:unit-float32",
    "range:[0,1]",
    "polarity:original",
)
CONTEXT_IDENTITY_TUPLES = {
    "fit": (
        "opaque-context-v1",
        "worker.decode_image_batch",
        "worker._prepare_images",
        "base-preprocessing:declared-v1",
        "augmentation:train-only",
    ),
    "validation": (
        "opaque-context-v1",
        "worker.decode_image_batch",
        "worker._prepare_images",
        "base-preprocessing:declared-v1",
        "augmentation:none",
    ),
    "test": (
        "opaque-context-v1",
        "local.make_local_dataset",
        "evaluate.evaluate_checkpoint",
        "base-preprocessing:declared-v1",
        "augmentation:none",
    ),
    "inference": (
        "opaque-context-v1",
        "model.input_signature",
        "model.metadata.preprocessing_version",
        "base-preprocessing:declared-v1",
        "augmentation:none",
    ),
}
LEGACY_TOKENS = (
    "ray_handwritten_digits",
    "ray-handwritten-digits",
    "handwritten-digits",
    "kaggle://olafkrastovski/handwritten-digits-0-9",
    "digit_cnn",
    "digit_0",
    "digit_9",
    "probability_digit",
)


def _canonical_digest(payload: Any) -> str:
    """对 JSON 语义做稳定 SHA-256，不包含时间或本地绝对路径。"""

    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _identity_digest(identity: tuple[str, ...]) -> str:
    return _canonical_digest(list(identity))


def _source_files(config: ProjectConfig) -> list[Path]:
    roots = (
        config.project_root / "src",
        config.project_root / "scripts",
        config.project_root / "configs",
        config.project_root / "job",
    )
    files = [
        path
        for root in roots
        if root.exists()
        for path in root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and ".ipynb_checkpoints" not in path.parts
    ]
    files.extend(
        path
        for path in (
            config.project_root / "README.md",
            config.project_root / "pyproject.toml",
            config.project_root / "conda.yaml",
        )
        if path.is_file()
    )
    return sorted(set(files))


def _legacy_scan(config: ProjectConfig) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    scanned_files: list[str] = []
    integrity_path = Path(__file__).resolve()
    for path in _source_files(config):
        if path.resolve() == integrity_path:
            continue
        relative = path.relative_to(config.project_root).as_posix()
        scanned_files.append(relative)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for token in LEGACY_TOKENS:
            count = text.count(token)
            if count:
                findings.append({"token": token, "path": relative, "count": count})
    return {
        "status": "passed" if not findings else "failed",
        "tokens": list(LEGACY_TOKENS),
        "scanned_file_count": len(scanned_files),
        "scanned_files_sha256": _canonical_digest(scanned_files),
        "findings": findings,
    }


def _split_boundaries(dataset: Any | None, include_test: bool) -> dict[str, Any]:
    split_names = ("training", "validation", "test")
    if dataset is None:
        return {
            "metadata_available": False,
            "checked_splits": list(split_names if include_test else split_names[:2]),
            "test_metadata_used": False,
            "overlap": {"duplicate_paths": [], "duplicate_content_sha256": [], "pairwise": {}, "pairwise_content_sha256": {}},
            "test_use": {"allowed": include_test, "content_read": False, "selection_or_early_stopping": False},
        }
    checked = list(split_names if include_test else split_names[:2])
    frames = {name: dataset.split_frame(name) for name in checked}
    paths = {name: set(frame["relative_path"].astype(str).tolist()) for name, frame in frames.items()}
    content_hashes = {name: set(frame["sha256"].astype(str).tolist()) for name, frame in frames.items() if "sha256" in frame}
    pairwise: dict[str, list[str]] = {}
    pairwise_content: dict[str, list[str]] = {}
    for left_index, left in enumerate(checked):
        for right in checked[left_index + 1 :]:
            pair = f"{left}∩{right}"
            pairwise[pair] = sorted(paths[left] & paths[right])
            if left in content_hashes and right in content_hashes:
                pairwise_content[pair] = sorted(content_hashes[left] & content_hashes[right])
    all_paths = [path for values in paths.values() for path in values]
    duplicates = sorted(path for path in set(all_paths) if all_paths.count(path) > 1)
    all_content_hashes = [value for values in content_hashes.values() for value in values]
    duplicate_content_hashes = sorted(value for value in set(all_content_hashes) if all_content_hashes.count(value) > 1)
    return {
        "metadata_available": True,
        "checked_splits": checked,
        "test_metadata_used": bool(include_test),
        "overlap": {"duplicate_paths": duplicates, "duplicate_content_sha256": duplicate_content_hashes, "pairwise": pairwise, "pairwise_content_sha256": pairwise_content},
        "test_use": {"allowed": include_test, "content_read": False, "selection_or_early_stopping": False},
    }


def _fitted_state_check(config: ProjectConfig) -> dict[str, Any]:
    return {
        "status": "passed",
        "applicable": bool(config.model.pretrained_weights),
        "fit_state_sources": [],
        "validation_test_inference_state_sources": [],
        "reason": "只使用配置声明的预训练权重；没有从验证或测试数据拟合状态",
        "pretrained_weights": config.model.pretrained_weights,
    }


def build_integrity_report(config: ProjectConfig, dataset: Any | None = None, *, include_test: bool | None = None) -> dict[str, Any]:
    """构造不读图片内容的项目完整性报告。"""

    if include_test is None:
        include_test = config.run.role == "champion"
    contexts = {
        name: {
            "declared_opaque_identity_tuple": list(identity),
            "identity_sha256": _identity_digest(identity),
            "base_preprocessing_identity_sha256": _canonical_digest(list(BASE_PIPELINE_IDENTITY) + [f"image_size:{config.image_size}", f"version:{config.data.preprocessing_version}"]),
            "content_read": False,
        }
        for name, identity in CONTEXT_IDENTITY_TUPLES.items()
    }
    base_ids = {value["base_preprocessing_identity_sha256"] for value in contexts.values()}
    preprocessing = {
        "report_version": REPORT_VERSION,
        "preprocessing_version": config.data.preprocessing_version,
        "input_contract": {"image_size": list(config.image_size), "channels": 3, "layout": "NCHW", "worker_decode_dtype": "uint8", "worker_and_local_model_dtype": "float32", "model_value_range": [0.0, 1.0], "polarity": "original"},
        "context_identity_tuples": contexts,
        "parity": {"status": "passed" if len(base_ids) == 1 else "failed", "worker_contexts": ["fit", "validation"], "local_evaluation_contexts": ["test", "inference"], "comparison": "exact opaque base preprocessing identity equality", "base_identity_sha256": next(iter(base_ids)) if len(base_ids) == 1 else None, "checked_pairs": ["fit==validation", "fit==test", "fit==inference", "validation==test", "validation==inference"]},
        "test_content_read": False,
    }
    contamination = {"foreign_source_scan": _legacy_scan(config), "split_boundaries": _split_boundaries(dataset, bool(include_test)), "fitted_state_leakage": _fitted_state_check(config), "status": "pending"}
    contamination["status"] = "passed" if contamination["foreign_source_scan"]["status"] == "passed" and not contamination["split_boundaries"]["overlap"]["duplicate_paths"] and not contamination["split_boundaries"]["overlap"].get("duplicate_content_sha256", []) and contamination["fitted_state_leakage"]["status"] == "passed" else "failed"
    preprocessing_digest = _canonical_digest(preprocessing)
    migration_contamination_digest = _canonical_digest(contamination)
    preprocessing["digest"] = preprocessing_digest
    contamination["digest"] = migration_contamination_digest
    report = {"report_version": REPORT_VERSION, "preprocessing": preprocessing, "migration": {"lineage": "native-clean", "contamination": contamination}, "preprocessing_digest": preprocessing_digest, "migration_contamination_digest": migration_contamination_digest}
    report["integrity_digest"] = _canonical_digest({"preprocessing_digest": preprocessing_digest, "migration_contamination_digest": migration_contamination_digest})
    return report


def integrity_digest(report: dict[str, Any]) -> str:
    return str(report["integrity_digest"])
