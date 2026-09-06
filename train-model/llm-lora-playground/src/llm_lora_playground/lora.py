"""Optional PEFT integration with strict target-module validation."""

from __future__ import annotations

from typing import Any
from pathlib import Path
import json


class LoRAContractError(ValueError):
    pass


def adapter_key_architecture(adapter_dir: str | Path) -> str:
    """Infer the PEFT module tree without loading model weights.

    Qwen3.5 text-only and conditional-generation models intentionally have
    different module prefixes.  A string replacement at serving time is not a
    supported migration, so unknown/mixed prefixes fail closed.
    """

    directory = Path(adapter_dir)
    weights = directory / "adapter_model.safetensors"
    if not weights.is_file():
        raise LoRAContractError(f"adapter weights are missing: {weights}")
    try:
        from safetensors import safe_open

        with safe_open(str(weights), framework="pt") as handle:
            keys = list(handle.keys())
    except Exception as exc:
        raise LoRAContractError(f"cannot inspect adapter weights: {type(exc).__name__}: {exc}") from exc
    if not keys:
        raise LoRAContractError("adapter weights contain no tensors")
    if all("base_model.model.model.language_model.layers." in key for key in keys):
        return "qwen3_5_conditional_generation"
    if all("base_model.model.model.layers." in key for key in keys):
        return "qwen3_5_causal_lm"
    if all("base_model.model.language_model.model.layers." in key for key in keys):
        return "qwen3_5_conditional_generation"
    raise LoRAContractError("adapter tensor keys do not match a supported Qwen3.5 module tree")


def validate_adapter_architecture(adapter_dir: str | Path, *, expected: str) -> dict[str, Any]:
    directory = Path(adapter_dir)
    config_path = directory / "adapter_config.json"
    if not config_path.is_file():
        raise LoRAContractError(f"adapter config is missing: {config_path}")
    metadata = json.loads(config_path.read_text(encoding="utf-8"))
    observed = str(metadata.get("model_architecture") or adapter_key_architecture(directory))
    if observed != expected:
        raise LoRAContractError(
            f"adapter architecture mismatch: expected {expected}, observed {observed}; "
            "re-export the adapter from the same model class as serving"
        )
    return {"model_architecture": observed, "target_modules": list(metadata.get("target_modules") or [])}


def validate_target_modules(model: Any, target_modules: list[str]) -> list[str]:
    candidates = [name for name, _ in model.named_modules() if name]
    matched = [name for name in candidates if any(name == target or name.endswith("." + target) for target in target_modules)]
    missing = [target for target in target_modules if not any(name == target or name.endswith("." + target) for name in candidates)]
    if missing:
        preview = ", ".join(candidates[:20])
        raise LoRAContractError(f"LoRA target modules not found: {missing}; available candidates: {preview}")
    return matched


def build_lora_model(model: Any, lora_config: dict[str, Any]) -> Any:
    targets = list(lora_config.get("target_modules", []))
    validate_target_modules(model, targets)
    try:
        from peft import LoraConfig, get_peft_model
    except ImportError as exc:
        raise RuntimeError("PEFT is required to inject LoRA adapters") from exc
    config = LoraConfig(
        r=int(lora_config["rank"]),
        lora_alpha=int(lora_config["alpha"]),
        lora_dropout=float(lora_config["dropout"]),
        target_modules=targets,
        bias=lora_config.get("bias", "none"),
        task_type="CAUSAL_LM",
    )
    return get_peft_model(model, config)


def load_adapter(base_model: Any, adapter_dir: str, expected_identity: dict[str, str] | None = None) -> Any:
    try:
        from peft import PeftModel
    except ImportError as exc:
        raise RuntimeError("PEFT is required to load LoRA adapters") from exc
    return PeftModel.from_pretrained(base_model, adapter_dir, is_trainable=False)
