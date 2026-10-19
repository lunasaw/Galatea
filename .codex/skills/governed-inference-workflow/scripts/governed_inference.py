#!/usr/bin/env python3
"""Evidence-bound controller for Codex-managed Galatea inference Jobs."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import stat
import subprocess
import tempfile
from typing import Any, Mapping, Sequence
from urllib import error, parse, request
import zipfile

import yaml


PLAN_SCHEMA = "galatea.inference-plan/v1"
EXECUTION_MODE = "governed-ray-serve-inference"
DEFAULT_RAY_ADDRESS = "http://127.0.0.1:8265"
DEFAULT_LD_LIBRARY_PATH = (
    "/data/conda/envs/ray-llm-py312/lib:"
    "/data/conda/envs/ray-llm-py312/lib/python3.12/site-packages/nvidia/cuda_runtime/lib"
)
HEX_DIGEST_LENGTH = 64
MAX_HTTP_RESPONSE_BYTES = 1024 * 1024
GOVERNED_METADATA_KEYS = (
    "galatea.execution.identity",
    "galatea.project",
    "galatea.release.id",
    "galatea.submission.id",
    "galatea.readiness.digest",
    "galatea.execution.mode",
    "galatea.promotable",
)


class GovernedInferenceError(RuntimeError):
    """Raised when a governed inference invariant is not satisfied."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_string(mapping: Mapping[str, Any], key: str, location: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GovernedInferenceError(f"{location}.{key} must be a non-empty string")
    return value.strip()


def _require_sha256(value: str, location: str, *, prefixed: bool = False) -> str:
    if prefixed and not value.startswith("sha256:"):
        raise GovernedInferenceError(f"{location} must use the sha256: prefix")
    raw = value.removeprefix("sha256:") if prefixed else value
    if len(raw) != HEX_DIGEST_LENGTH or any(char not in "0123456789abcdef" for char in raw):
        raise GovernedInferenceError(f"{location} must be a lowercase SHA-256 digest")
    return ("sha256:" + raw) if prefixed else raw


def _resolve_under(root: Path, relative: str, location: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute():
        raise GovernedInferenceError(f"{location} must be relative")
    resolved = (root / candidate).resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise GovernedInferenceError(f"{location} escapes its root")
    return resolved


def _load_json(path: Path, location: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GovernedInferenceError(f"cannot read {location}: {exc}") from exc
    if not isinstance(value, dict):
        raise GovernedInferenceError(f"{location} must contain a JSON object")
    return value


def _load_project(project_root: Path) -> dict[str, Any]:
    manifest_path = project_root / "galatea.project.yaml"
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise GovernedInferenceError(f"cannot read project manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise GovernedInferenceError("project manifest must be a YAML object")
    if manifest.get("apiVersion") != "galatea/v1" or manifest.get("kind") != "TrainingProject":
        raise GovernedInferenceError("project manifest must be a galatea/v1 TrainingProject")
    metadata = manifest.get("metadata")
    spec = manifest.get("spec")
    if not isinstance(metadata, dict) or not isinstance(spec, dict):
        raise GovernedInferenceError("project manifest lacks metadata or spec")
    if spec.get("executionBackend") != "ray":
        raise GovernedInferenceError("inference project must declare executionBackend: ray")
    entrypoints = spec.get("entrypoints")
    if not isinstance(entrypoints, dict):
        raise GovernedInferenceError("project manifest lacks fixed entrypoints")
    checked: dict[str, list[str]] = {}
    for name, terminal_flag in (("inferenceCheck", "--check-config"), ("inference", "--run")):
        argv = entrypoints.get(name)
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            raise GovernedInferenceError(f"spec.entrypoints.{name} must be a non-empty argv array")
        if argv.count("{config}") != 1 or terminal_flag not in argv:
            raise GovernedInferenceError(
                f"spec.entrypoints.{name} must contain one {{config}} and {terminal_flag}"
            )
        checked[name] = list(argv)
    return {
        "name": _required_string(metadata, "name", "metadata"),
        "manifest_path": manifest_path,
        "manifest_sha256": _sha256_file(manifest_path),
        "entrypoints": checked,
        "execution_backend": "ray",
    }


def _safe_archive_members(archive: zipfile.ZipFile) -> set[str]:
    names: set[str] = set()
    for item in archive.infolist():
        path = PurePosixPath(item.filename)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise GovernedInferenceError(f"release archive contains unsafe path: {item.filename}")
        mode = item.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise GovernedInferenceError(f"release archive contains symlink: {item.filename}")
        names.add(path.as_posix())
    return names


def _validate_release(
    release_manifest_path: Path,
    project_name: str,
    project_root: Path,
    config_relative: str,
    entrypoints: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    release = _load_json(release_manifest_path, "release manifest")
    release_id = _required_string(release, "release_id", "release")
    if release_manifest_path.parent.name != release_id:
        raise GovernedInferenceError("release directory name must equal release_id")
    if release.get("project") != project_name:
        raise GovernedInferenceError("release project does not match selected project")
    files = release.get("files")
    runtime_env = release.get("runtime_env")
    if not isinstance(files, dict) or not isinstance(runtime_env, dict):
        raise GovernedInferenceError("release must contain files and runtime_env objects")
    verified_files: dict[str, dict[str, Any]] = {}
    for key in ("working_dir", "py_module"):
        item = files.get(key)
        if not isinstance(item, dict):
            raise GovernedInferenceError(f"release.files.{key} is required")
        filename = _required_string(item, "filename", f"release.files.{key}")
        if Path(filename).name != filename:
            raise GovernedInferenceError(f"release.files.{key}.filename must be a basename")
        path = release_manifest_path.parent / filename
        expected_digest = _require_sha256(
            _required_string(item, "sha256", f"release.files.{key}"),
            f"release.files.{key}.sha256",
        )
        expected_size = item.get("size_bytes")
        if not isinstance(expected_size, int) or expected_size <= 0:
            raise GovernedInferenceError(f"release.files.{key}.size_bytes must be positive")
        if not path.is_file() or path.stat().st_size != expected_size or _sha256_file(path) != expected_digest:
            raise GovernedInferenceError(f"release file verification failed: {path}")
        verified_files[key] = {
            "filename": filename,
            "key": _required_string(item, "key", f"release.files.{key}"),
            "sha256": expected_digest,
            "size_bytes": expected_size,
        }
    working_uri = _required_string(runtime_env, "working_dir", "release.runtime_env")
    expected_suffix = "/" + verified_files["working_dir"]["key"]
    if not working_uri.startswith("s3://") or not working_uri.endswith(expected_suffix):
        raise GovernedInferenceError("release runtime working_dir does not match its verified object key")
    py_modules = runtime_env.get("py_modules")
    expected_module_suffix = "/" + verified_files["py_module"]["key"]
    if (
        not isinstance(py_modules, list)
        or len(py_modules) != 1
        or not isinstance(py_modules[0], str)
        or not py_modules[0].startswith("s3://")
        or not py_modules[0].endswith(expected_module_suffix)
    ):
        raise GovernedInferenceError(
            "release runtime py_modules must bind the verified Python module object key"
        )
    _required_string(runtime_env, "py_executable", "release.runtime_env")
    env_vars = runtime_env.get("env_vars")
    if not isinstance(env_vars, dict) or env_vars.get("RAYLLM_ENABLE_REQUEST_PROMPT_LOGS") != "0":
        raise GovernedInferenceError(
            "release must set RAYLLM_ENABLE_REQUEST_PROMPT_LOGS=0"
        )

    archive_path = release_manifest_path.parent / verified_files["working_dir"]["filename"]
    with zipfile.ZipFile(archive_path) as archive:
        names = _safe_archive_members(archive)
        required_paths = {config_relative}
        for argv in entrypoints.values():
            for argument in argv[1:]:
                if argument.startswith("-") or argument == "{config}":
                    continue
                if "/" in argument and not Path(argument).is_absolute():
                    required_paths.add(PurePosixPath(argument).as_posix())
        missing = sorted(required_paths - names)
        if missing:
            raise GovernedInferenceError("release archive lacks required paths: " + ", ".join(missing))
        source_config = _resolve_under(project_root, config_relative, "config")
        if not source_config.is_file():
            raise GovernedInferenceError(f"inference config does not exist: {source_config}")
        if archive.read(config_relative) != source_config.read_bytes():
            raise GovernedInferenceError("current inference config differs from immutable Release")
    return {
        "release_id": release_id,
        "manifest_sha256": _sha256_file(release_manifest_path),
        "files": verified_files,
        "runtime_env": copy.deepcopy(runtime_env),
        "archive_path": archive_path,
    }


def _run_release_preflight(
    release: Mapping[str, Any],
    config_relative: str,
    check_entrypoint: Sequence[str],
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="galatea-inference-plan-") as directory:
        stage = Path(directory)
        with zipfile.ZipFile(Path(release["archive_path"])) as archive:
            _safe_archive_members(archive)
            archive.extractall(stage)
        config_path = _resolve_under(stage, config_relative, "released config")
        argv = [str(config_path) if item == "{config}" else item for item in check_entrypoint]
        environment = dict(os.environ)
        environment["LD_LIBRARY_PATH"] = DEFAULT_LD_LIBRARY_PATH
        result = subprocess.run(
            argv,
            cwd=stage,
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            raise GovernedInferenceError(
                f"released inference preflight failed with exit code {result.returncode}"
            )
        try:
            checked = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise GovernedInferenceError("released inference preflight returned invalid JSON") from exc
    if not isinstance(checked, dict) or checked.get("status") != "ok":
        raise GovernedInferenceError("released inference preflight did not return status=ok")
    config_digest = _require_sha256(
        _required_string(checked, "config_digest", "preflight"),
        "preflight.config_digest",
    )
    model_manifest_digest = _require_sha256(
        _required_string(checked, "model_manifest_sha256", "preflight"),
        "preflight.model_manifest_sha256",
    )
    return {
        "status": "ok",
        "config_digest": config_digest,
        "model_manifest_sha256": model_manifest_digest,
        "checkpoint_step": checked.get("checkpoint_step"),
        "model_compatibility": checked.get("model_compatibility"),
    }


def build_plan(
    *,
    project_root: Path,
    config_relative: str,
    release_manifest_path: Path,
    attempt: str,
    ray_address: str,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    release_manifest_path = release_manifest_path.resolve()
    if not attempt.strip():
        raise GovernedInferenceError("attempt must be non-empty")
    if parse.urlparse(ray_address).scheme not in {"http", "https"}:
        raise GovernedInferenceError("Ray address must be HTTP(S)")
    project = _load_project(project_root)
    config_path = _resolve_under(project_root, config_relative, "config")
    config_relative = config_path.relative_to(project_root).as_posix()
    release = _validate_release(
        release_manifest_path,
        project["name"],
        project_root,
        config_relative,
        project["entrypoints"],
    )
    preflight = _run_release_preflight(release, config_relative, project["entrypoints"]["inferenceCheck"])
    requested_resources = {"num_gpus": 1, "cpus": 4, "memory_gb": 8}
    identity_payload = {
        "project": project["name"],
        "operation": "inference",
        "role": "trial",
        "promotable": False,
        "attempt": attempt,
        "config_path": config_relative,
        "config_digest": preflight["config_digest"],
        "model_manifest_digest": preflight["model_manifest_sha256"],
        "release": {
            "id": release["release_id"],
            "manifest_sha256": release["manifest_sha256"],
            "files": release["files"],
            "runtime_env": release["runtime_env"],
        },
        "project_manifest_sha256": project["manifest_sha256"],
        "entrypoints": project["entrypoints"],
        "execution_backend": project["execution_backend"],
        "requested_resources": requested_resources,
    }
    execution_identity = _digest(identity_payload)
    readiness_evidence_digest = _digest(
        {"identity": execution_identity, "operation": "inference-readiness"}
    )
    submission_id = (
        f"{project['name'].lower().replace('_', '-')}-inference-"
        f"{execution_identity.removeprefix('sha256:')[:12]}"
    )
    return {
        "schema_version": PLAN_SCHEMA,
        "project": project["name"],
        "operation": "inference",
        "role": "trial",
        "promotable": False,
        "attempt": attempt,
        "project_root": str(project_root),
        "config_path": config_relative,
        "release_manifest_path": str(release_manifest_path),
        "release_id": release["release_id"],
        "release_manifest_sha256": release["manifest_sha256"],
        "ray_address": ray_address.rstrip("/"),
        "requested_resources": requested_resources,
        "preflight": preflight,
        "execution_identity": execution_identity,
        "readiness_evidence_digest": readiness_evidence_digest,
        "submission_id": submission_id,
    }


def _write_plan(path: Path, plan: Mapping[str, Any]) -> None:
    payload = json.dumps(plan, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    path = path.resolve()
    if path.exists():
        if path.read_bytes() != payload:
            raise GovernedInferenceError(f"refusing to overwrite a different plan: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        staged.write_bytes(payload)
        os.chmod(staged, 0o600)
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def _http_json(
    url: str,
    *,
    method: str = "GET",
    body: Mapping[str, Any] | None = None,
    allow_not_found: bool = False,
) -> dict[str, Any] | None:
    payload = None if body is None else _canonical(body)
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=payload, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=30) as response:
            raw = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
    except error.HTTPError as exc:
        if allow_not_found and exc.code == 404:
            return None
        raise GovernedInferenceError(f"Ray Jobs API returned HTTP {exc.code}") from exc
    except (error.URLError, TimeoutError) as exc:
        raise GovernedInferenceError(f"Ray Jobs API is unavailable: {exc}") from exc
    if len(raw) > MAX_HTTP_RESPONSE_BYTES:
        raise GovernedInferenceError("Ray Jobs API response exceeds size limit")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GovernedInferenceError("Ray Jobs API returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise GovernedInferenceError("Ray Jobs API response must be a JSON object")
    return value


def _job_url(plan: Mapping[str, Any]) -> str:
    submission_id = _required_string(plan, "submission_id", "plan")
    return f"{_required_string(plan, 'ray_address', 'plan').rstrip('/')}/api/jobs/{parse.quote(submission_id, safe='')}"


def _expected_metadata(plan: Mapping[str, Any]) -> dict[str, str]:
    return {
        "project": str(plan["project"]),
        "role": "trial",
        "attempt": str(plan["attempt"]),
        "release_id": str(plan["release_id"]),
        "evidence_digest": str(plan["readiness_evidence_digest"]),
        "galatea.execution.identity": str(plan["execution_identity"]),
        "galatea.project": str(plan["project"]),
        "galatea.release.id": str(plan["release_id"]),
        "galatea.submission.id": str(plan["submission_id"]),
        "galatea.readiness.digest": str(plan["readiness_evidence_digest"]),
        "galatea.execution.mode": EXECUTION_MODE,
        "galatea.promotable": "false",
        "idempotency_key": str(plan["execution_identity"]),
    }


def _verify_existing_job(job: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    if job.get("submission_id") != plan["submission_id"]:
        raise GovernedInferenceError("Ray Job response has a mismatched submission ID")
    metadata = job.get("metadata")
    if not isinstance(metadata, dict):
        raise GovernedInferenceError("existing Ray Job lacks governed metadata")
    for key, expected in _expected_metadata(plan).items():
        if metadata.get(key) != expected:
            raise GovernedInferenceError(f"existing Ray Job has mismatched {key}")


def _recompute_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    if plan.get("schema_version") != PLAN_SCHEMA:
        raise GovernedInferenceError("unsupported plan schema")
    current = build_plan(
        project_root=Path(_required_string(plan, "project_root", "plan")),
        config_relative=_required_string(plan, "config_path", "plan"),
        release_manifest_path=Path(_required_string(plan, "release_manifest_path", "plan")),
        attempt=_required_string(plan, "attempt", "plan"),
        ray_address=_required_string(plan, "ray_address", "plan"),
    )
    if _canonical(current) != _canonical(plan):
        raise GovernedInferenceError("inference plan is stale or its inputs changed")
    return current


def submit(plan_path: Path, authorized_evidence_digest: str) -> dict[str, Any]:
    plan = _recompute_plan(_load_json(plan_path.resolve(), "inference plan"))
    authorized_evidence_digest = _require_sha256(
        authorized_evidence_digest,
        "authorized evidence digest",
        prefixed=True,
    )
    if authorized_evidence_digest != plan["readiness_evidence_digest"]:
        raise GovernedInferenceError("authorization is not bound to the current readiness evidence")
    existing = _http_json(_job_url(plan), allow_not_found=True)
    if existing is not None:
        _verify_existing_job(existing, plan)
        return {
            "status": existing.get("status", "UNKNOWN"),
            "submission_id": plan["submission_id"],
            "reused": True,
            "execution_identity": plan["execution_identity"],
            "readiness_evidence_digest": plan["readiness_evidence_digest"],
        }

    project = _load_project(Path(plan["project_root"]))
    release = _load_json(Path(plan["release_manifest_path"]), "release manifest")
    runtime_env = copy.deepcopy(release["runtime_env"])
    env_vars = runtime_env.setdefault("env_vars", {})
    if not isinstance(env_vars, dict):
        raise GovernedInferenceError("release runtime_env.env_vars must be an object")
    env_vars.update({
        "GALATEA_PROJECT": plan["project"],
        "GALATEA_RELEASE_ID": plan["release_id"],
        "GALATEA_READINESS_DIGEST": plan["readiness_evidence_digest"],
        "GALATEA_EXECUTION_IDENTITY": plan["execution_identity"],
        "GALATEA_SUBMISSION_ID": plan["submission_id"],
        "GALATEA_EXECUTION_MODE": EXECUTION_MODE,
        "GALATEA_INFERENCE_AUTHORIZED": "true",
        "RAY_JOB_SUBMISSION_ID": plan["submission_id"],
        "RAY_INFERENCE_SUBMISSION_ID": plan["submission_id"],
        "RAY_INFERENCE_CONFIG_DIGEST": plan["preflight"]["config_digest"],
        "RAY_INFERENCE_MODEL_MANIFEST_DIGEST": plan["preflight"]["model_manifest_sha256"],
    })
    entrypoint = [
        plan["config_path"] if item == "{config}" else item
        for item in project["entrypoints"]["inference"]
    ]
    metadata = _expected_metadata(plan)
    for key in GOVERNED_METADATA_KEYS:
        if not metadata.get(key):
            raise GovernedInferenceError(f"submission metadata lacks {key}")
    body = {
        "entrypoint": shlex.join(entrypoint),
        "submission_id": plan["submission_id"],
        "runtime_env": runtime_env,
        "metadata": metadata,
        "entrypoint_num_cpus": 2,
        "entrypoint_num_gpus": 0,
        "entrypoint_memory": 2 * 1024**3,
    }
    jobs_url = f"{plan['ray_address'].rstrip('/')}/api/jobs/"
    try:
        created = _http_json(jobs_url, method="POST", body=body)
    except GovernedInferenceError:
        after_failure = _http_json(_job_url(plan), allow_not_found=True)
        if after_failure is None:
            raise
        _verify_existing_job(after_failure, plan)
        created = after_failure
    return {
        "status": (created or {}).get("status", "SUBMITTED"),
        "submission_id": (created or {}).get("submission_id", plan["submission_id"]),
        "reused": False,
        "execution_identity": plan["execution_identity"],
        "readiness_evidence_digest": plan["readiness_evidence_digest"],
    }


def observe(plan_path: Path) -> dict[str, Any]:
    plan = _load_json(plan_path.resolve(), "inference plan")
    job = _http_json(_job_url(plan), allow_not_found=True)
    if job is None:
        return {"status": "NOT_FOUND", "submission_id": plan["submission_id"]}
    _verify_existing_job(job, plan)
    return {
        "status": job.get("status", "UNKNOWN"),
        "submission_id": plan["submission_id"],
        "error_type": job.get("error_type"),
        "driver_exit_code": job.get("driver_exit_code"),
    }


def _print(value: Mapping[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True))


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--project-root", type=Path, required=True)
    plan_parser.add_argument("--config", required=True)
    plan_parser.add_argument("--release-manifest", type=Path, required=True)
    plan_parser.add_argument("--attempt", required=True)
    plan_parser.add_argument("--ray-address", default=DEFAULT_RAY_ADDRESS)
    plan_parser.add_argument("--plan-out", type=Path, required=True)
    submit_parser = subparsers.add_parser("submit")
    submit_parser.add_argument("--plan", type=Path, required=True)
    submit_parser.add_argument("--authorized-evidence-digest", required=True)
    observe_parser = subparsers.add_parser("observe")
    observe_parser.add_argument("--plan", type=Path, required=True)
    parsed = parser.parse_args(arguments)
    try:
        if parsed.command == "plan":
            planned = build_plan(
                project_root=parsed.project_root,
                config_relative=parsed.config,
                release_manifest_path=parsed.release_manifest,
                attempt=parsed.attempt,
                ray_address=parsed.ray_address,
            )
            _write_plan(parsed.plan_out, planned)
            _print(planned)
        elif parsed.command == "submit":
            _print(submit(parsed.plan, parsed.authorized_evidence_digest))
        else:
            _print(observe(parsed.plan))
        return 0
    except (GovernedInferenceError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        _print({"status": "blocked", "error": f"{type(exc).__name__}: {exc}"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
