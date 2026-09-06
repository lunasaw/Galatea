#!/usr/bin/env python3
"""Governed Ray Job driver for the experimental LoRA inference service."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from llm_lora_playground.inference_service import (  # noqa: E402
    InferenceContractError,
    build_serving_manifest,
    create_deployment,
    validate_inference_config,
    validate_model_adapter_compatibility,
    run_adapter_effectiveness_probe,
    write_serving_manifest,
    validate_inference_binding,
)
from llm_lora_playground.planning import code_revision  # noqa: E402


def _submission_id() -> str:
    value = os.environ.get("RAY_JOB_SUBMISSION_ID") or os.environ.get("RAY_INFERENCE_SUBMISSION_ID")
    if not value:
        raise RuntimeError("inference service requires RAY_JOB_SUBMISSION_ID")
    return value


def _require_job_boundary() -> None:
    validate_inference_binding()
    # Ray Job injects this identifier in some versions, while the runtime
    # context is the authoritative check across local and remote workers.
    try:
        import ray

        if not ray.get_runtime_context().get_job_id():
            raise RuntimeError("inference service must run inside a Ray Job")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("inference service could not verify its Ray Job context") from exc


def run(config_path: Path) -> int:
    _require_job_boundary()
    preflight = validate_inference_config(config_path)
    compatibility = validate_model_adapter_compatibility(preflight)
    # Readiness must prove the adapter affects the exact model class used by
    # serving.  A successful download/registration alone is insufficient.
    probe_results = run_adapter_effectiveness_probe(preflight)
    expected_digest = os.environ.get("RAY_INFERENCE_CONFIG_DIGEST")
    if expected_digest and expected_digest != preflight["config_digest"]:
        raise RuntimeError("inference config digest does not match the submission binding")
    expected_manifest = os.environ.get("RAY_INFERENCE_MODEL_MANIFEST_DIGEST")
    if expected_manifest and expected_manifest != preflight["model_manifest_sha256"]:
        raise RuntimeError("model manifest digest does not match the submission binding")
    binding = validate_inference_binding()
    submission_id = binding["submission_id"]
    if submission_id != _submission_id():
        raise RuntimeError("submission identity does not match the Galatea binding")
    manifest = build_serving_manifest(preflight, submission_id, os.environ.get("CODE_REVISION", code_revision(PROJECT_ROOT)))
    manifest["governance_binding"] = binding
    manifest["model_compatibility"] = compatibility
    manifest["adapter_effectiveness_probes"] = probe_results
    manifest_path = write_serving_manifest(manifest, Path(str(preflight["values"]["output_root"])), submission_id)
    print(json.dumps({"status": "starting", "submission_id": submission_id, "manifest": str(manifest_path), "config_digest": preflight["config_digest"], "model_manifest_sha256": preflight["model_manifest_sha256"]}, ensure_ascii=False, sort_keys=True), flush=True)

    from ray import serve

    service = preflight["values"]["service"]
    # Ray LLM/vLLM loads CUDA libraries from the isolated official runtime.
    # Keep this process-bound and do not persist credentials or prompts.
    serve.start(
        http_options={
            "host": str(service["host"]),
            "port": int(service["port"]),
            "location": "HeadOnly",
        }
    )
    app = create_deployment(preflight)
    official_model_id = preflight.get("official_model_id")
    if official_model_id:
        print(json.dumps({"status": "ray-llm-ready", "model": official_model_id, "engine": "ray-serve-llm-vllm", "promotable": False, "test_access": "untouched"}, ensure_ascii=False, sort_keys=True), flush=True)
    serve.run(
        app,
        name=str(service["name"]),
        route_prefix=str(service["route_prefix"]),
        blocking=True,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    try:
        if args.check_config:
            preflight = validate_inference_config(args.config.resolve())
            compatibility = validate_model_adapter_compatibility(preflight)
            print(json.dumps({"status": "ok", "config_digest": preflight["config_digest"], "model_manifest_sha256": preflight["model_manifest_sha256"], "checkpoint_step": preflight["checkpoint"].step, "model_compatibility": compatibility}, ensure_ascii=False, sort_keys=True))
            return 0
        if not args.run:
            parser.error("one of --check-config or --run is required")
        return run(args.config.resolve())
    except (InferenceContractError, RuntimeError, FileNotFoundError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
