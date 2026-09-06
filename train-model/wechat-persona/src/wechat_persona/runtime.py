"""Configuration and local prototype safety boundaries."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

from ._common import canonical_json
from .redact import scan_redacted_text


class RuntimeEligibilityError(ValueError):
    pass


@dataclass(frozen=True)
class ChatResponse:
    text: str
    metadata: dict[str, Any]
    fallback: bool = False

    def __str__(self) -> str:
        return self.text

    def startswith(self, prefix: str, *args: Any) -> bool:
        return self.text.startswith(prefix, *args)


def load_project_config(path: Path | str) -> dict[str, Any]:
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("configuration root must be a mapping")
    return value


def _has_secret_key(value: Any, prefix: str = "") -> list[str]:
    pattern = re.compile(r"(^|_)(password|secret|token|api[_-]?key|access[_-]?key)(_|$)", re.I)
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            if pattern.search(key_text):
                found.append(f"{prefix}{key_text}")
            found.extend(_has_secret_key(child, f"{prefix}{key_text}."))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value):
            found.extend(_has_secret_key(child, f"{prefix}{index}."))
    return found


def validate_project_config(config: Mapping[str, Any], *, raise_on_error: bool = False) -> list[str]:
    values = dict(config)
    errors: list[str] = []
    if values.get("schema_version") != "wechat-persona-config-v1":
        errors.append("schema_version must be wechat-persona-config-v1")
    if values.get("project") != "wechat-persona":
        errors.append("project must be wechat-persona")
    task = values.get("task")
    allowed_tasks = {"data_engineering", "memory_rag", "causal-language-model-sft-lora", "capacity_comparison", "local_prototype", "screenplay_generation"}
    if task not in allowed_tasks:
        errors.append("task is unsupported")
    run = values.get("run", {})
    if not isinstance(run, Mapping) or run.get("role") not in {"check", "plan", "smoke", "trial", "baseline", "champion", "evaluation", "prototype"}:
        errors.append("run.role is required")
    if not isinstance(run.get("promotable", False), bool):
        errors.append("run.promotable must be boolean")
    dataset = values.get("dataset")
    if task in {"data_engineering", "memory_rag", "causal-language-model-sft-lora", "capacity_comparison"}:
        if not isinstance(dataset, Mapping):
            errors.append("dataset is required")
        else:
            for key in ("dataset_id", "source_sha256", "manifest_sha256", "split_sha256", "consent_scope", "preprocessing_version"):
                if not dataset.get(key):
                    errors.append(f"dataset.{key} is required")
            for key in ("source_sha256", "manifest_sha256", "split_sha256"):
                if dataset.get(key) and not re.fullmatch(r"[0-9a-f]{64}", str(dataset[key])):
                    errors.append(f"dataset.{key} must be a lowercase SHA-256 digest")
    if task in {"causal-language-model-sft-lora", "capacity_comparison"}:
        execution = values.get("execution", {})
        if execution.get("backend") != "ray":
            errors.append("execution.backend must be ray")
        for key in ("release_id", "readiness_digest", "execution_identity"):
            if not execution.get(key):
                errors.append(f"execution.{key} is required")
        resources = execution.get("resources", {})
        for key in ("cpus", "memory_gb", "num_gpus", "placement"):
            if key not in resources:
                errors.append(f"execution.resources.{key} is required")
        model = values.get("model", {})
        for key in ("model_id", "model_revision", "tokenizer_revision", "dtype"):
            if not model.get(key):
                errors.append(f"model.{key} is required")
        training = values.get("training", {})
        for key in ("epochs", "batch_size", "learning_rate", "seed"):
            if key not in training:
                errors.append(f"training.{key} is required")
        lora = values.get("lora", {})
        if not lora.get("target_modules"):
            errors.append("lora.target_modules is required")
        evaluation = values.get("evaluation", {})
        for key in ("protocol_version", "objective_metric", "objective_direction", "test_access"):
            if not evaluation.get(key):
                errors.append(f"evaluation.{key} is required")
        if evaluation.get("test_access") != "untouched" and run.get("role") != "champion":
            errors.append("only champion may access test")
        if run.get("role") == "champion" and run.get("promotable") is not True:
            errors.append("champion must be promotable")
    if task == "memory_rag":
        rag = values.get("rag", {})
        if rag.get("owner_scope_required") is not True:
            errors.append("rag.owner_scope_required must be true")
    if task == "local_prototype":
        runtime = values.get("runtime", {})
        if runtime.get("ai_identity_label") != "AI 生成内容":
            errors.append("runtime.ai_identity_label must identify AI")
    if task == "screenplay_generation":
        screenplay = values.get("screenplay", {})
        for key in ("allow_voice", "allow_face", "allow_auto_message"):
            if screenplay.get(key) is not False:
                errors.append(f"screenplay.{key} must be false")
    errors.extend(f"configuration contains secret-like key: {key}" for key in _has_secret_key(values))
    errors = sorted(set(errors))
    if errors and raise_on_error:
        raise ValueError("; ".join(errors))
    return errors


def check_runtime_eligibility(candidate: Mapping[str, Any], protocol_digest: str) -> bool:
    if candidate.get("quality_evidence_status") != "accepted":
        raise RuntimeEligibilityError("quality evidence is not accepted")
    if candidate.get("human_review_completed") is not True:
        raise RuntimeEligibilityError("human review is incomplete")
    if candidate.get("withdrawn") is True or candidate.get("governance_status") == "withdrawn":
        raise RuntimeEligibilityError("candidate is withdrawn")
    if candidate.get("protocol_digest") != protocol_digest:
        raise RuntimeEligibilityError("protocol digest mismatch")
    return True


_UNSAFE_MARKERS = (
    "<secret>", "<pii_", "ignore previous", "pretend to be the real person",
    "you are human", "我就是真人", "只有我懂你", "不许离开我", "你必须只依赖我",
)


def load_eligible_runtime(
    config: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    index: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind a local prototype to reviewed, immutable upstream identities."""
    errors = validate_project_config(config)
    if errors:
        raise RuntimeEligibilityError("invalid runtime config: " + "; ".join(errors))
    runtime = config.get("runtime", {})
    if runtime.get("enabled") is not True:
        raise RuntimeEligibilityError("local prototype is disabled")
    model = config.get("model", {})
    expected_protocol = str(model.get("protocol_version", ""))
    check_runtime_eligibility(candidate, expected_protocol)
    for key in ("model_id", "model_revision", "adapter_id"):
        if str(model.get(key, "")) != str(candidate.get(key, model.get(key, ""))):
            raise RuntimeEligibilityError(f"{key} mismatch")
    if index is not None:
        if index.get("quality_evidence_status") != "accepted":
            raise RuntimeEligibilityError("memory index quality evidence is not accepted")
        if index.get("human_review_completed") is not True or index.get("withdrawn") is True:
            raise RuntimeEligibilityError("memory index is not eligible")
    return {
        "status": "enabled",
        "model_id": model.get("model_id"),
        "model_revision": model.get("model_revision"),
        "adapter_id": model.get("adapter_id"),
        "protocol_digest": expected_protocol,
        "index_id": index.get("index_id") if index else None,
        "local_only": True,
    }


def safe_chat_response(
    user_message: str,
    *,
    owner_scope: str,
    memory_enabled: bool = False,
    generator: Callable[[Any], str] | None = None,
    retrieved: Sequence[Any] = (),
) -> ChatResponse:
    if not owner_scope:
        raise RuntimeEligibilityError("owner_scope is required")
    memory_ids = [getattr(item, "memory_id", getattr(getattr(item, "record", None), "memory_id", "")) for item in (retrieved if memory_enabled else ())]
    prompt = {
        "system": (
            "你是明确标注的 AI 文本助手，不是真人，不鼓励排他依赖。"
            "仅使用提供的已审核记忆；没有证据时说明不知道或不确定。"
        ),
        "owner_scope": owner_scope,
        "memory_enabled": memory_enabled,
        "memory_ids": memory_ids,
        "user_message": str(user_message),
    }
    try:
        output = str(generator(prompt) if generator else "我没有可用的生成模型。")
    except Exception:
        output = "我暂时无法安全生成回复。"
    lowered = output.casefold()
    unsafe = any(marker in lowered for marker in _UNSAFE_MARKERS) or scan_redacted_text(output)["hard_leak_count"] > 0
    metadata = {"memory_ids": memory_ids, "owner_scope": owner_scope, "memory_enabled": memory_enabled, "policy_version": "local-chat-policy-v1"}
    if unsafe or not output.strip():
        return ChatResponse("AI 生成内容：我无法安全回答这个请求。", metadata, True)
    return ChatResponse(f"AI 生成内容：{output.strip()}", metadata, False)


def protocol_digest(protocol: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(protocol)).encode("utf-8")).hexdigest()
