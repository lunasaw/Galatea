"""Frozen Trial recovery and durable, fail-closed final evaluation claims.

State lives on the explicitly shared local POSIX project volume, outside Ray
sessions and Releases. This is a single-host contract, not an NFS/HA service.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FINAL_PROTOCOL = "ray-cats-dogs-frozen-checkpoint-final-v1"
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
FROZEN_ARTIFACTS = {
    "config/resolved-config.json", "reports/model-selection.json",
    "checkpoints/best/best-model.pt", "checkpoints/best/training-state.json",
}


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def state_root(config: Any) -> Path:
    return config.data.cache_dir.parent / "governance"


def secure_directory(path: Path) -> Path:
    if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
        raise RuntimeError("Final evaluation state requires an absolute non-symlink path")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def claim_test_once(root: Path, binding: dict[str, Any]) -> dict[str, Any]:
    """A second acquisition always fails, including the same Job after restart."""

    for key in ("checkpoint_sha256", "split_sha256"):
        if not SHA256.fullmatch(str(binding.get(key, ""))):
            raise RuntimeError(f"Invalid test-once binding: {key}")
    for key in ("submission_id", "execution_identity", "candidate_run_id", "run_id"):
        if not isinstance(binding.get(key), str) or not binding[key]:
            raise RuntimeError(f"Missing test-once binding: {key}")
    if binding.get("protocol") != FINAL_PROTOCOL:
        raise RuntimeError("Unsupported final evaluation protocol")
    # Attempts/submissions must never change the exclusion key.
    scope = {key: binding[key] for key in ("checkpoint_sha256", "split_sha256", "protocol")}
    key = hashlib.sha256(canonical(scope)).hexdigest()
    directory = secure_directory(root / "test-uses")
    path = directory / f"{key}.json"
    record = {
        "schema_version": "ray-cats-dogs/test-once/v1",
        **binding,
        "claim_key": key,
        "claimed_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        descriptor = os.open(
            path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600
        )
    except FileExistsError as error:
        # Do not recover/delete even an empty or corrupt crash marker.
        raise RuntimeError("Final test already claimed; read the original evidence") from error
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical(record))
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return record


def validate_candidate_config(candidate: dict[str, Any], config: Any) -> None:
    """Preserve the selected single-worker model, preprocessing and fitting semantics."""

    current = config.as_dict()
    for key in ("project_name", "model", "training", "data"):
        if candidate[key] != current[key]:
            raise RuntimeError(f"Frozen Trial {key} differs from final evaluation config")
    if candidate["run"]["seed"] != config.run.seed:
        raise RuntimeError("Frozen Trial seed differs")
    for key in ("num_workers", "use_gpu", "cpus_per_worker"):
        if candidate["ray"][key] != current["ray"][key]:
            raise RuntimeError(f"Frozen Trial Ray {key} differs")


def validate_freeze(
    freeze: dict[str, Any], config: Any, dataset: Any, provenance: dict[str, str]
) -> None:
    if freeze.get("schema_version") != "ray-cats-dogs/candidate-freeze/v1":
        raise RuntimeError("Missing or invalid frozen candidate")
    expected = {
        "project": config.project_name,
        "release_id": provenance["galatea.release.id"],
        "submission_id": provenance["galatea.submission.id"],
        "execution_identity": provenance["galatea.execution.identity"],
        "readiness_digest": provenance["galatea.readiness.digest"],
        "candidate_run_id": provenance.get("candidate_run_id"),
        "candidate_evidence_digest": provenance.get("candidate_evidence_digest"),
        "config_digest": config.config_digest,
        "split_sha256": dataset.split_digest,
        "content_sha256": dataset.content_digest,
        "protocol": FINAL_PROTOCOL,
    }
    if any(freeze.get(key) != value or value is None for key, value in expected.items()):
        raise RuntimeError("Frozen candidate differs from actual governed execution or data")
    identity = {
        "readinessIdentity": freeze["readiness_identity"],
        "candidateRunId": freeze["candidate_run_id"],
        "candidateEvidenceDigest": freeze["candidate_evidence_digest"],
    }
    if "sha256:" + hashlib.sha256(canonical(identity)).hexdigest() != freeze["execution_identity"]:
        raise RuntimeError("Candidate evidence is not bound to the execution identity")
    if not SHA256.fullmatch(str(freeze.get("checkpoint_sha256", ""))):
        raise RuntimeError("Frozen candidate checkpoint digest is invalid")


def recover_frozen_checkpoint(
    config: Any, dataset: Any, provenance: dict[str, str], destination: Path
) -> tuple[Any, dict[str, Any]]:
    """Use only Tracking/Artifact APIs; never load a local Trial storage path."""

    import mlflow
    from mlflow import MlflowClient
    from ray.train import Checkpoint

    identity = provenance["galatea.execution.identity"].removeprefix("sha256:")
    if not SHA256.fullmatch(identity):
        raise RuntimeError("Invalid final execution identity")
    path = secure_directory(state_root(config) / "freezes") / f"{identity}.json"
    if path.is_symlink():
        raise RuntimeError("Frozen candidate cannot be a symlink")
    freeze = json.loads(path.read_bytes())
    validate_freeze(freeze, config, dataset, provenance)
    run = MlflowClient().get_run(freeze["candidate_run_id"])
    expected_tags = {
        "project": config.project_name,
        "run.role": "trial",
        "run.outcome": "succeeded",
        "galatea.execution.mode": "governed-ray-job",
        "galatea.promotable": "true",
        "test.evaluated": "false",
    }
    if run.info.status != "FINISHED" or any(
        run.data.tags.get(key) != value for key, value in expected_tags.items()
    ):
        raise RuntimeError("Frozen candidate is not a successful eligible untouched Trial")
    if any(name.startswith("test_") for name in run.data.metrics):
        raise RuntimeError("Trial already contains final test metrics")
    for name, value in (
        ("data.split_sha256", dataset.split_digest),
        ("data.content_sha256", dataset.content_digest),
    ):
        if run.data.params.get(name) != value:
            raise RuntimeError("Frozen Trial data identity differs")
    paths = download_frozen_artifacts(
        run.info.run_id, freeze["artifact_digests"], destination
    )
    candidate_config = json.loads(paths["config/resolved-config.json"].read_bytes())
    validate_candidate_config(candidate_config, config)
    selection = json.loads(paths["reports/model-selection.json"].read_bytes())
    if (
        selection.get("best_model_sha256") != freeze["checkpoint_sha256"]
        or selection.get("test_evaluated_during_selection") is not False
        or selection.get("mlflow_artifact_roundtrip_verified") is not True
        or selection.get("fresh_process_load_verified") is not True
    ):
        raise RuntimeError("Frozen Trial selection evidence is invalid")
    checkpoint = destination / "checkpoints/best"
    digest = hashlib.sha256((checkpoint / "best-model.pt").read_bytes()).hexdigest()
    if digest != freeze["checkpoint_sha256"]:
        raise RuntimeError("Frozen Trial checkpoint SHA-256 differs")
    return Checkpoint.from_directory(checkpoint), freeze


def download_frozen_artifacts(
    run_id: str, digests: dict[str, str], destination: Path
) -> dict[str, Path]:
    import mlflow

    if set(digests) != FROZEN_ARTIFACTS:
        raise RuntimeError("Frozen candidate must contain the exact required Artifact set")
    paths = {}
    for artifact, expected in digests.items():
        target = destination / artifact
        target.parent.mkdir(parents=True, exist_ok=True)
        downloaded = Path(mlflow.artifacts.download_artifacts(
            run_id=run_id, artifact_path=artifact, dst_path=str(target.parent)
        ))
        if "sha256:" + hashlib.sha256(downloaded.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Frozen candidate Artifact changed: {artifact}")
        # MLflow versions may return basename-only or preserve relative paths.
        if downloaded.resolve() != target.resolve():
            shutil.copyfile(downloaded, target)
        paths[artifact] = target
    return paths
