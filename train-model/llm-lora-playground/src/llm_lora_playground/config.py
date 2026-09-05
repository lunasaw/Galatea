"""Configuration loading and validation for the bounded baseline."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ProjectConfig:
    values: dict[str, Any]
    source_path: Path

    def __getattr__(self, name: str) -> Any:
        try:
            return self.values[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


TrainingConfig = ProjectConfig


def load_config(path: Path) -> ProjectConfig:
    values = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(values, dict):
        raise ValueError("configuration root must be a mapping")
    return ProjectConfig(values=values, source_path=path.resolve())


def canonical_config_digest(config: ProjectConfig) -> str:
    payload = json.dumps(config.values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


canonical_training_config_digest = canonical_config_digest


def load_training_config(path: Path) -> TrainingConfig:
    return load_config(path)


def validate_config(config: ProjectConfig) -> list[str]:
    errors: list[str] = []
    model = config.values.get("model", {})
    generation = config.values.get("generation", {})
    if model.get("id") != "Qwen/Qwen3.5-0.8B":
        errors.append("model.id must be Qwen/Qwen3.5-0.8B")
    if model.get("dtype") != "bfloat16":
        errors.append("model.dtype must be bfloat16")
    if model.get("device") != "cuda:0":
        errors.append("model.device must be cuda:0")
    if model.get("enable_thinking") is not False:
        errors.append("model.enable_thinking must be false")
    if generation.get("max_new_tokens") != 128:
        errors.append("generation.max_new_tokens must be 128")
    resources = config.values.get("resources", {"num_gpus": 1})
    if resources.get("num_gpus", 1) != 1:
        errors.append("num_gpus must be 1")
    if config.values.get("prompt", {}).get("use_tokenizer_chat_template") is not True:
        errors.append("prompt.use_tokenizer_chat_template must be true")
    return errors


def validate_training_config(config: TrainingConfig) -> list[str]:
    """Validate the project 2-4 training contract without loading model weights."""
    errors = list(validate_config(config))
    values = config.values
    data = values.get("data", {})
    lora = values.get("lora", {})
    training = values.get("training", {})
    resources = values.get("resources", {})
    if values.get("run_kind") not in {"smoke", "baseline", "evaluation", "ray_smoke", "owner_bulk_approved_experiment"}:
        errors.append("run_kind must be smoke, baseline, evaluation, ray_smoke, or owner_bulk_approved_experiment")
    if not data.get("assistant_only_loss"):
        errors.append("data.assistant_only_loss must be true")
    if data.get("packing") is not False:
        errors.append("data.packing must be false")
    if not lora.get("target_modules"):
        errors.append("lora.target_modules must not be empty")
    if values.get("objective_metric") != "validation_loss":
        errors.append("objective_metric must be validation_loss")
    if values.get("objective_mode") not in {"min", "max"}:
        errors.append("objective_mode must be min or max")
    if resources.get("cpus") != 4 or resources.get("memory_gb") != 8:
        errors.append("resources must declare 4 CPUs and 8 GiB")
    if values.get("run_kind") == "smoke" and training.get("max_steps") != 10:
        errors.append("smoke max_steps must be 10")
    if values.get("run_kind") == "baseline" and training.get("epochs") != 1:
        errors.append("baseline epochs must be 1")
    if values.get("run_kind") == "baseline" and training.get("max_steps") is not None:
        errors.append("baseline max_steps must be null")
    if values.get("run_kind") == "owner_bulk_approved_experiment":
        experiment = values.get("experiment", {})
        if experiment.get("owner_bulk_approved") is not True:
            errors.append("owner_bulk_approved experiment requires experiment.owner_bulk_approved=true")
        if experiment.get("formal_training_eligible") is not False:
            errors.append("owner_bulk_approved experiment must remain formal_training_eligible=false")
        if not experiment.get("approval_basis"):
            errors.append("owner_bulk_approved experiment requires experiment.approval_basis")
        if training.get("epochs") != 1:
            errors.append("owner_bulk_approved experiment epochs must be 1")
        if training.get("max_steps") is not None:
            errors.append("owner_bulk_approved experiment max_steps must be null")
    secret_key_pattern = re.compile(r"(^|_)(token|password|secret|access[_-]?key|secret[_-]?key)(_|$)", re.IGNORECASE)

    def find_secret_keys(value: Any, prefix: str = "") -> list[str]:
        found: list[str] = []
        if isinstance(value, dict):
            for key, child in value.items():
                key_text = str(key)
                if secret_key_pattern.search(key_text):
                    found.append(f"{prefix}{key_text}")
                found.extend(find_secret_keys(child, f"{prefix}{key_text}."))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                found.extend(find_secret_keys(child, f"{prefix}{index}."))
        return found

    for key in find_secret_keys(values):
        errors.append(f"configuration contains secret-like key: {key}")
    return sorted(set(errors))


def env_or_config(config: ProjectConfig, env_name: str, dotted: tuple[str, ...], default: str | None = None) -> str | None:
    value = os.environ.get(env_name)
    if value:
        return value
    current: Any = config.values
    for part in dotted:
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return str(current) if current is not None else default
