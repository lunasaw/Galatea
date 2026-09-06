"""Thin Ray Jobs submission wrapper; training remains in ``train_lora.train``."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import load_training_config
from .planning import code_revision


@dataclass(frozen=True)
class RayJobHandle:
    submission_id: str
    command: list[str]


def submit_job(config_path: Path, address: str, runtime_env: dict[str, Any], data: Path | None = None, submission_id: str | None = None, dry_run: bool = True) -> RayJobHandle:
    if not dry_run:
        raise RuntimeError(
            "direct Ray Job submission is disabled for this project; use "
            "Galatea galatea_plan_run followed by galatea_submit_job"
        )
    if config_path.is_absolute():
        project_root = config_path.resolve().parents[1]
    else:
        project_root = Path.cwd().resolve()
        config_path = (project_root / config_path).resolve()
    if config_path.is_absolute():
        project_root = config_path.resolve().parents[1]
    submission_id = submission_id or f"toy-lora-{config_path.stem}"
    config_relative = config_path.relative_to(project_root)
    args = ["python", "scripts/submit_train.py", "--config", str(config_relative), "--run"]
    if data:
        args += ["--data", str(data)]
    env = dict(runtime_env)
    env.setdefault("env_vars", {})
    config = load_training_config(config_path)
    tracking = config.values.get("tracking", {})
    model = config.values.get("model", {})
    env["env_vars"] = {
        **env["env_vars"],
        "RAY_JOB_SUBMISSION_ID": submission_id,
        "MLFLOW_TRACKING_URI": os.environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000"),
        "MLFLOW_EXPERIMENT_NAME": os.environ.get("MLFLOW_EXPERIMENT_NAME", str(tracking.get("default_experiment_name", "llm-lora-playground"))),
        "QWEN35_MODEL_PATH": os.environ.get("QWEN35_MODEL_PATH", str(model.get("local_path", ""))),
        "CODE_REVISION": os.environ.get("CODE_REVISION", code_revision(project_root)),
    }
    ray_bin = os.environ.get("RAY_BIN", "/data/conda/envs/attend-ray-py312/bin/ray")
    env.setdefault("working_dir", str(project_root))
    command = [ray_bin, "job", "submit", "--address", address, "--submission-id", submission_id, "--runtime-env-json", json.dumps(env), "--metadata-json", json.dumps({"project": "llm-lora-playground", "execution_mode": "ray_job", "submission_id": submission_id}), "--entrypoint-num-cpus", "4", "--entrypoint-num-gpus", "1", "--entrypoint-memory", str(8 * 1024**3), "--", *args]
    if not dry_run:
        subprocess.run(command, check=True)
    return RayJobHandle(submission_id, command)
