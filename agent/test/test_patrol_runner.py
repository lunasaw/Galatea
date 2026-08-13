"""Offline tests for deterministic patrol runner and tool output envelope."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.patrol.runner import PatrolRunner
from agent.tools.patrol_output import build_tool_envelope


class TestPatrolRunner(unittest.IsolatedAsyncioTestCase):
    def test_tool_envelope_summarizes_and_redacts_raw_payload(self):
        payload = {"status": "inactive", "log": "x" * 5000, "token": "SECRET"}
        envelope = build_tool_envelope(
            source_tool="check_service_health",
            payload=payload,
            kind="service_health",
            source_uri="systemd://mlflow.service",
            raw_uri="state://patrol/s1/r1/raw/mlflow.json",
            summary="MLflow inactive token=SECRET " + "x" * 1000,
            max_summary_chars=120,
        )

        self.assertIn("summary_for_model", envelope)
        self.assertIn("evidence", envelope)
        self.assertIn("raw_ref", envelope)
        self.assertLessEqual(len(envelope["summary_for_model"]), 120)
        self.assertNotIn("SECRET", envelope["summary_for_model"])
        self.assertTrue(envelope["raw_ref"]["digest"].startswith("sha256:"))
        self.assertEqual(envelope["legacy_payload"], payload)

    async def test_run_once_without_llm_generates_warning_and_no_training(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / "train-model" / "cats-and-dogs").mkdir(parents=True)
            runner = PatrolRunner(
                project_root=project_root,
                state_dir=project_root / "platform-data" / "agent-state",
                session_id="s1",
                project_scope=["cats-and-dogs"],
                service_checks=[("ray", 8265)],
                tool_overrides={
                    "check_service_health": lambda **_: {"name": "ray", "status": "inactive", "port": 8265},
                    "inspect_ray_status": lambda **_: {"is_available": False, "error": "Ray down"},
                },
            )

            result = await runner.run_once()
            self.assertEqual(result.status, "warning")
            self.assertTrue(any(f.type == "service_unavailable" for f in result.findings))
            self.assertTrue(all(r.type in {"wait", "inspect_failed_run"} for r in result.recommendations))
            self.assertTrue(all("submit" not in event.event_type for event in result.audit_events))
            result.validate_traceability()

            stored = await runner.session_store.load_session("s1")
            self.assertIsNotNone(stored)
            assert stored is not None
            self.assertEqual(len(stored.open_findings), 1)

    async def test_resume_refresh_marks_recovered_service_finding_resolved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            (project_root / "train-model" / "cats-and-dogs").mkdir(parents=True)
            state_dir = project_root / "platform-data" / "agent-state"
            failing = PatrolRunner(
                project_root=project_root,
                state_dir=state_dir,
                session_id="s1",
                project_scope=["cats-and-dogs"],
                service_checks=[("ray", 8265)],
                tool_overrides={
                    "check_service_health": lambda **_: {"name": "ray", "status": "inactive", "port": 8265},
                    "inspect_ray_status": lambda **_: {"is_available": False, "error": "Ray down"},
                },
            )
            await failing.run_once()

            recovered = PatrolRunner(
                project_root=project_root,
                state_dir=state_dir,
                session_id="s1",
                project_scope=["cats-and-dogs"],
                service_checks=[("ray", 8265)],
                tool_overrides={
                    "check_service_health": lambda **_: {"name": "ray", "status": "active", "port": 8265},
                    "inspect_ray_status": lambda **_: {"is_available": True, "raw_output": "Resources"},
                },
            )
            result = await recovered.run_once(resume=True)
            self.assertEqual(result.status, "ok")
            stored = await recovered.session_store.load_session("s1")
            self.assertIsNotNone(stored)
            assert stored is not None
            self.assertEqual(stored.open_findings, [])
            self.assertEqual(stored.closed_findings[0].status, "resolved")


class TestPatrolChannelsAndSdk(unittest.TestCase):
    def test_markdown_report_redacts_secrets_and_keeps_raw_refs(self):
        from agent.patrol.channels import render_markdown_report
        from agent.schemas.patrol import EvidenceRecord, Finding, PatrolRunResult, RawRef

        evidence = EvidenceRecord(
            evidence_id="ev_secret",
            kind="service_health",
            source_tool="check_service_health",
            source_uri="systemd://mlflow.service",
            raw_ref=RawRef(uri="state://patrol/s1/r1/raw/mlflow.json", digest="sha256:" + "6" * 64),
            summary="MLflow inactive token=SECRET",
        )
        finding = Finding(
            finding_id="fd_secret",
            target={"kind": "service", "id": "mlflow"},
            type="service_unavailable",
            severity="warning",
            summary="MLflow inactive password=hunter2",
            evidence_ids=["ev_secret"],
        )
        result = PatrolRunResult(
            patrol_run_id="pr_1",
            session_id="s1",
            status="warning",
            project_scope=["cats-and-dogs"],
            summary="password=hunter2",
            findings=[finding],
            evidence=[evidence],
        )

        markdown = render_markdown_report(result)
        self.assertNotIn("hunter2", markdown)
        self.assertNotIn("SECRET", markdown)
        self.assertIn("state://patrol/s1/r1/raw/mlflow.json", markdown)
        self.assertIn("ev_secret", markdown)

    def test_sdk_config_uses_structured_patrol_schema_and_safe_tools(self):
        from pathlib import Path
        from agent.patrol.sdk import make_patrol_sdk_config, patrol_run_result_json_schema

        config = make_patrol_sdk_config(project_root=Path.cwd(), project_scope=["cats-and-dogs"])
        schema = patrol_run_result_json_schema()

        self.assertEqual(config.agent_type, "patrol-push")
        self.assertIn("patrol_run_id", schema["properties"])
        self.assertNotIn("Bash", config.allowed_tools)
        self.assertIn("Bash", config.disallowed_tools)
        self.assertIn("mcp__galatea-platform__inspect_ray_status", config.allowed_tools)

    def test_llm_candidate_apply_is_downgraded_by_action_policy_failure(self):
        from agent.patrol.sdk import validate_llm_patrol_result
        from agent.policies.patrol import PatrolActionPolicy
        from agent.schemas.patrol import ActionLevel, EvidenceRecord, RawRef, Recommendation

        evidence = EvidenceRecord(
            evidence_id="ev_smoke",
            kind="quality_gate",
            source_tool="run_smoke_inference",
            source_uri="mlflow-artifacts:/model/report",
            raw_ref=RawRef(uri="state://patrol/s1/r1/raw/smoke.json", digest="sha256:" + "7" * 64),
            summary="Smoke passed.",
        )
        recommendation = Recommendation(
            recommendation_id="rec_apply",
            type="rerun_smoke",
            target={"project_name": "cats-and-dogs"},
            severity="info",
            confidence=0.7,
            evidence_ids=["ev_smoke"],
            risk="low",
            requires_approval=False,
        )
        candidate = {
            "patrol_run_id": "pr_1",
            "session_id": "s1",
            "status": "ok",
            "project_scope": ["cats-and-dogs"],
            "summary": "candidate",
            "findings": [],
            "recommendations": [recommendation.model_dump(mode="json")],
            "evidence": [evidence.model_dump(mode="json")],
        }

        result = validate_llm_patrol_result(
            candidate,
            action_policy=PatrolActionPolicy(max_action_level=ActionLevel.RECOMMEND, project_scope=["cats-and-dogs"]),
        )
        self.assertEqual(result.status, "needs_approval")
        self.assertEqual(result.failures[0].failure_type, "policy_blocked")
        self.assertIn("requires approval", result.recommendations[0].metadata["policy_decision"])


if __name__ == "__main__":
    unittest.main()
