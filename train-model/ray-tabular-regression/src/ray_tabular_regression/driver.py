import os
import signal
import threading
import time

from .binding import verify_execution_binding, verify_runtime_origin
from .admission import issue


def _watch_deadline(deadline, cleanup_seconds):
    delay = max(0, deadline - time.time() - cleanup_seconds)
    timer = threading.Timer(delay, lambda: os.kill(os.getpid(), signal.SIGTERM))
    timer.daemon = True; timer.start()
    return timer


def execute(public_key_pem, ray_context, *, fit, client_factory=None):
    binding = verify_execution_binding(os.environ.get("GALATEA_EXECUTION_BINDING"),
        os.environ.get("GALATEA_EXECUTION_SIGNATURE"), public_key_pem)
    if client_factory is None:
        from ray.job_submission import JobSubmissionClient
        client_factory = JobSubmissionClient
    admission = issue(binding, public_key_pem=public_key_pem, ray_context=ray_context, client_factory=client_factory)
    now = time.time()
    max_total_seconds = min(binding["deadline_at"] - now, binding["resources"]["seconds"])
    if max_total_seconds <= 0:
        raise RuntimeError("authorized execution budget exhausted")
    binding["runtime_max_total_seconds"] = max_total_seconds
    timer = _watch_deadline(now + max_total_seconds, binding["resources"]["cleanup_seconds"])
    try:
        return fit(binding, admission)
    finally:
        timer.cancel()
