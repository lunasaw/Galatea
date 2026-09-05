"""Small MLflow API-only tracking helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ArtifactIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class RunContext:
    run_id: str
    experiment_id: str
    manifest_digest: str


def build_run_manifest(**kwargs: Any) -> dict[str, Any]:
    manifest = dict(kwargs)
    manifest.setdefault("project", "llm-lora-playground")
    manifest.setdefault("inference_baseline_only", True)
    return manifest


def start_inference_run(manifest: dict[str, Any], tracking_uri: str, experiment_name: str) -> RunContext:
    import mlflow

    mlflow.set_tracking_uri(tracking_uri)
    experiment_id = mlflow.get_experiment_by_name(experiment_name)
    if experiment_id is None:
        experiment_id = mlflow.create_experiment(experiment_name)
    else:
        experiment_id = experiment_id.experiment_id
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with mlflow.start_run(experiment_id=experiment_id) as run:
        mlflow.set_tags({"project": "llm-lora-playground", "task": "inference_baseline", "inference_baseline_only": "true"})
        mlflow.log_param("manifest_digest", digest)
        return RunContext(run.info.run_id, str(experiment_id), digest)


def log_baseline_metrics(context: RunContext, metrics: dict[str, float], tracking_uri: str) -> None:
    import mlflow

    mlflow.set_tracking_uri(tracking_uri)
    client = mlflow.tracking.MlflowClient(tracking_uri=tracking_uri)
    client.log_batch(context.run_id, metrics=[mlflow.entities.Metric(key=k, value=float(v), timestamp=0, step=0) for k, v in metrics.items()])


def verify_artifact_roundtrip(client: Any, run_id: str, artifact_path: str, expected_sha256: str) -> None:
    local = client.download_artifacts(run_id, artifact_path)
    digest = hashlib.sha256(Path(local).read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise ArtifactIntegrityError(f"artifact digest mismatch for {artifact_path}: expected {expected_sha256}, got {digest}")


def download_and_verify_artifact(client: Any, run_id: str, artifact_path: str, expected_sha256: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        local = Path(client.download_artifacts(run_id, artifact_path, dst_path=str(output_dir)))
    except TypeError:
        local = Path(client.download_artifacts(run_id, artifact_path))
        target = output_dir / Path(artifact_path).name
        target.write_bytes(local.read_bytes())
        local = target
    digest = hashlib.sha256(local.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise ArtifactIntegrityError(f"artifact digest mismatch for {artifact_path}: expected {expected_sha256}, got {digest}")
    return local
