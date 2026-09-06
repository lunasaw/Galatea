"""Small MLflow API-only tracking helpers."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
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


def set_training_tags(context: RunContext, tags: dict[str, Any], tracking_uri: str | None = None) -> None:
    import mlflow

    tracking_uri = tracking_uri or __import__("os").environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
    client = mlflow.tracking.MlflowClient(tracking_uri=tracking_uri)
    for key, value in tags.items():
        client.set_tag(context.run_id, key, str(value).lower() if isinstance(value, bool) else str(value))


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
    tags = {
        "project": "llm-lora-playground",
        "task": str(manifest.get("task", "synthetic_sft_lora")),
        "run_kind": str(manifest.get("run_kind", "training")),
        "run.role": str(manifest.get("role", "trial")),
        "run.outcome": "running",
        "config_digest": str(manifest.get("config_digest", "")),
        "dataset_manifest_digest": str(manifest.get("dataset_manifest_digest", "")),
        "owner_bulk_approved": str(bool(manifest.get("owner_bulk_approved", False))).lower(),
        "formal_training_eligible": str(bool(manifest.get("formal_training_eligible", True))).lower(),
    }
    for key in (
        "ray_job_id", "ray_submission_id", "execution_mode", "attempt_id",
        "parent_attempt_id", "quality_evidence_status", "human_review_completed",
        "candidate_run_id", "candidate_evidence_digest", "test_evaluation_id",
        "galatea.execution.identity", "galatea.project", "galatea.release.id",
        "galatea.submission.id", "galatea.readiness.digest", "galatea.execution.mode",
        "galatea.promotable", "artifact.roundtrip_verified", "model.uri",
    ):
        if manifest.get(key) is not None:
            tags[key] = str(manifest[key])
    run = client.create_run(
        experiment_id=str(experiment_id),
        tags=tags,
    )
    client.log_batch(
        run.info.run_id,
        params=[
            mlflow.entities.Param("manifest_digest", digest),
            mlflow.entities.Param("objective_metric", str(manifest.get("objective_metric", "validation_loss"))),
            mlflow.entities.Param("objective_mode", str(manifest.get("objective_mode", "min"))),
            *([mlflow.entities.Param("dataset_id", str(manifest["dataset_id"]))] if manifest.get("dataset_id") else []),
            *([mlflow.entities.Param("execution_mode", str(manifest["execution_mode"]))] if manifest.get("execution_mode") else []),
            *([mlflow.entities.Param("ray_job_id", str(manifest["ray_job_id"]))] if manifest.get("ray_job_id") else []),
            *([mlflow.entities.Param("ray_submission_id", str(manifest["ray_submission_id"]))] if manifest.get("ray_submission_id") else []),
            *([mlflow.entities.Param("code_revision", str(manifest["code_revision"]))] if manifest.get("code_revision") else []),
            *([mlflow.entities.Param("environment_digest", str(manifest["environment_digest"]))] if manifest.get("environment_digest") else []),
            *([mlflow.entities.Param("data.content_sha256", str(manifest["dataset_manifest_digest"]))] if manifest.get("dataset_manifest_digest") else []),
            *([mlflow.entities.Param("data.split_sha256", str(manifest["split_digest"]))] if manifest.get("split_digest") else []),
            *([mlflow.entities.Param("data.preprocessing_version", str(manifest["preprocessing_version"]))] if manifest.get("preprocessing_version") else []),
            *([mlflow.entities.Param("evaluation.protocol_version", str(manifest["evaluation_protocol_version"]))] if manifest.get("evaluation_protocol_version") else []),
            *([mlflow.entities.Param("training.epochs", str(manifest["epochs"]))] if manifest.get("epochs") is not None else []),
            *([mlflow.entities.Param("training.max_steps", str(manifest["max_steps"]))] if manifest.get("max_steps") is not None else []),
            *([mlflow.entities.Param("training.per_device_train_batch_size", str(manifest["per_device_train_batch_size"]))] if manifest.get("per_device_train_batch_size") is not None else []),
            *([mlflow.entities.Param("training.gradient_accumulation_steps", str(manifest["gradient_accumulation_steps"]))] if manifest.get("gradient_accumulation_steps") is not None else []),
            *([mlflow.entities.Param("training.learning_rate", str(manifest["learning_rate"]))] if manifest.get("learning_rate") is not None else []),
            *([mlflow.entities.Param("training.warmup_ratio", str(manifest["warmup_ratio"]))] if manifest.get("warmup_ratio") is not None else []),
            *([mlflow.entities.Param("training.scheduler", str(manifest["scheduler"]))] if manifest.get("scheduler") else []),
            *([mlflow.entities.Param("training.optimizer", str(manifest["optimizer"]))] if manifest.get("optimizer") else []),
            *([mlflow.entities.Param("training.seed", str(manifest["seed"]))] if manifest.get("seed") is not None else []),
            *([mlflow.entities.Param("lora.rank", str(manifest["lora_rank"]))] if manifest.get("lora_rank") is not None else []),
            *([mlflow.entities.Param("lora.alpha", str(manifest["lora_alpha"]))] if manifest.get("lora_alpha") is not None else []),
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
    timestamp = int(time.time() * 1000)
    client.log_batch(context.run_id, metrics=[mlflow.entities.Metric(key=k, value=float(v), timestamp=timestamp, step=step) for k, v in metrics.items()])


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


def log_artifact_directory(context: RunContext, path: Path, artifact_path: str, tracking_uri: str | None = None) -> None:
    import mlflow

    if not path.is_dir():
        raise FileNotFoundError(path)
    tracking_uri = tracking_uri or __import__("os").environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
    client = mlflow.tracking.MlflowClient(tracking_uri=tracking_uri)
    client.log_artifacts(context.run_id, str(path), artifact_path=artifact_path)


def log_json_artifact(context: RunContext, payload: dict[str, Any], artifact_path: str, tracking_uri: str | None = None) -> ArtifactRecord:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="llm-lora-json-") as directory:
        path = Path(directory) / Path(artifact_path).name
        path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        return log_artifact_with_sha256(context, path, str(Path(artifact_path).parent), tracking_uri)


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
    timestamp = int(time.time() * 1000)
    client.log_batch(context.run_id, metrics=[mlflow.entities.Metric(key=k, value=float(v), timestamp=timestamp, step=0) for k, v in metrics.items()])


def verify_artifact_roundtrip(client: Any, run_id: str, artifact_path: str, expected_sha256: str) -> None:
    local = client.download_artifacts(run_id, artifact_path)
    digest = hashlib.sha256(Path(local).read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise ArtifactIntegrityError(f"artifact digest mismatch for {artifact_path}: expected {expected_sha256}, got {digest}")


def verify_adapter_roundtrip(
    client: Any,
    run_id: str,
    model_path: Path,
    expected_config_sha256: str,
    expected_weights_sha256: str,
) -> None:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="llm-lora-roundtrip-") as directory:
        config_path = Path(client.download_artifacts(run_id, "model/adapter_config.json", dst_path=directory))
        weights_path = Path(client.download_artifacts(run_id, "model/adapter_model.safetensors", dst_path=directory))
        if hashlib.sha256(config_path.read_bytes()).hexdigest() != expected_config_sha256:
            raise ArtifactIntegrityError("adapter config round-trip digest mismatch")
        if hashlib.sha256(weights_path.read_bytes()).hexdigest() != expected_weights_sha256:
            raise ArtifactIntegrityError("adapter weights round-trip digest mismatch")
        _load_adapter_in_fresh_process(model_path, config_path.parent)


def _load_adapter_in_fresh_process(model_path: Path, adapter_dir: Path) -> None:
    program = """
import sys
import torch
from peft import PeftModel
from transformers import Qwen3_5ForConditionalGeneration

base = Qwen3_5ForConditionalGeneration.from_pretrained(
    sys.argv[1], local_files_only=True, dtype=torch.float32, device_map=None
)
loaded = PeftModel.from_pretrained(base, sys.argv[2], is_trainable=False)
if loaded is None:
    raise RuntimeError("adapter loader returned no model")
print("fresh-process-adapter-load-ok")
"""
    environment = dict(os.environ)
    environment["CUDA_VISIBLE_DEVICES"] = ""
    try:
        completed = subprocess.run(
            [sys.executable, "-c", program, str(model_path), str(adapter_dir)],
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ArtifactIntegrityError(f"fresh-process adapter load could not run: {type(exc).__name__}: {exc}") from exc
    if completed.returncode != 0 or "fresh-process-adapter-load-ok" not in completed.stdout:
        detail = (completed.stderr or completed.stdout).strip()[-1000:]
        raise ArtifactIntegrityError(
            f"fresh-process adapter round-trip load failed with exit code {completed.returncode}: {detail}"
        )


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
