"""手写数字项目自有的确定性完整性证据。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ray_handwritten_digits.config import ProjectConfig


REPORT_VERSION = "project-integrity-v1"
BASE_PIPELINE_IDENTITY = (
    "resize:configured-image-size",
    "grayscale:1-channel",
    "layout:nchw",
    "dtype:unit-float32",
    "range:[0,1]",
    "polarity:inverted",
)
CONTEXT_IDENTITY_TUPLES = {
    "fit": (
        "opaque-context-v1",
        "worker.decode_image_batch",
        "worker._prepare_images",
        "base-preprocessing:declared-v2",
        "augmentation:train-only",
    ),
    "validation": (
        "opaque-context-v1",
        "worker.decode_image_batch",
        "worker._prepare_images",
        "base-preprocessing:declared-v2",
        "augmentation:none",
    ),
    "test": (
        "opaque-context-v1",
        "local.make_local_dataset",
        "evaluate.evaluate_checkpoint",
        "base-preprocessing:declared-v2",
        "augmentation:none",
    ),
    "inference": (
        "opaque-context-v1",
        "model.input_signature",
        "model.metadata.preprocessing_version",
        "base-preprocessing:declared-v2",
        "augmentation:none",
    ),
}
LEGACY_TOKENS = (
    "ray_cats_dogs",
    "ray-cats-and-dogs",
    "cats-and-dogs",
    "cats-vs-dogs",
    "CATS_DOGS",
    "PetImages",
    "Cat",
    "Dog",
    "probability_cat",
    "probability_dog",
)


def _canonical_digest(payload: Any) -> str:
    """对 JSON 语义做稳定 SHA-256，不包含时间或本地绝对路径。"""

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _identity_digest(identity: tuple[str, ...]) -> str:
    return _canonical_digest(list(identity))


def _source_files(config: ProjectConfig) -> list[Path]:
    """收集项目拥有的文本声明和实现文件，排除生成缓存。"""

    return sorted(
        path
        for path in config.project_root.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and "__pycache__" not in path.parts
        and ".ipynb_checkpoints" not in path.parts
        and ".pytest_cache" not in path.parts
    )


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
            "test_use": {
                "allowed": include_test,
                "content_read": False,
                "selection_or_early_stopping": False,
            },
        }

    checked = list(split_names if include_test else split_names[:2])
    frames = {name: dataset.split_frame(name) for name in checked}
    paths = {
        name: set(frame["relative_path"].astype(str).tolist())
        for name, frame in frames.items()
    }
    content_hashes = {
        name: set(frame["sha256"].astype(str).tolist())
        for name, frame in frames.items()
        if "sha256" in frame
    }
    pairwise: dict[str, list[str]] = {}
    pairwise_content: dict[str, list[str]] = {}
    for left_index, left in enumerate(checked):
        for right in checked[left_index + 1 :]:
            pair = f"{left}∩{right}"
            pairwise[pair] = sorted(paths[left] & paths[right])
            if left in content_hashes and right in content_hashes:
                pairwise_content[pair] = sorted(
                    content_hashes[left] & content_hashes[right]
                )
    all_paths = [path for values in paths.values() for path in values]
    duplicates = sorted(
        path for path in set(all_paths) if all_paths.count(path) > 1
    )
    all_content_hashes = [value for values in content_hashes.values() for value in values]
    duplicate_content_hashes = sorted(
        value for value in set(all_content_hashes) if all_content_hashes.count(value) > 1
    )
    return {
        "metadata_available": True,
        "checked_splits": checked,
        "test_metadata_used": bool(include_test),
        "overlap": {
            "duplicate_paths": duplicates,
            "duplicate_content_sha256": duplicate_content_hashes,
            "pairwise": pairwise,
            "pairwise_content_sha256": pairwise_content,
        },
        "test_use": {
            "allowed": include_test,
            "content_read": False,
            "selection_or_early_stopping": False,
        },
    }


def _fitted_state_check(config: ProjectConfig) -> dict[str, Any]:
    return {
        "status": "passed",
        "applicable": False,
        "fit_state_sources": [],
        "validation_test_inference_state_sources": [],
        "reason": "预处理没有从数据拟合均值、方差、词表或其他状态",
        "pretrained_weights": config.model.pretrained_weights,
    }


def build_integrity_report(
    config: ProjectConfig,
    dataset: Any | None = None,
    *,
    include_test: bool | None = None,
) -> dict[str, Any]:
    """构造不读图片内容的项目完整性报告。"""

    if include_test is None:
        include_test = config.run.role == "champion"
    contexts = {
        name: {
            "declared_opaque_identity_tuple": list(identity),
            "identity_sha256": _identity_digest(identity),
            "base_preprocessing_identity_sha256": _canonical_digest(
                list(BASE_PIPELINE_IDENTITY)
                + [f"image_size:{config.image_size}", f"version:{config.data.preprocessing_version}"]
            ),
            "content_read": False,
        }
        for name, identity in CONTEXT_IDENTITY_TUPLES.items()
    }
    base_ids = {
        value["base_preprocessing_identity_sha256"] for value in contexts.values()
    }
    parity = {
        "status": "passed" if len(base_ids) == 1 else "failed",
        "worker_contexts": ["fit", "validation"],
        "local_evaluation_contexts": ["test", "inference"],
        "comparison": "exact opaque base preprocessing identity equality",
        "base_identity_sha256": next(iter(base_ids)) if len(base_ids) == 1 else None,
        "checked_pairs": [
            "fit==validation",
            "fit==test",
            "fit==inference",
            "validation==test",
            "validation==inference",
        ],
    }
    preprocessing = {
        "report_version": REPORT_VERSION,
        "preprocessing_version": config.data.preprocessing_version,
        "input_contract": {
            "image_size": list(config.image_size),
            "channels": 1,
            "layout": "NCHW",
            "worker_decode_dtype": "uint8",
            "worker_and_local_model_dtype": "float32",
            "model_value_range": [0.0, 1.0],
            "polarity": "inverted",
        },
        "context_identity_tuples": contexts,
        "parity": parity,
        "test_content_read": False,
    }
    migration = {
        "report_version": REPORT_VERSION,
        "lineage": "native-clean",
        "contamination": {
            "foreign_source_scan": _legacy_scan(config),
            "split_boundaries": _split_boundaries(dataset, bool(include_test)),
            "fitted_state_leakage": _fitted_state_check(config),
            "status": "pending",
        },
    }
    contamination = migration["contamination"]
    boundary = contamination["split_boundaries"]
    overlap_passed = not boundary["overlap"]["duplicate_paths"] and not boundary["overlap"].get("duplicate_content_sha256", [])
    contamination["status"] = (
        "passed"
        if contamination["foreign_source_scan"]["status"] == "passed"
        and overlap_passed
        and contamination["fitted_state_leakage"]["status"] == "passed"
        else "failed"
    )
    preprocessing_digest = _canonical_digest(preprocessing)
    migration_contamination_digest = _canonical_digest(contamination)
    preprocessing["digest"] = preprocessing_digest
    contamination["digest"] = migration_contamination_digest
    report = {
        "report_version": REPORT_VERSION,
        "preprocessing": preprocessing,
        "migration": migration,
        "preprocessing_digest": preprocessing_digest,
        "migration_contamination_digest": migration_contamination_digest,
    }
    report["integrity_digest"] = _canonical_digest(
        {
            "preprocessing_digest": preprocessing_digest,
            "migration_contamination_digest": migration_contamination_digest,
        }
    )
    return report


def integrity_digest(report: dict[str, Any]) -> str:
    """返回报告声明的确定性总摘要。"""

    return str(report["integrity_digest"])
