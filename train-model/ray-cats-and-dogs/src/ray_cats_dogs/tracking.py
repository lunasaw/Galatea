"""MLflow ownership, lineage, and Ray controller metric logging."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mlflow
import mlflow.data
from mlflow import MlflowClient
from mlflow.entities import Metric
from ray._private.profiling import chrome_tracing_dump
from ray.train import UserCallback
from ray.util.state import list_tasks

from ray_cats_dogs.config import ProjectConfig
from ray_cats_dogs.data import PreparedDataset


@dataclass(frozen=True)
class TrackingContext:
    experiment_id: str
    experiment_name: str
    artifact_location: str


RAY_TASK_TIMELINE_ARTIFACT_PATH = "ray/task-timeline.json"
RAY_TASK_TIMELINE_METADATA_PATH = "ray/task-timeline-metadata.json"
RAY_TASK_TIMELINE_LIMIT = 10_000


def inspect_tracking(config: ProjectConfig) -> TrackingContext | None:
    """Inspect an existing Experiment without creating server-side state."""

    mlflow.set_tracking_uri(config.mlflow.tracking_uri)
    client = MlflowClient()
    try:
        client.search_experiments(max_results=1)
        experiment = client.get_experiment_by_name(config.mlflow.experiment_name)
    except Exception as error:
        raise ConnectionError(
            f"MLflow is unavailable at {config.mlflow.tracking_uri}"
        ) from error
    if experiment is None:
        return None
    artifact_scheme = experiment.artifact_location.split(":", maxsplit=1)[0]
    if config.mlflow.require_remote_artifacts and artifact_scheme not in {
        "mlflow-artifacts",
        "s3",
    }:
        raise RuntimeError(
            "MLflow experiment must use the Artifact proxy or S3/MinIO; found "
            f"{experiment.artifact_location}"
        )
    return TrackingContext(
        experiment_id=experiment.experiment_id,
        experiment_name=experiment.name,
        artifact_location=experiment.artifact_location,
    )


def preflight_tracking(config: ProjectConfig) -> TrackingContext:
    """Verify Tracking/Artifacts and create the configured Experiment if needed."""

    tracking = inspect_tracking(config)
    if tracking is not None:
        return tracking
    experiment = mlflow.set_experiment(config.mlflow.experiment_name)
    artifact_scheme = experiment.artifact_location.split(":", maxsplit=1)[0]
    if config.mlflow.require_remote_artifacts and artifact_scheme not in {
        "mlflow-artifacts",
        "s3",
    }:
        raise RuntimeError(
            "MLflow experiment must use the Artifact proxy or S3/MinIO; found "
            f"{experiment.artifact_location}"
        )
    return TrackingContext(
        experiment_id=experiment.experiment_id,
        experiment_name=experiment.name,
        artifact_location=experiment.artifact_location,
    )


def _command_output(arguments: list[str], cwd: Path) -> str | None:
    result = subprocess.run(
        arguments,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def code_identity(config: ProjectConfig) -> dict[str, str]:
    """Hash project-owned executable inputs, including untracked new source."""

    digest = hashlib.sha256()
    roots = (config.project_root / "src", config.project_root / "scripts")
    paths = [
        path
        for root in roots
        if root.exists()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    paths.extend(
        path
        for path in (
            config.project_root / "conda.yaml",
            config.project_root / "pyproject.toml",
        )
        if path.is_file()
    )
    for path in sorted(paths):
        digest.update(path.relative_to(config.project_root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    repo_root = config.project_root.parents[1]
    git_commit = _command_output(["git", "rev-parse", "HEAD"], repo_root)
    git_status = _command_output(["git", "status", "--porcelain"], repo_root)
    return {
        "source_sha256": digest.hexdigest(),
        "git_commit": git_commit or "uncommitted",
        "git_dirty": str(bool(git_status)).lower(),
    }


def idempotency_key(
    config: ProjectConfig,
    dataset: PreparedDataset,
    source_digest: str,
    integrity_digest_value: str,
) -> str:
    payload = "|".join(
        (
            config.project_name,
            dataset.content_digest,
            dataset.split_digest,
            source_digest,
            integrity_digest_value,
            config.config_digest,
            str(config.run.seed),
            config.run.role,
        )
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def runs_for_identity(
    tracking: TrackingContext,
    identity_key: str,
) -> list[Any]:
    client = MlflowClient()
    return list(
        client.search_runs(
            [tracking.experiment_id],
            filter_string=f"tags.idempotency_key = '{identity_key}'",
            order_by=["attributes.start_time DESC"],
        )
    )


def successful_run(runs: list[Any]) -> Any | None:
    return next(
        (
            run
            for run in runs
            if run.info.status == "FINISHED"
            and run.data.tags.get("run.outcome") == "succeeded"
            and run.data.tags.get("artifact.roundtrip_verified") == "true"
        ),
        None,
    )


def _flatten(prefix: str, value: Any, output: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten(f"{prefix}.{key}" if prefix else key, item, output)
    elif isinstance(value, (list, tuple)):
        output[prefix] = json.dumps(value, separators=(",", ":"))
    elif value is None:
        output[prefix] = "null"
    else:
        output[prefix] = value


def _split_digest(frame: Any) -> str:
    payload = frame[
        ["relative_path", "label", "bytes", "sha256"]
    ].to_csv(index=False, lineterminator="\n")
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


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


def log_run_inputs(
    config: ProjectConfig,
    dataset: PreparedDataset,
    code: dict[str, str],
    ray_job_id: str,
    identity_key: str,
    integrity: dict[str, Any] | None = None,
) -> None:
    parameters: dict[str, Any] = {}
    _flatten("", config.as_dict(), parameters)
    integrity_artifacts: dict[str, tuple[str, str, str]] = {}
    if integrity:
        preprocessing_status = str(integrity["preprocessing"]["parity"]["status"])
        migration_status = str(integrity["migration"]["contamination"]["status"])
        preprocessing_text, preprocessing_artifact_digest = _integrity_artifact(
            "preprocessing", config.run.role, preprocessing_status, integrity["preprocessing"]
        )
        migration_text, migration_artifact_digest = _integrity_artifact(
            "migration", config.run.role, migration_status, integrity["migration"]["contamination"]
        )
        integrity_artifacts = {
            "preprocessing": (preprocessing_text, preprocessing_artifact_digest, preprocessing_status),
            "migration": (migration_text, migration_artifact_digest, migration_status),
        }
    parameters.update(
        {
            "data.dataset_version": dataset.dataset_version,
            "data.content_sha256": dataset.content_digest,
            "data.split_sha256": dataset.split_digest,
            "code.source_sha256": code["source_sha256"],
            "code.git_commit": code["git_commit"],
            "ray.job_id": ray_job_id,
            "run.idempotency_key": identity_key,
            "integrity.digest": integrity["integrity_digest"] if integrity else "null",
            "integrity.preprocessing_artifact_digest": integrity_artifacts.get("preprocessing", ("", "null", "unknown"))[1],
            "integrity.migration_artifact_digest": integrity_artifacts.get("migration", ("", "null", "unknown"))[1],
        }
    )
    mlflow.log_params(parameters)
    if integrity:
        mlflow.set_tags({
            "integrity.preprocessing.status": integrity_artifacts["preprocessing"][2],
            "integrity.migration.status": integrity_artifacts["migration"][2],
        })
        mlflow.log_text(integrity_artifacts["preprocessing"][0], "reports/preprocessing-parity.json")
        mlflow.log_text(integrity_artifacts["migration"][0], "reports/migration-contamination.json")
    mlflow.log_dict(config.as_dict(), "config/resolved-config.json")
    mlflow.log_dict(dataset.profile, "data/dataset-profile.json")
    mlflow.log_artifact(str(dataset.manifest_path), artifact_path="data")
    mlflow.log_dict(
        {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "host": socket.gethostname(),
            "packages": {
                package: importlib.metadata.version(package)
                for package in (
                    "ray",
                    "mlflow",
                    "torch",
                    "torchvision",
                    "numpy",
                    "pandas",
                    "tqdm",
                )
            },
        },
        "environment/runtime.json",
    )
    for path in sorted((config.project_root / "src").rglob("*.py")):
        relative_parent = path.relative_to(config.project_root).parent.as_posix()
        mlflow.log_artifact(str(path), artifact_path=f"source/{relative_parent}")
    for path in (config.source_config_path, config.project_root / "conda.yaml"):
        mlflow.log_artifact(str(path), artifact_path="source")

    columns = ["relative_path", "class_name", "label", "bytes", "sha256"]
    for split_name in ("training", "validation", "test"):
        split_frame = dataset.split_frame(split_name)[columns]
        mlflow_dataset = mlflow.data.from_pandas(
            split_frame,
            source=dataset.source_uri,
            targets="label",
            name=f"microsoft-cats-vs-dogs-{split_name}",
            digest=_split_digest(split_frame),
        )
        mlflow.log_input(mlflow_dataset, context=split_name)


def verify_artifact_round_trip(run_id: str, destination: Path) -> None:
    payload = {"run_id": run_id, "verified_at_unix_ms": int(time.time() * 1000)}
    mlflow.log_dict(payload, "verification/artifact-round-trip.json")
    destination.mkdir(parents=True, exist_ok=True)
    downloaded = mlflow.artifacts.download_artifacts(
        run_id=run_id,
        artifact_path="verification/artifact-round-trip.json",
        dst_path=str(destination),
    )
    with Path(downloaded).open(encoding="utf-8") as file_handle:
        restored = json.load(file_handle)
    if restored != payload:
        raise RuntimeError("MLflow Artifact API returned different verification content")


def log_ray_task_timeline(run_id: str, ray_job_id: str) -> dict[str, Any]:
    """Persist this Job's Dashboard task timeline through MLflow Artifacts."""

    tasks = list_tasks(
        filters=[("job_id", "=", ray_job_id)],
        limit=RAY_TASK_TIMELINE_LIMIT,
        detail=True,
        raise_on_missing_output=True,
    )
    timeline_json = chrome_tracing_dump(tasks)
    events = json.loads(timeline_json)
    timeline_bytes = timeline_json.encode("utf-8")
    timeline_sha256 = hashlib.sha256(timeline_bytes).hexdigest()

    mlflow.log_text(
        timeline_json,
        RAY_TASK_TIMELINE_ARTIFACT_PATH,
        run_id=run_id,
    )
    with tempfile.TemporaryDirectory(
        prefix="ray-cats-dogs-timeline-check-"
    ) as verification_directory:
        downloaded = mlflow.artifacts.download_artifacts(
            run_id=run_id,
            artifact_path=RAY_TASK_TIMELINE_ARTIFACT_PATH,
            dst_path=verification_directory,
        )
        downloaded_sha256 = hashlib.sha256(Path(downloaded).read_bytes()).hexdigest()
    if downloaded_sha256 != timeline_sha256:
        raise RuntimeError("MLflow Ray task timeline failed SHA-256 verification")

    metadata = {
        "artifact_path": RAY_TASK_TIMELINE_ARTIFACT_PATH,
        "format": "chrome-trace-event",
        "ray_job_id": ray_job_id,
        "task_count": len(tasks),
        "complete_event_count": sum(event.get("ph") == "X" for event in events),
        "trace_event_count": len(events),
        "sha256": timeline_sha256,
        "mlflow_artifact_roundtrip_verified": True,
    }
    mlflow.log_dict(
        metadata,
        RAY_TASK_TIMELINE_METADATA_PATH,
        run_id=run_id,
    )
    mlflow.set_tags(
        {
            "ray.task_timeline.logged": "true",
            "ray.task_timeline.empty": str(not events).lower(),
            "ray.task_timeline.sha256": timeline_sha256,
        }
    )
    return metadata


class RayMlflowCallback(UserCallback):
    """Log rank-zero Ray reports through the controller to one existing Run."""

    def __init__(self, tracking_uri: str, run_id: str) -> None:
        self.tracking_uri = tracking_uri
        self.run_id = run_id

    def _client(self) -> MlflowClient:
        return MlflowClient(tracking_uri=self.tracking_uri)

    def after_report(
        self,
        run_context: Any,
        metrics: list[dict[str, Any]],
        checkpoint: Any,
    ) -> None:
        if not metrics:
            return
        rank_zero = next(
            (
                report
                for report in metrics
                if report.get("worker_rank") == 0
            ),
            metrics[0],
        )
        step = int(rank_zero.get("epoch", 0))
        timestamp = int(time.time() * 1000)
        excluded = {"epoch", "worker_rank"}
        batch = [
            Metric(key, float(value), timestamp, step)
            for key, value in rank_zero.items()
            if key not in excluded
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ]
        if batch:
            self._client().log_batch(self.run_id, metrics=batch, synchronous=True)
        if checkpoint is not None:
            self._client().set_tag(
                self.run_id,
                "ray.latest_checkpoint_uri",
                str(checkpoint.path),
                synchronous=True,
            )

    def after_exception(
        self,
        run_context: Any,
        worker_exceptions: dict[int, Exception],
    ) -> None:
        summary = "; ".join(
            f"rank={rank}:{type(error).__name__}"
            for rank, error in sorted(worker_exceptions.items())
        )
        self._client().set_tag(
            self.run_id,
            "ray.last_worker_failure",
            summary[:5000],
            synchronous=True,
        )
