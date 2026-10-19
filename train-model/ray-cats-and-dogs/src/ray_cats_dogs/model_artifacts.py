"""Portable model packaging and forward-only, isolated recovery checks."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


def model_code_paths() -> list[str]:
    # MLflow adds model/code, so copy the importable package directly under it.
    return [str(Path(__file__).resolve().parent)]


def verify_packaged_model(
    directory: Path, image_size: tuple[int, int]
) -> dict[str, Any]:
    program = """
import json, sys
from pathlib import Path
import mlflow.pytorch
import torch
torch.set_num_threads(2)
model = mlflow.pytorch.load_model(sys.argv[1], map_location="cpu")
import ray_cats_dogs
assert Path(ray_cats_dogs.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
model.eval()
with torch.inference_mode():
    output = model(torch.zeros(1, 3, int(sys.argv[2]), int(sys.argv[3])))
assert tuple(output.shape) == (1, 2) and torch.isfinite(output).all()
print(json.dumps({"default_loader_verified": True, "isolated_process": True,
                  "device": "cpu", "output_shape": list(output.shape),
                  "package_from_model_artifact": True, "test_data_read": False}))
"""
    with tempfile.TemporaryDirectory(prefix="ray-cats-dogs-model-isolated-") as cwd:
        result = subprocess.run(
            [sys.executable, "-I", "-c", program, str(directory.resolve()),
             *map(str, image_size)],
            cwd=cwd,
            env={**os.environ, "CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "2"},
            capture_output=True,
            text=True,
            timeout=120,
        )
    if result.returncode != 0:
        raise RuntimeError(f"Packaged model failed isolated default loading: {result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])
