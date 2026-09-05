"""Validation-only selection and deterministic evaluation protocol helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


class EvaluationProtocolError(ValueError):
    pass


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


def freeze_candidate(candidate: dict[str, Any], protocol: dict[str, Any]) -> dict[str, Any]:
    if any(key.startswith("test_") for key in candidate):
        raise EvaluationProtocolError("test metrics cannot be used to freeze a candidate")
    required = {"run_id", "validation_loss"}
    if not required.issubset(candidate):
        raise EvaluationProtocolError("candidate must contain validation evidence")
    payload = {"candidate": candidate, "protocol": protocol}
    return {"candidate": candidate, "protocol": protocol, "freeze_id": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()}
