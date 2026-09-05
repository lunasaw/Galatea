"""Safe checkpoint recovery helpers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any


class RecoveryContractError(ValueError):
    pass


@dataclass(frozen=True)
class AttemptContext:
    attempt_id: str
    retry_of: str
    resumed_from: str


def validate_resume_identity(checkpoint: dict[str, Any], current_config: dict[str, Any]) -> None:
    if checkpoint.get("status") != "complete":
        raise RecoveryContractError("resume requires a complete checkpoint")
    for key in ("config_digest", "dataset_manifest_digest"):
        if key in checkpoint and key in current_config and checkpoint.get(key) != current_config.get(key):
            raise RecoveryContractError(f"resume identity mismatch: {key}")
    if not checkpoint.get("path"):
        raise RecoveryContractError("checkpoint path is required")


def create_resume_attempt(previous_run_id: str, checkpoint: dict[str, Any], config: dict[str, Any]) -> AttemptContext:
    validate_resume_identity(checkpoint, config)
    return AttemptContext(f"attempt-{uuid.uuid4().hex}", previous_run_id, str(checkpoint["path"]))


def find_latest_complete_checkpoint(run_id: str, client: Any) -> Any:
    records = client.list_checkpoints(run_id)
    complete = [record for record in records if getattr(record, "status", None) == "complete"]
    return max(complete, key=lambda record: getattr(record, "step", -1), default=None)


def mark_attempt_status(attempt_id: str, status: str, reason: str | None = None) -> dict[str, Any]:
    if status not in {"running", "completed", "failed", "interrupted", "blocked"}:
        raise RecoveryContractError(f"invalid attempt status: {status}")
    result = {"attempt_id": attempt_id, "status": status}
    if reason:
        result["failure_reason"] = reason
    return result
