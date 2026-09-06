#!/usr/bin/env python3
"""Submit the immutable LoRA inference service as a Ray Job."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from llm_lora_playground.config import canonical_config_digest, load_config  # noqa: E402
from llm_lora_playground.inference_service import sha256_file, validate_inference_config  # noqa: E402
from llm_lora_playground.planning import code_revision  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", default="http://127.0.0.1:8265")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "configs/inference-ray.yaml")
    parser.add_argument("--submission-id")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    config_path = args.config.resolve()
    if args.stop:
        if not args.submission_id:
            parser.error("--stop requires --submission-id")
        ray_bin = os.environ.get("RAY_BIN", "/data/conda/envs/attend-ray-py312/bin/ray")
        return subprocess.call([ray_bin, "job", "stop", "--address", args.address, args.submission_id])
    preflight = validate_inference_config(config_path)
    submission_id = args.submission_id or f"llm-lora-inference-{preflight['config_digest'][:12]}"
    config_relative = config_path.relative_to(PROJECT_ROOT)
    runtime_env = {
        "conda": "/data/conda/envs/llm-lora-playground-py312",
        "working_dir": str(PROJECT_ROOT),
        "env_vars": {
            "RAY_JOB_SUBMISSION_ID": submission_id,
            "RAY_INFERENCE_SUBMISSION_ID": submission_id,
            "RAY_INFERENCE_AUTHORIZED": "true",
            "RAY_INFERENCE_CONFIG_DIGEST": preflight["config_digest"],
            "RAY_INFERENCE_MODEL_MANIFEST_DIGEST": preflight["model_manifest_sha256"],
            "QWEN35_MODEL_PATH": str(preflight["base_model_path"]),
            "LORA_ADAPTER_PATH": str(preflight["adapter_path"]),
            "LORA_CHECKPOINT_MANIFEST_PATH": str(preflight["manifest_path"]),
            "CODE_REVISION": os.environ.get("CODE_REVISION", code_revision(PROJECT_ROOT)),
        },
    }
    entrypoint = ["python", "scripts/serve_lora.py", "--config", str(config_relative), "--run"]
    ray_bin = os.environ.get("RAY_BIN", "/data/conda/envs/attend-ray-py312/bin/ray")
    command = [
        ray_bin,
        "job",
        "submit",
        "--address",
        args.address,
        "--submission-id",
        submission_id,
        "--working-dir",
        str(PROJECT_ROOT),
        "--runtime-env-json",
        json.dumps(runtime_env),
        "--metadata-json",
        json.dumps({
            "project": "llm-lora-playground",
            "execution_mode": "ray-serve-inference",
            "submission_id": submission_id,
            "config_digest": preflight["config_digest"],
            "model_manifest_sha256": preflight["model_manifest_sha256"],
            "promotable": "false",
            "role": "trial",
        }),
        "--entrypoint-num-cpus",
        "2",
        "--entrypoint-num-gpus",
        "0",
        "--entrypoint-memory",
        str(2 * 1024**3),
        "--",
        *entrypoint,
    ]
    print(json.dumps({"status": "planned" if not args.run else "submitting", "submission_id": submission_id, "config_digest": preflight["config_digest"], "model_manifest_sha256": preflight["model_manifest_sha256"], "endpoint": f"http://{preflight['values']['service']['host']}:{preflight['values']['service']['port']}/v1"}, ensure_ascii=False, sort_keys=True))
    if not args.run:
        print(json.dumps({"command": command}, ensure_ascii=False, sort_keys=True))
        return 0
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
