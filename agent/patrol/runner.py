"""Deterministic, read-only patrol runner for offline and scheduled use."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from agent.policies.patrol import PatrolLifecyclePolicy
from agent.schemas.patrol import (
    AuditEvent,
    EvidenceRecord,
    FailureType,
    Finding,
    PatrolFailure,
    PatrolRunResult,
    PatrolRunStatus,
    RawRef,
    Recommendation,
    RecommendationType,
    Recoverability,
    Severity,
    current_timestamp,
    new_id,
)
from agent.state.patrol import FilePatrolSessionStore, PatrolSession, new_patrol_session
from agent.tools import inspection
from agent.tools.patrol_output import build_tool_envelope, summarize_payload
from agent.workflows.patrol import PatrolRunState, PatrolRunStateMachine

ToolOverride = Callable[..., Dict[str, Any]]


class PatrolRunner:
    """Run one deterministic patrol round without requiring an LLM."""

    def __init__(
        self,
        *,
        project_root: Path | str,
        state_dir: Path | str,
        session_id: str,
        project_scope: Optional[List[str]] = None,
        service_checks: Optional[List[Tuple[str, int]]] = None,
        mlflow_experiments: Optional[List[Tuple[str, str]]] = None,
        tool_overrides: Optional[Dict[str, ToolOverride]] = None,
        lifecycle_policy: Optional[PatrolLifecyclePolicy] = None,
        next_check_delay_seconds: int = 3600,
    ) -> None:
        self.project_root = Path(project_root)
        self.session_id = session_id
        self.project_scope = project_scope or []
        self.service_checks = service_checks or [("mlflow", 5000), ("minio", 9000)]
        self.mlflow_experiments = mlflow_experiments or []
        self.tool_overrides = tool_overrides or {}
        self.lifecycle_policy = lifecycle_policy or PatrolLifecyclePolicy()
        self.session_store = FilePatrolSessionStore(Path(state_dir))
        self.next_check_delay_seconds = next_check_delay_seconds

    async def run_once(self, *, resume: bool = False) -> PatrolRunResult:
        """Execute one read-only patrol round and persist the patrol session."""
        patrol_run_id = new_id("pr")
        machine = PatrolRunStateMachine(patrol_run_id)
        audit_events: List[AuditEvent] = []
        evidence: List[EvidenceRecord] = []
        failures: List[PatrolFailure] = []
        machine.start()
        self._audit(audit_events, "patrol_run_started", patrol_run_id)

        session = await self._load_or_create_session(resume=resume)
        existing_open = [finding.model_copy(deep=True) for finding in session.open_findings]
        active_fingerprints: set[str] = set()
        pushed_recommendations: List[Recommendation] = []
        discovered_projects = session.project_scope or self.project_scope
        if not discovered_projects:
            discovered_projects = self._discover_project_scope()
        if not self.project_scope:
            self.project_scope = list(discovered_projects)
        project_scope = list(discovered_projects)

        try:
            self._transition(machine, PatrolRunState.INSPECT)
            envelopes = await self._inspect(patrol_run_id, audit_events)
            for envelope in envelopes:
                evidence.extend(EvidenceRecord.model_validate(item) for item in envelope.get("evidence", []))

            self._transition(machine, PatrolRunState.CLASSIFY_FINDINGS)
            observed_findings = self._classify_findings(envelopes)
            for finding in observed_findings:
                finding_evidence = [record for record in evidence if record.evidence_id in finding.evidence_ids]
                stored = self.lifecycle_policy.upsert_finding(existing_open, finding, finding_evidence)
                active_fingerprints.add(stored.fingerprint or "")
                self._audit(audit_events, "finding_updated", patrol_run_id, finding_id=stored.finding_id)

            resolved = self.lifecycle_policy.mark_resolved_if_missing(
                existing_open,
                active_fingerprints=active_fingerprints,
            )
            open_findings = [finding for finding in existing_open if finding.status.value == "open"]
            open_findings.extend(
                finding
                for finding in observed_findings
                if finding.status.value == "open" and finding.fingerprint not in active_fingerprints
            )

            self._transition(machine, PatrolRunState.RECOMMEND)
            candidate_recommendations = self._recommend(open_findings)
            session_recommendations = [recommendation.model_copy(deep=True) for recommendation in session.recommendations]
            for recommendation in candidate_recommendations:
                pushed, should_push = self.lifecycle_policy.upsert_recommendation(
                    session_recommendations,
                    recommendation,
                )
                if should_push:
                    pushed_recommendations.append(pushed)
                    self._audit(
                        audit_events,
                        "recommendation_created",
                        patrol_run_id,
                        recommendation_id=pushed.recommendation_id,
                    )

            self._transition(machine, PatrolRunState.PERSIST_STATE)
            next_check_at = (datetime.now(timezone.utc) + timedelta(seconds=self.next_check_delay_seconds)).isoformat()
            status = _result_status(open_findings, failures, pushed_recommendations)
            result = PatrolRunResult(
                patrol_run_id=patrol_run_id,
                session_id=self.session_id,
                status=status,
                project_scope=project_scope,
                summary=self._summarize_result(open_findings, pushed_recommendations, failures),
                findings=open_findings,
                recommendations=pushed_recommendations,
                evidence=evidence,
                failures=failures,
                next_check_at=next_check_at,
                state_update={
                    "state_machine": machine.to_dict(),
                    "resolved_finding_ids": [finding.finding_id for finding in resolved],
                    "suppressed_recommendations": self.lifecycle_policy.suppressed_recommendation_count,
                },
                audit_events=audit_events,
            )
            result.validate_traceability()
            session.recommendations = session_recommendations
            session.apply_run_result(result, closed_findings=resolved)
            await self.session_store.save_session(session)
            self._audit(audit_events, "patrol_run_completed", patrol_run_id, status=status.value)
            self._transition(machine, PatrolRunState.SCHEDULE_NEXT)
            self._transition(machine, PatrolRunState.IDLE)
            return result
        except Exception as exc:  # noqa: BLE001 - patrol must persist classified failures
            self._transition_if_allowed(machine, PatrolRunState.INSPECT_FAILED)
            self._transition_if_allowed(machine, PatrolRunState.DEGRADED_SUMMARY)
            failure = PatrolFailure(
                failure_id=new_id("fl"),
                failure_type=FailureType.TRANSIENT,
                recoverability=Recoverability.RETRYABLE,
                recommended_next_action="retry_later",
                message=str(exc),
            )
            failures.append(failure)
            next_check_at = (datetime.now(timezone.utc) + timedelta(seconds=self.next_check_delay_seconds)).isoformat()
            result = PatrolRunResult(
                patrol_run_id=patrol_run_id,
                session_id=self.session_id,
                status=PatrolRunStatus.FAILED,
                project_scope=project_scope,
                summary=f"Patrol failed and produced a degraded summary: {exc}",
                findings=[],
                recommendations=[],
                evidence=evidence,
                failures=failures,
                next_check_at=next_check_at,
                state_update={"state_machine": machine.to_dict()},
                audit_events=audit_events,
            )
            session.apply_run_result(result)
            await self.session_store.save_session(session)
            return result

    async def _load_or_create_session(self, *, resume: bool) -> PatrolSession:
        existing = await self.session_store.load_session(self.session_id)
        if existing is not None:
            if self.project_scope and not existing.project_scope:
                existing.project_scope = list(self.project_scope)
            return existing
        return new_patrol_session(self.session_id, self.project_scope or self._discover_project_scope())

    async def _inspect(self, patrol_run_id: str, audit_events: List[AuditEvent]) -> List[Dict[str, Any]]:
        envelopes: List[Dict[str, Any]] = []
        projects = self.project_scope or self._call_tool("list_training_projects", project_root=str(self.project_root)).get("projects", [])
        if not self.project_scope:
            self.project_scope = list(projects)
        list_payload = {"projects": projects, "count": len(projects)}
        envelopes.append(
            self._record_tool_payload(
                patrol_run_id,
                audit_events,
                "list_training_projects",
                list_payload,
                kind="project_structure",
                source_uri=f"file://{self.project_root / 'train-model'}",
                summary=summarize_payload(list_payload, subject="training projects"),
            )
        )
        for project_name in projects:
            payload = self._call_tool(
                "inspect_project_structure",
                project_root=str(self.project_root),
                project_name=project_name,
            )
            envelopes.append(
                self._record_tool_payload(
                    patrol_run_id,
                    audit_events,
                    "inspect_project_structure",
                    payload,
                    kind="project_structure",
                    source_uri=f"file://{self.project_root / 'train-model' / project_name}",
                    summary=summarize_payload(payload, subject=f"project {project_name}"),
                )
            )
        for service_name, port in self.service_checks:
            payload = self._call_tool("check_service_health", service_name=service_name, port=port)
            envelopes.append(
                self._record_tool_payload(
                    patrol_run_id,
                    audit_events,
                    "check_service_health",
                    payload,
                    kind="service_health",
                    source_uri=f"systemd://{service_name}.service",
                    summary=summarize_payload(payload, subject=f"service {service_name}"),
                    metadata={"service_name": service_name, "port": port},
                )
            )
        ray_payload = self._call_tool("inspect_ray_status")
        envelopes.append(
            self._record_tool_payload(
                patrol_run_id,
                audit_events,
                "inspect_ray_status",
                ray_payload,
                kind="service_health",
                source_uri="ray://cluster/status",
                summary=summarize_payload(ray_payload, subject="Ray cluster"),
            )
        )
        for tracking_uri, experiment_name in self.mlflow_experiments:
            payload = self._call_tool(
                "inspect_mlflow_experiment",
                tracking_uri=tracking_uri,
                experiment_name=experiment_name,
            )
            envelopes.append(
                self._record_tool_payload(
                    patrol_run_id,
                    audit_events,
                    "inspect_mlflow_experiment",
                    payload,
                    kind="mlflow_run",
                    source_uri=f"mlflow://experiments/{experiment_name}",
                    summary=summarize_payload(payload, subject=f"MLflow experiment {experiment_name}"),
                    metadata={"tracking_uri": tracking_uri, "experiment_name": experiment_name},
                )
            )
        return envelopes

    def _classify_findings(self, envelopes: Iterable[Dict[str, Any]]) -> List[Finding]:
        findings: List[Finding] = []
        for envelope in envelopes:
            payload = envelope.get("legacy_payload", {})
            evidence_ids = [item["evidence_id"] for item in envelope.get("evidence", [])]
            source_tool = envelope.get("evidence", [{}])[0].get("source_tool") if envelope.get("evidence") else "unknown"
            if source_tool == "check_service_health":
                status = str(payload.get("status", "unknown"))
                if status not in {"active", "ok", "healthy"}:
                    service_name = str(payload.get("name", "unknown"))
                    findings.append(
                        Finding(
                            finding_id=new_id("fd"),
                            target={"kind": "service", "id": service_name},
                            type="service_unavailable",
                            severity=Severity.WARNING,
                            summary=f"Service {service_name} is {status}.",
                            evidence_ids=evidence_ids,
                        )
                    )
            elif source_tool == "inspect_ray_status":
                if not bool(payload.get("is_available", False)):
                    findings.append(
                        Finding(
                            finding_id=new_id("fd"),
                            target={"kind": "service", "id": "ray"},
                            type="service_unavailable",
                            severity=Severity.WARNING,
                            summary=f"Ray cluster is unavailable: {payload.get('error', 'unknown error')}",
                            evidence_ids=evidence_ids,
                        )
                    )
            elif source_tool == "inspect_mlflow_experiment":
                if payload.get("error") or payload.get("exists") is False:
                    findings.append(
                        Finding(
                            finding_id=new_id("fd"),
                            target={"kind": "service", "id": "mlflow"},
                            type="service_unavailable",
                            severity=Severity.WARNING,
                            summary=f"MLflow experiment inspection failed: {payload.get('error', 'not found')}",
                            evidence_ids=evidence_ids,
                        )
                    )
        return findings

    def _recommend(self, open_findings: List[Finding]) -> List[Recommendation]:
        recommendations: List[Recommendation] = []
        for finding in open_findings:
            if finding.type == "service_unavailable":
                recommendations.append(
                    Recommendation(
                        recommendation_id=new_id("rec"),
                        type=RecommendationType.WAIT,
                        target={"project_name": project_scope_name(self.project_scope), **finding.target},
                        severity=finding.severity,
                        confidence=0.4,
                        finding_ids=[finding.finding_id],
                        evidence_ids=finding.evidence_ids,
                        risk="low",
                        requires_approval=False,
                    )
                )
        return recommendations

    def _record_tool_payload(
        self,
        patrol_run_id: str,
        audit_events: List[AuditEvent],
        tool_name: str,
        payload: Dict[str, Any],
        *,
        kind: str,
        source_uri: str,
        summary: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._audit(audit_events, "tool_called", patrol_run_id, tool_name=tool_name)
        raw_path = self._raw_path(patrol_run_id, tool_name, len(audit_events))
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
        raw_uri = f"state://patrol/{self.session_id}/{patrol_run_id}/raw/{raw_path.name}"
        envelope = build_tool_envelope(
            source_tool=tool_name,
            payload=payload,
            kind=kind,
            source_uri=source_uri,
            raw_uri=raw_uri,
            summary=summary,
            metadata=metadata,
        )
        self._audit(
            audit_events,
            "tool_summarized",
            patrol_run_id,
            tool_name=tool_name,
            evidence_ids=[item["evidence_id"] for item in envelope["evidence"]],
            raw_uri=raw_uri,
        )
        return envelope

    def _raw_path(self, patrol_run_id: str, tool_name: str, index: int) -> Path:
        safe_tool = "".join(char if char.isalnum() or char in "-_" else "_" for char in tool_name)
        return self.session_store.state_dir / "raw" / self.session_id / patrol_run_id / f"{index:03d}-{safe_tool}.json"

    def _call_tool(self, tool_name: str, **kwargs: Any) -> Dict[str, Any]:
        if tool_name in self.tool_overrides:
            return self.tool_overrides[tool_name](**kwargs)
        if tool_name == "list_training_projects":
            projects = inspection.list_training_projects(kwargs["project_root"])
            return {"projects": projects, "count": len(projects)}
        if tool_name == "inspect_project_structure":
            return inspection.inspect_project_structure(kwargs["project_root"], kwargs["project_name"])
        if tool_name == "check_service_health":
            return inspection.check_service_health(kwargs["service_name"], kwargs["port"], kwargs.get("endpoint", "127.0.0.1"))
        if tool_name == "inspect_ray_status":
            return inspection.inspect_ray_status()
        if tool_name == "inspect_mlflow_experiment":
            return inspection.inspect_mlflow_experiment(kwargs["tracking_uri"], kwargs["experiment_name"])
        raise KeyError(f"Unknown patrol tool: {tool_name}")

    def _discover_project_scope(self) -> List[str]:
        payload = self._call_tool("list_training_projects", project_root=str(self.project_root))
        return list(payload.get("projects", []))

    def _transition(self, machine: PatrolRunStateMachine, state: PatrolRunState) -> None:
        transition = machine.transition_to(state)
        if not transition.allowed:
            raise RuntimeError(transition.reason)

    def _transition_if_allowed(self, machine: PatrolRunStateMachine, state: PatrolRunState) -> None:
        transition = machine.transition_to(state)
        if not transition.allowed:
            return

    def _audit(self, events: List[AuditEvent], event_type: str, patrol_run_id: str, **metadata: Any) -> None:
        events.append(
            AuditEvent(
                event_type=event_type,
                patrol_run_id=patrol_run_id,
                session_id=self.session_id,
                metadata=metadata,
            )
        )

    def _summarize_result(
        self,
        findings: List[Finding],
        recommendations: List[Recommendation],
        failures: List[PatrolFailure],
    ) -> str:
        if failures:
            return f"Patrol completed with {len(failures)} failure(s)."
        if findings:
            return f"Patrol found {len(findings)} open finding(s) and pushed {len(recommendations)} recommendation(s)."
        return "Patrol found no open findings."


def _result_status(
    findings: List[Finding],
    failures: List[PatrolFailure],
    recommendations: List[Recommendation],
) -> PatrolRunStatus:
    if failures:
        return PatrolRunStatus.FAILED
    if any(recommendation.requires_approval for recommendation in recommendations):
        return PatrolRunStatus.NEEDS_APPROVAL
    if findings:
        return PatrolRunStatus.WARNING
    return PatrolRunStatus.OK


def project_scope_name(project_scope: List[str]) -> str:
    return project_scope[0] if project_scope else "platform"
