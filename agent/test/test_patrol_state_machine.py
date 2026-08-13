"""Tests for patrol run state machine and finding lifecycle."""

from __future__ import annotations

import unittest

from agent.schemas.patrol import EvidenceRecord, Finding, RawRef, Severity
from agent.workflows.patrol import PatrolRunState, PatrolRunStateMachine
from agent.policies.patrol import PatrolLifecyclePolicy


class TestPatrolStateMachine(unittest.TestCase):
    def test_success_path_returns_to_idle_after_schedule_next(self):
        machine = PatrolRunStateMachine("run-1")
        machine.start()
        for state in [
            PatrolRunState.INSPECT,
            PatrolRunState.CLASSIFY_FINDINGS,
            PatrolRunState.RECOMMEND,
            PatrolRunState.PERSIST_STATE,
            PatrolRunState.SCHEDULE_NEXT,
            PatrolRunState.IDLE,
        ]:
            transition = machine.transition_to(state)
            self.assertTrue(transition.allowed, transition.reason)
        self.assertEqual(machine.current_state, PatrolRunState.IDLE)
        self.assertEqual(machine.status, "completed")

    def test_failure_path_can_degrade_and_schedule_next(self):
        machine = PatrolRunStateMachine("run-2")
        machine.start()
        self.assertTrue(machine.transition_to(PatrolRunState.INSPECT).allowed)
        self.assertTrue(machine.transition_to(PatrolRunState.INSPECT_FAILED).allowed)
        self.assertTrue(machine.transition_to(PatrolRunState.DEGRADED_SUMMARY).allowed)
        self.assertTrue(machine.transition_to(PatrolRunState.PERSIST_STATE).allowed)
        self.assertTrue(machine.transition_to(PatrolRunState.SCHEDULE_NEXT).allowed)
        self.assertTrue(machine.transition_to(PatrolRunState.IDLE).allowed)
        self.assertEqual(machine.status, "completed")

    def test_invalid_transition_is_rejected_without_mutating_state(self):
        machine = PatrolRunStateMachine("run-3")
        transition = machine.transition_to(PatrolRunState.RECOMMEND)
        self.assertFalse(transition.allowed)
        self.assertEqual(machine.current_state, PatrolRunState.IDLE)

    def test_finding_lifecycle_dedup_escalation_and_resolution(self):
        policy = PatrolLifecyclePolicy(escalate_after=3)
        evidence = EvidenceRecord(
            evidence_id="ev_ray",
            kind="service_health",
            source_tool="inspect_ray_status",
            source_uri="ray://cluster/status",
            raw_ref=RawRef(uri="state://patrol/s1/r1/raw/ray.json", digest="sha256:" + "1" * 64),
            summary="Ray is unavailable.",
        )
        finding = Finding(
            finding_id="fd_ray",
            target={"kind": "service", "id": "ray"},
            type="service_unavailable",
            severity=Severity.WARNING,
            summary="Ray is unavailable.",
            evidence_ids=["ev_ray"],
        )

        open_findings = []
        first = policy.upsert_finding(open_findings, finding, [evidence])
        second = policy.upsert_finding(open_findings, finding.model_copy(deep=True), [evidence])
        third = policy.upsert_finding(open_findings, finding.model_copy(deep=True), [evidence])

        self.assertEqual(len(open_findings), 1)
        self.assertEqual(first.finding_id, second.finding_id)
        self.assertEqual(third.occurrence_count, 3)
        self.assertEqual(third.severity, Severity.CRITICAL)

        policy.mark_resolved_if_missing(open_findings, active_fingerprints=set())
        self.assertEqual(open_findings[0].status, "resolved")
        self.assertIsNotNone(open_findings[0].resolved_at)


if __name__ == "__main__":
    unittest.main()
