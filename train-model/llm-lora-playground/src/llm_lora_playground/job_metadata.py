"""Atomic Ray Job metadata and checkpoint pointer updates."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_job_metadata(**kwargs: Any) -> dict[str, Any]:
    required = {"ray_job_id", "mlflow_run_id", "config_digest", "dataset_manifest_digest", "code_revision", "environment_digest", "attempt_id", "requested_resources"}
    missing = required - set(kwargs)
    if missing:
        raise ValueError(f"missing metadata fields: {sorted(missing)}")
    metadata = {"schema_version": "toy-lora-job-v1", **kwargs}
    metadata.setdefault("status", "submitted")
    metadata.setdefault("created_at", _now())
    metadata.setdefault("updated_at", metadata["created_at"])
    return metadata


def write_job_metadata_atomic(metadata: dict[str, Any], path: Path) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(metadata, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def update_checkpoint_pointer(metadata: dict[str, Any], checkpoint_record: dict[str, Any]) -> dict[str, Any]:
    updated = dict(metadata)
    updated["checkpoint_uri"] = checkpoint_record["uri"]
    updated["checkpoint_digest"] = checkpoint_record["digest"]
    updated["updated_at"] = _now()
    return updated
