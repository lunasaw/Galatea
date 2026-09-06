"""构建、发布并提交 House Prices 的不可变 Ray Job release。"""

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
DEFAULT_PREFIX = "ray-runtime/ray-kaggle-house-prices"
DEFAULT_RAY_ADDRESS = "http://127.0.0.1:8265"
DEFAULT_RUNTIME_MODE = "conda"
RUNTIME_MODES = ("conda", "pip")
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
    with path.open("rb") as file_handle:
        for block in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _write_immutable(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise FileExistsError(f"拒绝覆盖内容不同的 release 文件: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _is_runtime_file(relative_path: Path) -> bool:
    if not relative_path.parts or relative_path.parts[0] in {"job", "notebooks", "tests"}:
        return False
    if any(part in {".git", ".ipynb_checkpoints", ".pytest_cache", "__pycache__"} for part in relative_path.parts):
        return False
    return relative_path.suffix not in {".pyc", ".pyo"}


def create_working_dir_archive(project_root: Path, destination: Path) -> None:
    """创建时间戳固定且排除测试与发布脚本的工作目录压缩包。"""

    project_root = project_root.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in sorted(project_root.rglob("*")):
            relative = source.relative_to(project_root)
            if not source.is_file() or not _is_runtime_file(relative):
                continue
            if source.is_symlink():
                raise ValueError(f"release 不允许符号链接: {source}")
            info = zipfile.ZipInfo(relative.as_posix(), date_time=FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | stat.S_IMODE(source.stat().st_mode)) << 16
            archive.writestr(info, source.read_bytes())


def _normalize_zip(path: Path) -> None:
    normalized = path.with_name(f".{path.name}.normalized")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(normalized, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as target:
        for item in sorted(source.infolist(), key=lambda value: value.filename):
            info = zipfile.ZipInfo(item.filename, date_time=FIXED_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = item.external_attr
            target.writestr(info, source.read(item.filename))
    normalized.replace(path)


def build_runtime_environment(
    project_root: Path,
    *,
    runtime_mode: str = DEFAULT_RUNTIME_MODE,
    pip_requirements: Path | None = None,
    pip_packages: Sequence[str] = (),
) -> tuple[dict[str, Any], dict[str, str]]:
    """Return a Ray dependency section and immutable environment identity."""

    if runtime_mode not in RUNTIME_MODES:
        raise ValueError(f"unsupported runtime mode: {runtime_mode}")
    root = project_root.resolve()
    import yaml

    if runtime_mode == "conda":
        if pip_requirements is not None or pip_packages:
            raise ValueError("pip requirements or packages are only valid in pip mode")
        environment_path = root / "conda.yaml"
        if not environment_path.is_file():
            raise FileNotFoundError(f"project Conda environment does not exist: {environment_path}")
        environment = yaml.safe_load(environment_path.read_text(encoding="utf-8"))
        if not isinstance(environment, dict) or not isinstance(environment.get("dependencies"), list):
            raise ValueError(f"project Conda environment is invalid: {environment_path}")
        return (
            {"conda": environment},
            {"runtime_mode": "conda", "environment_source": "conda.yaml", "environment_sha256": _sha256(environment_path)},
        )

    if pip_requirements is None and not pip_packages:
        raise ValueError("pip mode requires explicit pip requirements or packages")
    packages = []
    if pip_requirements is not None:
        packages.extend(
            line.strip()
            for line in pip_requirements.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    packages.extend(pip_packages)
    if not packages:
        raise ValueError("pip mode requires explicit pip requirements or packages")
    source = str(pip_requirements) if pip_requirements else "pip-packages"
    digest = _sha256(pip_requirements) if pip_requirements else hashlib.sha256(_canonical_json({"packages": packages})).hexdigest()
    return (
        {"pip": {"packages": packages, "pip_check": True}},
        {"runtime_mode": "pip", "environment_source": source, "environment_sha256": digest},
    )


def build_wheel(archive: Path, destination: Path) -> Path:
    """从同一工作目录构建不带依赖的项目 wheel。"""

    destination.mkdir(parents=True, exist_ok=True)
    source = destination.parent / "wheel-source"
    source.mkdir()
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(source)
    # wheel 构建需要读取项目根目录的 pyproject，而工作目录压缩包的内容正好就是该根目录。
    result = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--quiet", "--no-deps", "--no-build-isolation", "--wheel-dir", str(destination), str(source)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"wheel 构建失败: {result.stderr.strip() or result.stdout.strip()}")
    wheels = sorted(destination.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"期望一个 wheel，实际得到 {len(wheels)} 个")
    _normalize_zip(wheels[0])
    return wheels[0]


def _git_identity(project_root: Path) -> dict[str, Any]:
    repository_root = project_root.parents[1]

    def command(*args: str) -> str | None:
        result = subprocess.run(["git", *args], cwd=repository_root, capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    return {"commit": command("rev-parse", "HEAD") or "uncommitted", "dirty": bool(command("status", "--porcelain"))}


def build_release(project_root: Path, output_root: Path, *, bucket: str = DEFAULT_BUCKET, prefix: str = DEFAULT_PREFIX, setup_timeout_seconds: int = 600, runtime_mode: str = DEFAULT_RUNTIME_MODE, pip_requirements: Path | None = None, pip_packages: Sequence[str] = ()) -> BuiltRelease:
    """生成内容寻址的工作目录、wheel、运行时环境和 release manifest。"""

    project_root = project_root.resolve()
    output_root = output_root.resolve()
    if output_root == project_root or output_root.is_relative_to(project_root):
        raise ValueError("release 输出目录必须在源项目之外")
    dependency_runtime_env, environment_identity = build_runtime_environment(project_root, runtime_mode=runtime_mode, pip_requirements=pip_requirements, pip_packages=pip_packages)
    with tempfile.TemporaryDirectory(prefix="ray-kaggle-house-prices-release-") as temporary:
        stage = Path(temporary)
        archive = stage / "working-dir.zip"
        create_working_dir_archive(project_root, archive)
        wheel = build_wheel(archive, stage / "wheel")
        import ray

        environment = {"python_version": ".".join(map(str, sys.version_info[:3])), "ray_version": ray.__version__}
        packages = {"working_dir_sha256": _sha256(archive), "wheel_sha256": _sha256(wheel)}
        identity = {"build_environment": environment, "environment": environment_identity, "git": _git_identity(project_root), "packages": packages}
        release_id = hashlib.sha256(_canonical_json(identity)).hexdigest()[:20]
        directory = output_root / release_id
        directory.mkdir(parents=True, exist_ok=True)
        local_archive = directory / archive.name
        local_wheel = directory / wheel.name
        for source, target in ((archive, local_archive), (wheel, local_wheel)):
            if target.exists() and _sha256(target) != _sha256(source):
                raise FileExistsError(f"拒绝覆盖不同的 release 文件: {target}")
            if not target.exists():
                shutil.copyfile(source, target)

    normalized_prefix = prefix.strip("/")
    release_prefix = f"{normalized_prefix}/{release_id}"
    working_key = f"{release_prefix}/{local_archive.name}"
    wheel_key = f"{release_prefix}/{local_wheel.name}"
    runtime_env = {
        "working_dir": f"s3://{bucket}/{working_key}",
        "py_modules": [f"s3://{bucket}/{wheel_key}"],
        **dependency_runtime_env,
        "config": {"setup_timeout_seconds": setup_timeout_seconds},
    }
    from ray.runtime_env import RuntimeEnv

    RuntimeEnv(**runtime_env)
    manifest = {
        "schema_version": 1,
        "project": "ray-kaggle-house-prices",
        "release_id": release_id,
        "build_environment": environment,
        **environment_identity,
        "git": identity["git"],
        "runtime_env": runtime_env,
        "s3": {"bucket": bucket, "prefix": release_prefix},
        "files": {
            "working_dir": {"filename": local_archive.name, "key": working_key, "sha256": _sha256(local_archive), "size_bytes": local_archive.stat().st_size},
            "py_module": {"filename": local_wheel.name, "key": wheel_key, "sha256": _sha256(local_wheel), "size_bytes": local_wheel.stat().st_size},
        },
    }
    runtime_env_path = directory / "runtime-env.yaml"
    manifest_path = directory / "release.json"
    import yaml

    _write_immutable(runtime_env_path, yaml.safe_dump(runtime_env, sort_keys=False).encode("utf-8"))
    _write_immutable(manifest_path, _canonical_json(manifest))
    return BuiltRelease(directory, manifest, manifest_path, runtime_env_path)


def load_aws_environment(path: Path, environ: MutableMapping[str, str] | None = None) -> None:
    """读取受支持的 AWS/MinIO 环境变量，不执行 shell 语法。"""

    target = os.environ if environ is None else environ
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"无效环境变量行: {path}:{number}")
        key, value = (part.strip() for part in line.split("=", 1))
        if key in AWS_ENV_KEYS:
            target.setdefault(key, value.strip("'\""))


def _upload(client: Any, bucket: str, key: str, path: Path) -> str:
    digest = _sha256(path)
    try:
        existing = client.head_object(Bucket=bucket, Key=key)
    except Exception as error:
        status = getattr(error, "response", {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status != 404:
            raise
    else:
        if existing.get("Metadata", {}).get("sha256") != digest or existing.get("ContentLength") != path.stat().st_size:
            raise FileExistsError(f"拒绝覆盖不同的对象: s3://{bucket}/{key}")
        return "existing"
    with path.open("rb") as handle:
        client.put_object(Bucket=bucket, Key=key, Body=handle, ContentType=mimetypes.guess_type(path.name)[0] or "application/octet-stream", Metadata={"sha256": digest})
    return "uploaded"


def publish_release(release: BuiltRelease, endpoint_url: str = DEFAULT_ENDPOINT_URL) -> dict[str, str]:
    """将 release 四个文件写入 MinIO，并保证不可变性。"""

    import boto3

    client = boto3.Session().client("s3", endpoint_url=endpoint_url)
    bucket = release.manifest["s3"]["bucket"]
    prefix = release.manifest["s3"]["prefix"]
    statuses = {}
    for source in (release.manifest_path, release.runtime_env_path, release.directory / release.manifest["files"]["working_dir"]["filename"], release.directory / release.manifest["files"]["py_module"]["filename"]):
        key = f"{prefix}/{source.name}"
        statuses[f"s3://{bucket}/{key}"] = _upload(client, bucket, key, source)
    return statuses


def build_training_entrypoint(config: str, mode: str) -> str:
    """生成固定的非 shell 训练命令字符串。"""

    command = ["python", "scripts/train.py", "--config", config]
    if mode == "check-config":
        command.append("--check-config")
    elif mode == "plan":
        command.append("--plan")
    elif mode != "train":
        raise ValueError(f"不支持的提交模式: {mode}")
    return shlex.join(command)


def load_release_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError(f"unsupported release manifest: {path}")
    runtime_env = manifest.get("runtime_env")
    if not isinstance(runtime_env, dict):
        raise ValueError(f"release manifest has no runtime_env: {path}")
    runtime_mode = manifest.get("runtime_mode")
    if runtime_mode is not None:
        if runtime_mode not in RUNTIME_MODES:
            raise ValueError(f"release manifest has unsupported runtime_mode: {path}")
        digest = manifest.get("environment_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"release manifest has invalid environment_sha256: {path}")
        if runtime_mode not in runtime_env:
            raise ValueError(f"release manifest runtime_env does not match runtime_mode: {path}")
    from ray.runtime_env import RuntimeEnv

    RuntimeEnv(**runtime_env)
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="House Prices Ray release 工具")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/ray-kaggle-house-prices-job"))
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_AWS_ENV_FILE)
    parser.add_argument("--endpoint-url", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-cd", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--address", default=DEFAULT_RAY_ADDRESS)
    parser.add_argument("--submission-id")
    parser.add_argument("--config", default="configs/smoke.yaml")
    parser.add_argument("--mode", choices=("check-config", "plan", "train"), default="check-config")
    parser.add_argument("--runtime-mode", choices=RUNTIME_MODES)
    parser.add_argument("--pip-requirements", type=Path)
    parser.add_argument("--pip-package", dest="pip_packages", action="append", default=[])
    return parser


def ci_main(arguments: Sequence[str] | None = None) -> int:
    """构建、可选发布并输出 release 结果。"""

    parsed = _parser().parse_args(arguments)
    runtime_mode = parsed.runtime_mode or DEFAULT_RUNTIME_MODE
    release = build_release(parsed.project_root, parsed.output_dir, bucket=parsed.bucket, prefix=parsed.prefix, runtime_mode=runtime_mode, pip_requirements=parsed.pip_requirements, pip_packages=parsed.pip_packages)
    statuses = {}
    if not parsed.dry_run:
        load_aws_environment(parsed.env_file)
        statuses = publish_release(release, parsed.endpoint_url or os.environ.get("AWS_ENDPOINT_URL_S3", DEFAULT_ENDPOINT_URL))
    print(json.dumps({"dry_run": parsed.dry_run, "runtime_mode": release.manifest.get("runtime_mode", DEFAULT_RUNTIME_MODE), "environment_source": release.manifest.get("environment_source", "conda.yaml"), "environment_sha256": release.manifest.get("environment_sha256"), "manifest_path": str(release.manifest_path), "release_id": release.manifest["release_id"], "objects": statuses}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def publish_main(arguments: Sequence[str] | None = None) -> int:
    return ci_main(arguments)


def cd_main(arguments: Sequence[str] | None = None) -> int:
    """验证 release manifest，并在非 dry-run 时提交 Ray Job。"""

    parsed = _parser().parse_args(arguments)
    if parsed.manifest is None:
        raise SystemExit("--manifest 是必需的")
    manifest = load_release_manifest(parsed.manifest)
    if (
        parsed.runtime_mode is not None
        and manifest.get("runtime_mode") is not None
        and parsed.runtime_mode != manifest.get("runtime_mode")
    ):
        raise ValueError(
            f"manifest runtime_mode is {manifest.get('runtime_mode')!r}, not {parsed.runtime_mode!r}"
        )
    entrypoint = build_training_entrypoint(parsed.config, parsed.mode)
    result: dict[str, Any] = {"address": parsed.address, "entrypoint": entrypoint, "runtime_mode": manifest.get("runtime_mode"), "environment_source": manifest.get("environment_source"), "environment_sha256": manifest.get("environment_sha256"), "submission_id": parsed.submission_id or f"ray-kaggle-house-prices-{Path(parsed.config).stem}-{datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%S')}-{secrets.token_hex(4)}"}
    if not parsed.dry_run:
        from ray.job_submission import JobSubmissionClient

        client = JobSubmissionClient(parsed.address)
        result["job_id"] = client.submit_job(entrypoint=entrypoint, submission_id=result["submission_id"], runtime_env=manifest["runtime_env"], metadata={"project": manifest["project"], "release_id": manifest["release_id"], "runtime_mode": manifest.get("runtime_mode"), "environment_sha256": manifest.get("environment_sha256")})
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def submit_main(arguments: Sequence[str] | None = None) -> int:
    return cd_main(arguments)
