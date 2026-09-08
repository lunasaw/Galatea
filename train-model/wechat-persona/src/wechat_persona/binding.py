"""Verify MCP-signed execution bindings and the originating Ray Job."""
from __future__ import annotations

import base64
import json
import time
from typing import Any, Callable, Mapping


class BindingError(RuntimeError):
    pass


def _reject(message: str) -> None:
    raise BindingError(message)


def verify_execution_binding(
    raw: str | None,
    signature: str | None,
    public_key_pem: bytes,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Verify the exact canonical packet emitted by Galatea MCP."""

    if not raw or not signature:
        _reject("signed Galatea execution binding required")
    try:
        from cryptography.hazmat.primitives import serialization

        key = serialization.load_pem_public_key(public_key_pem)
        key.verify(base64.b64decode(signature, validate=True), raw.encode("utf-8"))
        value = json.loads(
            raw,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON value: {value}")
            ),
        )
    except Exception as exc:
        raise BindingError("invalid execution binding signature") from exc
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if canonical != raw or value.get("schema_version") != "galatea.execution/v1":
        _reject("non-canonical or unsupported execution binding")
    current = time.time() if now is None else now
    if current >= float(value.get("deadline_at", 0)):
        _reject("execution binding expired")
    if not isinstance(value.get("ray_address"), str) or not value["ray_address"]:
        _reject("signed Ray address required")
    role = value.get("role")
    expected_views = {"test"} if role == "evaluate" else {"train", "validation"}
    if role not in {"baseline", "trial", "champion", "evaluate"}:
        _reject("unsupported governed role")
    if set(value.get("views", {})) != expected_views:
        _reject("role data views violate isolation contract")
    if value.get("resources", {}).get("workers") != 1:
        _reject("wechat-persona supports exactly one authoritative worker")
    if role == "evaluate":
        for key in ("champion_run_id", "champion_model_sha256"):
            if not value.get(key):
                _reject(f"evaluate binding requires {key}")
    elif value.get("champion_model_sha256"):
        _reject("training role cannot receive final-test model binding")
    return dict(value)


def _job_id(value: Any) -> str | None:
    if value is None:
        return None
    text = value.hex() if hasattr(value, "hex") and not isinstance(value, str) else str(value)
    return text.casefold().removeprefix("0x")


def verify_runtime_origin(
    binding: Mapping[str, Any],
    ray_context: Any,
    client_factory: Callable[[str], Any],
    *,
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Bind admission to the authorized Ray submission and signed metadata."""

    if ray_context is None:
        _reject("driver must run inside initialized Ray")
    runtime_job_id = _job_id(ray_context.get_job_id())
    if not runtime_job_id:
        _reject("Ray runtime job ID unavailable")
    client = client_factory(str(binding["ray_address"]))
    wait_until = min(float(binding["deadline_at"]), now() + 10.0)
    while True:
        try:
            info = client.get_job_info(str(binding["submission_id"]))
        except Exception as exc:
            raise BindingError("authorized Ray job record unavailable") from exc
        submitted_job_id = _job_id(getattr(info, "job_id", None))
        if submitted_job_id:
            break
        if now() >= wait_until:
            _reject("authorized Ray job ID was not assigned before admission deadline")
        sleep(min(0.25, max(0.0, wait_until - now())))
    if submitted_job_id != runtime_job_id:
        _reject("Ray runtime job ID does not match authorized submission")
    if dict(getattr(info, "metadata", {}) or {}) != dict(binding["metadata"]):
        _reject("Ray job metadata does not match signed binding")
    return dict(binding)
