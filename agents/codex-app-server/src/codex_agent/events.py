from __future__ import annotations

import json
import os
from .state import safe_path, identifier, fsync_directory
from pathlib import Path
import threading
import time

SENSITIVE_KEYS = {"token", "secret", "password", "api_key", "authorization", "prompt", "reasoning", "test_sample", "test_label", "raw_log", "signed_url"}


def _sensitive(key: str) -> bool:
    normalized = key.lower()
    return normalized in SENSITIVE_KEYS or any(fragment in normalized for fragment in ("_token", "_secret", "credential", "private_key"))


def public_detail(value, *, max_string=2048):
    if isinstance(value, dict):
        return {key: public_detail(item, max_string=max_string) for key, item in value.items() if not _sensitive(key)}
    if isinstance(value, list):
        return [public_detail(item, max_string=max_string) for item in value[:100]]
    if isinstance(value, str) and len(value) > max_string:
        return {"value": value[:max_string], "truncated": True}
    return value


PUBLIC_FIELDS = {"status", "action", "tool", "call_id", "ok", "category", "operation_id",
                 "method", "item_type", "from_seq", "to_seq", "reason", "arguments", "read_only", "elapsed_ms"}


class EventLog:
    """Durable append-only public projection with indexed, bounded replay."""

    def __init__(self, root: Path):
        self.root, self._lock = Path(root).absolute(), threading.RLock()
        if any(p.is_symlink() for p in (self.root, *self.root.parents)):
            raise ValueError("unsafe event root")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._indexes = {}

    def _path(self, session_id):
        return safe_path(self.root, f"{identifier(session_id)}.jsonl")

    def _index(self, session_id):
        if session_id in self._indexes:
            return self._indexes[session_id]
        path = self._path(session_id)
        offsets, end, torn = [], 0, False
        if path.exists():
            with path.open("rb") as stream:
                while line := stream.readline():
                    if not line.endswith(b"\n"):
                        torn = True
                        break
                    try:
                        record = json.loads(line)
                        if record["seq"] != len(offsets) + 1 or record["session_id"] != session_id:
                            raise ValueError("event sequence gap")
                    except (KeyError, ValueError) as exc:
                        raise RuntimeError("corrupt event history; observation unavailable") from exc
                    offsets.append(end)
                    end = stream.tell()
        self._indexes[session_id] = offsets
        if torn:
            with path.open("r+b") as stream:
                stream.truncate(end)
                stream.flush()
                os.fsync(stream.fileno())
            self.append(session_id, {"kind": "observation_gap", "public": {
                "from_seq": len(offsets) + 1, "to_seq": len(offsets) + 1, "reason": "incomplete durable record"}})
        return offsets

    def append(self, session_id: str, event: dict) -> dict:
        with self._lock:
            offsets = self._index(session_id)
            path = self._path(session_id)
            public = {k: v for k, v in event.get("public", {}).items() if k in PUBLIC_FIELDS}
            record = {"seq": len(offsets) + 1, "session_id": session_id, "timestamp": time.time(),
                      "kind": event.get("kind", "observation"), "public": public_detail(public)}
            for key in ("turn_id", "item_id"):
                if isinstance(event.get(key), str):
                    record[key] = event[key][:160]
            encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode() + b"\n"
            with path.open("ab") as stream:
                os.chmod(path, 0o600)
                offset = stream.tell()
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            fsync_directory(path.parent)
            offsets.append(offset)
            return record

    def replay(self, session_id: str, after=0, limit=256):
        if type(after) is not int or after < 0:
            raise ValueError("invalid event cursor")
        with self._lock:
            offsets = self._index(session_id)
            if after > len(offsets):
                raise ValueError("event cursor ahead of durable history")
            if after == len(offsets):
                return []
            with self._path(session_id).open("rb") as stream:
                stream.seek(offsets[after])
                return [json.loads(stream.readline()) for _ in range(min(limit, len(offsets) - after))]

    def loop(self, session_id):
        steps, cursor = {}, 0
        while batch := self.replay(session_id, cursor):
            for event in batch:
                cursor = event["seq"]
                public = event["public"]
                key = public.get("call_id") or event.get("item_id") or f"event-{cursor}"
                steps[key] = {**steps.get(key, {}), **event, "step_id": key}
                if len(steps) > 1000:
                    del steps[next(iter(steps))]
        return {"after": cursor, "steps": list(steps.values())}
