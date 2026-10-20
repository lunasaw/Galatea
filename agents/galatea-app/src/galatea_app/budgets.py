"""Distinguish enforceable memory ceilings from explicit scheduling-only budgets."""

import math


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def valid_limits(limits):
    if not isinstance(limits, dict) or not {
        "attempts", "retries", "wall_seconds", "memory_bytes"
    } <= limits.keys():
        return False
    if not (
        type(limits["attempts"]) is int and limits["attempts"] >= 1
        and type(limits["retries"]) is int and limits["retries"] >= 0
        and _number(limits["wall_seconds"]) and limits["wall_seconds"] > 0
    ):
        return False
    return all(
        (key == "memory_bytes" and value is None)
        or (_number(value) and value >= (1 if key == "memory_bytes" else 0))
        for key, value in limits.items()
    )


def within_limits(requested, allowed, *, allow_unbounded_memory=False):
    """A missing/finite policy never authorizes a request with no memory ceiling."""
    if not valid_limits(requested) or not valid_limits(allowed):
        return False
    if not all(
        requested[key] <= allowed[key]
        for key in ("attempts", "retries", "wall_seconds")
    ):
        return False
    ceiling = allowed["memory_bytes"]
    memory = requested["memory_bytes"]
    if ceiling is None:
        return allow_unbounded_memory is True
    return memory is not None and memory <= ceiling
