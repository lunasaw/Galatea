from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone


class TaskError(Exception):
    def __init__(self, code, message=None):
        self.code = code
        super().__init__(message or code)

    def envelope(self):
        return {
            "ok": False,
            "code": self.code,
            "message": str(self),
            "retryable": self.code in {"UNAVAILABLE", "RATE_LIMITED"},
        }


def require(condition, code, message=None):
    if not condition:
        raise TaskError(code, message)


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex


def utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
