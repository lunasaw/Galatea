"""Content-addressed checkpoint writes with complete/incomplete states."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
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
    parent = output_dir / attempt
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / f"step-{step}"
    if target.exists():
        raise FileExistsError(f"refusing to overwrite checkpoint: {target}")
    stage = Path(tempfile.mkdtemp(prefix=f".step-{step}.", dir=parent))
    try:
        files = {}
        for name, content in sorted(state.items()):
            if Path(name).is_absolute() or ".." in Path(name).parts:
                raise CheckpointContractError("checkpoint file path escapes directory")
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            files[name] = _digest_bytes(content)
        status = "complete" if mark_complete else "incomplete"
        payload = {"schema_version": "llm-lora-checkpoint-v2", "status": status, "step": step, "files": files, "metadata": metadata}
        digest = _digest_bytes(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode())
        payload["manifest_sha256"] = digest
        (stage / "checkpoint_manifest.json").write_text(json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(stage, target)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return CheckpointManifest(target, status, step, digest, files, metadata)


def load_checkpoint(path: Path) -> CheckpointManifest:
    manifest_path = path / "checkpoint_manifest.json" if path.is_dir() else path
    if not manifest_path.is_file():
        raise CheckpointContractError(f"checkpoint manifest is missing: {manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "llm-lora-checkpoint-v2":
        raise CheckpointContractError("resume requires an llm-lora-checkpoint-v2 checkpoint")
    expected = payload.get("manifest_sha256")
    digest_payload = dict(payload)
    digest_payload.pop("manifest_sha256", None)
    actual = _digest_bytes(json.dumps(digest_payload, sort_keys=True, ensure_ascii=False).encode())
    if actual != expected:
        raise CheckpointContractError("checkpoint manifest digest mismatch")
    manifest = CheckpointManifest(
        manifest_path.parent,
        str(payload.get("status")),
        int(payload.get("step", 0)),
        actual,
        dict(payload.get("files", {})),
        dict(payload.get("metadata", {})),
    )
    verify_checkpoint(manifest)
    return manifest


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
