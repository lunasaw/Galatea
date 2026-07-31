"""Driver-owned Ray Train and MLflow orchestration."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

import mlflow
import pandas as pd

from ray_cats_dogs.config import ProjectConfig
from ray_cats_dogs.data import (
    PreparedDataset,
    build_ray_datasets,
    prepare_dataset,
    validate_equal_shards,
)
from ray_cats_dogs.evaluate import evaluate_checkpoint
from ray_cats_dogs.runtime import (
    controller_pickle_by_value,
    ray_init_runtime_env,
    worker_runtime_env,
)
from ray_cats_dogs.tracking import (
    RayMlflowCallback,
    code_identity,
    idempotency_key,
    inspect_tracking,
    log_run_inputs,
    preflight_tracking,
    runs_for_identity,
    successful_run,
    verify_artifact_round_trip,
)
from ray_cats_dogs.worker import train_loop_per_worker


os.environ.setdefault("MLFLOW_RECORD_ENV_VARS_IN_MODEL_LOGGING", "false")


def config_plan(config: ProjectConfig) -> dict[str, Any]:
    return {
        "config": config.as_dict(),
        "config_digest": config.config_digest,
        "objective": {
            "metric": config.training.objective_metric,
            "mode": config.training.objective_mode,
            "uses_test_holdout": False,
        },
        "requested_resources": {
            "training_workers": config.ray.num_workers,
            "cpu_per_worker": config.ray.cpus_per_worker,
            "gpu_per_worker": 1 if config.ray.use_gpu else 0,
            "memory_per_worker_bytes": config.ray.memory_per_worker_bytes,
            "placement_strategy": config.ray.placement_strategy,
            "evaluation_cpu": config.ray.evaluation_cpus,
            "evaluation_gpu": config.ray.evaluation_gpus,
            "evaluation_memory_bytes": config.ray.evaluation_memory_bytes,
        },
    }


def read_only_plan(config: ProjectConfig) -> dict[str, Any]:
    tracking = inspect_tracking(config)
    dataset = prepare_dataset(config.data, config.run.seed)
    validate_equal_shards(dataset, config.ray.num_workers)
    code = code_identity(config)
    identity_key = idempotency_key(config, dataset, code["source_sha256"])
    prior_runs = runs_for_identity(tracking, identity_key) if tracking else []
    existing = successful_run(prior_runs)
    return {
        **config_plan(config),
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
    requirements = {
        "CPU": config.ray.num_workers * config.ray.cpus_per_worker,
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
    mlflow.log_dict(
        {
            "selection_metric": config.training.objective_metric,
            "selection_mode": config.training.objective_mode,
            "best_metric": state["best_metric"],
            "best_epoch": state["best_epoch"],
            "ray_checkpoint_uri": str(result.checkpoint.path),
            "best_model_sha256": local_model_digest,
            "mlflow_artifact_roundtrip_verified": True,
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
) -> dict[str, Any]:
    test_frame = dataset.split_frame("test")
    task = ray_module.remote(evaluate_checkpoint).options(
        num_cpus=config.ray.evaluation_cpus,
        num_gpus=config.ray.evaluation_gpus,
        memory=config.ray.evaluation_memory_bytes,
    )
    return ray_module.get(
        task.remote(
            checkpoint,
            test_frame[["relative_path", "label"]].to_dict(orient="records"),
            str(config.data.root / "PetImages"),
            config.image_size,
            config.training.per_worker_batch_size,
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


def _log_mlflow_model(checkpoint: Any, config: ProjectConfig) -> str:
    import mlflow.pytorch
    import numpy as np
    import torch
    from mlflow.models import infer_signature

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
            code_paths=[str(Path(__file__).resolve().parents[1])],
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
    return model_info.model_uri


def run_training(config: ProjectConfig, *, force: bool = False) -> dict[str, Any]:
    """Run one idempotent Ray Train workload and one authoritative MLflow Run."""

    tracking = preflight_tracking(config)
    dataset = prepare_dataset(config.data, config.run.seed)
    validate_equal_shards(dataset, config.ray.num_workers)
    code = code_identity(config)
    identity_key = idempotency_key(config, dataset, code["source_sha256"])
    prior_runs = runs_for_identity(tracking, identity_key)
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
            try:
                log_run_inputs(config, dataset, code, ray_job_id, identity_key)
                mlflow.log_dict(
                    available_resources, "ray/available-resources-at-start.json"
                )
                if "://" not in config.ray.storage_path:
                    Path(config.ray.storage_path).mkdir(parents=True, exist_ok=True)
                ray_datasets = build_ray_datasets(dataset, config.data)
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
                quality_passed = None
                test_metrics: dict[str, float] = {}
                if config.evaluation.evaluate_test:
                    phase = "final-test-evaluation"
                    evaluation = _run_test_evaluation(
                        ray, config, dataset, result.checkpoint
                    )
                    test_metrics = evaluation["metrics"]
                    quality_passed = _log_evaluation(evaluation, config, dataset)
                    mlflow.set_tag("test.evaluated", "true")

                model_uri = None
                if config.run.log_model:
                    phase = "model-logging"
                    model_uri = _log_mlflow_model(result.checkpoint, config)
                    mlflow.set_tag("model.uri", model_uri)

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
                    "training_started": True,
                }
            except BaseException as error:
                mlflow.set_tags(
                    {
                        "run.outcome": "failed",
                        "failure.phase": phase,
                        "failure.type": type(error).__name__,
                    }
                )
                raise
    finally:
        if initialized_here:
            ray.shutdown()
