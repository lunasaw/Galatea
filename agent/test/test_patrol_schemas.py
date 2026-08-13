"""Offline tests for patrol domain schemas and evidence traceability."""

from __future__ import annotations

import json
import unittest

from pydantic import ValidationError

from agent.schemas.patrol import (
    EvidenceRecord,
    Finding,
    FindingStatus,
    PatrolMemory,
    PatrolRunResult,
    RawRef,
    Recommendation,
    RecommendationType,
    Severity,
    make_fingerprint,
)


class TestPatrolSchemas(unittest.TestCase):
    def test_evidence_requires_stable_raw_digest(self):
        raw_ref = RawRef(uri="state://patrol/s1/r1/raw/ray.json", digest="sha256:" + "a" * 64)
        evidence = EvidenceRecord(
            evidence_id="ev_ray",
            kind="ray_job",
            source_tool="inspect_ray_status",
            source_uri="ray://cluster/status",
            raw_ref=raw_ref,
            summary="Ray is unavailable.",
        )

        loaded = EvidenceRecord.model_validate(json.loads(evidence.model_dump_json()))
        self.assertEqual(loaded.raw_ref.digest, raw_ref.digest)
        self.assertEqual(loaded.sensitivity, "internal")

        with self.assertRaises(ValidationError):
            EvidenceRecord(
                evidence_id="ev_bad",
                kind="ray_job",
                source_tool="inspect_ray_status",
                source_uri="ray://cluster/status",
                raw_ref=RawRef(uri="state://patrol/s1/raw/bad.json", digest="bad-digest"),
                summary="bad",
            )

    def test_fingerprints_are_stable_for_mapping_order(self):
        first = make_fingerprint({"target": {"id": "ray", "kind": "service"}, "type": "service_unavailable"})
        second = make_fingerprint({"type": "service_unavailable", "target": {"kind": "service", "id": "ray"}})
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("sha256:"))

    def test_patrol_run_result_recommendations_trace_to_evidence(self):
        evidence = EvidenceRecord(
            evidence_id="ev_1",
            kind="service_health",
            source_tool="check_service_health",
            source_uri="systemd://mlflow.service",
            raw_ref=RawRef(uri="state://patrol/s1/r1/raw/mlflow.json", digest="sha256:" + "1" * 64),
            summary="MLflow service is inactive.",
        )
        finding = Finding(
            finding_id="fd_1",
            target={"kind": "service", "id": "mlflow"},
            type="service_unavailable",
            severity=Severity.WARNING,
            status=FindingStatus.OPEN,
            summary="MLflow service is inactive.",
            evidence_ids=[evidence.evidence_id],
        )
        recommendation = Recommendation(
            recommendation_id="rec_1",
            type=RecommendationType.WAIT,
            target={"project_name": "cats-and-dogs"},
            severity=Severity.WARNING,
            confidence=0.4,
            finding_ids=[finding.finding_id],
            evidence_ids=[evidence.evidence_id],
            risk="low",
        )
        result = PatrolRunResult(
            patrol_run_id="pr_1",
            session_id="s1",
            status="warning",
            project_scope=["cats-and-dogs"],
            summary="One service warning.",
            findings=[finding],
            recommendations=[recommendation],
            evidence=[evidence],
        )

        result.validate_traceability()
        self.assertEqual(result.findings[0].fingerprint, finding.fingerprint)

        broken = result.model_copy(deep=True)
        broken.recommendations[0].evidence_ids = ["ev_missing"]
        with self.assertRaises(ValueError):
            broken.validate_traceability()

    def test_memory_collects_required_fields_from_structured_objects(self):
        memory = PatrolMemory(
            patrol_run_id="pr_1",
            project_name="cats-and-dogs",
            summary="Ray job job-1 produced run abc.",
            open_findings=[
                Finding(
                    finding_id="fd_1",
                    target={"kind": "ray_job", "id": "job-1", "ray_job_id": "job-1"},
                    type="service_unavailable",
                    severity="warning",
                    summary="Ray job failed.",
                    evidence_ids=["ev_1"],
                )
            ],
            evidence_index=[
                EvidenceRecord(
                    evidence_id="ev_1",
                    kind="ray_job",
                    source_tool="inspect_ray_status",
                    source_uri="ray://jobs/job-1",
                    raw_ref=RawRef(uri="state://patrol/s1/r1/raw/job.json", digest="sha256:" + "2" * 64),
                    summary="Ray job id job-1 maps to MLflow run abc.",
                    metadata={"ray_job_id": "job-1", "mlflow_run_id": "abc"},
                )
            ],
            next_check_at="2026-08-13T10:00:00+00:00",
        )

        fields = memory.required_field_values()
        self.assertEqual(fields["patrol_run_id"], {"pr_1"})
        self.assertEqual(fields["project_name"], {"cats-and-dogs"})
        self.assertEqual(fields["ray_job_id"], {"job-1"})
        self.assertEqual(fields["mlflow_run_id"], {"abc"})
        self.assertEqual(fields["finding_id"], {"fd_1"})


if __name__ == "__main__":
    unittest.main()
