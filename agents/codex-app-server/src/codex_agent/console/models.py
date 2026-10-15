"""Public observation contracts; cursors use decimal strings on the wire."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ObservationBatch:
    batch_id: str
    epoch: str
    events: tuple[dict, ...]
    changes: tuple[dict, ...] = ()


@dataclass(frozen=True)
class EventQuery:
    session_id: str | None = None
    after: str | None = None
    until: str | None = None
    limit: int = 200
    filters: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SnapshotQuery:
    session_id: str
    turn_id: str | None = None
    view: str = 'all'
    until: str | None = None
    limit: int = 200
    page: str | None = None
