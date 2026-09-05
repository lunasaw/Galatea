"""Validation-only selection and deterministic evaluation protocol helpers."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from typing import Any


class EvaluationProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class EvaluationResult:
    variant: str
    split: str
    metrics: dict[str, float]
    style_checks: dict[str, Any]
    test_evaluation_id: str | None = None


@dataclass(frozen=True)
class FrozenCandidate:
    candidate: dict[str, Any]
    protocol: dict[str, Any]
    freeze_id: str


@dataclass(frozen=True)
class TestEvaluationClaim:
    test_evaluation_id: str
    freeze_id: str
    split_manifest_sha256: str


def compute_automatic_metrics(records: list[dict[str, Any]]) -> dict[str, float]:
    lengths = [len(str(record.get("output", "")).split()) for record in records]
    losses = [float(record["validation_loss"]) for record in records if "validation_loss" in record]
    return {
        "record_count": len(records),
        "generated_length_mean": sum(lengths) / len(lengths) if lengths else 0.0,
        "validation_loss": sum(losses) / len(losses) if losses else 0.0,
        "format_follow_rate": sum(bool(record.get("output")) for record in records) / len(records) if records else 0.0,
    }


def run_fixed_style_checks(records: list[dict[str, Any]], ruleset_version: str) -> dict[str, Any]:
    outputs = [str(record.get("output", "")) for record in records]
    return {"ruleset_version": ruleset_version, "record_count": len(records), "non_empty": all(bool(output.strip()) for output in outputs)}


def freeze_candidate(candidate: dict[str, Any], protocol: dict[str, Any]) -> FrozenCandidate:
    if any(key.startswith("test_") for key in candidate):
        raise EvaluationProtocolError("test metrics cannot be used to freeze a candidate")
    required = {"run_id", "validation_loss"}
    if not required.issubset(candidate):
        raise EvaluationProtocolError("candidate must contain validation evidence")
    payload = {"candidate": candidate, "protocol": protocol}
    return FrozenCandidate(candidate, protocol, hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest())


def evaluate_variant(variant: str, frozen_protocol: dict[str, Any], model_ref: Any, adapter_ref: Any = None) -> EvaluationResult:
    """Evaluate a supplied callable/model without changing the frozen protocol.

    ``model_ref`` may be a callable accepting a record or an iterable of precomputed
    records.  This keeps protocol tests independent of optional GPU libraries while
    allowing the production script to pass a real generation function.
    """
    if variant not in {"base", "prompt-only", "lora"}:
        raise EvaluationProtocolError(f"unknown variant: {variant}")
    records = list(model_ref() if callable(model_ref) else model_ref)
    split = str(frozen_protocol.get("split", "validation"))
    if split == "test" and not frozen_protocol.get("test_evaluation_id"):
        raise EvaluationProtocolError("test evaluation requires a frozen test_evaluation_id")
    return EvaluationResult(
        variant=variant,
        split=split,
        metrics=compute_automatic_metrics(records),
        style_checks=run_fixed_style_checks(records, str(frozen_protocol.get("ruleset_version", "style-v1"))),
        test_evaluation_id=frozen_protocol.get("test_evaluation_id"),
    )


def claim_test_evaluation(freeze_id: str, split_manifest_sha256: str, ledger_path: Any) -> TestEvaluationClaim:
    """Atomically consume the one permitted test evaluation for a frozen candidate."""
    from pathlib import Path

    ledger_path = Path(ledger_path).resolve()
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.is_file() else {"claims": []}
    if any(claim.get("freeze_id") == freeze_id for claim in ledger.get("claims", [])):
        raise EvaluationProtocolError(f"test evaluation already exists for frozen candidate: {freeze_id}")
    evaluation_id = hashlib.sha256(f"{freeze_id}:{split_manifest_sha256}".encode()).hexdigest()
    claim = {"test_evaluation_id": evaluation_id, "freeze_id": freeze_id, "split_manifest_sha256": split_manifest_sha256}
    ledger.setdefault("claims", []).append(claim)
    fd, temp_name = tempfile.mkstemp(prefix=f".{ledger_path.name}.", dir=ledger_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(ledger, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, ledger_path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return TestEvaluationClaim(**claim)
