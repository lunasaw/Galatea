"""Audit event persistence for patrol-push runs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, List

from agent.schemas.patrol import AuditEvent

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.:-]+$")


class FileAuditEventWriter:
    """Append-only JSONL audit writer scoped by patrol session."""

    def __init__(self, state_dir: Path | str) -> None:
        self.state_dir = Path(state_dir)
        self.audit_dir = self.state_dir / "patrol-audit"

    def write_event(self, session_id: str, event: AuditEvent) -> Path:
        """Append one audit event and return the JSONL path."""
        path = self._path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        return path

    def write_events(self, session_id: str, events: Iterable[AuditEvent]) -> Path:
        """Append a batch of audit events and return the JSONL path."""
        path = self._path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
                handle.write("\n")
        return path

    def read_events(self, session_id: str) -> List[AuditEvent]:
        """Read audit events for a session."""
        path = self._path(session_id)
        if not path.exists():
            return []
        return [
            AuditEvent.model_validate(json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _path(self, session_id: str) -> Path:
        if not _SAFE_NAME.match(session_id) or ".." in session_id:
            raise ValueError(f"Unsafe audit session_id: {session_id}")
        return self.audit_dir / f"{session_id}.jsonl"
