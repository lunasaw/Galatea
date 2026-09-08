"""Deadline-enforcing wrapper for the sole governed Ray execution path."""
from __future__ import annotations

import os
import signal
import threading
import time
from typing import Any, Callable, Mapping

from .admission import issue
from .binding import verify_execution_binding


def _watch_deadline(deadline: float, cleanup_seconds: int) -> threading.Timer:
    delay = max(0.0, deadline - time.time() - cleanup_seconds)
    timer = threading.Timer(delay, lambda: os.kill(os.getpid(), signal.SIGTERM))
    timer.daemon = True
    timer.start()
    return timer


def execute(
    public_key_pem: bytes,
    ray_context: Any,
    *,
    fit: Callable[[Mapping[str, Any], Any], dict[str, Any]],
    raw_binding: str | None = None,
    signature: str | None = None,
    client_factory: Any = None,
) -> dict[str, Any]:
    raw = raw_binding if raw_binding is not None else os.environ.get("GALATEA_EXECUTION_BINDING")
    signed = signature if signature is not None else os.environ.get("GALATEA_EXECUTION_SIGNATURE")
    binding = verify_execution_binding(raw, signed, public_key_pem)
    if client_factory is None:
        from ray.job_submission import JobSubmissionClient

        client_factory = JobSubmissionClient
    admission = issue(
        binding,
        raw_binding=raw,
        signature=signed,
        public_key_pem=public_key_pem,
        ray_context=ray_context,
        client_factory=client_factory,
    )
    now = time.time()
    maximum = min(
        float(binding["deadline_at"]) - now,
        float(binding["resources"]["seconds"]),
    )
    if maximum <= 0:
        raise RuntimeError("authorized execution budget exhausted")
    binding["runtime_max_total_seconds"] = maximum
    timer = _watch_deadline(now + maximum, int(binding["resources"]["cleanup_seconds"]))
    try:
        return fit(binding, admission)
    finally:
        timer.cancel()
