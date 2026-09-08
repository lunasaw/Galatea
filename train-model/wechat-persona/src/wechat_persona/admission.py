"""Unforgeable in-process capability issued only by the governed Driver."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .binding import verify_execution_binding, verify_runtime_origin


class _Admission:
    __slots__ = ("binding_digest",)

    def __init__(self, binding_digest: str):
        self.binding_digest = binding_digest


def _digest(binding: Mapping[str, Any]) -> str:
    safe = {key: value for key, value in binding.items() if not key.startswith("runtime_")}
    raw = json.dumps(
        safe,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def issue(
    binding: Mapping[str, Any],
    *,
    raw_binding: str | None,
    signature: str | None,
    public_key_pem: bytes,
    ray_context: Any,
    client_factory: Any,
) -> _Admission:
    verified = verify_execution_binding(raw_binding, signature, public_key_pem)
    if _digest(verified) != _digest(binding):
        raise PermissionError("binding differs from signed admission")
    verify_runtime_origin(verified, ray_context, client_factory)
    return _Admission(_digest(verified))


def require(value: Any, binding: Mapping[str, Any]) -> None:
    if not isinstance(value, _Admission) or value.binding_digest != _digest(binding):
        raise PermissionError("verified Driver admission capability required")
