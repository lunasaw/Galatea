from __future__ import annotations

from contextlib import contextmanager
import fcntl
import re
import hashlib
import json
import os
from pathlib import Path
import threading
import uuid

from .catalog import canonical_bytes


def payload_digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", value):
        raise ValueError("invalid identifier")
    return value


def safe_path(root: Path, relative: str) -> Path:
    part = Path(relative)
    if part.is_absolute() or ".." in part.parts or not part.parts:
        raise ValueError("unsafe state path")
    path = root
    for component in part.parts:
        path = path / component
        if path.is_symlink():
            raise ValueError("symlink in state path")
    return path


def fsync_directory(path: Path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class AtomicJsonStore:
    def __init__(self, root: Path):
        self.root = Path(root).absolute()
        if any(path.is_symlink() for path in (self.root, *self.root.parents)):
            raise ValueError("unsafe state root")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o750)
        self._lock = threading.RLock()

    def read(self, relative: str, default=None):
        path = safe_path(self.root, relative)
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                raise RuntimeError("state record too large")
            with path.open(encoding="utf-8") as stream:
                return json.load(stream)
        except FileNotFoundError:
            return default
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"corrupt state: {path}") from exc

    def write(self, relative: str, value: object) -> None:
        path = safe_path(self.root, relative)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        with self._lock:
            try:
                with temporary.open("xb") as stream:
                    os.chmod(temporary, 0o640)
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                fsync_directory(path.parent)
            finally:
                temporary.unlink(missing_ok=True)


class OperationJournal:
    def __init__(self, store: AtomicJsonStore):
        self.store, self._lock = store, threading.RLock()

    def get(self, call_id: str):
        return self.store.read(f"tool-calls/{identifier(call_id)}.json")

    def records(self):
        return [self.get(path.stem) for path in sorted((self.store.root / 'tool-calls').glob('*.json'))]

    def begin(self, call_id: str, identity: dict) -> dict:
        with self._lock:
            current = self.get(call_id)
            digest = payload_digest(identity)
            if current:
                if current["arguments_digest"] != digest:
                    raise ValueError("call id conflict")
                return current
            current = {"call_id": call_id, "arguments_digest": digest, "state": "intent_recorded", "identity": identity}
            self.store.write(f"tool-calls/{call_id}.json", current)
            return current

    def transition(self, call_id: str, state: str, **fields):
        with self._lock:
            current = self.get(call_id)
            if not current:
                raise RuntimeError("missing intent")
            current = dict(current, state=state, **fields)
            self.store.write(f"tool-calls/{call_id}.json", current)
            return current


@contextmanager
def single_writer_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("state root already locked") from exc
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode())
        os.fsync(descriptor)
        yield
    finally:
        # Keep the inode: unlinking permits two independent locks after a race.
        os.close(descriptor)
