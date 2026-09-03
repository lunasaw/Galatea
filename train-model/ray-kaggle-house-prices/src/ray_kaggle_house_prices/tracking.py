"""MLflow 跟踪、代码身份、数据输入和 Artifact 回读。"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import mlflow
from mlflow import MlflowClient

from ray_kaggle_house_prices.config import ProjectConfig
from ray_kaggle_house_prices.data import PreparedDataset


def _command(arguments: list[str], cwd: Path) -> str | None:
    result = subprocess.run(arguments, cwd=cwd, capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def inspect_tracking(config: ProjectConfig) -> dict[str, str] | None:
    """只通过 MLflow API 检查实验，不访问服务端数据库文件。"""

    mlflow.set_tracking_uri(config.mlflow.tracking_uri)
    client = MlflowClient()
    experiment = client.get_experiment_by_name(config.mlflow.experiment_name)
    if experiment is None:
        return None
    scheme = experiment.artifact_location.split(":", maxsplit=1)[0]
    if config.mlflow.require_remote_artifacts and scheme not in {"mlflow-artifacts", "s3"}:
        raise RuntimeError(f"MLflow Artifact 位置不是远端代理或 S3: {experiment.artifact_location}")
    return {
        "experiment_id": experiment.experiment_id,
        "experiment_name": experiment.name,
        "artifact_location": experiment.artifact_location,
    }


def preflight_tracking(config: ProjectConfig) -> dict[str, str]:
    found = inspect_tracking(config)
    if found is not None:
        return found
    experiment = mlflow.set_experiment(config.mlflow.experiment_name)
    return {
        "experiment_id": experiment.experiment_id,
        "experiment_name": experiment.name,
        "artifact_location": experiment.artifact_location,
    }


def code_identity(config: ProjectConfig) -> dict[str, str]:
    digest = hashlib.sha256()
    paths = [
        path
        for root in (config.project_root / "src", config.project_root / "scripts")
        if root.exists()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    for path in (config.project_root / "conda.yaml", config.project_root / "pyproject.toml"):
        if path.is_file():
            paths.append(path)
    for path in sorted(paths):
        digest.update(path.relative_to(config.project_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    repository_root = config.project_root.parents[1]
    status = _command(["git", "status", "--porcelain"], repository_root)
    return {
        "source_sha256": digest.hexdigest(),
        "git_commit": _command(["git", "rev-parse", "HEAD"], repository_root) or "uncommitted",
        "git_dirty": str(bool(status)).lower(),
        "python": platform.python_version(),
    }


def idempotency_key(config: ProjectConfig, dataset: PreparedDataset, code: dict[str, str], integrity_digest: str) -> str:
    selected_digest = "none"
    if config.models.selected_parameters_path is not None and config.models.selected_parameters_path.is_file():
        selected_digest = hashlib.sha256(config.models.selected_parameters_path.read_bytes()).hexdigest()
    payload = "|".join(
        (
            config.project_name,
            dataset.content_digest,
            dataset.split_digest,
            code["source_sha256"],
            integrity_digest,
            selected_digest,
            config.config_digest,
            str(config.run.seed),
            config.run.role,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def flatten(prefix: str, value: Any, output: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            flatten(f"{prefix}.{key}" if prefix else str(key), item, output)
    elif isinstance(value, (list, tuple)):
        output[prefix] = json.dumps(value, ensure_ascii=False, sort_keys=True)
    elif value is None:
        output[prefix] = "null"
    else:
        output[prefix] = value


def _integrity_artifact(report_id: str, role: str, status: str, payload: dict[str, Any]) -> tuple[str, str]:
    envelope = {
        "schema_version": "galatea/integrity/v1",
        "report_id": report_id,
        "role": role,
        "status": status,
        "payload": payload,
    }
    content_digest = hashlib.sha256(
        json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    complete = {**envelope, "content_digest": content_digest}
    serialized = json.dumps(complete, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    artifact_digest = f"sha256:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"
    return serialized, artifact_digest


def log_run_inputs(config: ProjectConfig, dataset: PreparedDataset, code: dict[str, str], integrity: dict[str, Any]) -> None:
    params: dict[str, Any] = {}
    flatten("config", config.as_dict(), params)
    preprocessing_status = str(integrity["preprocessing"]["parity"]["status"])
    migration_status = str(integrity["migration"]["contamination"]["status"])
    preprocessing_text, preprocessing_artifact_digest = _integrity_artifact(
        "preprocessing", config.run.role, preprocessing_status, integrity["preprocessing"]
    )
    migration_text, migration_artifact_digest = _integrity_artifact(
        "migration", config.run.role, migration_status, integrity["migration"]["contamination"]
    )
    params.update(
        {
            "data.content_sha256": dataset.content_digest,
            "data.split_sha256": dataset.split_digest,
            "data.dataset_version": dataset.dataset_version,
            "data.preprocessing_version": config.data.preprocessing_version,
            "data.manifest_path": str(dataset.manifest_path),
            "models.selected_parameters_sha256": (
                hashlib.sha256(config.models.selected_parameters_path.read_bytes()).hexdigest()
                if config.models.selected_parameters_path is not None and config.models.selected_parameters_path.is_file()
                else "none"
            ),
            "code.source_sha256": code["source_sha256"],
            "code.git_commit": code["git_commit"],
            "code.git_dirty": code["git_dirty"],
            "integrity.preprocessing_artifact_digest": preprocessing_artifact_digest,
            "integrity.migration_artifact_digest": migration_artifact_digest,
        }
    )
    safe_params = {key: str(value)[:500] for key, value in params.items()}
    mlflow.log_params(safe_params)
    mlflow.set_tags(
        {
            "integrity.preprocessing.status": preprocessing_status,
            "integrity.migration.status": migration_status,
        }
    )
    mlflow.log_dict(dataset.profile, "data/profile.json")
    mlflow.log_text(preprocessing_text, "reports/preprocessing-parity.json")
    mlflow.log_text(migration_text, "reports/migration-contamination.json")
    mlflow.log_artifact(str(dataset.manifest_path), artifact_path="data")


def verify_artifact_roundtrip(run_id: str, artifact_path: str) -> str:
    with tempfile.TemporaryDirectory(prefix="house-prices-artifact-check-") as directory:
        downloaded = mlflow.artifacts.download_artifacts(run_id=run_id, artifact_path=artifact_path, dst_path=directory)
        path = Path(downloaded)
        if not path.is_file():
            raise RuntimeError(f"Artifact 回读失败: {artifact_path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest
