"""Patrol session persistence separate from Claude conversation sessions."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from agent.schemas.patrol import (
    EvidenceRecord,
    Finding,
    FindingStatus,
    PatrolMemory,
    PatrolRunResult,
    Recommendation,
    current_timestamp,
)

_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")


class PatrolSession(BaseModel):
    """Durable train-inference patrol session state."""

    session_id: str
    agent_type: str = "train-inference-integrated"
    project_scope: List[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=current_timestamp)
    updated_at: str = Field(default_factory=current_timestamp)
    patrol_runs: List[PatrolRunResult] = Field(default_factory=list)
    open_findings: List[Finding] = Field(default_factory=list)
    closed_findings: List[Finding] = Field(default_factory=list)
    recommendations: List[Recommendation] = Field(default_factory=list)
    approval_requests: List[Dict[str, Any]] = Field(default_factory=list)
    evidence_index: List[EvidenceRecord] = Field(default_factory=list)
    memory: PatrolMemory
    budget: Dict[str, Any] = Field(default_factory=dict)
    last_context_summary: str = ""
    forked_from: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def sync_from_memory_on_create(self) -> "PatrolSession":
        if not self.open_findings and self.memory.open_findings:
            self.open_findings = list(self.memory.open_findings)
        if not self.closed_findings and self.memory.closed_findings:
            self.closed_findings = list(self.memory.closed_findings)
        if not self.recommendations and self.memory.recommendations:
            self.recommendations = list(self.memory.recommendations)
        if not self.approval_requests and self.memory.approval_requests:
            self.approval_requests = list(self.memory.approval_requests)
        if not self.evidence_index and self.memory.evidence_index:
            self.evidence_index = list(self.memory.evidence_index)
        if not self.project_scope and self.memory.project_name:
            self.project_scope = [self.memory.project_name]
        return self

    def refresh_memory(self) -> None:
        """Mirror top-level durable state into the compact memory object."""
        self.memory.open_findings = list(self.open_findings)
        self.memory.closed_findings = list(self.closed_findings)
        self.memory.recommendations = list(self.recommendations)
        self.memory.approval_requests = list(self.approval_requests)
        self.memory.evidence_index = list(self.evidence_index)
        self.memory.summary = self.last_context_summary or self.memory.summary
        self.updated_at = current_timestamp()

    def apply_run_result(self, result: PatrolRunResult, closed_findings: Optional[List[Finding]] = None) -> None:
        """Update session state from one patrol result."""
        self.patrol_runs.append(result)
        self._merge_evidence(result.evidence)
        self.open_findings = [finding for finding in result.findings if finding.status == FindingStatus.OPEN]
        if closed_findings:
            self._merge_closed_findings(closed_findings)
        self._merge_recommendations(result.recommendations)
        self.approval_requests = _merge_dicts_by_id(
            self.approval_requests,
            result.approval_requests,
            ["approval_request_id", "approval_id"],
        )
        self.memory.patrol_run_id = result.patrol_run_id
        if result.project_scope:
            self.project_scope = list(result.project_scope)
            self.memory.project_name = result.project_scope[0]
        self.memory.next_check_at = result.next_check_at
        self.last_context_summary = result.summary
        self.refresh_memory()

    def _merge_evidence(self, records: List[EvidenceRecord]) -> None:
        by_id = {record.evidence_id: record for record in self.evidence_index}
        for record in records:
            by_id[record.evidence_id] = record
        self.evidence_index = list(by_id.values())

    def _merge_recommendations(self, records: List[Recommendation]) -> None:
        by_fingerprint = {record.fingerprint: record for record in self.recommendations}
        for record in records:
            by_fingerprint[record.fingerprint] = record
        self.recommendations = list(by_fingerprint.values())

    def _merge_closed_findings(self, findings: List[Finding]) -> None:
        by_fingerprint = {finding.fingerprint: finding for finding in self.closed_findings}
        for finding in findings:
            by_fingerprint[finding.fingerprint] = finding
        self.closed_findings = list(by_fingerprint.values())


class FilePatrolSessionStore:
    """File-backed patrol session store for local/offline use."""

    def __init__(self, state_dir: Path | str) -> None:
        self.state_dir = Path(state_dir)
        self.sessions_dir = self.state_dir / "patrol-sessions"

    async def save_session(self, session: PatrolSession) -> None:
        """Atomically save a patrol session as JSON."""
        session.refresh_memory()
        path = self._session_path(session.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(session.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(path)

    async def load_session(self, session_id: str) -> Optional[PatrolSession]:
        """Load a patrol session by ID, returning None if missing."""
        path = self._session_path(session_id)
        if not path.exists():
            return None
        return PatrolSession.model_validate_json(path.read_text(encoding="utf-8"))

    async def delete_session(self, session_id: str) -> bool:
        """Delete a patrol session file if present."""
        path = self._session_path(session_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    async def list_sessions(self) -> List[str]:
        """List known patrol session IDs."""
        if not self.sessions_dir.exists():
            return []
        return sorted(path.stem for path in self.sessions_dir.glob("*.json"))

    async def fork_session(self, source_session_id: str, new_session_id: str) -> PatrolSession:
        """Copy one session into an isolated fork and persist it."""
        source = await self.load_session(source_session_id)
        if source is None:
            raise KeyError(f"Patrol session not found: {source_session_id}")
        if await self.load_session(new_session_id) is not None:
            raise ValueError(f"Patrol session already exists: {new_session_id}")
        forked = source.model_copy(deep=True)
        forked.session_id = new_session_id
        forked.forked_from = source_session_id
        forked.created_at = current_timestamp()
        forked.updated_at = forked.created_at
        forked.metadata = {**forked.metadata, "forked_from": source_session_id}
        await self.save_session(forked)
        return forked

    def _session_path(self, session_id: str) -> Path:
        if not _SAFE_SESSION_ID.match(session_id) or ".." in session_id:
            raise ValueError(f"Unsafe patrol session_id: {session_id}")
        return self.sessions_dir / f"{session_id}.json"


def new_patrol_session(session_id: str, project_scope: List[str]) -> PatrolSession:
    """Create an empty patrol session with valid memory."""
    project_name = project_scope[0] if project_scope else "platform"
    memory = PatrolMemory(
        patrol_run_id="not-started",
        project_name=project_name,
        summary="No patrol rounds have completed yet.",
    )
    return PatrolSession(session_id=session_id, project_scope=project_scope, memory=memory)


def _merge_dicts_by_id(
    current: List[Dict[str, Any]],
    incoming: List[Dict[str, Any]],
    id_keys: List[str],
) -> List[Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    for item in [*current, *incoming]:
        item_id = next((str(item[key]) for key in id_keys if item.get(key)), None)
        if item_id is None:
            item_id = str(len(by_id))
        by_id[item_id] = item
    return list(by_id.values())
