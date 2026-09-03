"""Build, publish, and submit immutable Ray Job runtime packages."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence


DEFAULT_AWS_ENV_FILE = Path("/etc/minio/training-data-s3.env")
DEFAULT_BUCKET = "training-data"
DEFAULT_ENDPOINT_URL = "http://127.0.0.1:9000"
DEFAULT_PREFIX = "ray-runtime/ray-handwritten-digits"
DEFAULT_RAY_ADDRESS = "http://127.0.0.1:8265"
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
AWS_ENV_KEYS = {
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_DEFAULT_REGION",
    "AWS_REGION",
    "AWS_ENDPOINT_URL",
    "AWS_ENDPOINT_URL_S3",
}
EXCLUDED_TOP_LEVEL = {"job", "notebooks", "tests"}
EXCLUDED_PARTS = {
    ".git",
    ".ipynb_checkpoints",
    ".pytest_cache",
    "__pycache__",
}


@dataclass(frozen=True)
class BuiltRelease:
    """Local files and metadata for one immutable runtime release."""

    directory: Path
    manifest: dict[str, Any]
    manifest_path: Path
    runtime_env_path: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for block in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    ).encode("utf-8") + b"\n"


def _write_immutable(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise FileExistsError(f"refusing to overwrite different file: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _is_runtime_file(relative_path: Path) -> bool:
    if not relative_path.parts:
        return False
    if relative_path.parts[0] in EXCLUDED_TOP_LEVEL:
        return False
    if any(part in EXCLUDED_PARTS for part in relative_path.parts):
        return False
    if relative_path.suffix in {".pyc", ".pyo"}:
        return False
    return True


def create_working_dir_archive(project_root: Path, destination: Path) -> None:
    """Create a deterministic ZIP with only files needed by the remote driver."""

    project_root = project_root.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        destination,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for source_path in sorted(project_root.rglob("*")):
            relative_path = source_path.relative_to(project_root)
            if not source_path.is_file() or not _is_runtime_file(relative_path):
                continue
            if source_path.is_symlink():
                raise ValueError(
                    f"runtime package must not contain symlinks: {source_path}"
                )
            archive_info = zipfile.ZipInfo(
                relative_path.as_posix(),
                date_time=FIXED_ZIP_TIMESTAMP,
            )
            archive_info.compress_type = zipfile.ZIP_DEFLATED
            archive_info.external_attr = (
                stat.S_IFREG | stat.S_IMODE(source_path.stat().st_mode)
            ) << 16
            archive.writestr(archive_info, source_path.read_bytes())


def normalize_zip_archive(archive_path: Path) -> None:
    """Remove build-time ZIP metadata so identical wheels hash identically."""

    normalized_path = archive_path.with_name(f".{archive_path.name}.normalized")
    with zipfile.ZipFile(archive_path) as source_archive, zipfile.ZipFile(
        normalized_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as normalized_archive:
        source_entries = sorted(
            source_archive.infolist(),
            key=lambda item: item.filename,
        )
        for source_info in source_entries:
            normalized_info = zipfile.ZipInfo(
                source_info.filename,
                date_time=FIXED_ZIP_TIMESTAMP,
            )
            normalized_info.compress_type = zipfile.ZIP_DEFLATED
            normalized_info.external_attr = source_info.external_attr
            normalized_info.create_system = source_info.create_system
            normalized_archive.writestr(
                normalized_info,
                source_archive.read(source_info.filename),
            )
    normalized_path.replace(archive_path)


def build_wheel(working_dir_archive: Path, destination: Path) -> Path:
    """Build the worker module wheel from a temporary copy of the release source."""

    destination.mkdir(parents=True)
    source_copy = destination.parent / "wheel-source"
    source_copy.mkdir()
    with zipfile.ZipFile(working_dir_archive) as archive:
        archive.extractall(source_copy)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--quiet",
            "--no-cache-dir",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(destination),
            str(source_copy),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"wheel build failed: {detail}")
    wheels = sorted(destination.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one wheel, found {len(wheels)} in {destination}")
    normalize_zip_archive(wheels[0])
    return wheels[0]


def _git_identity(project_root: Path) -> dict[str, Any]:
    repository_root = project_root.parents[1]

    def run_git(*arguments: str) -> str | None:
        result = subprocess.run(
            ["git", *arguments],
            cwd=repository_root,
            check=False,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    commit = run_git("rev-parse", "HEAD")
    status_output = run_git("status", "--porcelain")
    return {
        "commit": commit or "uncommitted",
        "dirty": bool(status_output) if status_output is not None else None,
    }


def _normalize_s3_location(bucket: str, prefix: str) -> tuple[str, str]:
    normalized_bucket = bucket.strip()
    normalized_prefix = prefix.strip("/")
    if not normalized_bucket or "/" in normalized_bucket:
        raise ValueError("bucket must be a non-empty S3 bucket name")
    if not normalized_prefix:
        raise ValueError("prefix must be a non-empty S3 object prefix")
    if any(part in {"", ".", ".."} for part in normalized_prefix.split("/")):
        raise ValueError("prefix must not contain empty, '.' or '..' path segments")
    return normalized_bucket, normalized_prefix


def build_release(
    project_root: Path,
    output_root: Path,
    bucket: str = DEFAULT_BUCKET,
    prefix: str = DEFAULT_PREFIX,
    setup_timeout_seconds: int = 600,
) -> BuiltRelease:
    """Build a content-addressed working directory, wheel, and runtime env."""

    project_root = project_root.resolve()
    output_root = output_root.resolve()
    if output_root == project_root or output_root.is_relative_to(project_root):
        raise ValueError("release output must be outside the source project")
    bucket, prefix = _normalize_s3_location(bucket, prefix)
    if setup_timeout_seconds <= 0:
        raise ValueError("setup timeout must be positive")

    with tempfile.TemporaryDirectory(prefix="ray-handwritten-digits-release-") as temporary:
        stage = Path(temporary)
        working_dir_archive = stage / "working-dir.zip"
        create_working_dir_archive(project_root, working_dir_archive)
        wheel = build_wheel(working_dir_archive, stage / "wheel")
        package_identity = {
            "working_dir_sha256": _sha256(working_dir_archive),
            "wheel_sha256": _sha256(wheel),
        }
        git_identity = _git_identity(project_root)
        python_version = (
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        )
        import ray

        build_environment = {
            "python_version": python_version,
            "ray_version": ray.__version__,
        }
        release_identity = {
            "build_environment": build_environment,
            "git": git_identity,
            "packages": package_identity,
        }
        release_id = hashlib.sha256(_canonical_json(release_identity)).hexdigest()[:20]
        release_prefix = f"{prefix}/{release_id}"
        release_directory = output_root / release_id
        release_directory.mkdir(parents=True, exist_ok=True)

        local_working_dir = release_directory / working_dir_archive.name
        local_wheel = release_directory / wheel.name
        for source, target in (
            (working_dir_archive, local_working_dir),
            (wheel, local_wheel),
        ):
            if target.exists() and _sha256(target) != _sha256(source):
                raise FileExistsError(f"refusing to overwrite different file: {target}")
            if not target.exists():
                shutil.copyfile(source, target)

    working_dir_key = f"{release_prefix}/{local_working_dir.name}"
    wheel_key = f"{release_prefix}/{local_wheel.name}"
    runtime_env = {
        "working_dir": f"s3://{bucket}/{working_dir_key}",
        "py_modules": [f"s3://{bucket}/{wheel_key}"],
        "config": {"setup_timeout_seconds": setup_timeout_seconds},
    }

    from ray.runtime_env import RuntimeEnv

    RuntimeEnv(**runtime_env)
    manifest = {
        "schema_version": 1,
        "project": "ray-handwritten-digits",
        "release_id": release_id,
        "build_environment": build_environment,
        "git": git_identity,
        "runtime_env": runtime_env,
        "s3": {"bucket": bucket, "prefix": release_prefix},
        "files": {
            "working_dir": {
                "filename": local_working_dir.name,
                "key": working_dir_key,
                "sha256": _sha256(local_working_dir),
                "size_bytes": local_working_dir.stat().st_size,
            },
            "py_module": {
                "filename": local_wheel.name,
                "key": wheel_key,
                "sha256": _sha256(local_wheel),
                "size_bytes": local_wheel.stat().st_size,
            },
        },
    }
    runtime_env_path = release_directory / "runtime-env.yaml"
    manifest_path = release_directory / "release.json"

    import yaml

    _write_immutable(
        runtime_env_path,
        yaml.safe_dump(runtime_env, sort_keys=False).encode("utf-8"),
    )
    _write_immutable(manifest_path, _canonical_json(manifest))
    return BuiltRelease(
        directory=release_directory,
        manifest=manifest,
        manifest_path=manifest_path,
        runtime_env_path=runtime_env_path,
    )


def load_aws_environment(
    env_file: Path,
    environ: MutableMapping[str, str] | None = None,
) -> None:
    """Load supported AWS variables without evaluating shell syntax."""

    environment = os.environ if environ is None else environ
    if not env_file.is_file():
        raise FileNotFoundError(f"S3 environment file does not exist: {env_file}")
    for line_number, raw_line in enumerate(
        env_file.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if "=" not in line:
            raise ValueError(f"invalid environment assignment at {env_file}:{line_number}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key not in AWS_ENV_KEYS:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        environment.setdefault(key, value)


def _s3_client(endpoint_url: str):
    try:
        import boto3
    except ImportError as error:
        raise RuntimeError("boto3 is required to publish Ray runtime packages") from error

    session = boto3.Session()
    if session.get_credentials() is None:
        raise RuntimeError("no AWS credentials are available for the MinIO publisher")
    region = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION")
    return session.client("s3", endpoint_url=endpoint_url, region_name=region)


def _upload_immutable(client: Any, bucket: str, key: str, source: Path) -> str:
    source_sha256 = _sha256(source)
    try:
        existing = client.head_object(Bucket=bucket, Key=key)
    except Exception as error:
        response = getattr(error, "response", {})
        error_code = str(response.get("Error", {}).get("Code", ""))
        status_code = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if error_code not in {"404", "NoSuchKey", "NotFound"} and status_code != 404:
            raise
    else:
        existing_sha256 = existing.get("Metadata", {}).get("sha256")
        if (
            existing_sha256 != source_sha256
            or existing.get("ContentLength") != source.stat().st_size
        ):
            raise FileExistsError(
                f"refusing to overwrite non-matching s3://{bucket}/{key}"
            )
        return "existing"

    content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    with source.open("rb") as file_handle:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=file_handle,
            ContentType=content_type,
            Metadata={"sha256": source_sha256},
        )
    return "uploaded"


def publish_release(release: BuiltRelease, endpoint_url: str) -> dict[str, str]:
    """Upload all release files without overwriting mismatched objects."""

    client = _s3_client(endpoint_url)
    bucket = release.manifest["s3"]["bucket"]
    prefix = release.manifest["s3"]["prefix"]
    client.head_bucket(Bucket=bucket)
    statuses: dict[str, str] = {}
    filenames = {
        release.manifest["files"]["working_dir"]["filename"],
        release.manifest["files"]["py_module"]["filename"],
        release.manifest_path.name,
        release.runtime_env_path.name,
    }
    for filename in sorted(filenames):
        source = release.directory / filename
        if not source.is_file():
            raise FileNotFoundError(f"release file does not exist: {source}")
        key = f"{prefix}/{source.name}"
        statuses[f"s3://{bucket}/{key}"] = _upload_immutable(
            client,
            bucket,
            key,
            source,
        )
    return statuses


def load_release_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError(f"unsupported release manifest: {path}")
    runtime_env = manifest.get("runtime_env")
    if not isinstance(runtime_env, dict):
        raise ValueError(f"release manifest has no runtime_env: {path}")

    from ray.runtime_env import RuntimeEnv

    RuntimeEnv(**runtime_env)
    return manifest


def build_training_entrypoint(
    config: str,
    mode: str,
    overrides: Sequence[str] = (),
    force: bool = False,
) -> str:
    command = ["python", "scripts/train.py", "--config", config]
    for override in overrides:
        command.extend(["--set", override])
    if mode == "check-config":
        command.append("--check-config")
    elif mode == "plan":
        command.append("--plan")
    elif mode != "train":
        raise ValueError(f"unsupported submission mode: {mode}")
    if force:
        command.append("--force")
    return shlex.join(command)


def generate_submission_id(config: str, mode: str) -> str:
    """Create a readable, collision-resistant ID for one submission attempt."""

    config_name = re.sub(
        r"[^a-z0-9]+",
        "-",
        Path(config).stem.lower(),
    ).strip("-")[:48].rstrip("-")
    if not config_name:
        config_name = "config"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz")
    attempt_token = secrets.token_hex(4)
    return f"ray-handwritten-digits-{config_name}-{mode}-{timestamp}-{attempt_token}"


def submit_release(
    manifest: Mapping[str, Any],
    address: str,
    submission_id: str,
    entrypoint: str,
) -> dict[str, Any]:
    from ray.job_submission import JobSubmissionClient

    metadata = {
        "project": str(manifest["project"]),
        "release_id": str(manifest["release_id"]),
        "working_dir_sha256": str(manifest["files"]["working_dir"]["sha256"]),
    }
    client = JobSubmissionClient(address)

    def existing_submission() -> dict[str, Any] | None:
        existing = next(
            (
                job
                for job in client.list_jobs()
                if job.submission_id == submission_id
            ),
            None,
        )
        if existing is None:
            return None
        existing_metadata = existing.metadata or {}
        same_release = all(
            existing_metadata.get(key) == value for key, value in metadata.items()
        )
        if existing.entrypoint != entrypoint or not same_release:
            raise RuntimeError(
                f"Ray Job {submission_id!r} already exists with a different "
                "release or entrypoint; choose a new --submission-id"
            )
        status = getattr(existing.status, "value", str(existing.status))
        if status in {"FAILED", "STOPPED"}:
            raise RuntimeError(
                f"Ray Job {submission_id!r} is {status}; choose a new "
                "--submission-id for the retry"
            )
        return {
            "job_id": submission_id,
            "reused": True,
            "status": status,
        }

    reused = existing_submission()
    if reused is not None:
        return reused
    try:
        job_id = client.submit_job(
            entrypoint=entrypoint,
            submission_id=submission_id,
            runtime_env=dict(manifest["runtime_env"]),
            metadata=metadata,
        )
    except RuntimeError as error:
        if "already exists" not in str(error):
            raise
        reused = existing_submission()
        if reused is None:
            raise
        return reused
    return {"job_id": job_id, "reused": False}


def _project_root_from_entrypoint() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_ray_jobs_address() -> str:
    api_address = os.environ.get("RAY_API_SERVER_ADDRESS")
    ray_address = os.environ.get("RAY_ADDRESS")
    if api_address:
        return api_address
    if ray_address and ray_address.startswith(("http://", "https://")):
        return ray_address
    return DEFAULT_RAY_ADDRESS


def _add_ci_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project-root",
        type=Path,
        default=_project_root_from_entrypoint(),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/tmp/ray-handwritten-digits-job"),
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("RAY_JOB_S3_BUCKET", DEFAULT_BUCKET),
    )
    parser.add_argument(
        "--prefix",
        default=os.environ.get("RAY_JOB_S3_PREFIX", DEFAULT_PREFIX),
    )
    parser.add_argument("--env-file", type=Path, default=DEFAULT_AWS_ENV_FILE)
    parser.add_argument("--endpoint-url")
    parser.add_argument("--setup-timeout-seconds", type=int, default=600)


def _add_cd_arguments(
    parser: argparse.ArgumentParser,
    *,
    require_manifest: bool,
) -> None:
    if require_manifest:
        parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--address",
        default=_default_ray_jobs_address(),
    )
    parser.add_argument(
        "--submission-id",
        help=(
            "explicit Ray Job submission ID; defaults to a unique ID containing "
            "the config, mode, UTC timestamp, and a random attempt token"
        ),
    )
    parser.add_argument("--config", default="configs/smoke.yaml")
    parser.add_argument(
        "--mode",
        choices=("check-config", "plan", "train"),
        default="check-config",
    )
    parser.add_argument("--set", dest="overrides", action="append", default=[])
    parser.add_argument("--force", action="store_true")


def build_and_publish_release(
    project_root: Path,
    output_dir: Path,
    *,
    bucket: str,
    prefix: str,
    env_file: Path,
    endpoint_url: str | None,
    setup_timeout_seconds: int,
    dry_run: bool,
) -> tuple[BuiltRelease, dict[str, Any]]:
    """Build a release and optionally upload its immutable files to MinIO."""

    release = build_release(
        project_root,
        output_dir,
        bucket=bucket,
        prefix=prefix,
        setup_timeout_seconds=setup_timeout_seconds,
    )
    statuses: dict[str, str] = {}
    resolved_endpoint = endpoint_url
    if not dry_run:
        load_aws_environment(env_file)
        resolved_endpoint = (
            resolved_endpoint
            or os.environ.get("AWS_ENDPOINT_URL_S3")
            or os.environ.get("AWS_ENDPOINT_URL")
            or DEFAULT_ENDPOINT_URL
        )
        statuses = publish_release(release, resolved_endpoint)
    result = {
        "dry_run": dry_run,
        "endpoint_url": resolved_endpoint if not dry_run else None,
        "manifest_path": str(release.manifest_path),
        "release_id": release.manifest["release_id"],
        "runtime_env_path": str(release.runtime_env_path),
        "objects": statuses,
    }
    return release, result


def deploy_release_manifest(
    manifest_path: Path,
    *,
    address: str,
    submission_id: str | None,
    config: str,
    mode: str,
    overrides: Sequence[str] = (),
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Validate a release manifest and optionally submit it to Ray Jobs."""

    manifest = load_release_manifest(manifest_path)
    resolved_submission_id = submission_id or generate_submission_id(config, mode)
    entrypoint = build_training_entrypoint(
        config,
        mode,
        overrides=overrides,
        force=force,
    )
    result: dict[str, Any] = {
        "address": address,
        "dry_run": dry_run,
        "entrypoint": entrypoint,
        "runtime_env": manifest["runtime_env"],
        "submission_id": resolved_submission_id,
    }
    if not dry_run:
        result.update(
            submit_release(
                manifest,
                address,
                resolved_submission_id,
                entrypoint,
            )
        )
    return result


def publish_main(arguments: Sequence[str] | None = None) -> int:
    """Compatibility entry point that performs only the CI publish stage."""

    parser = argparse.ArgumentParser(
        description="Build and publish an immutable Ray runtime env to MinIO",
    )
    _add_ci_arguments(parser)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build release files but do not contact MinIO",
    )
    parsed = parser.parse_args(arguments)
    _, result = build_and_publish_release(
        parsed.project_root,
        parsed.output_dir,
        bucket=parsed.bucket,
        prefix=parsed.prefix,
        env_file=parsed.env_file,
        endpoint_url=parsed.endpoint_url,
        setup_timeout_seconds=parsed.setup_timeout_seconds,
        dry_run=parsed.dry_run,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cd_main(arguments: Sequence[str] | None = None) -> int:
    """Run the CD stage for an already published release manifest."""

    parser = argparse.ArgumentParser(
        description="Submit a published MinIO-backed Ray Job release",
    )
    _add_cd_arguments(parser, require_manifest=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print the request without contacting Ray",
    )
    parsed = parser.parse_args(arguments)
    result = deploy_release_manifest(
        parsed.manifest,
        address=parsed.address,
        submission_id=parsed.submission_id,
        config=parsed.config,
        mode=parsed.mode,
        overrides=parsed.overrides,
        force=parsed.force,
        dry_run=parsed.dry_run,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def submit_main(arguments: Sequence[str] | None = None) -> int:
    """Compatibility alias for the CD entry point."""

    return cd_main(arguments)


def ci_main(arguments: Sequence[str] | None = None) -> int:
    """Build and publish a release, then run CD by default."""

    parser = argparse.ArgumentParser(
        description="Build, publish, and deploy a MinIO-backed Ray Job release",
    )
    _add_ci_arguments(parser)
    _add_cd_arguments(parser, require_manifest=False)
    parser.add_argument(
        "--no-cd",
        action="store_true",
        help="stop after building and publishing the release",
    )
    parser.add_argument(
        "--cd-dry-run",
        action="store_true",
        help="publish to MinIO but only validate the Ray submission request",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build locally and validate CD without contacting MinIO or Ray",
    )
    parsed = parser.parse_args(arguments)
    release, ci_result = build_and_publish_release(
        parsed.project_root,
        parsed.output_dir,
        bucket=parsed.bucket,
        prefix=parsed.prefix,
        env_file=parsed.env_file,
        endpoint_url=parsed.endpoint_url,
        setup_timeout_seconds=parsed.setup_timeout_seconds,
        dry_run=parsed.dry_run,
    )
    cd_result: dict[str, Any] | None = None
    if not parsed.no_cd:
        cd_result = deploy_release_manifest(
            release.manifest_path,
            address=parsed.address,
            submission_id=parsed.submission_id,
            config=parsed.config,
            mode=parsed.mode,
            overrides=parsed.overrides,
            force=parsed.force,
            dry_run=parsed.dry_run or parsed.cd_dry_run,
        )
    print(
        json.dumps(
            {"ci": ci_result, "cd": cd_result},
            indent=2,
            sort_keys=True,
        )
    )
    return 0
