"""Synchronous capture boundary. Never wait for storage or a browser here."""
import asyncio
import copy
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

native_context = ContextVar('galatea_observation_context', default=None)


@contextmanager
def observation_context(**values):
    token = native_context.set(values)
    try:
        yield
    finally:
        native_context.reset(token)


class ConsoleObserver:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.error = None
        self.captured = 0
        self.loop = None

    def __call__(self, direction, message, *, context=None, runtime_instance_id=None, source='codex', kind=None):
        try:
            entry = {'direction': direction, 'payload': copy.deepcopy(message),
                     'capture_id': uuid.uuid4().hex,
                     'context': copy.deepcopy(context or {}), 'runtime_instance_id': runtime_instance_id,
                     'source': source, 'kind': kind,
                     'observed_at': datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')}
            if self.loop is not None:
                try:
                    current = asyncio.get_running_loop()
                except RuntimeError:
                    current = None
                if current is not self.loop:
                    self.loop.call_soon_threadsafe(self._enqueue, entry)
                    return entry['capture_id']
            self._enqueue(entry)
            return entry['capture_id']
        except Exception as exc:
            self.error = type(exc).__name__

    def _enqueue(self, entry):
        self.captured += 1
        self.queue.put_nowait(entry)
