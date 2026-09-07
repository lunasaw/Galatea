"""Single-host durable state; never interpret corruption as a new campaign."""
import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path

class Store:
    def __init__(self, root: Path):
        if Path(root).is_symlink():
            raise ValueError('State root cannot be a symlink')
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, campaign_id):
        if not isinstance(campaign_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', campaign_id):
            raise ValueError('Invalid campaign identity')
        path = self.root / 'campaigns' / campaign_id / 'state.json'
        if any(p.is_symlink() for p in [path, path.parent, path.parent.parent]):
            raise ValueError('State path cannot contain symlinks')
        return path

    @contextmanager
    def lock(self):
        with os.fdopen(os.open(self.root / 'runner.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'a') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError('Runner already owns this volume') from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def load(self, campaign_id):
        path = self.path(campaign_id)
        if path.stat().st_size > 1024 * 1024:
            raise ValueError('State exceeds size limit')
        value = json.loads(path.read_text(), parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid numeric state')))
        if not isinstance(value, dict) or value.get('schema_version') != 1 or value.get('campaign_id') != campaign_id:
            raise ValueError('Invalid durable state; manual reconciliation required')
        return value

    def save(self, campaign_id, snapshot):
        if snapshot.get('schema_version') != 1 or snapshot.get('campaign_id') != campaign_id:
            raise ValueError('State identity mismatch')
        path = self.path(campaign_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.state-')
        try:
            with os.fdopen(fd, 'w') as handle:
                json.dump(snapshot, handle, allow_nan=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
