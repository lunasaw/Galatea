"""Durable redacted observation batches with the same replay contract as memory."""
import json
import os
import sqlite3

from .memory import MemoryConsoleObservationStore, encoded
from ..models import ObservationBatch
from ..ports import CommitConflict
from ..projection import Projector


class SqliteConsoleObservationStore(MemoryConsoleObservationStore):
    def __init__(self, path):
        super().__init__()
        self.path, self.db = path, None
        self._replaying = False

    async def open(self, epoch):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(self.path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS batches (position INTEGER PRIMARY KEY, id TEXT UNIQUE, payload BLOB NOT NULL)')
        self.db.execute('INSERT OR IGNORE INTO metadata VALUES (?, ?)', ('epoch', epoch))
        self.db.commit()
        epoch = self.db.execute("SELECT value FROM metadata WHERE key='epoch'").fetchone()[0]
        await super().open(epoch)
        self._replaying = True
        self.projector = Projector()
        projected = []
        try:
            for (raw,) in self.db.execute('SELECT payload FROM batches ORDER BY position'):
                data = json.loads(raw)
                await super().commit(ObservationBatch(data['batch_id'], epoch, tuple(data['events']), tuple(data['changes'])))
                # The audit batches remain immutable. Rebuild their derived
                # view with current projection rules, including old phase and
                # steered-message records; retain synthetic adapter fixtures.
                if all(row.get('schema_version') in {'galatea.console.event/v1', 'codex-agent.console.event/v1'} for row in data['events']):
                    for row in data['events']:
                        projected.extend(self.projector.changes(row))
                    projected.extend(c for c in data['changes'] if c.get('type') == 'binding')
                else:
                    projected.extend(data['changes'])
            self._changes = projected
        finally:
            self._replaying = False
        return await self.stats()

    async def _before_publish(self, batch):
        if self._replaying:
            return
        raw = encoded({'batch_id': batch.batch_id, 'events': batch.events, 'changes': batch.changes})
        previous = self.db.execute('SELECT payload FROM batches WHERE id=?', (batch.batch_id,)).fetchone()
        if previous and previous[0] != raw:
            raise CommitConflict('durable batch identity conflict')
        if not previous:
            with self.db:
                self.db.execute('INSERT INTO batches(id,payload) VALUES (?,?)', (batch.batch_id, raw))

    async def stats(self):
        return {**await super().stats(), 'backend': 'sqlite', 'retention': 'persistent'}

    async def close(self):
        await super().close()
        if self.db:
            self.db.close()
            self.db = None
