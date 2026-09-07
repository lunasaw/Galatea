"""Single-writer local persistence; deliberately unsuitable for shared NFS/HA."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path

from .errors import DomainError

SCHEMA = "galatea.state/v1"
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
MAX_STATE = 8 * 1024 * 1024


def identifier(value: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise DomainError("unsafe-path")
    return value


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class StateStore:
    def __init__(self, root: Path):
        root = root.absolute()
        if any(p.is_symlink() for p in [root, *root.parents]):
            # macOS /tmp resolves to /private/tmp; callers should resolve the trusted root.
            raise DomainError("unsafe-path")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = root
        self.mutex = threading.RLock()
        self._fd = os.open(root / "writer.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._fd)
            self._fd = None
            raise DomainError("writer-locked") from exc
        for name in ("campaigns", "evaluation-uses"):
            target = root / name
            if target.is_symlink():
                self.close()
                raise DomainError("unsafe-path")
            target.mkdir(exist_ok=True, mode=0o700)

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def path(self, kind: str, key: str) -> Path:
        if self._fd is None:
            raise DomainError("writer-closed")
        if kind not in {"campaigns", "evaluation-uses"}:
            raise DomainError("unsafe-path")
        path = self.root / kind / (identifier(key) + ".json")
        if path.is_symlink() or path.parent.is_symlink():
            raise DomainError("unsafe-path")
        return path

    def read(self, kind: str, key: str) -> dict:
        path = self.path(kind, key)
        try:
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
                raw = stream.read(MAX_STATE + 1)
            if len(raw) > MAX_STATE:
                raise ValueError("oversize")
            value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(value, dict) or value.get("schema_version") != SCHEMA:
                raise ValueError("schema")
            return value
        except FileNotFoundError as exc:
            raise DomainError("not-found") from exc
        except (ValueError, OSError) as exc:
            raise DomainError("state-corrupt", next_action="operator-recovery") from exc

    def save(self, kind: str, key: str, value: dict) -> None:
        with self.mutex:
            path = self.path(kind, key)
            raw = canonical(value)
            if value.get("schema_version") != SCHEMA or len(raw) > MAX_STATE:
                raise DomainError("state-invalid")
            fd, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                sync_directory(path.parent)
            finally:
                Path(temporary).unlink(missing_ok=True)

    def ids(self) -> list[str]:
        return sorted(p.stem for p in (self.root / "campaigns").glob("*.json"))

    def claim(self, holdout: str, value: dict) -> None:
        with self.mutex:
            path = self.path("evaluation-uses", holdout)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                if self.read("evaluation-uses", holdout) != value:
                    raise DomainError("holdout-used", next_action="read-original-evaluation")
                return
            # A crash during this write leaves a corrupt marker, intentionally fail-closed.
            with os.fdopen(fd, "wb") as stream:
                stream.write(canonical(value))
                stream.flush()
                os.fsync(stream.fileno())
            sync_directory(path.parent)
