"""Read-only training readiness plan for Galatea and manual Ray submissions."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .config import ProjectConfig, canonical_training_config_digest, validate_training_config
from .datasets import compute_dataset_digest, load_samples, partition_samples
from .runtime import collect_environment_snapshot, environment_digest, validate_training_environment
from .integrity import build_integrity_report


class PlanContractError(ValueError):
    pass


def resolve_repository_root(config: ProjectConfig) -> Path:
    configured = os.environ.get("GALATEA_REPOSITORY_ROOT")
    if configured:
        root = Path(configured).expanduser().resolve()
        if not (root / "train-model" / "llm-lora-playground").is_dir():
            raise PlanContractError(f"GALATEA_REPOSITORY_ROOT is not a Galatea repository: {root}")
        return root
    return config.source_path.resolve().parents[3]


def resolve_data_path(config: ProjectConfig, data: Path | None = None) -> Path:
    repo_root = resolve_repository_root(config)
    candidate = data.expanduser() if data is not None else Path(str(config.values.get("data", {}).get("uri", ""))).expanduser()
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    candidate = candidate.resolve()
    return candidate / "dataset.jsonl" if candidate.is_dir() else candidate


def resolve_output_root(config: ProjectConfig) -> Path:
    repo_root = resolve_repository_root(config)
    candidate = Path(str(config.values.get("output_root", "platform-data/llm-baselines/toy-lora/training"))).expanduser()
    return (candidate if candidate.is_absolute() else repo_root / candidate).resolve()


def code_revision(project_root: Path) -> str:
    explicit = os.environ.get("CODE_REVISION") or os.environ.get("GALATEA_CODE_REVISION")
    if explicit:
        return explicit
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root.parent.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unresolved"


def model_snapshot_digest(model_path: Path) -> str:
    index = model_path / "model.safetensors.index.json"
    config = model_path / "config.json"
    if not index.is_file() or not config.is_file():
        raise PlanContractError("model snapshot requires config.json and model.safetensors.index.json")
    payload = {
        "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
        "index_sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def build_training_plan(config: ProjectConfig, data: Path | None = None) -> dict[str, Any]:
    errors = validate_training_config(config)
    data_path = resolve_data_path(config, data)
    if not data_path.is_file() or data_path.is_symlink():
        errors.append(f"dataset file is missing or unsafe: {data_path}")
        raise PlanContractError("; ".join(sorted(set(errors))))
    dataset_digest = compute_dataset_digest(data_path)
    declared_digest = config.values.get("data", {}).get("content_sha256")
    if declared_digest and declared_digest != dataset_digest:
        errors.append("data.content_sha256 does not match the immutable dataset")
    samples = list(load_samples(data_path))
    splits = partition_samples(samples, config.values["data"])
    integrity = build_integrity_report(config, splits)
    declared_split = config.values.get("data", {}).get("split_sha256")
    if declared_split and declared_split != splits.digest:
        errors.append("data.split_sha256 does not match the resolved split population")
    declared_count = config.values.get("data", {}).get("sample_count")
    if declared_count is not None and int(declared_count) != len(samples):
        errors.append("data.sample_count does not match the immutable dataset")
    model_path = Path(str(config.values["model"]["local_path"])).expanduser().resolve()
    errors.extend(validate_training_environment(model_path))
    if errors:
        raise PlanContractError("; ".join(sorted(set(errors))))
    snapshot = collect_environment_snapshot()
    project_root = config.source_path.resolve().parents[1]
    role = str(config.values["run"]["role"])
    execution_identity = {
        "project": "llm-lora-playground",
        "role": role,
        "config_digest": canonical_training_config_digest(config),
        "dataset_digest": dataset_digest,
        "split_digest": splits.digest,
        "code_revision": code_revision(project_root),
        "environment_digest": environment_digest(snapshot),
        "model_digest": model_snapshot_digest(model_path),
    }
    idempotency_key = hashlib.sha256(json.dumps(execution_identity, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "status": "planned",
        "config": {
            "run": {
                "role": role,
                "log_model": bool(config.values["run"].get("log_model", False)),
                "promotable": bool(config.values["run"].get("promotable", False)),
            },
            "evaluation": {
                "evaluate_test": bool(config.values["evaluation"].get("evaluate_test", False)),
                "protocol_version": str(config.values["evaluation"]["protocol_version"]),
            },
            "ray": {
                "backend": "ray_job",
                "worker_count": int(config.values["execution"]["worker_count"]),
                "python_version": ".".join(map(str, __import__("sys").version_info[:3])),
                "ray_version": snapshot["packages"]["ray"],
            },
        },
        "config_digest": execution_identity["config_digest"],
        "dataset": {
            "id": str(config.values["data"]["dataset_id"]),
            "path": str(data_path),
            "content_sha256": dataset_digest,
            "split_sha256": splits.digest,
            "preprocessing_version": str(config.values["data"]["preprocessing_version"]),
            "split_strategy": splits.strategy,
            "sample_count": len(samples),
            "split_counts": splits.counts,
            "test_access": "enabled_once" if config.values["evaluation"].get("evaluate_test") else "untouched",
        },
        "code": {
            "revision": execution_identity["code_revision"],
            "model_snapshot_sha256": execution_identity["model_digest"],
            "environment_sha256": execution_identity["environment_digest"],
        },
        "requested_resources": dict(config.values["resources"]),
        "objective": {
            "metric": str(config.values["objective_metric"]),
            "mode": str(config.values["objective_mode"]),
            "uses_test_holdout": bool(config.values["evaluation"].get("evaluate_test", False)),
        },
        "tracking": {
            "tracking_uri_env": str(config.values.get("tracking", {}).get("tracking_uri_env", "MLFLOW_TRACKING_URI")),
            "experiment_name": os.environ.get(
                str(config.values.get("tracking", {}).get("experiment_name_env", "MLFLOW_EXPERIMENT_NAME")),
                str(config.values.get("tracking", {}).get("default_experiment_name", "llm-lora-playground")),
            ),
        },
        "output_root": str(resolve_output_root(config)),
        "idempotency_key": idempotency_key,
        "will_train": True,
        "will_create_mlflow_run": True,
        "integrity": integrity,
    }
