"""Governed Ray LLM inference runtime for an immutable PEFT checkpoint.

The deployment uses the official ``ray.serve.llm`` + vLLM integration and
exposes its OpenAI-compatible chat-completions contract.  No request or
generated text is written to disk or logs.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .checkpoints import CheckpointManifest, load_checkpoint
from .config import canonical_config_digest, load_config
from .lora import load_adapter
from .models.causal_lm import ModelConfig, prepare_inputs


class InferenceContractError(ValueError):
    """Raised when an inference config or immutable model artifact is unsafe."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required(mapping: dict[str, Any], key: str, prefix: str) -> Any:
    value = mapping.get(key)
    if value in (None, ""):
        raise InferenceContractError(f"{prefix}.{key} is required")
    return value


def validate_inference_binding(
    *,
    environment: dict[str, str] | None = None,
) -> dict[str, str]:
    """Require an externally supplied Galatea inference authorization binding.

    The binding is deliberately separate from the model config.  A submitter
    may calculate config/artifact digests, but it cannot self-authorize a
    serving run by setting a local boolean.  Galatea (or an equivalent
    governed controller) must inject all identity fields and the explicit
    inference mode.
    """

    values = dict(os.environ if environment is None else environment)
    required = {
        "GALATEA_PROJECT": "llm-lora-playground",
        "GALATEA_RELEASE_ID": None,
        "GALATEA_READINESS_DIGEST": None,
        "GALATEA_EXECUTION_IDENTITY": None,
        "GALATEA_SUBMISSION_ID": None,
        "GALATEA_EXECUTION_MODE": "governed-ray-serve-inference",
        "GALATEA_INFERENCE_AUTHORIZED": "true",
    }
    missing: list[str] = []
    for key, expected in required.items():
        actual = values.get(key, "").strip()
        if not actual:
            missing.append(key)
        elif expected is not None and actual != expected:
            raise InferenceContractError(f"{key} must equal {expected}")
    if missing:
        raise InferenceContractError(
            "inference requires Galatea authorization binding: " + ", ".join(missing)
        )
    if not values["GALATEA_READINESS_DIGEST"].startswith("sha256:"):
        raise InferenceContractError("GALATEA_READINESS_DIGEST must be a sha256 digest")
    if not values["GALATEA_EXECUTION_IDENTITY"].startswith("sha256:"):
        raise InferenceContractError("GALATEA_EXECUTION_IDENTITY must be a sha256 digest")
    return {
        "project": values["GALATEA_PROJECT"],
        "release_id": values["GALATEA_RELEASE_ID"],
        "readiness_digest": values["GALATEA_READINESS_DIGEST"],
        "execution_identity": values["GALATEA_EXECUTION_IDENTITY"],
        "submission_id": values["GALATEA_SUBMISSION_ID"],
        "execution_mode": values["GALATEA_EXECUTION_MODE"],
    }


def validate_inference_config(config_path: Path) -> dict[str, Any]:
    """Load and validate the inference contract without loading model weights."""

    config = load_config(config_path)
    values = config.values
    errors: list[str] = []
    if values.get("schema_version") != "llm-lora-inference-v1":
        errors.append("schema_version must be llm-lora-inference-v1")
    if values.get("project") != "llm-lora-playground":
        errors.append("project must be llm-lora-playground")

    service = values.get("service", {})
    model = values.get("model", {})
    generation = values.get("generation", {})
    governance = values.get("governance", {})
    resources = values.get("resources", {})
    for key in ("name", "host", "port", "route_prefix", "replicas", "max_ongoing_requests"):
        if key not in service:
            errors.append(f"service.{key} is required")
    if service.get("host") not in {"127.0.0.1", "localhost"}:
        errors.append("service.host must remain loopback for the experimental service")
    if service.get("port") != 8000:
        errors.append("service.port must be 8000")
    if service.get("replicas") != 1:
        errors.append("service.replicas must be 1 for the single-GPU checkpoint")
    if service.get("max_ongoing_requests", 0) <= 0:
        errors.append("service.max_ongoing_requests must be positive")

    for key in ("id", "lora_id", "base_model_path", "adapter_path", "checkpoint_manifest_path", "source_mlflow_run_id", "engine", "protocol", "dtype", "device", "max_input_tokens", "lora_loading_path"):
        if key not in model:
            errors.append(f"model.{key} is required")
    if model.get("engine") != "ray-serve-llm-vllm":
        errors.append("model.engine must be ray-serve-llm-vllm")
    if model.get("protocol") != "openai-chat-completions-v1":
        errors.append("model.protocol must be openai-chat-completions-v1")
    if model.get("dtype") != "bfloat16":
        errors.append("model.dtype must be bfloat16")
    if model.get("device") != "cuda:0":
        errors.append("model.device must be cuda:0")
    if model.get("trust_remote_code") is not False:
        errors.append("model.trust_remote_code must be false")
    if model.get("enable_thinking") is not False:
        errors.append("model.enable_thinking must be false")
    lora_loading_path = str(model.get("lora_loading_path", ""))
    if not lora_loading_path.startswith(("s3://", "gs://", "abfss://", "azure://")):
        errors.append("model.lora_loading_path must be a controlled remote URI")
    if model.get("max_model_len", 0) <= 0:
        errors.append("model.max_model_len must be positive")
    if model.get("max_num_seqs", 0) <= 0:
        errors.append("model.max_num_seqs must be positive")
    if model.get("max_loras", 0) <= 0:
        errors.append("model.max_loras must be positive")
    if not 0 < float(model.get("gpu_memory_utilization", 0)) <= 1:
        errors.append("model.gpu_memory_utilization must be in (0, 1]")
    if governance.get("role") != "trial":
        errors.append("governance.role must be trial")
    if governance.get("promotable") is not False:
        errors.append("governance.promotable must be false")
    if governance.get("test_access") != "untouched":
        errors.append("governance.test_access must be untouched")
    if resources.get("num_gpus") != 1 or resources.get("cpus") != 4 or resources.get("memory_gb") != 8:
        errors.append("resources must declare 1 GPU, 4 CPUs and 8 GiB")
    if generation.get("max_new_tokens", 0) <= 0 or generation.get("max_new_tokens", 0) > 128:
        errors.append("generation.max_new_tokens must be in [1, 128]")
    if generation.get("repetition_penalty", 0) < 1.0:
        errors.append("generation.repetition_penalty must be at least 1")
    if generation.get("no_repeat_ngram_size", 0) < 0:
        errors.append("generation.no_repeat_ngram_size must be non-negative")

    for key in ("base_model_path", "adapter_path", "checkpoint_manifest_path"):
        value = model.get(key)
        if value:
            path = Path(str(value)).expanduser().resolve()
            if not path.exists():
                errors.append(f"model.{key} does not exist: {path}")
            elif path.is_symlink():
                errors.append(f"model.{key} must not be a symlink: {path}")
    if errors:
        raise InferenceContractError("; ".join(sorted(set(errors))))

    base_path = Path(str(model["base_model_path"])).expanduser().resolve()
    adapter_path = Path(str(model["adapter_path"])).expanduser().resolve()
    manifest_path = Path(str(model["checkpoint_manifest_path"])).expanduser().resolve()
    if not base_path.is_dir():
        raise InferenceContractError(f"base model path is not a directory: {base_path}")
    if not adapter_path.is_dir():
        raise InferenceContractError(f"adapter path is not a directory: {adapter_path}")
    if not manifest_path.is_file():
        raise InferenceContractError(f"checkpoint manifest is not a file: {manifest_path}")
    checkpoint = load_checkpoint(manifest_path.parent)
    adapter_config = adapter_path / "adapter_config.json"
    adapter_weights = adapter_path / "adapter_model.safetensors"
    if not adapter_config.is_file() or not adapter_weights.is_file():
        raise InferenceContractError("adapter must contain adapter_config.json and adapter_model.safetensors")
    adapter_metadata = json.loads(adapter_config.read_text(encoding="utf-8"))
    declared_base = str(adapter_metadata.get("base_model_name_or_path", ""))
    if declared_base and Path(declared_base).resolve() != base_path:
        raise InferenceContractError("adapter base_model_name_or_path does not match model.base_model_path")
    return {
        "config": config,
        "values": values,
        "config_digest": canonical_config_digest(config),
        "base_model_path": base_path,
        "adapter_path": adapter_path,
        "manifest_path": manifest_path,
        "checkpoint": checkpoint,
        "model_manifest_sha256": sha256_file(manifest_path),
        "adapter_config_sha256": sha256_file(adapter_config),
        "adapter_weights_sha256": sha256_file(adapter_weights),
    }


def build_serving_manifest(preflight: dict[str, Any], submission_id: str, code_revision: str) -> dict[str, Any]:
    values = preflight["values"]
    model = values["model"]
    checkpoint: CheckpointManifest = preflight["checkpoint"]
    return {
        "schema_version": "llm-lora-serving-v1",
        "service_name": values["service"]["name"],
        "submission_id": submission_id,
        "project": values["project"],
        "config_digest": preflight["config_digest"],
        "source_mlflow_run_id": model["source_mlflow_run_id"],
        "checkpoint_step": checkpoint.step,
        "checkpoint_manifest_sha256": preflight["model_manifest_sha256"],
        "adapter_config_sha256": preflight["adapter_config_sha256"],
        "adapter_weights_sha256": preflight["adapter_weights_sha256"],
        "base_model_path": str(preflight["base_model_path"]),
        "adapter_path": str(preflight["adapter_path"]),
        "engine": model["engine"],
        "protocol": model["protocol"],
        "governance": values["governance"],
        "code_revision": code_revision,
        "status": "starting",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def write_serving_manifest(manifest: dict[str, Any], output_root: Path, submission_id: str) -> Path:
    target = output_root.expanduser().resolve() / "runs" / submission_id / "serving_manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_suffix(".tmp")
    staged.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(staged, target)
    return target


def _json_error(message: str, status_code: int = 400) -> tuple[dict[str, Any], int]:
    return {"error": {"message": message, "type": "invalid_request_error", "param": None, "code": None}}, status_code


def create_deployment(preflight: dict[str, Any]):
    """Build the official Ray Serve LLM application backed by vLLM."""

    from ray.serve.llm import LLMConfig, LoraConfig, build_openai_app

    values = preflight["values"]
    model_values = values["model"]
    service_values = values["service"]
    model_id = str(model_values["id"])
    lora_id = str(model_values["lora_id"])
    base_model_path = str(preflight["base_model_path"])
    llm_config = LLMConfig(
        model_loading_config={"model_id": model_id, "model_source": base_model_path},
        engine_kwargs={
            "max_model_len": int(model_values["max_model_len"]),
            "max_num_seqs": int(model_values["max_num_seqs"]),
            "dtype": str(model_values["dtype"]),
            "enable_lora": True,
            "max_lora_rank": int(json.loads((Path(preflight["adapter_path"]) / "adapter_config.json").read_text(encoding="utf-8"))["r"]),
            "max_loras": int(model_values["max_loras"]),
            "gpu_memory_utilization": float(model_values["gpu_memory_utilization"]),
            "trust_remote_code": bool(model_values["trust_remote_code"]),
            "disable_log_stats": True,
        },
        placement_group_config={"bundles": [{"CPU": int(values["resources"]["cpus"]), "GPU": int(values["resources"]["num_gpus"])}], "strategy": "STRICT_PACK"},
        lora_config=LoraConfig(
            dynamic_lora_loading_path=str(model_values["lora_loading_path"]),
            max_num_adapters_per_replica=int(model_values["max_loras"]),
            download_timeout_s=60,
            max_download_tries=3,
        ),
        deployment_config={
            "num_replicas": int(service_values["replicas"]),
            "max_ongoing_requests": int(service_values["max_ongoing_requests"]),
            "ray_actor_options": {"num_cpus": 0, "num_gpus": 0},
        },
        log_engine_metrics=False,
    )
    app = build_openai_app({"llm_configs": [llm_config]})
    # The official ingress discovers adapters as <base_id>:<lora_id>.
    preflight["official_model_id"] = f"{model_id}:{lora_id}"
    return app
