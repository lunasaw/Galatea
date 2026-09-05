"""Thin Ray Jobs submission wrapper; training remains in ``train_lora.train``."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RayJobHandle:
    submission_id: str
    command: list[str]


def submit_job(config_path: Path, address: str, runtime_env: dict[str, Any], data: Path | None = None, submission_id: str | None = None, dry_run: bool = True) -> RayJobHandle:
    project_root = config_path.resolve().parents[1]
    submission_id = submission_id or f"toy-lora-{config_path.stem}"
    args = ["python", "scripts/submit_train.py", "--config", str(config_path.name)]
    if data:
        args += ["--data", str(data)]
    command = ["ray", "job", "submit", "--address", address, "--submission-id", submission_id, "--working-dir", str(project_root), "--runtime-env-json", json.dumps(runtime_env), "--entrypoint-num-cpus", "4", "--entrypoint-num-gpus", "1", "--entrypoint-memory", str(8 * 1024**3), "--", *args]
    if not dry_run:
        subprocess.run(command, check=True)
    return RayJobHandle(submission_id, command)
