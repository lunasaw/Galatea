"""Ray 运行时环境和治理血缘辅助函数。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping


RAY_JOB_CONFIG_ENV = "RAY_JOB_CONFIG_JSON_ENV_VAR"


def execution_provenance(project_name: str, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """从 Ray Job 元数据读取治理来源；本地运行明确标记为不可提升。"""

    environment = os.environ if environ is None else environ
    raw_config = environment.get(RAY_JOB_CONFIG_ENV)
    if not raw_config:
        return {
            "galatea.execution.mode": "local-dev",
            "galatea.promotable": "false",
            "galatea.project": project_name,
        }
    try:
        payload = json.loads(raw_config)
    except json.JSONDecodeError as error:
        raise RuntimeError("Ray Job 配置不是有效 JSON") from error
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return {
            "galatea.execution.mode": "ray-job-unmanaged",
            "galatea.promotable": "false",
            "galatea.project": project_name,
        }
    expected = (
        "galatea.execution.identity",
        "galatea.project",
        "galatea.release.id",
        "galatea.submission.id",
        "galatea.readiness.digest",
        "galatea.execution.mode",
        "galatea.promotable",
    )
    values = {key: metadata[key] for key in expected if isinstance(metadata.get(key), str)}
    governed = (
        set(values) == set(expected)
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


def build_runtime_env(project_root: Path) -> dict[str, Any]:
    """为 Ray Job 声明项目工作目录和 Python 模块。"""

    package_root = project_root.resolve() / "src" / "ray_kaggle_house_prices"
    if not (package_root / "__init__.py").is_file():
        raise ValueError(f"项目 Python 包不存在: {package_root}")
    return {
        "working_dir": str(project_root.resolve()),
        "py_modules": [str(package_root)],
        "excludes": [".git/**", "tests/**", "job/**", "notebooks/**", "**/__pycache__/**"],
    }
