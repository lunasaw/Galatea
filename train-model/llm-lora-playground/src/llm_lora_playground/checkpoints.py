"""Content-addressed checkpoint writes with complete/incomplete states."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CheckpointContractError(ValueError):
    pass


@dataclass(frozen=True)
class CheckpointManifest:
    path: Path
    status: str
    step: int
    digest: str
    files: dict[str, str]
    metadata: dict[str, Any]


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def save_checkpoint(state: dict[str, bytes], output_dir: Path, metadata: dict[str, Any], mark_complete: bool = True) -> CheckpointManifest:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    attempt = str(metadata.get("attempt_id", "attempt-unknown"))
    step = int(metadata.get("step", 0))
    target = output_dir / attempt / f"step-{step}"
    target.mkdir(parents=True, exist_ok=False)
    files = {}
    for name, content in sorted(state.items()):
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise CheckpointContractError("checkpoint file path escapes directory")
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        files[name] = _digest_bytes(content)
    status = "complete" if mark_complete else "incomplete"
    payload = {"schema_version": "toy-lora-checkpoint-v1", "status": status, "step": step, "files": files, "metadata": metadata}
    digest = _digest_bytes(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode())
    payload["manifest_sha256"] = digest
    (target / "checkpoint_manifest.json").write_text(json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return CheckpointManifest(target, status, step, digest, files, metadata)


def verify_checkpoint(manifest: CheckpointManifest) -> None:
    if manifest.status != "complete":
        raise CheckpointContractError("only complete checkpoints may be verified or resumed")
    for name, expected in manifest.files.items():
        path = manifest.path / name
        if not path.is_file() or _digest_bytes(path.read_bytes()) != expected:
            raise CheckpointContractError(f"checkpoint file digest mismatch: {name}")
    raw = json.loads((manifest.path / "checkpoint_manifest.json").read_text(encoding="utf-8"))
    if raw.get("status") != "complete":
        raise CheckpointContractError("checkpoint manifest is not complete")


def checkpoint_record(manifest: CheckpointManifest) -> dict[str, Any]:
    """Return the small pointer payload safe to put in a Run/Job manifest."""
    return {"uri": str(manifest.path), "digest": manifest.digest, "step": manifest.step, "status": manifest.status}
