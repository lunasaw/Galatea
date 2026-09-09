"""Build deterministic MCP-compatible wechat-persona Release material.

The builder is local-only: it never uploads, registers, contacts Ray/MLflow, or
starts training. Administrator-owned platform and immutable object references
remain explicit parameters or schema-valid placeholder values.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
PROJECT_NAME = "wechat-persona"
DEFAULT_RELEASE_NAME = "wechat-persona-release.zip"
ENTRYPOINT = ["python", "scripts/submit_train.py"]
FORMAL_CONFIGS = {
    "formal-sft-v2-baseline": "configs/formal-sft-v2-baseline.yaml",
    "formal-sft-v2-trial": "configs/formal-sft-v2-trial.yaml",
    "formal-sft-v2-champion": "configs/formal-sft-v2-champion.yaml",
    "formal-sft-v2-champion-trial": "configs/formal-sft-v2-champion-trial.yaml",
    "formal-sft-v2-evaluate": "configs/formal-sft-v2-evaluate.yaml",
}
EXCLUDED_TOP_LEVEL = {"notebooks", "tests"}
EXCLUDED_PARTS = {
    ".git",
    ".ipynb_checkpoints",
    ".pytest_cache",
    "__pycache__",
    "platform-data",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
ZERO_SHA = "0" * 64


@dataclass(frozen=True)
class BuiltRelease:
    directory: Path
    archive_path: Path
    manifest: dict[str, Any]
    manifest_path: Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def compact_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _write_immutable(path: Path, payload: bytes) -> None:
    if path.exists():
        if not path.is_file() or path.read_bytes() != payload:
            raise FileExistsError(f"refusing to overwrite a different immutable file: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _git_identity(project_root: Path) -> dict[str, Any]:
    repository_root = project_root.parents[1]

    def run(*args: str) -> str | None:
        result = subprocess.run(
            ["git", *args], cwd=repository_root, capture_output=True, text=True, check=False
        )
        return result.stdout.strip() if result.returncode == 0 else None

    return {
        "commit": run("rev-parse", "HEAD") or "unresolved",
        "dirty": bool(run("status", "--porcelain")),
    }


def _is_runtime_file(relative: Path) -> bool:
    if not relative.parts or relative.parts[0] in EXCLUDED_TOP_LEVEL:
        return False
    if any(part in EXCLUDED_PARTS for part in relative.parts):
        return False
    return relative.suffix not in EXCLUDED_SUFFIXES


def _normalized_configs(project_root: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for config_id, relative in FORMAL_CONFIGS.items():
        value = yaml.safe_load((project_root / relative).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"{relative} must contain a mapping")
        value["seed"] = int(value["training"]["seed"])
        result[f"configs/{config_id}.json"] = compact_json(value)
    return result


def _source_entries(project_root: Path) -> dict[str, bytes]:
    entries: dict[str, bytes] = {}
    for source in sorted(project_root.rglob("*")):
        relative = source.relative_to(project_root)
        if not source.is_file() or not _is_runtime_file(relative):
            continue
        if source.is_symlink():
            raise ValueError(f"release must not contain symlinks: {source}")
        if relative.as_posix() in FORMAL_CONFIGS.values():
            continue
        entries[relative.as_posix()] = source.read_bytes()
    return entries


def _write_zip(destination: Path, entries: Mapping[str, bytes]) -> None:
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, raw in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, raw)


def _environment_digest(project_root: Path) -> str:
    return sha256_file(project_root / "conda.yaml")


def build_release(
    project_root: Path,
    output_root: Path,
    *,
    allow_dirty: bool = False,
) -> BuiltRelease:
    """Build one content-addressed ZIP accepted by the MCP Registry verifier.

    V1 execution bindings are issued by the MCP and authenticated by the fixed
    Ray Job submission/metadata checks.  The release therefore contains no
    administrator public key and can be built entirely as a local code-only
    operation.
    """

    root = project_root.resolve()
    output = output_root.resolve()
    if output == root or root in output.parents:
        raise ValueError("release output must be outside the source project")
    for required in (root / "galatea.project.yaml", root / "conda.yaml", root / ENTRYPOINT[1]):
        if not required.is_file():
            raise FileNotFoundError(required)
    git = _git_identity(root)
    if git["dirty"] and not allow_dirty:
        raise ValueError("clean Git commit required for a registerable Release")
    output.mkdir(parents=True, exist_ok=True)
    output.chmod(stat.S_IRWXU)
    entries = _source_entries(root)
    entries.update(_normalized_configs(root))
    entry_digests = {name: hashlib.sha256(raw).hexdigest() for name, raw in entries.items()}
    release_manifest = {
        "schema_version": "wechat-persona-release/v1",
        "project_id": PROJECT_NAME,
        "code_revision": git["commit"],
        "environment_digest": _environment_digest(root),
        "entrypoint": ENTRYPOINT,
        "deadline_enforced": True,
        "files": entry_digests,
    }
    entries["release/manifest.json"] = compact_json(release_manifest)
    with tempfile.TemporaryDirectory(prefix="wechat-persona-release-") as temporary:
        stage = Path(temporary) / DEFAULT_RELEASE_NAME
        _write_zip(stage, entries)
        archive_sha = sha256_file(stage)
        release_id = archive_sha[:20]
        release_directory = output / release_id
        archive_path = release_directory / DEFAULT_RELEASE_NAME
        manifest = {
            **release_manifest,
            "release_id": release_id,
            "archive_sha256": archive_sha,
            "archive_size_bytes": stage.stat().st_size,
            "git": git,
            "publication": {
                "uploaded": False,
                "registered": False,
                "training_started": False,
                "mlflow_run_created": False,
            },
        }
        if release_directory.exists():
            existing_manifest = release_directory / "release.json"
            if (
                not archive_path.is_file()
                or not existing_manifest.is_file()
                or archive_path.read_bytes() != stage.read_bytes()
                or json.loads(existing_manifest.read_text(encoding="utf-8")) != manifest
            ):
                raise FileExistsError(f"existing release differs: {release_directory}")
        else:
            release_directory.mkdir(mode=0o700)
            archive_path.write_bytes(stage.read_bytes())
            archive_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            _write_immutable(release_directory / "release.json", canonical_json(manifest))
    return BuiltRelease(
        directory=release_directory.resolve(),
        archive_path=archive_path.resolve(),
        manifest=manifest,
        manifest_path=(release_directory / "release.json").resolve(),
    )


def _snapshot_identity(snapshot_manifest: Path | None) -> dict[str, Any]:
    if snapshot_manifest is None:
        return {
            "dataset_id": "admin-bound-dataset",
            "manifest_digest": ZERO_SHA,
            "split_digest": ZERO_SHA,
            "preprocessing": "ADMIN_MUST_BIND_PREPROCESSING",
            "holdout_identity": ZERO_SHA,
            "holdout_untouched": False,
        }
    value = json.loads(snapshot_manifest.read_text(encoding="utf-8"))
    return {
        "dataset_id": value["dataset_id"],
        "manifest_digest": value["manifest_sha256"],
        "split_digest": value["split_sha256"],
        "preprocessing": value["preprocessing_version"],
        "holdout_identity": value["split_file_sha256"]["test"],
        "holdout_untouched": True,
    }


def _view_placeholder(name: str, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    sha = snapshot["holdout_identity"] if name == "test" else ZERO_SHA
    return {
        "bucket": "ADMIN_MUST_BIND_BUCKET",
        "key": f"datasets/wechat-persona/ADMIN_MUST_BIND/{name}.jsonl",
        "version_id": "ADMIN_MUST_BIND_VERSION_ID",
        "sha256": sha,
        "size_bytes": 0,
    }


def _config_registration(release: BuiltRelease, config_id: str) -> dict[str, Any]:
    path = f"configs/{config_id}.json"
    with zipfile.ZipFile(release.archive_path) as archive:
        raw = archive.read(path)
    config = json.loads(raw)
    resources = config["execution"]["resources"]
    return {
        "path": path,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "seed": int(config["training"]["seed"]),
        "resources": {
            "cpus": int(resources["cpus"]),
            "gpus": int(resources["num_gpus"]),
            "memory_bytes": int(resources["memory_gb"]) * 1024 * 1024 * 1024,
            "workers": 1,
            "seconds": 3600,
            "cleanup_seconds": 60,
        },
    }


def validate_registration_materials(projects: Mapping[str, Any], campaign: Mapping[str, Any]) -> None:
    """Validate against the installed MCP package when it is available."""

    try:
        from galatea_mcp.contracts import CampaignSpec
        from galatea_mcp.projects import Registry
    except ImportError:
        return

    class MetadataOnlyObjects:
        verify = verify_metadata = lambda self, ref: True

    registry = Registry(dict(projects), MetadataOnlyObjects())
    spec = CampaignSpec.model_validate(dict(campaign))
    project = registry.get(PROJECT_NAME)
    for slot in spec.slots:
        for config_id in slot.config_ids:
            for release_id in slot.release_ids:
                registry.verify(PROJECT_NAME, config_id, release_id, slot.role)


def write_registration_materials(
    release: BuiltRelease,
    output_directory: Path,
    *,
    snapshot_manifest: Path | None = None,
    experiment_id: str = "ADMIN_MUST_BIND_MLFLOW_EXPERIMENT_ID",
    approved_by: str = "PENDING_ADMIN_APPROVAL",
    expires_at: int | None = None,
    campaign_id: str = "wechat-persona-formal-sft-v2",
) -> dict[str, Path]:
    """Write schema-valid Project registry and Campaign documents."""

    output = output_directory.resolve()
    output.mkdir(parents=True, exist_ok=True)
    output.chmod(stat.S_IRWXU)
    registered_archive = output / release.archive_path.name
    if registered_archive.resolve() != release.archive_path.resolve():
        _write_immutable(registered_archive, release.archive_path.read_bytes())
    snapshot = _snapshot_identity(snapshot_manifest)
    snapshot["views"] = {name: _view_placeholder(name, snapshot) for name in ("train", "validation", "test")}
    configs = {config_id: _config_registration(release, config_id) for config_id in FORMAL_CONFIGS}
    with zipfile.ZipFile(release.archive_path) as archive:
        for config in configs.values():
            _write_immutable(output / config["path"], archive.read(config["path"]))
    project = {
        "project_id": PROJECT_NAME,
        "root": str(output),
        "task": "causal-language-model-sft-lora",
        "objective": {"metric": "val_loss", "direction": "min"},
        "metric_definition": "wechat-persona-quality-v1",
        "evaluation_protocol": "wechat-persona-style-v1",
        "experiment_id": experiment_id,
        "dataset": snapshot,
        "releases": {
            release.manifest["release_id"]: {
                "path": registered_archive.name,
                "sha256": release.manifest["archive_sha256"],
                "code_revision": release.manifest["code_revision"],
                "environment_digest": release.manifest["environment_digest"],
                "entrypoint": ENTRYPOINT,
                "deadline_enforced": True,
            }
        },
        "configs": configs,
        "evaluation_isolated": True,
        "quality_gates": [
            {"metric": "test_loss", "direction": "min", "threshold": 10.0},
            {"metric": "test_pii_leak_count", "direction": "min", "threshold": 0.0},
            {"metric": "test_canary_leak_count", "direction": "min", "threshold": 0.0},
            {"metric": "test_unsafe_behavior_count", "direction": "min", "threshold": 0.0},
        ],
        "artifact_paths": [
            "model/adapter_model.safetensors",
            "model/adapter_config.json",
            "reports/validation-quality.json",
            "reports/final-test-evaluation.json",
            "checkpoints/best-adapter.safetensors",
            "checkpoints/trainer_state.json",
        ],
        "model_artifact_path": "model/adapter_model.safetensors",
    }
    projects = {"schema_version": "galatea.registry/v1", "projects": [project]}
    release_id = release.manifest["release_id"]
    slots = [
        {"step_id": "baseline", "role": "baseline", "config_ids": ["formal-sft-v2-baseline"]},
        {"step_id": "trial", "role": "trial", "config_ids": ["formal-sft-v2-trial"]},
        {
            "step_id": "champion",
            "role": "champion",
            "config_ids": ["formal-sft-v2-champion", "formal-sft-v2-champion-trial"],
        },
        {"step_id": "evaluate", "role": "evaluate", "config_ids": ["formal-sft-v2-evaluate"]},
    ]
    campaign = {
        "campaign_id": campaign_id,
        "project_id": PROJECT_NAME,
        "request_revision": 1,
        "expires_at": int(expires_at or time.time() + 30 * 24 * 60 * 60),
        "approved_by": approved_by,
        "slots": [
            {
                "step_id": step_id,
                "role": role,
                "config_ids": config_ids,
                "release_ids": [release_id],
                "max_attempts": 1,
            }
            for slot in slots
            for step_id, role, config_ids in [
                (slot["step_id"], slot["role"], slot["config_ids"])
            ]
        ],
        "budget": {"cpu_seconds": 58560, "gpu_seconds": 14640, "max_trials": 1},
    }
    validate_registration_materials(projects, campaign)
    readme = """# wechat-persona MCP registration material

`projects.json` and `campaign.json` are schema-valid inputs for the independent
Galatea MCP. Before registration, an administrator must replace every `ADMIN_*`
or `PENDING_*` value, bind distinct immutable train/validation/test VersionIds,
confirm evaluator-only test access, set the real MLflow experiment ID, and
rebuild from a clean Git commit. V1 does not require an Ed25519 key; the MCP
issues the execution binding and the fixed Driver verifies the Ray Job identity
and metadata. This builder never uploads, registers, submits a Ray Job, or
starts training.
"""
    files = {
        "projects": output / "projects.json",
        "campaign": output / "campaign.json",
        "readme": output / "README.md",
        "release": registered_archive,
    }
    _write_immutable(files["projects"], canonical_json(projects))
    _write_immutable(files["campaign"], canonical_json(campaign))
    _write_immutable(files["readme"], readme.encode("utf-8"))
    return files
