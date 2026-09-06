"""Fail-closed project 5-9 governance state machine."""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any, Mapping


class GovernanceError(ValueError):
    pass


STATE_SEQUENCE = (
    "DISCOVERED",
    "CONSENT_VERIFIED",
    "IMPORTED",
    "NORMALIZED_REDACTED",
    "SESSIONIZED_SPLIT_FROZEN",
    "REVIEWED",
    "FORMAL_DATASET_READY",
    "RAG_INDEX_VALIDATED",
    "LORA_TRIAL_VALIDATED",
    "CANDIDATE_FROZEN",
    "TEST_ONCE_COMPLETED",
    "HUMAN_SAFETY_APPROVED",
    "LOCAL_PROTOTYPE_ENABLED",
)
TERMINAL_STATES = {"BLOCKED", "WITHDRAWN"}
BLOCK_CODE = re.compile(r"^[a-z][a-z0-9_]*$")


def _event(from_status: str, to_status: str, **details: Any) -> dict[str, Any]:
    return {
        "from": from_status,
        "to": to_status,
        "at": datetime.now(timezone.utc).isoformat(),
        **details,
    }


def advance_state(state: Mapping[str, Any], target: str) -> dict[str, Any]:
    current = str(state.get("status", ""))
    if current in TERMINAL_STATES:
        raise GovernanceError(f"cannot advance terminal state {current}")
    try:
        current_index = STATE_SEQUENCE.index(current)
        target_index = STATE_SEQUENCE.index(target)
    except ValueError as exc:
        raise GovernanceError("unknown governance state") from exc
    if target_index != current_index + 1:
        raise GovernanceError(f"invalid state transition: {current} -> {target}")
    updated = copy.deepcopy(dict(state))
    updated["status"] = target
    updated.setdefault("history", []).append(_event(current, target))
    return updated


def block_state(state: Mapping[str, Any], reason_code: str) -> dict[str, Any]:
    if not BLOCK_CODE.fullmatch(reason_code) or "_" not in reason_code:
        raise GovernanceError("block reason must be a structured snake_case code")
    current = str(state.get("status", ""))
    updated = copy.deepcopy(dict(state))
    updated["status"] = "BLOCKED"
    updated["block_reason_code"] = reason_code
    updated.setdefault("history", []).append(_event(current, "BLOCKED", reason_code=reason_code))
    return updated


def withdraw_state(state: Mapping[str, Any], withdrawal_scope: str) -> dict[str, Any]:
    if not withdrawal_scope:
        raise GovernanceError("withdrawal scope is required")
    current = str(state.get("status", ""))
    updated = copy.deepcopy(dict(state))
    updated["status"] = "WITHDRAWN"
    updated["withdrawal_scope"] = withdrawal_scope
    updated["invalidated_object_ids"] = list(state.get("downstream_object_ids", ()))
    updated.setdefault("history", []).append(
        _event(current, "WITHDRAWN", withdrawal_scope=withdrawal_scope)
    )
    return updated
