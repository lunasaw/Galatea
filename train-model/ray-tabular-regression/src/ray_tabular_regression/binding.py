import base64
import json
import time


class BindingError(RuntimeError):
    pass


def _reject(message):
    raise BindingError(message)


def verify_execution_binding(raw, signature, public_key_pem, ray_context=None, *, now=None):
    if not raw or not signature:
        _reject("signed Galatea execution binding required")
    try:
        from cryptography.hazmat.primitives import serialization
        key = serialization.load_pem_public_key(public_key_pem)
        key.verify(base64.b64decode(signature, validate=True), raw.encode())
        value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite")))
    except Exception as exc:
        raise BindingError("invalid execution binding signature") from exc
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, allow_nan=False)
    if canonical != raw or value.get("schema_version") != "galatea.execution/v1":
        _reject("non-canonical or unsupported execution binding")
    if (now if now is not None else time.time()) >= value.get("deadline_at", 0):
        _reject("execution binding expired")
    if not isinstance(value.get("ray_address"), str) or not value["ray_address"]:
        _reject("signed Ray address required")
    role, views = value.get("role"), set(value.get("views", {}))
    expected = {"test"} if role == "evaluate" else {"train", "validation"}
    if views != expected or role not in {"baseline", "trial", "champion", "evaluate"}:
        _reject("role data views violate isolation contract")
    resources = value.get("resources", {})
    if resources.get("workers") != 1:
        _reject("reference workload supports exactly one worker")
    return value


def _job_id(value):
    if value is None:
        return None
    text = value.hex() if hasattr(value, "hex") and not isinstance(value, str) else str(value)
    return text.lower().removeprefix("0x")


def verify_runtime_origin(binding, ray_context, client_factory, *, now=time.time, sleep=time.sleep):
    if ray_context is None:
        _reject("driver must run inside initialized Ray")
    runtime_job_id = _job_id(ray_context.get_job_id())
    if not runtime_job_id:
        _reject("Ray runtime job ID unavailable")
    client = client_factory(binding["ray_address"])
    wait_until = min(float(binding["deadline_at"]), now() + 10.0)
    while True:
        try:
            info = client.get_job_info(binding["submission_id"])
        except Exception as exc:
            raise BindingError("authorized Ray job record unavailable") from exc
        submitted_job_id = _job_id(getattr(info, "job_id", None))
        if submitted_job_id:
            break
        if now() >= wait_until:
            _reject("authorized Ray job ID was not assigned before admission deadline")
        sleep(min(0.25, max(0, wait_until - now())))
    if submitted_job_id != runtime_job_id:
        _reject("Ray runtime job ID does not match authorized submission")
    if dict(getattr(info, "metadata", {}) or {}) != binding["metadata"]:
        _reject("Ray job metadata does not match signed binding")
    return binding
