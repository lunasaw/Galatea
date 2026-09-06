#!/usr/bin/env python3
"""Ray Job Driver: the sole owner of the parent MLflow training Run."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from llm_lora_playground.config import load_training_config  # noqa: E402
from llm_lora_playground.job_metadata import build_job_metadata, update_job_status, write_job_metadata_atomic  # noqa: E402
from llm_lora_playground.integrity import integrity_envelope  # noqa: E402
from llm_lora_playground.planning import build_training_plan  # noqa: E402
from llm_lora_playground.tracking import (  # noqa: E402
    finish_training_run,
    log_artifact_with_sha256,
    log_json_artifact,
    set_training_tags,
    start_training_run,
    verify_adapter_roundtrip,
    verify_artifact_roundtrip,
)
from llm_lora_playground.training import train  # noqa: E402


def _ray_job_id() -> str:
    value = os.environ.get("RAY_JOB_ID")
    if value:
        return value
    try:
        import ray

        value = ray.get_runtime_context().get_job_id()
        return value.hex() if hasattr(value, "hex") else str(value)
    except Exception:
        return "unknown-ray-job"


def _ray_metadata() -> dict[str, str]:
    try:
        payload = json.loads(os.environ.get("RAY_JOB_CONFIG_JSON_ENV_VAR", "{}"))
    except json.JSONDecodeError:
        return {}
    metadata = payload.get("metadata", {})
    return {str(key): str(value) for key, value in metadata.items()} if isinstance(metadata, dict) else {}


def _submission_id(metadata: dict[str, str]) -> str:
    return (
        os.environ.get("RAY_JOB_SUBMISSION_ID")
        or os.environ.get("GALATEA_SUBMISSION_ID")
        or metadata.get("galatea.submission.id")
        or metadata.get("job_submission_id")
        or _ray_job_id()
    )


def _require_governed_ray_metadata(
    metadata: dict[str, str],
    submission_id: str,
    *,
    expected_role: str,
    expected_promotable: bool,
) -> None:
    """Refuse to create a training Run outside the Galatea authorization boundary.

    The project Driver is intentionally not a general-purpose ``ray job submit``
    entrypoint.  Galatea must bind the immutable release, readiness evidence,
    execution identity and promotability before this process is allowed to create
    an MLflow Run or touch model weights.
    """
    required = (
        "galatea.execution.identity",
        "galatea.project",
        "galatea.release.id",
        "galatea.submission.id",
        "galatea.readiness.digest",
        "galatea.execution.mode",
        "galatea.promotable",
        "role",
        "attempt",
    )
    missing = [key for key in required if not metadata.get(key)]
    if missing:
        raise RuntimeError(
            "training Driver requires Galatea authorization metadata; missing "
            + ", ".join(missing)
        )
    if metadata["galatea.execution.mode"] != "governed-ray-job":
        raise RuntimeError("training Driver requires galatea.execution.mode=governed-ray-job")
    if metadata["galatea.project"] != "llm-lora-playground":
        raise RuntimeError("Galatea project metadata does not belong to llm-lora-playground")
    if metadata["galatea.submission.id"] != submission_id:
        raise RuntimeError("Galatea submission identity does not match the Ray Job submission id")
    if metadata["galatea.promotable"] not in {"true", "false"}:
        raise RuntimeError("Galatea promotability must be an explicit boolean")
    if metadata["galatea.promotable"] != str(expected_promotable).lower():
        raise RuntimeError("Galatea promotability does not match the resolved config")
    if metadata["role"] != expected_role:
        raise RuntimeError("Galatea role does not match the resolved config")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", metadata["attempt"]):
        raise RuntimeError("Galatea attempt contains unsupported characters")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", metadata["galatea.execution.identity"]):
        raise RuntimeError("Galatea execution identity must be a SHA-256 digest")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", metadata["galatea.readiness.digest"]):
        raise RuntimeError("Galatea readiness digest must be a SHA-256 digest")


def _safe_failure(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:500]}"


def _finalize_metadata(
    context: object,
    metadata: dict[str, object],
    metadata_path: Path,
    status: str,
    reason: str | None = None,
) -> dict[str, object]:
    updated = update_job_status(metadata, status, reason)
    write_job_metadata_atomic(updated, metadata_path)
    log_artifact_with_sha256(context, metadata_path, "ray")
    return updated


def run_worker(config: object, runtime: dict[str, object], resume_from: str | None = None, data: Path | None = None):
    return train(config, runtime=runtime, resume_from=resume_from, data=data)


def run_driver(config_path: Path, data: Path | None = None, resume_from: str | None = None):
    config = load_training_config(config_path)
    plan = build_training_plan(config, data)
    ray_metadata = _ray_metadata()
    ray_job_id = _ray_job_id()
    submission_id = _submission_id(ray_metadata)
    if ray_job_id == "unknown-ray-job" or submission_id == "unknown-ray-job":
        raise RuntimeError("training Driver is not running inside a Ray Job")
    _require_governed_ray_metadata(
        ray_metadata,
        submission_id,
        expected_role=str(config.values["run"]["role"]),
        expected_promotable=bool(config.values["run"]["promotable"]),
    )
    candidate_run_id = ray_metadata.get("candidate_run_id")
    candidate_evidence_digest = ray_metadata.get("candidate_evidence_digest")
    if config.values["run"]["role"] == "champion" and config.values["evaluation"].get("evaluate_test"):
        if not candidate_run_id or not candidate_evidence_digest:
            raise RuntimeError(
                "champion training requires Galatea-bound candidate_run_id and candidate_evidence_digest"
            )
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", candidate_evidence_digest):
            raise RuntimeError("candidate_evidence_digest must be a SHA-256 digest")
    attempt_id = ray_metadata["attempt"]
    execution_mode = ray_metadata["galatea.execution.mode"]
    output_root = Path(str(plan["output_root"])).resolve()
    metadata_path = output_root / "job-metadata" / f"{submission_id}-{attempt_id}.json"
    manifest = {
        "run_kind": config.values.get("run_kind", "training"),
        "task": config.values.get("task", "synthetic_sft_lora"),
        "role": config.values["run"]["role"],
        "config_digest": plan["config_digest"],
        "dataset_manifest_digest": plan["dataset"]["content_sha256"],
        "split_digest": plan["dataset"]["split_sha256"],
        "preprocessing_version": plan["dataset"]["preprocessing_version"],
        "evaluation_protocol_version": config.values["evaluation"]["protocol_version"],
        "objective_metric": config.values["objective_metric"],
        "objective_mode": config.values["objective_mode"],
        "owner_bulk_approved": config.values.get("experiment", {}).get("owner_bulk_approved", False),
        "formal_training_eligible": config.values.get("experiment", {}).get("formal_training_eligible", True),
        "approval_basis": config.values.get("experiment", {}).get("approval_basis"),
        "human_review_completed": config.values.get("experiment", {}).get("human_review_completed", False),
        "quality_evidence_status": config.values.get("experiment", {}).get("quality_evidence_status"),
        "dataset_id": plan["dataset"]["id"],
        "ray_job_id": ray_job_id,
        "ray_submission_id": submission_id,
        "execution_mode": execution_mode,
        "attempt_id": attempt_id,
        "parent_attempt_id": os.environ.get("GALATEA_RESUMED_FROM_SUBMISSION_ID"),
        "code_revision": plan["code"]["revision"],
        "environment_digest": plan["code"]["environment_sha256"],
        "epochs": config.values["training"]["epochs"],
        "max_steps": config.values["training"].get("max_steps"),
        "per_device_train_batch_size": config.values["training"]["per_device_train_batch_size"],
        "gradient_accumulation_steps": config.values["training"]["gradient_accumulation_steps"],
        "learning_rate": config.values["training"]["learning_rate"],
        "warmup_ratio": config.values["training"]["warmup_ratio"],
        "scheduler": config.values["training"]["scheduler"],
        "optimizer": config.values["training"]["optimizer"],
        "seed": config.values["training"]["seed"],
        "lora_rank": config.values["lora"]["rank"],
        "lora_alpha": config.values["lora"]["alpha"],
        "artifact.roundtrip_verified": False,
        "candidate_run_id": candidate_run_id,
        "candidate_evidence_digest": candidate_evidence_digest,
        **{key: value for key, value in ray_metadata.items() if key.startswith("galatea.")},
    }
    context = start_training_run(manifest)
    job_metadata = build_job_metadata(
        ray_job_id=ray_job_id,
        mlflow_run_id=context.run_id,
        project="llm-lora-playground",
        run_kind=manifest["run_kind"],
        role=manifest["role"],
        config_digest=plan["config_digest"],
        dataset_manifest_digest=plan["dataset"]["content_sha256"],
        split_digest=plan["dataset"]["split_sha256"],
        preprocessing_version=plan["dataset"]["preprocessing_version"],
        code_revision=manifest["code_revision"],
        environment_digest=manifest["environment_digest"],
        attempt_id=attempt_id,
        requested_resources=plan["requested_resources"],
        ray_submission_id=submission_id,
        execution_mode=execution_mode,
        release_id=ray_metadata.get("galatea.release.id"),
        readiness_digest=ray_metadata.get("galatea.readiness.digest"),
        execution_identity=ray_metadata.get("galatea.execution.identity"),
        promotable=ray_metadata.get("galatea.promotable", "false") == "true",
        status="running",
    )
    write_job_metadata_atomic(job_metadata, metadata_path)
    log_artifact_with_sha256(context, metadata_path, "ray")
    preprocessing = integrity_envelope(
        "preprocessing",
        str(manifest["role"]),
        str(plan["integrity"]["preprocessing"]["parity"]["status"]),
        plan["integrity"]["preprocessing"],
    )
    migration = integrity_envelope(
        "migration",
        str(manifest["role"]),
        str(plan["integrity"]["migration"]["contamination"]["status"]["status"]),
        plan["integrity"]["migration"]["contamination"],
    )
    preprocessing_record = log_json_artifact(context, preprocessing, "reports/preprocessing-parity.json")
    migration_record = log_json_artifact(context, migration, "reports/migration-contamination.json")
    set_training_tags(
        context,
        {
            "integrity.preprocessing.status": preprocessing["status"],
            "integrity.migration.status": migration["status"],
        },
    )
    __import__("mlflow").tracking.MlflowClient(tracking_uri=os.environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")).log_batch(
        context.run_id,
        params=[
            __import__("mlflow").entities.Param("integrity.preprocessing_artifact_digest", f"sha256:{preprocessing_record.sha256}"),
            __import__("mlflow").entities.Param("integrity.migration_artifact_digest", f"sha256:{migration_record.sha256}"),
        ],
    )
    try:
        result = run_worker(
            config,
            runtime={
                "run_id": context.run_id,
                "tracking_owner": "driver",
                "tracking_context": context,
                "execution_mode": execution_mode,
                "ray_job_id": ray_job_id,
                "ray_submission_id": submission_id,
                "attempt_id": attempt_id,
                "job_metadata_path": str(metadata_path),
                "output_root": str(output_root),
                "code_revision": manifest["code_revision"],
                "environment_digest": manifest["environment_digest"],
                "release_id": ray_metadata.get("galatea.release.id"),
                "readiness_digest": ray_metadata.get("galatea.readiness.digest"),
                "execution_identity": ray_metadata.get("galatea.execution.identity"),
                "promotable": ray_metadata.get("galatea.promotable", "false") == "true",
                "candidate_run_id": candidate_run_id,
                "candidate_evidence_digest": candidate_evidence_digest,
            },
            resume_from=resume_from,
            data=data,
        )
        current = json.loads(metadata_path.read_text(encoding="utf-8"))
        if result.status == "interrupted":
            _finalize_metadata(context, current, metadata_path, "interrupted")
            set_training_tags(context, {"run.outcome": "interrupted"})
            finish_training_run(context, status="KILLED")
            return result
        if result.checkpoint is None:
            raise RuntimeError("completed training returned no checkpoint")
        verify_artifact_roundtrip(
            __import__("mlflow").tracking.MlflowClient(tracking_uri=os.environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")),
            context.run_id,
            f"checkpoints/{attempt_id}/step-{result.checkpoint.step}/checkpoint_manifest.json",
            __import__("hashlib").sha256((result.checkpoint.path / "checkpoint_manifest.json").read_bytes()).hexdigest(),
        )
        verify_adapter_roundtrip(
            __import__("mlflow").tracking.MlflowClient(tracking_uri=os.environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")),
            context.run_id,
            Path(str(config.values["model"]["local_path"])).resolve(),
            __import__("hashlib").sha256((result.checkpoint.path / "adapter/adapter_config.json").read_bytes()).hexdigest(),
            __import__("hashlib").sha256((result.checkpoint.path / "adapter/adapter_model.safetensors").read_bytes()).hexdigest(),
        )
        set_training_tags(context, {"artifact.roundtrip_verified": True, "run.outcome": "succeeded"})
        _finalize_metadata(context, current, metadata_path, "completed")
        finish_training_run(context, status="FINISHED")
        return result
    except BaseException as exc:
        try:
            current = json.loads(metadata_path.read_text(encoding="utf-8"))
            _finalize_metadata(context, current, metadata_path, "failed", _safe_failure(exc))
            set_training_tags(context, {"run.outcome": "failed", "artifact.roundtrip_verified": False})
        finally:
            finish_training_run(context, status="FAILED")
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--resume-from")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if not args.run:
        print(json.dumps({"status": "blocked", "reason": "Ray Driver requires --run"}, sort_keys=True))
        return 2
    result = run_driver(args.config, args.data, args.resume_from)
    print(json.dumps({"status": result.status, "run_id": result.run_id, "steps": result.steps}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
