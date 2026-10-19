"""Ray runtime environment and Train Controller serialization helpers."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator, Mapping


RAY_JOB_CONFIG_ENV = "RAY_JOB_CONFIG_JSON_ENV_VAR"
GALATEA_PROVENANCE_KEYS = (
    "galatea.execution.identity",
    "galatea.project",
    "galatea.release.id",
    "galatea.submission.id",
    "galatea.readiness.digest",
    "galatea.execution.mode",
    "galatea.promotable",
)
RUNTIME_ENV_EXCLUDES = [
    ".git/**",
    ".ipynb_checkpoints/**",
    ".pytest_cache/**",
    "**/__pycache__/**",
    "**/*.pyc",
    "notebooks/**",
    "tests/**",
]


def build_runtime_env(project_root: Path) -> dict[str, Any]:
    """Upload the project working directory and make its src package importable."""

    root = project_root.resolve()
    package_root = root / "src" / "ray_cats_dogs"
    if not (package_root / "__init__.py").is_file():
        raise ValueError(
            f"Ray runtime package directory does not exist: {package_root}"
        )
    return {
        "working_dir": str(root),
        "py_modules": [str(package_root)],
        "excludes": list(RUNTIME_ENV_EXCLUDES),
    }


def execution_provenance(
    project_name: str,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """从 Ray Job 元数据解析治理来源，本地执行始终标为不可提升。"""

    environment = os.environ if environ is None else environ
    raw_config = environment.get(RAY_JOB_CONFIG_ENV)
    if not raw_config:
        return {
            "galatea.execution.mode": "local-dev",
            "galatea.promotable": "false",
            "galatea.project": project_name,
        }
    try:
        config = json.loads(raw_config)
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("Ray Job 配置不是有效 JSON，无法验证 Galatea 来源") from error
    metadata = config.get("metadata")
    if not isinstance(metadata, dict):
        return {
            "galatea.execution.mode": "ray-job-unmanaged",
            "galatea.promotable": "false",
            "galatea.project": project_name,
        }
    values = {
        key: value
        for key in GALATEA_PROVENANCE_KEYS
        if isinstance((value := metadata.get(key)), str) and value
    }
    governed = (
        len(values) == len(GALATEA_PROVENANCE_KEYS)
        and values["galatea.execution.mode"] == "governed-ray-job"
        and values["galatea.promotable"] in {"true", "false"}
        and values["galatea.project"] == project_name
    )
    if governed:
        return values
    return {
        **values,
        "galatea.execution.mode": "ray-job-unmanaged",
        "galatea.promotable": "false",
        "galatea.project": project_name,
    }


def require_governed_execution(project_name: str, promotable: bool) -> dict[str, str]:
    """Check provenance against the actual Jobs API before any training write."""

    values = execution_provenance(project_name)
    if values["galatea.execution.mode"] != "governed-ray-job":
        raise RuntimeError("Training requires an immutable Release and Galatea-authorized Ray Job")
    if values["galatea.promotable"] != str(promotable).lower():
        raise RuntimeError("Ray Job promotability differs from the workload config")
    from ray.job_submission import JobSubmissionClient

    address = os.environ.get("RAY_DASHBOARD_ADDRESS", "http://127.0.0.1:8265")
    import ray
    import time

    if not ray.is_initialized():
        ray.init(address=os.environ.get("RAY_ADDRESS", "127.0.0.1:18104"))
    actual_job_id = ray.get_runtime_context().get_job_id()
    client = JobSubmissionClient(address)
    # The dashboard learns the Driver ID asynchronously after ray.init().
    for attempt in range(10):
        job = client.get_job_info(values["galatea.submission.id"])
        if job.job_id is not None or attempt == 9:
            break
        time.sleep(0.5)
    status = getattr(job.status, "value", job.status)
    if job.job_id != actual_job_id or status != "RUNNING":
        raise RuntimeError(
            "Governed binding does not identify the actual running Ray Driver: "
            f"expected={actual_job_id}, dashboard={job.job_id}, status={status}"
        )
    if any((job.metadata or {}).get(key) != value for key, value in values.items()):
        raise RuntimeError("Governed binding differs from actual Ray Job metadata")
    # Candidate fields come from the actual Jobs API, after Driver verification.
    for key in ("candidate_run_id", "candidate_evidence_digest"):
        value = (job.metadata or {}).get(key)
        if isinstance(value, str) and value:
            values[key] = value
    return values


def ray_init_runtime_env(
    project_root: Path,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Build a runtime env unless a Ray Job already injected one."""

    environment = os.environ if environ is None else environ
    if environment.get(RAY_JOB_CONFIG_ENV):
        return None
    return build_runtime_env(project_root)


def worker_runtime_env(ray_module: Any) -> dict[str, Any]:
    """Forward the uploaded job runtime env explicitly to Ray Train workers."""

    runtime_env = dict(ray_module.get_runtime_context().runtime_env or {})
    if not runtime_env.get("py_modules"):
        raise RuntimeError(
            "Ray runtime_env is missing py_modules; submit the job with "
            "build_runtime_env(project_root) so ray_cats_dogs is available to workers"
        )
    runtime_env.pop("excludes", None)
    return runtime_env


@contextmanager
def controller_pickle_by_value() -> Iterator[None]:
    """Serialize project callbacks/functions without Controller-side imports.

    Ray Train 2.53 creates its Controller with an internal runtime environment that
    does not include the job's working directory or py_modules. Workers still receive
    the uploaded runtime environment through RunConfig.worker_runtime_env.
    """

    import ray.cloudpickle as cloudpickle
    import ray_cats_dogs.input_pipeline as input_pipeline_module
    import ray_cats_dogs.tracking as tracking_module
    import ray_cats_dogs.worker as worker_module

    modules: tuple[ModuleType, ...] = (
        input_pipeline_module,
        tracking_module,
        worker_module,
    )
    for module in modules:
        cloudpickle.register_pickle_by_value(module)
    try:
        yield
    finally:
        for module in reversed(modules):
            cloudpickle.unregister_pickle_by_value(module)
