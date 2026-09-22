"""GPT-6-specific identity wrapper around the frozen repaired-candidate semantics."""
from __future__ import annotations

from . import topic_repair_protocol as legacy


METHOD = 'topic-repair-axes-v4-gpt6-v1'


def validate_candidate(candidate: dict) -> dict:
    return legacy.validate_candidate(candidate)


def validate_axes(candidate: dict, axes: dict) -> dict:
    return {**legacy.validate_axes(candidate, axes), 'method': METHOD}


def decode_response(response: dict, judge: dict, candidate: dict, exact_model: str) -> dict:
    return {**legacy.decode_response(response, judge, candidate, exact_model), 'method': METHOD}
