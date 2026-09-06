"""MLflow Artifact API round-trip verification helpers."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


class ArtifactContractError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact_roundtrip(
    client: Any,
    run_id: str,
    artifact_path: str,
    expected_sha256: str,
    destination: Path,
) -> dict[str, Any]:
    """Download through the tracking client and verify immutable contents."""
    if not run_id or not artifact_path:
        raise ArtifactContractError("run_id and artifact_path are required")
    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha256):
        raise ArtifactContractError("expected_sha256 must be a lowercase SHA-256 digest")
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    try:
        downloaded = Path(client.download_artifacts(run_id, artifact_path, str(destination))).resolve()
    except Exception as exc:
        raise ArtifactContractError("MLflow Artifact API download failed") from exc
    if destination not in downloaded.parents and downloaded != destination:
        raise ArtifactContractError("downloaded artifact escaped the verification directory")
    if not downloaded.is_file() or downloaded.is_symlink():
        raise ArtifactContractError("downloaded artifact is not a regular file")
    actual = _sha256_file(downloaded)
    if actual != expected_sha256:
        raise ArtifactContractError("artifact digest mismatch")
    return {
        "roundtrip_verified": True,
        "run_id": run_id,
        "artifact_path": artifact_path,
        "sha256": actual,
        "download_api": "mlflow-client",
    }
