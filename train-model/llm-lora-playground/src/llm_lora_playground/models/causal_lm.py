"""Qwen3.5 model loading and text generation."""

from __future__ import annotations

import hashlib
import inspect
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelConfig:
    model_id: str
    local_path: str
    dtype: str = "bfloat16"
    device: str = "cuda:0"
    max_input_tokens: int = 512
    trust_remote_code: bool = False
    enable_thinking: bool = False


@dataclass(frozen=True)
class GenerationConfig:
    do_sample: bool = True
    temperature: float = 0.7
    top_p: float = 0.9
    max_new_tokens: int = 128
    repetition_penalty: float = 1.0
    seed: int = 42


@dataclass
class ModelInputs:
    tensors: dict[str, Any]
    prompt_tokens: int


@dataclass
class LoadedCausalLM:
    model: Any
    tokenizer: Any
    resolved_revision: str
    device: str


@dataclass
class GenerationResult:
    prompt_tokens: int
    generated_tokens: int
    total_latency_ms: float = 0.0
    first_token_latency_ms: float = 0.0
    peak_gpu_memory_mib: float = 0.0
    output_sha256: str = ""
    status: str = "ok"
    error: str | None = None

    @property
    def tokens_per_second(self) -> float:
        return self.generated_tokens / (self.total_latency_ms / 1000.0) if self.total_latency_ms > 0 else 0.0


def _torch_dtype(name: str) -> Any:
    import torch

    return {"bfloat16": torch.bfloat16, "float16": torch.float16, "float32": torch.float32}[name]


def load_model_and_tokenizer(config: ModelConfig) -> LoadedCausalLM:
    try:
        import torch
        from transformers import AutoConfig, AutoModelForCausalLM, AutoProcessor
    except Exception as exc:
        raise RuntimeError(f"transformers/torch import failed: {type(exc).__name__}: {exc}") from exc
    model_path = Path(config.local_path)
    if not model_path.exists():
        raise FileNotFoundError(f"model path does not exist: {model_path}")
    try:
        model_config = AutoConfig.from_pretrained(model_path, local_files_only=True, trust_remote_code=config.trust_remote_code)
    except Exception as exc:
        raise RuntimeError(
            "Qwen3.5 architecture is not supported by the installed Transformers build; "
            f"model_type=qwen3_5, error={type(exc).__name__}: {exc}"
        ) from exc
    revision = getattr(model_config, "_name_or_path", None) or "local-" + hashlib.sha256(str(model_path).encode()).hexdigest()[:16]
    try:
        processor = AutoProcessor.from_pretrained(model_path, local_files_only=True, trust_remote_code=config.trust_remote_code)
        tokenizer = getattr(processor, "tokenizer", processor)
        # Qwen3.5 is a unified vision-language conditional-generation model;
        # Transformers 5.x exposes it through AutoModelForImageTextToText.
        model_loader = getattr(__import__("transformers", fromlist=["AutoModelForImageTextToText"]), "AutoModelForImageTextToText", AutoModelForCausalLM)
        model = model_loader.from_pretrained(
            model_path,
            local_files_only=True,
            dtype=_torch_dtype(config.dtype),
            device_map=config.device,
            trust_remote_code=config.trust_remote_code,
        )
    except Exception as exc:
        raise RuntimeError(f"Qwen3.5 model load failed: {type(exc).__name__}: {exc}") from exc
    return LoadedCausalLM(model=model, tokenizer=tokenizer, resolved_revision=str(revision), device=config.device)


def prepare_inputs(tokenizer: Any, messages: list[dict[str, str]], enable_thinking: bool, max_input_tokens: int = 512) -> ModelInputs:
    if not hasattr(tokenizer, "apply_chat_template"):
        raise TypeError("tokenizer does not provide apply_chat_template")
    kwargs = {
        "tokenize": True,
        "add_generation_prompt": True,
        "return_tensors": "pt",
        "truncation": True,
        "max_length": max_input_tokens,
    }
    signature = inspect.signature(tokenizer.apply_chat_template)
    if "enable_thinking" in signature.parameters:
        kwargs["enable_thinking"] = enable_thinking
    tensors = tokenizer.apply_chat_template(messages, **kwargs)
    if hasattr(tensors, "keys"):
        mapped = dict(tensors)
    else:
        mapped = {"input_ids": tensors}
    input_ids = mapped["input_ids"]
    prompt_tokens = int(input_ids.shape[-1])
    return ModelInputs(tensors=mapped, prompt_tokens=prompt_tokens)


def generate_one(loaded: LoadedCausalLM, inputs: ModelInputs, generation: GenerationConfig) -> GenerationResult:
    import torch

    try:
        torch.manual_seed(generation.seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(generation.seed)
            torch.cuda.reset_peak_memory_stats(loaded.device)
        tensors = {key: value.to(loaded.device) if hasattr(value, "to") else value for key, value in inputs.tensors.items()}
        kwargs = {
            **tensors,
            "do_sample": generation.do_sample,
            "temperature": generation.temperature,
            "top_p": generation.top_p,
            "max_new_tokens": generation.max_new_tokens,
            "repetition_penalty": generation.repetition_penalty,
        }
        start = time.perf_counter()
        with torch.inference_mode():
            output_ids = loaded.model.generate(**kwargs)
        if torch.cuda.is_available():
            torch.cuda.synchronize(loaded.device)
        total_ms = (time.perf_counter() - start) * 1000.0
        generated_ids = output_ids[..., inputs.prompt_tokens:]
        text = loaded.tokenizer.decode(generated_ids[0], skip_special_tokens=True)
        peak = torch.cuda.max_memory_allocated(loaded.device) / 2**20 if torch.cuda.is_available() else 0.0
        count = int(generated_ids.shape[-1])
        return GenerationResult(
            prompt_tokens=inputs.prompt_tokens,
            generated_tokens=count,
            total_latency_ms=total_ms,
            first_token_latency_ms=total_ms,
            peak_gpu_memory_mib=peak,
            output_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
    except Exception as exc:
        return GenerationResult(prompt_tokens=inputs.prompt_tokens, generated_tokens=0, status="decode_error", error=f"{type(exc).__name__}: {exc}")
