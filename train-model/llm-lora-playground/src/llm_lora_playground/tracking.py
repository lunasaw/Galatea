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


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_path: str
    sha256: str
    size_bytes: int


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


def start_training_run(manifest: dict[str, Any], tracking_uri: str | None = None, experiment_name: str | None = None) -> RunContext:
    """Create the parent training Run; the caller remains the sole owner."""
    import mlflow

    tracking_uri = tracking_uri or __import__("os").environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
    experiment_name = experiment_name or __import__("os").environ.get("MLFLOW_EXPERIMENT_NAME", "llm-lora-playground")
    mlflow.set_tracking_uri(tracking_uri)
    experiment = mlflow.get_experiment_by_name(experiment_name)
    experiment_id = experiment.experiment_id if experiment else mlflow.create_experiment(experiment_name)
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    client = mlflow.tracking.MlflowClient(tracking_uri=tracking_uri)
    run = client.create_run(
        experiment_id=str(experiment_id),
        tags={
            "project": "llm-lora-playground",
            "task": str(manifest.get("task", "synthetic_sft_lora")),
            "run_kind": str(manifest.get("run_kind", "training")),
            "config_digest": str(manifest.get("config_digest", "")),
            "dataset_manifest_digest": str(manifest.get("dataset_manifest_digest", "")),
            "owner_bulk_approved": str(bool(manifest.get("owner_bulk_approved", False))).lower(),
            "formal_training_eligible": str(bool(manifest.get("formal_training_eligible", True))).lower(),
        },
    )
    client.log_batch(
        run.info.run_id,
        params=[
            mlflow.entities.Param("manifest_digest", digest),
            mlflow.entities.Param("objective_metric", str(manifest.get("objective_metric", "validation_loss"))),
            mlflow.entities.Param("objective_mode", str(manifest.get("objective_mode", "min"))),
        ],
    )
    return RunContext(run.info.run_id, str(experiment_id), digest)


def finish_training_run(context: RunContext, status: str = "FINISHED", tracking_uri: str | None = None) -> None:
    import mlflow

    tracking_uri = tracking_uri or __import__("os").environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
    client = mlflow.tracking.MlflowClient(tracking_uri=tracking_uri)
    client.set_terminated(context.run_id, status=status)


def log_training_metrics(context: RunContext, metrics: dict[str, float], tracking_uri: str | None = None, step: int = 0) -> None:
    import mlflow

    tracking_uri = tracking_uri or __import__("os").environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
    mlflow.set_tracking_uri(tracking_uri)
    client = mlflow.tracking.MlflowClient(tracking_uri=tracking_uri)
    client.log_batch(context.run_id, metrics=[mlflow.entities.Metric(key=k, value=float(v), timestamp=0, step=step) for k, v in metrics.items()])


def log_artifact_with_sha256(context: RunContext, path: Path, artifact_path: str, tracking_uri: str | None = None) -> ArtifactRecord:
    import mlflow

    if not path.is_file():
        raise FileNotFoundError(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    tracking_uri = tracking_uri or __import__("os").environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
    mlflow.set_tracking_uri(tracking_uri)
    client = mlflow.tracking.MlflowClient(tracking_uri=tracking_uri)
    client.log_artifact(context.run_id, str(path), artifact_path=artifact_path)
    return ArtifactRecord(f"{artifact_path.rstrip('/')}/{path.name}", digest, path.stat().st_size)


def reproduce_evaluation(run_id: str, output_dir: Path, tracking_uri: str | None = None) -> dict[str, Any]:
    """Download a Run's recorded evaluation artifacts through MLflow APIs only.

    The function intentionally does not load model weights itself.  A caller can use
    the returned paths to start a fresh process with its configured base revision and
    adapter loader, then attach the reproduced metrics to the same evidence bundle.
    """
    if not tracking_uri:
        raise ArtifactIntegrityError("tracking_uri is required for artifact round-trip")
    try:
        import mlflow
    except ImportError as exc:
        raise ArtifactIntegrityError("MLflow is required for artifact round-trip") from exc
    mlflow.set_tracking_uri(tracking_uri)
    client = mlflow.tracking.MlflowClient(tracking_uri=tracking_uri)
    run = client.get_run(run_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    return {"run_id": run_id, "status": "downloaded", "output_dir": str(output_dir), "tags": dict(run.data.tags), "params": dict(run.data.params)}


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
