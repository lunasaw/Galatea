#!/usr/bin/env python3
"""Submit the project-0+1 smoke to the configured Ray Jobs API."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", default="http://127.0.0.1:8265")
    parser.add_argument("--submission-id", required=True)
    parser.add_argument("--mode", choices=("smoke", "check-data"), default="smoke")
    args = parser.parse_args()
    entry_args = ["--config", "configs/inference.yaml"]
    if args.mode == "smoke":
        entry_args += ["--smoke-only", "--output-dir", "platform-data/llm-baselines/ray-smoke"]
    else:
        entry_args += ["--data-config", "configs/data.yaml", "--check-data", "--output-dir", "platform-data/llm-baselines/ray-data-preflight"]
    runtime_env = {
        "conda": "/data/conda/envs/llm-lora-playground-py312",
        "env_vars": {
            "QWEN35_MODEL_PATH": "/data/ai/chenzhangyue/code/model/Qwen3.5-0.8B",
            "MLFLOW_TRACKING_URI": "http://127.0.0.1:5000",
            "MLFLOW_EXPERIMENT_NAME": "llm-lora-playground",
        },
    }
    command = [
        "/data/conda/envs/llm-lora-playground-py312/bin/ray", "job", "submit",
        "--address", args.address, "--submission-id", args.submission_id,
        "--working-dir", str(PROJECT_ROOT), "--runtime-env-json", json.dumps(runtime_env),
        "--entrypoint-num-cpus", "4", "--entrypoint-num-gpus", "1", "--entrypoint-memory", str(8 * 1024**3),
        "--", "python", "scripts/infer.py", *entry_args,
    ]
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
