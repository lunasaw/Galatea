"""Driver-owned Ray Train and MLflow orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import asdict
from contextlib import ExitStack
from types import SimpleNamespace
from pathlib import Path
from typing import Any

import mlflow
import pandas as pd
from mlflow.entities import SpanType

from ray_cats_dogs.config import ProjectConfig
from ray_cats_dogs.data import (
    PreparedDataset,
    build_ray_datasets,
    prepare_run_dataset,
    validate_equal_shards,
)
from ray_cats_dogs.evaluate import evaluate_checkpoint
from ray_cats_dogs.finalization import FINAL_PROTOCOL, recover_frozen_checkpoint, state_root
from ray_cats_dogs.integrity import build_integrity_report, integrity_digest
from ray_cats_dogs.runtime import (
    controller_pickle_by_value,
    ray_init_runtime_env,
    worker_runtime_env,
    require_governed_execution,
)
from ray_cats_dogs.tracking import (
    RayMlflowCallback,
    code_identity,
    idempotency_key,
    inspect_tracking,
    log_run_inputs,
    log_ray_task_timeline,
    preflight_tracking,
    runs_for_identity,
    successful_run,
    verify_artifact_round_trip,
)
from ray_cats_dogs.worker import train_loop_per_worker


os.environ.setdefault("MLFLOW_RECORD_ENV_VARS_IN_MODEL_LOGGING", "false")


def config_plan(
    config: ProjectConfig,
    dataset: PreparedDataset | None = None,
) -> dict[str, Any]:
    integrity = build_integrity_report(
        config,
        dataset,
        include_test=config.run.role == "champion",
    )
    return {
        "config": config.as_dict(),
        "integrity": {
            "preprocessing": integrity["preprocessing"],
            "migration": integrity["migration"],
            "preprocessing_digest": integrity["preprocessing_digest"],
            "migration_contamination_digest": integrity["migration_contamination_digest"],
            "integrity_digest": integrity["integrity_digest"],
        },
        "config_digest": config.config_digest,
        "objective": {
            "metric": config.training.objective_metric,
            "mode": config.training.objective_mode,
            "uses_test_holdout": False,
        },
        "artifacts": {
            "ray_task_timeline": config.ray.record_task_timeline,
        },
        "requested_resources": {
            "training_workers": config.ray.num_workers,
            "cpu_per_worker": config.ray.cpus_per_worker,
            "gpu_per_worker": 1 if config.ray.use_gpu else 0,
            # Galatea maps these fields to Ray Job Driver reservations.
            # GPUs are reserved separately by the Train placement group.
            "cpus": 1,
            "num_gpus": 0,
            "memory_gb": 1,
            "memory_per_worker_bytes": config.ray.memory_per_worker_bytes,
            "ray_data_decode_workers": config.ray.data_decode_workers,
            "ray_data_num_blocks": config.ray.data_num_blocks,
            "ray_data_decode_batch_size": config.ray.data_decode_batch_size,
            "ray_data_prefetch_batches": config.ray.data_prefetch_batches,
            "ray_data_cache_decoded": config.ray.data_cache_decoded,
            "placement_strategy": config.ray.placement_strategy,
            "evaluation_cpu": config.ray.evaluation_cpus,
            "evaluation_gpu": config.ray.evaluation_gpus,
            "evaluation_memory_bytes": config.ray.evaluation_memory_bytes,
        },
    }


def read_only_plan(config: ProjectConfig) -> dict[str, Any]:
    tracking = inspect_tracking(config)
    dataset = prepare_run_dataset(config)
    validate_equal_shards(dataset, config.ray.num_workers)
    code = code_identity(config)
    integrity = build_integrity_report(config, dataset, include_test=config.run.role == "champion")
    identity_key = idempotency_key(config, dataset, code["source_sha256"], integrity_digest(integrity))
    prior_runs = runs_for_identity(tracking, identity_key) if tracking else []
    existing = successful_run(prior_runs)
    return {
        **config_plan(config, dataset),
        "tracking": {
            "uri": config.mlflow.tracking_uri,
            "experiment": config.mlflow.experiment_name,
            "experiment_exists": tracking is not None,
            "experiment_id": tracking.experiment_id if tracking else None,
            "artifact_location": tracking.artifact_location if tracking else None,
            "will_create_on_training": tracking is None,
        },
        "dataset": {
            "version": dataset.dataset_version,
            "content_sha256": dataset.content_digest,
            "split_sha256": dataset.split_digest,
            "manifest_path": str(dataset.manifest_path),
            "split_counts": dataset.profile["split_counts"],
        },
        "code": code,
        "idempotency_key": identity_key,
        "prior_attempts": len(prior_runs),
        "successful_run_id": existing.info.run_id if existing else None,
        "will_train": existing is None,
    }


def _ray_job_id(ray_module: Any) -> str:
    job_id = ray_module.get_runtime_context().get_job_id()
    return job_id.hex() if hasattr(job_id, "hex") else str(job_id)


def _check_cluster_resources(ray_module: Any, config: ProjectConfig) -> dict[str, float]:
    available = {
        name: float(value) for name, value in ray_module.available_resources().items()
    }
    training_cpus = config.ray.num_workers * config.ray.cpus_per_worker
    data_cpus = float(config.ray.data_decode_workers)
    requirements = {
        "CPU": (
            max(training_cpus, data_cpus)
            if config.ray.data_cache_decoded
            else training_cpus + data_cpus
        ),
        "memory": config.ray.num_workers * config.ray.memory_per_worker_bytes,
    }
    if config.ray.use_gpu:
        requirements["GPU"] = float(config.ray.num_workers)
    shortages = {
        name: {"required": required, "available": available.get(name, 0.0)}
        for name, required in requirements.items()
        if available.get(name, 0.0) < required
    }
    if shortages:
        raise RuntimeError(f"Insufficient available Ray resources: {shortages}")
    return available


def _worker_loop_config(
    config: ProjectConfig,
    run_id: str,
    identity_key: str,
    dataset: PreparedDataset,
) -> dict[str, Any]:
    training_examples_per_worker = (
        len(dataset.split_frame("training")) // config.ray.num_workers
    )
    validation_examples_per_worker = (
        len(dataset.split_frame("validation")) // config.ray.num_workers
    )
    batch_size = config.training.per_worker_batch_size
    return {
        "seed": config.run.seed,
        "image_size": list(config.image_size),
        "model": asdict(config.model),
        "training": asdict(config.training),
        "objective_metric": config.training.objective_metric,
        "objective_mode": config.training.objective_mode,
        "mlflow_run_id": run_id,
        "idempotency_key": identity_key,
        "training_batches_per_worker": (
            training_examples_per_worker + batch_size - 1
        )
        // batch_size,
        "validation_batches_per_worker": (
            validation_examples_per_worker + batch_size - 1
        )
        // batch_size,
        "data_prefetch_batches": config.ray.data_prefetch_batches,
    }


def _log_checkpoint_and_selection(
    result: Any,
    config: ProjectConfig,
    run_id: str,
) -> None:
    if result.checkpoint is None:
        raise RuntimeError("Ray Train completed without a recoverable checkpoint")
    with result.checkpoint.as_directory() as checkpoint_directory:
        mlflow.log_artifacts(checkpoint_directory, artifact_path="checkpoints/best")
        best_model_path = Path(checkpoint_directory) / "best-model.pt"
        local_model_digest = hashlib.sha256(best_model_path.read_bytes()).hexdigest()
        state = json.loads(
            (Path(checkpoint_directory) / "training-state.json").read_text(
                encoding="utf-8"
            )
        )
    with tempfile.TemporaryDirectory(
        prefix="ray-cats-dogs-checkpoint-check-"
    ) as verification_directory:
        downloaded_model = mlflow.artifacts.download_artifacts(
            run_id=run_id,
            artifact_path="checkpoints/best/best-model.pt",
            dst_path=verification_directory,
        )
        downloaded_digest = hashlib.sha256(
            Path(downloaded_model).read_bytes()
        ).hexdigest()
        if downloaded_digest != local_model_digest:
            raise RuntimeError("MLflow checkpoint Artifact failed SHA-256 verification")
        # Load the retrieved checkpoint in a fresh CPU process before accepting
        # the artifact. This forward-only fixture never reads holdout images.
        import subprocess
        import sys

        subprocess.run(
            [
                sys.executable,
                str(config.project_root / "scripts" / "verify_checkpoint.py"),
                downloaded_model,
            ],
            env={**os.environ, "CUDA_VISIBLE_DEVICES": ""},
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    if downloaded_digest != local_model_digest:
        raise RuntimeError("MLflow checkpoint Artifact failed SHA-256 verification")
    mlflow.log_dict(
        {
            "selection_metric": config.training.objective_metric,
            "selection_mode": config.training.objective_mode,
            "best_metric": state["best_metric"],
            "best_epoch": state["best_epoch"],
            "ray_checkpoint_uri": str(result.checkpoint.path),
            "best_model_sha256": local_model_digest,
            "mlflow_artifact_roundtrip_verified": True,
            "fresh_process_load_verified": True,
            "test_evaluated_during_selection": False,
        },
        "reports/model-selection.json",
    )
    mlflow.log_metrics(
        {
            "best_objective": float(state["best_metric"]),
            "best_epoch": float(state["best_epoch"]),
        }
    )


def _run_test_evaluation(
    ray_module: Any,
    config: ProjectConfig,
    dataset: PreparedDataset,
    checkpoint: Any,
    freeze: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    test_frame = dataset.split_frame("test")
    task = ray_module.remote(evaluate_checkpoint).options(
        max_retries=0,
        runtime_env=worker_runtime_env(ray_module),
        num_cpus=config.ray.evaluation_cpus,
        num_gpus=config.ray.evaluation_gpus,
        memory=config.ray.evaluation_memory_bytes,
    )
    return ray_module.get(
        task.remote(
            checkpoint,
            test_frame[["relative_path", "label", "sha256"]].to_dict(orient="records"),
            str(config.data.root / "PetImages"),
            config.image_size,
            config.training.per_worker_batch_size,
            str(state_root(config)),
            {
                "checkpoint_sha256": freeze["checkpoint_sha256"],
                "split_sha256": freeze["split_sha256"],
                "protocol": FINAL_PROTOCOL,
                "submission_id": freeze["submission_id"],
                "execution_identity": freeze["execution_identity"],
                "candidate_run_id": freeze["candidate_run_id"],
                "candidate_evidence_digest": freeze["candidate_evidence_digest"],
                "readiness_digest": freeze["readiness_digest"],
                "release_id": freeze["release_id"],
                "run_id": run_id,
            },
        )
    )


def _log_evaluation(
    evaluation: dict[str, Any],
    config: ProjectConfig,
    dataset: PreparedDataset,
) -> bool:
    metrics = {name: float(value) for name, value in evaluation["metrics"].items()}
    predictions = pd.DataFrame(evaluation["predictions"])
    prediction_payload = predictions.to_csv(index=False, lineterminator="\n").encode()
    prediction_digest = hashlib.sha256(prediction_payload).hexdigest()
    quality_passed = metrics["test_accuracy"] >= config.evaluation.minimum_test_accuracy
    mlflow.log_dict(evaluation["test_once_claim"], "reports/test-once-claim.json")
    mlflow.log_metrics(metrics)
    mlflow.log_table(predictions, "outputs/test-predictions.json")
    mlflow.log_dict(
        {
            "dataset_version": dataset.dataset_version,
            "prediction_sha256": prediction_digest,
            "metrics": metrics,
            "classification_report": evaluation["classification_report"],
            "confusion_matrix": evaluation["confusion_matrix"],
            "quality_gate": {
                "minimum_test_accuracy": config.evaluation.minimum_test_accuracy,
                "passed": quality_passed,
            },
        },
        "reports/final-test-evaluation.json",
    )
    mlflow.set_tags(
        {
            "quality_gate.passed": str(quality_passed).lower(),
            "output.prediction_sha256": prediction_digest,
        }
    )
    return quality_passed


def _log_mlflow_model(
    checkpoint: Any,
    config: ProjectConfig,
    run_id: str,
) -> str:
    import mlflow.pytorch
    import numpy as np
    import torch
    from mlflow.models import infer_signature

    from ray_cats_dogs.model_artifacts import model_code_paths, verify_packaged_model
    from ray_cats_dogs.models import build_model

    with checkpoint.as_directory() as checkpoint_directory:
        state = torch.load(
            Path(checkpoint_directory) / "best-model.pt",
            map_location="cpu",
            weights_only=False,
        )
        model = build_model(
            state["model_config"],
            state["training_config"],
            tuple(state["image_size"]),
            int(state["seed"]),
        )
        model.load_state_dict(state["model_state_dict"])
        model.eval()
        input_example = np.zeros((2, 3, *config.image_size), dtype="float32")
        with torch.no_grad():
            output_example = model(torch.from_numpy(input_example)).numpy()
        model_info = mlflow.pytorch.log_model(
            model,
            name="model",
            signature=infer_signature(input_example, output_example),
            input_example=input_example,
            code_paths=model_code_paths(),
            serialization_format="pickle",
            metadata={
                "class_names": ["Cat", "Dog"],
                "preprocessing_version": config.data.preprocessing_version,
                "input_range": "NCHW float32 in [0,1]",
                "output_semantics": "raw class logits; apply softmax for probabilities",
                "cuda_runtime": torch.version.cuda,
            },
        )
    with tempfile.TemporaryDirectory(
        prefix="ray-cats-dogs-model-check-"
    ) as verification_directory:
        downloaded_model = mlflow.artifacts.download_artifacts(
            artifact_uri=model_info.model_uri,
            dst_path=verification_directory,
        )
        if not (Path(downloaded_model) / "MLmodel").is_file():
            raise RuntimeError("MLflow Logged Model is missing its MLmodel descriptor")
        verification = verify_packaged_model(Path(downloaded_model), config.image_size)
        mlflow.log_dict(verification, "verification/model-fresh-process-load.json")
        mlflow.log_artifacts(downloaded_model, artifact_path="model")
    with tempfile.TemporaryDirectory(
        prefix="ray-cats-dogs-run-model-check-"
    ) as verification_directory:
        downloaded_descriptor = mlflow.artifacts.download_artifacts(
            run_id=run_id,
            artifact_path="model/MLmodel",
            dst_path=verification_directory,
        )
        if not Path(downloaded_descriptor).is_file():
            raise RuntimeError("MLflow Run Artifact is missing model/MLmodel")
    return f"runs:/{run_id}/model"


@mlflow.trace(name="ray_cats_dogs.training", span_type=SpanType.CHAIN)
def run_training(config: ProjectConfig, *, force: bool = False) -> dict[str, Any]:
    """Run one idempotent Ray Train workload and one authoritative MLflow Run."""

    mlflow.update_current_trace(
        tags={
            "project": config.project_name,
            "run.role": config.run.role,
            "execution.type": "ray-train",
        },
        metadata={
            "mlflow.trace.session": config.project_name,
            "training.objective_metric": config.training.objective_metric,
            "training.objective_mode": config.training.objective_mode,
            "ray.num_workers": str(config.ray.num_workers),
        },
    )
    provenance = require_governed_execution(config.project_name, config.run.promotable)
    tracking = preflight_tracking(config)
    dataset = prepare_run_dataset(config)
    validate_equal_shards(dataset, config.ray.num_workers)
    code = code_identity(config)
    integrity = build_integrity_report(config, dataset, include_test=config.run.role == "champion")
    identity_key = idempotency_key(config, dataset, code["source_sha256"], integrity_digest(integrity))
    if config.run.role == "champion":
        if not config.evaluation.evaluate_test or not provenance.get("candidate_run_id"):
            raise RuntimeError("Champion requires a frozen Trial and final evaluation")
        identity_key = hashlib.sha256(
            (identity_key + provenance["galatea.execution.identity"]).encode()
        ).hexdigest()
    prior_runs = runs_for_identity(
        tracking,
        identity_key,
        execution_identity=provenance["galatea.execution.identity"],
    )
    existing = successful_run(prior_runs)
    if existing is not None and not force:
        return {
            "status": "already-succeeded",
            "run_id": existing.info.run_id,
            "artifact_uri": existing.info.artifact_uri,
            "idempotency_key": identity_key,
            "training_started": False,
        }
    running = next(
        (run for run in prior_runs if run.info.status == "RUNNING"), None
    )
    if running is not None and not force:
        return {
            "status": "already-running",
            "run_id": running.info.run_id,
            "artifact_uri": running.info.artifact_uri,
            "idempotency_key": identity_key,
            "training_started": False,
        }

    import ray
    from ray.train import CheckpointConfig, DataConfig, FailureConfig, RunConfig, ScalingConfig
    from ray.train.torch import TorchTrainer

    initialized_here = not ray.is_initialized()
    if initialized_here:
        ray.init(
            address=config.ray.address,
            runtime_env=ray_init_runtime_env(config.project_root),
            ignore_reinit_error=True,
        )
    cleanup = ExitStack()
    try:
        available_resources = _check_cluster_resources(ray, config)
        train_worker_runtime_env = worker_runtime_env(ray)
        ray_job_id = _ray_job_id(ray)
        attempt = len(prior_runs) + 1
        run_name = (
            f"{config.run.name_prefix}-{config.run.role}-"
            f"{identity_key[:8]}-a{attempt:02d}"
        )
        tags = {
            "project": config.project_name,
            "run.role": config.run.role,
            "run.outcome": "running",
            "lifecycle.stage": "development",
            "idempotency_key": identity_key,
            "dataset_version": dataset.dataset_version,
            "code.git_commit": code["git_commit"],
            "code.git_dirty": code["git_dirty"],
            "ray.job_id": ray_job_id,
            "execution.type": "ray-train",
            "test.evaluated": "false",
            "registry.promotion": "manual-only",
            "ray.task_timeline.requested": str(
                config.ray.record_task_timeline
            ).lower(),
            **provenance,
        }
        phase = "run-setup"
        with mlflow.start_run(
            experiment_id=tracking.experiment_id,
            run_name=run_name,
            tags=tags,
            description=(
                "Ray Train PyTorch CUDA 13 cats-vs-dogs workload with deterministic "
                "data lineage and driver-owned MLflow tracking."
            ),
            log_system_metrics=True,
        ) as active_run:
            run_id = active_run.info.run_id
            print(
                json.dumps(
                    {
                        "event": "run-started",
                        "mlflow_run_id": run_id,
                        "ray_job_id": ray_job_id,
                        "idempotency_key": identity_key,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            timeline_metadata = None
            try:
                log_run_inputs(config, dataset, code, ray_job_id, identity_key, integrity)
                mlflow.log_dict(
                    available_resources, "ray/available-resources-at-start.json"
                )
                freeze = None
                if config.run.role == "champion":
                    phase = "frozen-checkpoint-validation"
                    destination = Path(cleanup.enter_context(tempfile.TemporaryDirectory(
                        prefix="ray-cats-dogs-frozen-trial-"
                    )))
                    checkpoint, freeze = recover_frozen_checkpoint(
                        config, dataset, provenance, destination
                    )
                    result = SimpleNamespace(checkpoint=checkpoint)
                    mlflow.log_dict(freeze, "reports/candidate-freeze.json")
                    mlflow.set_tags({
                        "candidate.run_id": freeze["candidate_run_id"],
                        "candidate.checkpoint_sha256": freeze["checkpoint_sha256"],
                        "candidate.evidence_digest": freeze["candidate_evidence_digest"],
                        "training.started": "false",
                        "execution.type": "ray-frozen-checkpoint-evaluation",
                    })
                else:
                    if "://" not in config.ray.storage_path:
                        Path(config.ray.storage_path).mkdir(parents=True, exist_ok=True)
                    phase = "ray-data-preparation"
                    data_started_at = time.perf_counter()
                    ray_datasets = build_ray_datasets(dataset, config)
                    data_preparation_seconds = time.perf_counter() - data_started_at
                    mlflow.log_metric(
                        "data_preparation_seconds", data_preparation_seconds
                    )
                    mlflow.log_dict(
                        {
                            "decode_workers": config.ray.data_decode_workers,
                            "configured_blocks": config.ray.data_num_blocks,
                            "decode_batch_size": config.ray.data_decode_batch_size,
                            "prefetch_batches": config.ray.data_prefetch_batches,
                            "cache_decoded": config.ray.data_cache_decoded,
                            "preparation_seconds": data_preparation_seconds,
                        },
                        "ray/data-pipeline.json",
                    )
                    phase = "ray-training"
                    with controller_pickle_by_value():
                        trainer = TorchTrainer(
                            train_loop_per_worker=train_loop_per_worker,
                            train_loop_config=_worker_loop_config(
                                config, run_id, identity_key, dataset
                            ),
                            scaling_config=ScalingConfig(
                                num_workers=config.ray.num_workers,
                                use_gpu=config.ray.use_gpu,
                                resources_per_worker={
                                    "CPU": config.ray.cpus_per_worker,
                                    "memory": config.ray.memory_per_worker_bytes,
                                },
                                placement_strategy=config.ray.placement_strategy,
                            ),
                            dataset_config=DataConfig(
                                datasets_to_split=["training", "validation"]
                            ),
                            datasets=ray_datasets,
                            run_config=RunConfig(
                                name=f"{run_name}-ray",
                                storage_path=str(config.ray.storage_path),
                                failure_config=FailureConfig(
                                    max_failures=config.ray.max_failures
                                ),
                                checkpoint_config=CheckpointConfig(num_to_keep=1),
                                callbacks=[
                                    RayMlflowCallback(
                                        config.mlflow.tracking_uri, run_id
                                    )
                                ],
                                worker_runtime_env=train_worker_runtime_env,
                            ),
                        )
                        result = trainer.fit()

                phase = "checkpoint-logging"
                _log_checkpoint_and_selection(result, config, run_id)
                model_uri = None
                if config.run.log_model:
                    phase = "model-logging"
                    model_uri = _log_mlflow_model(result.checkpoint, config, run_id)
                    mlflow.set_tag("model.uri", model_uri)


                quality_passed = None
                test_metrics: dict[str, float] = {}
                if config.evaluation.evaluate_test:
                    phase = "final-test-evaluation"
                    evaluation = _run_test_evaluation(
                        ray, config, dataset, result.checkpoint, freeze, run_id
                    )
                    test_metrics = evaluation["metrics"]
                    quality_passed = _log_evaluation(evaluation, config, dataset)
                    mlflow.set_tag("test.evaluated", "true")


                if config.ray.record_task_timeline:
                    phase = "ray-task-timeline-logging"
                    timeline_metadata = log_ray_task_timeline(run_id, ray_job_id)

                phase = "artifact-verification"
                with tempfile.TemporaryDirectory(
                    prefix="ray-cats-dogs-artifact-check-"
                ) as verification_directory:
                    verify_artifact_round_trip(
                        run_id, Path(verification_directory)
                    )
                outcome_tags = {
                    "run.outcome": "succeeded",
                    "artifact.roundtrip_verified": "true",
                    "ray.checkpoint_uri": str(result.checkpoint.path),
                }
                if quality_passed is not None:
                    outcome_tags["quality_gate.passed"] = str(
                        quality_passed
                    ).lower()
                mlflow.set_tags(outcome_tags)
                mlflow.flush_async_logging()
                return {
                    "status": "succeeded",
                    "run_id": run_id,
                    "artifact_uri": active_run.info.artifact_uri,
                    "model_uri": model_uri,
                    "ray_job_id": ray_job_id,
                    "ray_checkpoint_uri": str(result.checkpoint.path),
                    "dataset_version": dataset.dataset_version,
                    "idempotency_key": identity_key,
                    "test_metrics": test_metrics,
                    "quality_gate_passed": quality_passed,
                    "ray_task_timeline": timeline_metadata,
                    "training_started": config.run.role != "champion",
                    "candidate_run_id": freeze["candidate_run_id"] if freeze else None,
                }
            except BaseException as error:
                failure_tags = {
                    "run.outcome": "failed",
                    "failure.phase": phase,
                    "failure.type": type(error).__name__,
                }
                if phase == "ray-task-timeline-logging":
                    failure_tags["ray.task_timeline.logged"] = "false"
                if (
                    isinstance(error, Exception)
                    and config.ray.record_task_timeline
                    and timeline_metadata is None
                    and phase != "ray-task-timeline-logging"
                ):
                    try:
                        log_ray_task_timeline(run_id, ray_job_id)
                    except Exception as timeline_error:
                        failure_tags.update(
                            {
                                "ray.task_timeline.logged": "false",
                                "ray.task_timeline.failure_type": type(
                                    timeline_error
                                ).__name__,
                            }
                        )
                mlflow.set_tags(failure_tags)
                raise
    finally:
        cleanup.close()
        if initialized_here:
            ray.shutdown()
