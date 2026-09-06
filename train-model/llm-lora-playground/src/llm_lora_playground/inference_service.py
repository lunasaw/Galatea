"""Ray Serve inference runtime for an immutable PEFT checkpoint.

The deployment deliberately uses the project's verified Transformers + PEFT
loader.  It exposes the OpenAI chat-completions contract used by Ray LLM
clients, while keeping the engine replaceable when the model becomes vLLM
compatible.  No request or generated text is written to disk or logs.
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

    for key in ("id", "base_model_path", "adapter_path", "checkpoint_manifest_path", "source_mlflow_run_id", "engine", "protocol", "dtype", "device", "max_input_tokens"):
        if key not in model:
            errors.append(f"model.{key} is required")
    if model.get("engine") != "transformers-peft-ray-serve":
        errors.append("model.engine must be transformers-peft-ray-serve")
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
    """Build a Ray Serve deployment application from a validated preflight."""

    from fastapi import FastAPI, Request
    from ray import serve

    values = preflight["values"]
    model_values = values["model"]
    generation_values = values["generation"]
    service_values = values["service"]
    model_id = str(model_values["id"])
    base_model_path = str(preflight["base_model_path"])
    adapter_path = str(preflight["adapter_path"])
    max_input_tokens = int(model_values["max_input_tokens"])
    system_policy = str(values["prompt"]["system_policy"])
    app = FastAPI(title="Galatea experimental LLM", docs_url=None, redoc_url=None)

    @serve.deployment
    @serve.ingress(app)
    class LoraInferenceDeployment:
        def __init__(self, runtime_values: dict[str, Any]):
            import torch
            from .models.causal_lm import load_model_and_tokenizer

            self.model_id = model_id
            self.engine = str(runtime_values["model"]["engine"])
            self.checkpoint_step = int(preflight["checkpoint"].step)
            self.device = str(runtime_values["model"]["device"])
            self.max_input_tokens = max_input_tokens
            self.system_policy = system_policy
            model_config = ModelConfig(
                model_id="Qwen/Qwen3.5-0.8B",
                local_path=base_model_path,
                dtype=str(runtime_values["model"]["dtype"]),
                device=self.device,
                max_input_tokens=max_input_tokens,
                trust_remote_code=False,
                enable_thinking=False,
            )
            loaded = load_model_and_tokenizer(model_config)
            self.tokenizer = loaded.tokenizer
            self.model = load_adapter(loaded.model, adapter_path)
            self.model.eval()
            self._torch = torch
            self._ready = True

        @app.get("/healthz")
        async def healthz(self) -> dict[str, Any]:
            return {
                "status": "ready" if self._ready else "starting",
                "model": self.model_id,
                "engine": self.engine,
                "checkpoint_step": self.checkpoint_step,
                "promotable": False,
                "test_access": "untouched",
            }

        @app.get("/v1/models")
        async def models(self) -> dict[str, Any]:
            return {
                "object": "list",
                "data": [{"id": self.model_id, "object": "model", "owned_by": "galatea-trial"}],
            }

        @app.post("/v1/chat/completions")
        async def chat_completions(self, request: Request) -> Any:
            try:
                payload = await request.json()
                messages = payload.get("messages")
                if not isinstance(messages, list) or not messages:
                    return _json_error("messages must be a non-empty array")
                clean_messages: list[dict[str, str]] = []
                for message in messages:
                    if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant"}:
                        return _json_error("messages contain an unsupported role")
                    content = message.get("content")
                    if not isinstance(content, str) or not content.strip():
                        return _json_error("message content must be a non-empty string")
                    clean_messages.append({"role": str(message["role"]), "content": content})
                if not any(message["role"] == "user" for message in clean_messages):
                    return _json_error("messages must contain a user message")
                requested_model = payload.get("model", self.model_id)
                if requested_model != self.model_id:
                    return _json_error(f"model must be {self.model_id}", 404)
                max_new_tokens = int(payload.get("max_tokens", generation_values["max_new_tokens"]))
                max_new_tokens = max(1, min(max_new_tokens, int(generation_values["max_new_tokens"])))
                do_sample = bool(payload.get("temperature", 0) not in (0, 0.0)) and bool(generation_values["do_sample"])
                temperature = float(payload.get("temperature", generation_values["temperature"]))
                top_p = float(payload.get("top_p", generation_values["top_p"]))
                seed = int(payload.get("seed", generation_values["seed"]))
                if not any(message["role"] == "system" for message in clean_messages):
                    clean_messages.insert(0, {"role": "system", "content": self.system_policy})
                text, prompt_tokens, completion_tokens, finish_reason = self._generate(
                    clean_messages, max_new_tokens, do_sample, temperature, top_p, seed
                )
                request_id = f"chatcmpl-{uuid.uuid4().hex}"
                return {
                    "id": request_id,
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": self.model_id,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish_reason}],
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": prompt_tokens + completion_tokens,
                    },
                }
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                return _json_error(f"invalid request: {exc}")
            except Exception:
                # Do not return private prompt/model internals in an HTTP error.
                return _json_error("inference failed", 500)

        def _generate(
            self,
            messages: list[dict[str, str]],
            max_new_tokens: int,
            do_sample: bool,
            temperature: float,
            top_p: float,
            seed: int,
        ) -> tuple[str, int, int, str]:
            self._torch.manual_seed(seed)
            if self._torch.cuda.is_available():
                self._torch.cuda.manual_seed_all(seed)
            inputs = prepare_inputs(self.tokenizer, messages, False, self.max_input_tokens)
            tensors = {key: value.to(self.device) if hasattr(value, "to") else value for key, value in inputs.tensors.items()}
            kwargs = {
                **tensors,
                "do_sample": do_sample,
                "temperature": temperature if do_sample else 1.0,
                "top_p": top_p if do_sample else 1.0,
                "max_new_tokens": max_new_tokens,
                "repetition_penalty": float(generation_values["repetition_penalty"]),
                "no_repeat_ngram_size": int(generation_values["no_repeat_ngram_size"]),
                "pad_token_id": self.tokenizer.pad_token_id,
                "eos_token_id": self.tokenizer.eos_token_id,
            }
            with self._torch.inference_mode():
                output_ids = self.model.generate(**kwargs)
            generated_ids = output_ids[..., inputs.prompt_tokens:]
            generated_tokens = int(generated_ids.shape[-1])
            text = self.tokenizer.decode(generated_ids[0], skip_special_tokens=True).strip()
            finish_reason = "length" if generated_tokens >= max_new_tokens else "stop"
            return text, inputs.prompt_tokens, generated_tokens, finish_reason

    deployment_options = {
        "num_replicas": int(service_values["replicas"]),
        "max_ongoing_requests": int(service_values["max_ongoing_requests"]),
        "health_check_period_s": float(service_values.get("health_check_period_s", 10)),
        "health_check_timeout_s": float(service_values.get("health_check_timeout_s", 30)),
        "ray_actor_options": {"num_gpus": 1, "num_cpus": 4, "memory": 8 * 1024**3},
    }
    return LoraInferenceDeployment.options(**deployment_options).bind(values)
