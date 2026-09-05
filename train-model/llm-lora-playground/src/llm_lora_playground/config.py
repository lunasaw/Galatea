"""Configuration loading and validation for the bounded baseline."""

from __future__ import annotations

import hashlib
import json
import os
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
    if values.get("run_kind") not in {"smoke", "baseline", "evaluation", "ray_smoke"}:
        errors.append("run_kind must be smoke, baseline, evaluation, or ray_smoke")
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
    serialized = json.dumps(values, ensure_ascii=False).lower()
    for marker in ("token", "password", "secret", "access_key", "minio_key"):
        if marker in serialized and marker not in {"token"}:
            errors.append(f"configuration contains secret-like key: {marker}")
            break
    tracking = values.get("tracking", {})
    if any(key.lower() in {"token", "password", "secret", "access_key", "secret_key"} for key in tracking):
        errors.append("tracking must not contain credentials")
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
