"""Read-only environment and GPU checks."""

from __future__ import annotations

import importlib.metadata
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any


def collect_environment_snapshot() -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "python": sys.version,
        "platform": platform.platform(),
        "packages": {},
    }
    for name in ("torch", "transformers", "mlflow", "ray", "accelerate", "datasets", "peft", "trl"):
        try:
            snapshot["packages"][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            snapshot["packages"][name] = None
    try:
        import torch

        snapshot["torch_cuda"] = torch.version.cuda
        snapshot["cuda_available"] = bool(torch.cuda.is_available())
        snapshot["gpu_count"] = int(torch.cuda.device_count())
        snapshot["gpus"] = [
            {"index": i, "name": torch.cuda.get_device_name(i), "memory_total_mib": int(torch.cuda.get_device_properties(i).total_memory / 2**20)}
            for i in range(torch.cuda.device_count())
        ]
    except Exception as exc:  # pragma: no cover - optional dependency path
        snapshot["torch_error"] = f"{type(exc).__name__}: {exc}"
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        snapshot["gpu_processes"] = completed.stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        snapshot["gpu_processes"] = []
    return snapshot


def environment_digest(snapshot: dict[str, Any] | None = None) -> str:
    payload = dict(snapshot or collect_environment_snapshot())
    # Active GPU processes are diagnostic occupancy, not an immutable software or
    # hardware identity. Including PIDs would make consecutive Galatea plan and
    # submit preflights produce different readiness identities.
    payload.pop("gpu_processes", None)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def validate_training_environment(
    model_path: Path,
    expected_python: str = "3.12.12",
    expected_ray: str = "2.58.0",
    expected_transformers: str = "5.16.1",
) -> list[str]:
    """Validate the fixed Ray/Qwen runtime without loading model weights."""

    errors: list[str] = []
    python_version = ".".join(map(str, sys.version_info[:3]))
    if python_version != expected_python:
        errors.append(f"python version must be {expected_python}, got {python_version}")
    required = {
        "ray": expected_ray,
        "transformers": expected_transformers,
        "torch": "2.11.0",
        "peft": "0.20.0",
        "accelerate": "1.14.0",
        "mlflow": "3.14.0",
    }
    for name, expected in required.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            errors.append(f"required package is missing: {name}")
            continue
        if actual != expected:
            errors.append(f"{name} version must be {expected}, got {actual}")
    if not model_path.is_dir():
        errors.append(f"model path is missing: {model_path}")
    else:
        try:
            from transformers import AutoConfig

            model_config = AutoConfig.from_pretrained(
                model_path,
                local_files_only=True,
                trust_remote_code=False,
            )
            if getattr(model_config, "model_type", None) != "qwen3_5":
                errors.append("model snapshot is not qwen3_5")
        except Exception as exc:
            errors.append(f"Qwen3.5 config preflight failed: {type(exc).__name__}: {exc}")
    return errors


def check_gpu_capabilities(device: str = "cuda:0", dtype: Any = None) -> dict[str, Any]:
    try:
        import torch

        dtype = dtype or torch.bfloat16
        if not torch.cuda.is_available():
            return {"status": "blocked", "reason": "cuda_unavailable", "device": device, "bf16_matmul_passed": False}
        index = int(device.split(":")[-1])
        if index >= torch.cuda.device_count():
            return {"status": "blocked", "reason": "device_unavailable", "device": device, "bf16_matmul_passed": False}
        a = torch.randn((16, 16), device=device, dtype=dtype)
        b = torch.randn((16, 16), device=device, dtype=dtype)
        _ = a @ b
        torch.cuda.synchronize(device)
        return {"status": "ok", "reason": None, "device": device, "bf16_matmul_passed": True}
    except Exception as exc:
        return {"status": "blocked", "reason": f"bf16_matmul_failed:{type(exc).__name__}", "device": device, "bf16_matmul_passed": False}
