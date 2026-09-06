"""Fail-closed Base-vs-LoRA effectiveness probes.

The presence of an adapter file or a successful adapter registration is not
evidence that the adapter is attached to the model that serves requests.  The
helpers in this module deliberately compare the same tokenized probes with
the adapter disabled and enabled, and can therefore be used by both a
Transformer loader and a serving readiness hook.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, Sequence


class AdapterEffectError(RuntimeError):
    """Raised when a LoRA adapter cannot be shown to affect model behaviour."""


@dataclass(frozen=True)
class AdapterProbe:
    probe_id: str
    input_ids: Any
    attention_mask: Any | None = None


@dataclass(frozen=True)
class ProbeResult:
    probe_id: str
    max_abs_logit_delta: float
    mean_abs_logit_delta: float
    changed_token_count: int
    base_output_sha256: str | None = None
    lora_output_sha256: str | None = None

    @property
    def changed(self) -> bool:
        return self.max_abs_logit_delta > 0.0 or self.changed_token_count > 0


def _logits(output: Any) -> Any:
    value = getattr(output, "logits", None)
    if value is None and isinstance(output, dict):
        value = output.get("logits")
    if value is None:
        raise AdapterEffectError("model probe output does not contain logits")
    return value


def _as_float(value: Any) -> float:
    return float(value.detach().float().cpu().item() if hasattr(value, "detach") else value)


def _forward(model: Any, probe: AdapterProbe) -> Any:
    kwargs = {"input_ids": probe.input_ids}
    if probe.attention_mask is not None:
        kwargs["attention_mask"] = probe.attention_mask
    output = model(**kwargs)
    logits = _logits(output)
    return logits.detach() if hasattr(logits, "detach") else logits


def _adapter_disabled(model: Any):
    context = getattr(model, "disable_adapter", None)
    if context is None:
        from contextlib import nullcontext

        return nullcontext()
    return context()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def compare_probe(model: Any, probe: AdapterProbe, *, tokenizer: Any | None = None) -> ProbeResult:
    """Compare logits for one probe with the adapter disabled/enabled."""

    try:
        import torch

        with torch.inference_mode():
            with _adapter_disabled(model):
                base_logits = _forward(model, probe)
            lora_logits = _forward(model, probe)
        delta = (lora_logits.float() - base_logits.float()).abs()
        changed = int((delta > 1e-7).sum().item())
        base_hash = lora_hash = None
        if tokenizer is not None:
            base_ids = base_logits.argmax(dim=-1)
            lora_ids = lora_logits.argmax(dim=-1)
            base_hash = _sha256_text(tokenizer.decode(base_ids[0].tolist(), skip_special_tokens=True))
            lora_hash = _sha256_text(tokenizer.decode(lora_ids[0].tolist(), skip_special_tokens=True))
        return ProbeResult(
            probe_id=probe.probe_id,
            max_abs_logit_delta=_as_float(delta.max()),
            mean_abs_logit_delta=_as_float(delta.mean()),
            changed_token_count=changed,
            base_output_sha256=base_hash,
            lora_output_sha256=lora_hash,
        )
    except AdapterEffectError:
        raise
    except Exception as exc:
        raise AdapterEffectError(f"adapter probe failed for {probe.probe_id}: {type(exc).__name__}: {exc}") from exc


def assert_adapter_effective(
    model: Any,
    probes: Sequence[AdapterProbe],
    *,
    tokenizer: Any | None = None,
    min_changed_probes: int = 1,
    min_abs_logit_delta: float = 1e-7,
) -> list[ProbeResult]:
    """Require at least one material difference across independent probes."""

    if not probes:
        raise AdapterEffectError("at least one adapter probe is required")
    results = [compare_probe(model, probe, tokenizer=tokenizer) for probe in probes]
    changed = [result for result in results if result.max_abs_logit_delta >= min_abs_logit_delta or result.changed_token_count]
    if len(changed) < min_changed_probes:
        raise AdapterEffectError(
            "LoRA readiness probe found no material Base-vs-LoRA difference "
            f"({len(changed)}/{len(results)} probes changed)"
        )
    return results


def probes_from_tokenizer(tokenizer: Any, conversations: Iterable[tuple[str, list[dict[str, str]]]], *, max_length: int = 256) -> list[AdapterProbe]:
    """Build deterministic probes from chat conversations without persisting text."""

    probes: list[AdapterProbe] = []
    for probe_id, messages in conversations:
        rendered = tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            truncation=True,
            max_length=max_length,
            return_tensors="pt",
        )
        if hasattr(rendered, "keys"):
            input_ids = rendered["input_ids"]
            attention_mask = rendered.get("attention_mask")
        else:
            input_ids = rendered
            attention_mask = None
        probes.append(AdapterProbe(probe_id, input_ids, attention_mask))
    return probes
