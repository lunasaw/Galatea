"""Ray runtime environment and Train Controller serialization helpers."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any, Iterator, Mapping


RAY_JOB_CONFIG_ENV = "RAY_JOB_CONFIG_JSON_ENV_VAR"
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
    import ray_cats_dogs.tracking as tracking_module
    import ray_cats_dogs.worker as worker_module

    modules: tuple[ModuleType, ...] = (tracking_module, worker_module)
    for module in modules:
        cloudpickle.register_pickle_by_value(module)
    try:
        yield
    finally:
        for module in reversed(modules):
            cloudpickle.unregister_pickle_by_value(module)
