"""Optional PEFT integration with strict target-module validation."""

from __future__ import annotations

from typing import Any


class LoRAContractError(ValueError):
    pass


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
