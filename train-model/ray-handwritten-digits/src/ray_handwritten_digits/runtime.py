"""Ray Runtime Environment 和 Controller 序列化辅助函数。"""

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
RUNTIME_ENV_EXCLUDES = [".git/**", ".ipynb_checkpoints/**", ".pytest_cache/**", "**/__pycache__/**", "**/*.pyc", "notebooks/**", "tests/**"]


def build_runtime_env(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    package_root = root / "src" / "ray_handwritten_digits"
    if not (package_root / "__init__.py").is_file():
        raise ValueError(f"Ray runtime package directory不存在: {package_root}")
    return {"working_dir": str(root), "py_modules": [str(package_root)], "excludes": list(RUNTIME_ENV_EXCLUDES)}


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
        and values["galatea.promotable"] == "true"
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


def ray_init_runtime_env(project_root: Path, environ: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    return None if (os.environ if environ is None else environ).get(RAY_JOB_CONFIG_ENV) else build_runtime_env(project_root)


def worker_runtime_env(ray_module: Any) -> dict[str, Any]:
    runtime_env = dict(ray_module.get_runtime_context().runtime_env or {})
    if not runtime_env.get("py_modules"):
        raise RuntimeError("Ray runtime_env 缺少 py_modules")
    runtime_env.pop("excludes", None)
    return runtime_env


@contextmanager
def controller_pickle_by_value() -> Iterator[None]:
    import ray.cloudpickle as cloudpickle
    import ray_handwritten_digits.input_pipeline as input_pipeline_module
    import ray_handwritten_digits.worker as worker_module
    modules: tuple[ModuleType, ...] = (input_pipeline_module, worker_module)
    for module in modules:
        cloudpickle.register_pickle_by_value(module)
    try:
        yield
    finally:
        for module in reversed(modules):
            cloudpickle.unregister_pickle_by_value(module)
