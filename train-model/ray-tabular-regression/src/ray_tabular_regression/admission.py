import hashlib
import json


class _Admission:
    __slots__ = ("binding_digest",)

    def __init__(self, binding_digest):
        self.binding_digest = binding_digest


def _digest(binding):
    safe = {key: value for key, value in binding.items() if not key.startswith("runtime_")}
    return hashlib.sha256(json.dumps(safe, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def issue(binding, *, public_key_pem, ray_context, client_factory, environ=None):
    import os
    from .binding import verify_execution_binding, verify_runtime_origin
    environment = os.environ if environ is None else environ
    verified = verify_execution_binding(environment.get('GALATEA_EXECUTION_BINDING'),
                                        environment.get('GALATEA_EXECUTION_SIGNATURE'), public_key_pem)
    if _digest(verified) != _digest(binding):
        raise PermissionError('binding differs from signed admission')
    verify_runtime_origin(verified, ray_context, client_factory)
    return _Admission(_digest(verified))


def require(value, binding):
    if not isinstance(value, _Admission) or value.binding_digest != _digest(binding):
        raise PermissionError("verified Driver admission capability required")
