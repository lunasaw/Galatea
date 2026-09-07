"""Read-only planning and fail-closed Ray training boundary."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from .consent import ConsentError, verify_consent
from .runtime import validate_project_config


class TrainingBoundaryError(RuntimeError):
    pass


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_training_plan(config: Mapping[str, Any]) -> dict[str, Any]:
    errors = validate_training_readiness(config)
    return {
        "status": "blocked" if errors else "planned",
        "project": config.get("project"),
        "task": config.get("task"),
        "config_digest": _digest(config),
        "role": config.get("run", {}).get("role"),
        "test_access": config.get("evaluation", {}).get("test_access", "untouched"),
        "execution_backend": config.get("execution", {}).get("backend"),
        "errors": errors,
        "will_create_mlflow_run": False,
    }


def validate_training_readiness(config: Mapping[str, Any]) -> list[str]:
    """Check immutable data, review, protocol and budget gates without fitting."""
    errors = list(validate_project_config(config))
    values = dict(config)
    dataset = values.get("dataset", {})
    governance = values.get("governance", values.get("experiment", {}))
    counts = dataset.get("counts", {})
    for split in ("train", "validation", "test"):
        if counts and int(counts.get(split, 0)) <= 0:
            errors.append(f"dataset.{split} must be non-empty")
    if dataset.get("formal_dataset_ready") is False:
        errors.append("dataset must be FORMAL_DATASET_READY")
    if governance:
        if governance.get("formal_training_eligible") is not True:
            errors.append("formal_training_eligible must be true")
        if governance.get("human_review_completed") is not True:
            errors.append("human_review_completed must be true")
        if governance.get("withdrawn") is True:
            errors.append("withdrawn dataset cannot train")
        if governance.get("pii_scan_passed") is not True:
            errors.append("pii_scan_passed must be true")
        if governance.get("canary_scan_passed") is not True:
            errors.append("canary_scan_passed must be true")
        if governance.get("cross_split_session_count", 0) != 0:
            errors.append("cross_split_session_count must be zero")
    training = values.get("training", {})
    if values.get("run", {}).get("role") == "smoke" and training.get("max_steps") != 10:
        errors.append("smoke max_steps must be 10")
    if values.get("run", {}).get("role") in {"trial", "baseline"} and training.get("epochs") != 1:
        errors.append("initial Trial/baseline epochs must be 1")
    consent_ledger = dataset.get("consent_ledger")
    if consent_ledger:
        try:
            verify_consent(
                Path(str(consent_ledger)),
                required_purposes={"persona_style", "evaluation"},
            )
        except (ConsentError, OSError) as exc:
            errors.append(str(exc))
    return sorted(set(errors))


_REQUIRED_RUNTIME = ("execution_mode", "release_id", "readiness_digest", "execution_identity", "attempt_id", "ray_submission_id", "ray_job_id", "galatea_project", "role", "promotable")


def _validate_runtime(config: Mapping[str, Any], runtime: Mapping[str, Any]) -> list[str]:
    missing = [key for key in _REQUIRED_RUNTIME if key not in runtime or runtime[key] in (None, "")]
    if missing:
        return missing
    errors: list[str] = []
    if runtime.get("execution_mode") != "governed-ray-job": errors.append("execution_mode")
    if runtime.get("galatea_project") != "wechat-persona": errors.append("galatea_project")
    if runtime.get("release_id") != config.get("execution", {}).get("release_id"): errors.append("release_id")
    if runtime.get("readiness_digest") != config.get("execution", {}).get("readiness_digest"): errors.append("readiness_digest")
    if runtime.get("execution_identity") != config.get("execution", {}).get("execution_identity"): errors.append("execution_identity")
    if runtime.get("role") != config.get("run", {}).get("role"): errors.append("role")
    if bool(runtime.get("promotable")) != bool(config.get("run", {}).get("promotable")): errors.append("promotable")
    for key in ("readiness_digest", "execution_identity"):
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", str(runtime.get(key))): errors.append(key)
    return errors


def validate_galatea_runtime(
    config: Mapping[str, Any], runtime: Mapping[str, Any]
) -> dict[str, Any]:
    errors = _validate_runtime(config, runtime)
    if errors:
        raise TrainingBoundaryError(
            "incomplete or mismatched Galatea binding: " + ", ".join(errors)
        )
    return dict(runtime)


def run_training(config: Mapping[str, Any], *, runtime: Mapping[str, Any] | None = None, resume_from: str | None = None) -> dict[str, Any]:
    runtime = runtime or {}
    if not runtime:
        raise TrainingBoundaryError("training requires Galatea-governed Ray metadata")
    errors = _validate_runtime(config, runtime)
    if errors:
        return {"status": "blocked", "reason": "incomplete or mismatched Galatea binding", "errors": errors, "will_create_mlflow_run": False}
    readiness_errors = validate_training_readiness(config)
    if readiness_errors:
        return {"status": "blocked", "reason": "training readiness failed", "errors": readiness_errors, "will_create_mlflow_run": False}
    # This package deliberately exposes only the contract boundary.  Actual fitting belongs to
    # an immutable Release's fixed Driver and is never performed by a direct Python call.
    return {"status": "blocked", "reason": "fixed Ray Driver must own model updates", "will_create_mlflow_run": False, "attempt_id": runtime["attempt_id"], "resumed_from": resume_from}
