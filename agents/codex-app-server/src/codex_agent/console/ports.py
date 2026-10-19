"""Storage semantics shared by the memory adapter and future implementations."""
from typing import Protocol
from .models import EventQuery, ObservationBatch, SnapshotQuery


class ObservationError(ValueError):
    code = 'OBSERVATION_UNAVAILABLE'
    status = 503


class InvalidCursor(ObservationError):
    code, status = 'INVALID_CURSOR', 400


class EpochMismatch(ObservationError):
    code, status = 'EPOCH_MISMATCH', 409


class CursorAhead(ObservationError):
    code, status = 'CURSOR_AHEAD', 409


class PageContextMismatch(ObservationError):
    code, status = 'PAGE_CONTEXT_MISMATCH', 409


class UnknownSession(ObservationError):
    code, status = 'UNKNOWN_SESSION', 404


class UnknownEvent(ObservationError):
    code, status = 'UNKNOWN_EVENT', 404


class StoreUnavailable(ObservationError):
    pass


class CommitConflict(ObservationError):
    pass


class SequenceGap(ObservationError):
    pass


class ConsoleObservationStore(Protocol):
    async def open(self, epoch: str) -> dict: ...
    async def commit(self, batch: ObservationBatch) -> dict: ...
    async def read_events(self, query: EventQuery) -> dict: ...
    async def snapshot(self, query: SnapshotQuery) -> dict: ...
    async def wait_for_change(self, after: str, timeout: float) -> str: ...
    async def stats(self) -> dict: ...
    async def close(self) -> None: ...
