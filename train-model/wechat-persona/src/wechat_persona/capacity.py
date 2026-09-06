"""Capacity-comparison and QLoRA compatibility gates."""
from __future__ import annotations

from typing import Any, Mapping


class CapacityContractError(ValueError):
    pass


def compare_model_configs(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Report experiment variables while treating resources as their own section."""
    left = dict(baseline)
    right = dict(candidate)
    sections = set(left) | set(right)
    changed: set[str] = set()
    for key in sections:
        if key == "execution":
            left_execution = dict(left.get("execution", {}))
            right_execution = dict(right.get("execution", {}))
            left_resources = left_execution.pop("resources", None)
            right_resources = right_execution.pop("resources", None)
            if left_resources != right_resources:
                changed.add("resources")
            if left_execution != right_execution:
                changed.add("execution")
        elif left.get(key) != right.get(key):
            changed.add(key)
    allowed = {"model", "resources", "task", "run"}
    unexpected = sorted(changed - allowed)
    if unexpected:
        raise CapacityContractError(
            "capacity comparison changed frozen sections: " + ", ".join(unexpected)
        )
    return {"changed_sections": sorted(changed), "unexpected_sections": unexpected}


def validate_qlora_preflight(config: Mapping[str, Any], evidence: Mapping[str, Any]) -> bool:
    quantization = config.get("quantization", {})
    model = config.get("model", {})
    if quantization.get("method") != "qlora" or quantization.get("bits") != 4:
        raise CapacityContractError("QLoRA config must use 4-bit quantization")
    if quantization.get("quant_type") != "nf4" or quantization.get("double_quant") is not True:
        raise CapacityContractError("QLoRA config must freeze NF4 double quantization")
    if model.get("compute_dtype") != "bfloat16":
        raise CapacityContractError("QLoRA compute dtype must be bfloat16")
    required = {
        "bitsandbytes_revision", "torch_revision", "transformers_revision",
        "cuda_revision", "gpu_model",
    }
    missing = sorted(key for key in required if not evidence.get(key))
    if missing:
        raise CapacityContractError("QLoRA environment evidence is incomplete: " + ", ".join(missing))
    if evidence.get("capacity_bottleneck_confirmed") is not True:
        raise CapacityContractError("4B QLoRA requires confirmed 0.8B/1.7B capacity bottleneck")
    if evidence.get("forward_passed") is not True:
        raise CapacityContractError("QLoRA forward preflight failed")
    if int(evidence.get("backward_steps", 0)) < 2:
        raise CapacityContractError("QLoRA requires a two-step governed backward fixture")
    if evidence.get("adapter_roundtrip_verified") is not True:
        raise CapacityContractError("QLoRA adapter round-trip is not verified")
    if evidence.get("cpu_fallback") is not False:
        raise CapacityContractError("QLoRA CPU fallback is forbidden")
    return True
