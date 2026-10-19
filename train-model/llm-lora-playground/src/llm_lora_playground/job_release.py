"""Build and publish immutable Ray Runtime Environment releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, MutableMapping, Sequence


DEFAULT_AWS_ENV_FILE = Path("/etc/minio/training-data-s3.env")
DEFAULT_BUCKET = "training-data"
DEFAULT_ENDPOINT_URL = "http://127.0.0.1:9000"
DEFAULT_PREFIX = "ray-runtime/llm-lora-playground"
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
AWS_ENV_KEYS = {
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "AWS_DEFAULT_REGION", "AWS_REGION", "AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_S3",
}


@dataclass(frozen=True)
class BuiltRelease:
    directory: Path
    manifest: dict[str, Any]
    manifest_path: Path
    runtime_env_path: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _write_immutable(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise FileExistsError(f"refusing to overwrite a different release file: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _runtime_file(relative: Path) -> bool:
    if not relative.parts or relative.parts[0] in {"job", "notebooks", "tests"}:
        return False
    if any(part in {".git", ".ipynb_checkpoints", ".pytest_cache", "__pycache__"} for part in relative.parts):
        return False
    return relative.suffix not in {".pyc", ".pyo"}


def create_working_dir_archive(project_root: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in sorted(project_root.resolve().rglob("*")):
            relative = source.relative_to(project_root.resolve())
            if not source.is_file() or not _runtime_file(relative):
                continue
            if source.is_symlink():
                raise ValueError(f"release does not allow symlinks: {source}")
            info = zipfile.ZipInfo(relative.as_posix(), date_time=FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | stat.S_IMODE(source.stat().st_mode)) << 16
            archive.writestr(info, source.read_bytes())


def _git_identity(project_root: Path) -> dict[str, Any]:
    repository_root = project_root.parents[1]

    def run(*args: str) -> str | None:
        result = subprocess.run(["git", *args], cwd=repository_root, capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    return {"commit": run("rev-parse", "HEAD") or "unresolved", "dirty": bool(run("status", "--porcelain"))}


def build_release(
    project_root: Path,
    output_root: Path,
    *,
    bucket: str = DEFAULT_BUCKET,
    prefix: str = DEFAULT_PREFIX,
    py_executable: str = "/data/conda/envs/ray-llm-py312/bin/python",
    repository_root: Path | None = None,
    mlflow_tracking_uri: str = "http://127.0.0.1:5000",
    mlflow_experiment_name: str = "llm-lora-playground",
) -> BuiltRelease:
    project_root = project_root.resolve()
    output_root = output_root.resolve()
    repository_root = (repository_root or project_root.parents[1]).resolve()
    if not (repository_root / "train-model" / "llm-lora-playground").is_dir():
        raise ValueError(f"repository_root is not the expected Galatea checkout: {repository_root}")
    if output_root == project_root or output_root.is_relative_to(project_root):
        raise ValueError("release output must stay outside the source project")
    environment_path = project_root / "conda.yaml"
    if not environment_path.is_file():
        raise FileNotFoundError(environment_path)
    with tempfile.TemporaryDirectory(prefix="llm-lora-release-") as directory:
        stage = Path(directory)
        archive = stage / "working-dir.zip"
        py_module = stage / "py-modules.zip"
        create_working_dir_archive(project_root, archive)
        create_working_dir_archive(project_root / "src", py_module)
        import ray

        identity = {
            "build_environment": {"python_version": ".".join(map(str, sys.version_info[:3])), "ray_version": ray.__version__},
            "environment_sha256": _sha256(environment_path),
            "git": _git_identity(project_root),
            "working_dir_sha256": _sha256(archive),
            "py_module_sha256": _sha256(py_module),
            "py_executable": py_executable,
            "repository_root": str(repository_root),
            "mlflow_tracking_uri": mlflow_tracking_uri,
            "mlflow_experiment_name": mlflow_experiment_name,
        }
        release_id = hashlib.sha256(_canonical(identity)).hexdigest()[:20]
        release_directory = output_root / release_id
        release_directory.mkdir(parents=True, exist_ok=True)
        local_archive = release_directory / archive.name
        local_py_module = release_directory / py_module.name
        for source, target in ((archive, local_archive), (py_module, local_py_module)):
            if target.exists() and _sha256(target) != _sha256(source):
                raise FileExistsError(f"refusing to overwrite a different release object: {target}")
            if not target.exists():
                shutil.copyfile(source, target)
    release_prefix = f"{prefix.strip('/')}/{release_id}"
    working_key = f"{release_prefix}/{local_archive.name}"
    py_module_key = f"{release_prefix}/{local_py_module.name}"
    runtime_env = {
        "working_dir": f"s3://{bucket}/{working_key}",
        "py_modules": [f"s3://{bucket}/{py_module_key}"],
        "py_executable": py_executable,
        "env_vars": {
            "CODE_REVISION": identity["git"]["commit"],
            "GALATEA_REPOSITORY_ROOT": str(repository_root),
            "MLFLOW_TRACKING_URI": mlflow_tracking_uri,
            "MLFLOW_EXPERIMENT_NAME": mlflow_experiment_name,
            "RAYLLM_ENABLE_REQUEST_PROMPT_LOGS": "0",
            "LD_LIBRARY_PATH": "/data/conda/envs/ray-llm-py312/lib:/data/conda/envs/ray-llm-py312/lib/python3.12/site-packages/nvidia/cuda_runtime/lib",
        },
        "config": {"setup_timeout_seconds": 600},
    }
    from ray.runtime_env import RuntimeEnv

    RuntimeEnv(**runtime_env)
    manifest = {
        "schema_version": 1,
        "project": "llm-lora-playground",
        "release_id": release_id,
        "build_environment": identity["build_environment"],
        "runtime_mode": "py_executable",
        "environment_source": "conda.yaml",
        "environment_sha256": identity["environment_sha256"],
        "git": identity["git"],
        "runtime_env": runtime_env,
        "s3": {"bucket": bucket, "prefix": release_prefix},
        "files": {
            "working_dir": {"filename": local_archive.name, "key": working_key, "sha256": _sha256(local_archive), "size_bytes": local_archive.stat().st_size},
            "py_module": {"filename": local_py_module.name, "key": py_module_key, "sha256": _sha256(local_py_module), "size_bytes": local_py_module.stat().st_size},
        },
    }
    manifest_path = release_directory / "release.json"
    runtime_env_path = release_directory / "runtime-env.json"
    _write_immutable(manifest_path, _canonical(manifest))
    _write_immutable(runtime_env_path, _canonical(runtime_env))
    return BuiltRelease(release_directory, manifest, manifest_path, runtime_env_path)


def load_aws_environment(path: Path, environ: MutableMapping[str, str] | None = None) -> None:
    target = os.environ if environ is None else environ
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"invalid environment line: {path}:{number}")
        key, value = (part.strip() for part in line.split("=", 1))
        if key in AWS_ENV_KEYS:
            target.setdefault(key, value.strip("'\""))


def _upload(client: Any, bucket: str, key: str, path: Path) -> str:
    digest = _sha256(path)
    try:
        existing = client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        status = getattr(exc, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status != 404:
            raise
    else:
        if existing.get("Metadata", {}).get("sha256") != digest or existing.get("ContentLength") != path.stat().st_size:
            raise FileExistsError(f"object exists with different content: s3://{bucket}/{key}")
        return "existing"
    with path.open("rb") as handle:
        client.put_object(Bucket=bucket, Key=key, Body=handle, ContentType=mimetypes.guess_type(path.name)[0] or "application/octet-stream", Metadata={"sha256": digest})
    return "uploaded"


def publish_release(release: BuiltRelease, endpoint_url: str = DEFAULT_ENDPOINT_URL) -> dict[str, str]:
    import boto3

    client = boto3.Session().client("s3", endpoint_url=endpoint_url)
    bucket = release.manifest["s3"]["bucket"]
    statuses = {}
    for source in (
        release.directory / release.manifest["files"]["working_dir"]["filename"],
        release.directory / release.manifest["files"]["py_module"]["filename"],
    ):
        key = f"{release.manifest['s3']['prefix']}/{source.name}"
        statuses[f"s3://{bucket}/{key}"] = _upload(client, bucket, key, source)
    return statuses


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output-dir", type=Path, default=Path("/data/ai/chenzhangyue/code/galatea/platform-data/llm-lora-playground-release"))
    parser.add_argument("--env-file", type=Path, default=DEFAULT_AWS_ENV_FILE)
    parser.add_argument("--endpoint-url", default=DEFAULT_ENDPOINT_URL)
    parser.add_argument("--repository-root", type=Path, default=Path("/data/ai/chenzhangyue/code/galatea"))
    parser.add_argument("--mlflow-tracking-uri", default=os.environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000"))
    parser.add_argument("--mlflow-experiment-name", default=os.environ.get("MLFLOW_EXPERIMENT_NAME", "llm-lora-playground"))
    parser.add_argument("--dry-run", action="store_true")
    parsed = parser.parse_args(arguments)
    release = build_release(
        parsed.project_root,
        parsed.output_dir,
        repository_root=parsed.repository_root,
        mlflow_tracking_uri=parsed.mlflow_tracking_uri,
        mlflow_experiment_name=parsed.mlflow_experiment_name,
    )
    objects = {}
    if not parsed.dry_run:
        load_aws_environment(parsed.env_file)
        objects = publish_release(release, parsed.endpoint_url)
    print(json.dumps({"release_id": release.manifest["release_id"], "manifest_path": str(release.manifest_path), "objects": objects, "dry_run": parsed.dry_run}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
