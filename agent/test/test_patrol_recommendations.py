"""Tests for patrol recommendation governance and action policy boundaries."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent.policies.patrol import PatrolActionPolicy, PatrolLifecyclePolicy
from agent.schemas.patrol import (
    ActionLevel,
    EvidenceRecord,
    Finding,
    RawRef,
    Recommendation,
    RecommendationType,
    Severity,
)


class TestPatrolRecommendations(unittest.TestCase):
    def _finding_and_evidence(self):
        evidence = EvidenceRecord(
            evidence_id="ev_artifact",
            kind="artifact",
            source_tool="inspect_model_artifact",
            source_uri="mlflow-artifacts:/model",
            raw_ref=RawRef(uri="state://patrol/s1/r1/raw/artifact.json", digest="sha256:" + "5" * 64),
            summary="Model artifact cannot be read back.",
        )
        finding = Finding(
            finding_id="fd_artifact",
            target={"kind": "artifact", "id": "model"},
            type="artifact_risk",
            severity=Severity.CRITICAL,
            summary="Model artifact cannot be read back.",
            evidence_ids=[evidence.evidence_id],
        )
        return finding, evidence

    def test_recommendation_dedupe_and_cooldown_suppresses_repush(self):
        finding, evidence = self._finding_and_evidence()
        policy = PatrolLifecyclePolicy(default_cooldown_seconds=3600)
        recommendation = Recommendation(
            recommendation_id="rec_review",
            type=RecommendationType.REQUEST_PROMOTION_REVIEW,
            target={"project_name": "cats-and-dogs", "artifact_uri": "mlflow-artifacts:/model"},
            severity=Severity.CRITICAL,
            confidence=0.9,
            finding_ids=[finding.finding_id],
            evidence_ids=[evidence.evidence_id],
            risk="high",
            requires_approval=True,
        )

        recommendations = []
        first, pushed_first = policy.upsert_recommendation(recommendations, recommendation)
        duplicate, pushed_duplicate = policy.upsert_recommendation(
            recommendations,
            recommendation.model_copy(deep=True),
            now=datetime.now(timezone.utc) + timedelta(minutes=5),
        )

        self.assertTrue(pushed_first)
        self.assertFalse(pushed_duplicate)
        self.assertEqual(first.recommendation_id, duplicate.recommendation_id)
        self.assertEqual(len(recommendations), 1)
        self.assertIsNotNone(recommendations[0].cooldown_until)

    def test_action_policy_blocks_apply_without_matching_approval(self):
        policy = PatrolActionPolicy(max_action_level=ActionLevel.RECOMMEND, project_scope=["cats-and-dogs"])
        decision = policy.check_action(
            action_type="apply_registry_alias",
            action_level=ActionLevel.APPLY,
            project_name="cats-and-dogs",
            risk="high",
            evidence_ids=["ev_quality"],
            approval_request_id=None,
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.failure_type, "policy_blocked")
        self.assertIn("requires approval", decision.reason)

    def test_action_policy_allows_approval_request_with_evidence(self):
        policy = PatrolActionPolicy(
            max_action_level=ActionLevel.REQUEST_APPROVAL,
            project_scope=["cats-and-dogs"],
            allow_request_approval=True,
        )
        decision = policy.check_action(
            action_type="request_promotion_review",
            action_level=ActionLevel.REQUEST_APPROVAL,
            project_name="cats-and-dogs",
            risk="high",
            evidence_ids=["ev_quality", "ev_artifact"],
            approval_request_id=None,
        )

        self.assertTrue(decision.allowed)
        self.assertIsNone(decision.failure_type)

    def test_high_confidence_recommendation_requires_evidence(self):
        recommendation = Recommendation(
            recommendation_id="rec_bad",
            type=RecommendationType.FIX_CONFIG,
            target={"project_name": "cats-and-dogs"},
            severity=Severity.WARNING,
            confidence=0.95,
            finding_ids=[],
            evidence_ids=[],
            risk="medium",
        )
        with self.assertRaises(ValueError):
            recommendation.validate_governance()


if __name__ == "__main__":
    unittest.main()
