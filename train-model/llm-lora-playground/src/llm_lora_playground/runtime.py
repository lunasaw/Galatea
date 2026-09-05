"""Read-only environment and GPU checks."""

from __future__ import annotations

import importlib.metadata
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
