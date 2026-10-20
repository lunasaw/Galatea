import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace
from galatea_app.integrations.project_governance import ProjectGovernance
from galatea_app.common import TaskError


class ProjectAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        target = {
            "clusterProfile": "dev",
            "rayApiServer": "http://ray:8265",
            "rayAddress": "ray:6379",
            "mlflowTrackingUri": "http://mlflow:5000",
        }
        self.plan = {
            "identity": "sha256:identity",
            "releaseId": "release",
            "attempt": "execution-123",
            "role": "smoke",
            "evidence": {"digest": "ready"},
            "plan": {
                "configDigest": "config",
                "dataset": {"content_sha256": "data", "split_sha256": "split"},
            },
        }
        (self.root / "smoke-execution-123-plan.json").write_text(
            json.dumps({"target": target, "planned": {"data": self.plan}})
        )
        (self.root / "smoke-execution-123-submission.json").write_text(
            json.dumps(
                {
                    "target": target,
                    "submitted": {
                        "ok": True,
                        "data": {
                            "submissionId": "job-123",
                            "idempotencyKey": "sha256:identity",
                            "readinessEvidenceDigest": "ready",
                        },
                    },
                }
            )
        )
        self.kwargs = {
            "project_id": "cats",
            "profile": "dev",
            "ray_api": target["rayApiServer"],
            "tracking_uri": target["mlflowTrackingUri"],
            "receipts_root": self.root,
            "project_root": self.root,
        }
        self.intent = {
            "idempotency_key": "execution-123",
            "immutable_inputs": {
                "route": "project_governance",
                "project_id": "cats",
                "profile": "dev",
                "release_digest": "release",
                "config_digest": "config",
                "data_digest": "data",
                "split_digest": "split",
                "role": "smoke",
                "final_test": False,
            },
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_restart_reconciles_exact_attempt_and_rejects_identical_inputs_other_attempt(
        self,
    ):
        adapter = ProjectGovernance(**self.kwargs)
        ref = adapter.reconcile(self.intent)
        self.assertEqual(ref["id"], "job-123")
        self.assertTrue(adapter.owns(self.intent, ref))
        self.assertFalse(
            adapter.owns({**self.intent, "idempotency_key": "other-attempt"}, ref)
        )
        self.assertTrue(ProjectGovernance(**self.kwargs).owns(self.intent, ref))

    def test_stop_uses_fixed_controller_and_registered_receipt(self):
        adapter = ProjectGovernance(**self.kwargs)
        with patch(
            "subprocess.run",
            return_value=SimpleNamespace(
                returncode=0, stdout='{"ok":true,"data":{"stopped":true}}', stderr=""
            ),
        ) as run:
            self.assertTrue(adapter.stop({"id": "job-123"})["data"]["stopped"])
            argv = run.call_args.args[0]
            self.assertIn("project_stop.mjs", argv[1])
            self.assertEqual(argv[-1], "Task cancellation")
            with self.assertRaises(TaskError):
                adapter.stop({"id": "another-job"})
        self.assertEqual(run.call_count, 1)

    def test_execution_budget_uses_authoritative_ray_time(self):
        adapter = ProjectGovernance(**self.kwargs)
        adapter.http = Mock()
        adapter.http.get.return_value.json.return_value = {"start_time": 200000}
        with patch("galatea_app.integrations.project_governance.time.time", return_value=300):
            self.assertEqual(adapter.execution_started_at({"id": "job-123"}), 200)
            for invalid in (None, True, 0, float("nan"), 400000):
                adapter.http.get.return_value.json.return_value = {"start_time": invalid}
                with self.assertRaises(TaskError):
                    adapter.execution_started_at({"id": "job-123"})

    def test_terminal_missing_run_fails_after_tracking_visibility_grace(self):
        adapter = ProjectGovernance(**self.kwargs)
        adapter.http = Mock()
        adapter.http.get.return_value.status_code = 200
        adapter.http.get.return_value.json.return_value = {
            "status": "SUCCEEDED", "end_time": 200000,
        }
        client = Mock()
        client.search_runs.return_value = []
        with patch("mlflow.MlflowClient", return_value=client), patch(
            "galatea_app.integrations.project_governance.time.time", return_value=250
        ):
            self.assertEqual(adapter.observe({"id": "job-123"})["status"], "unknown")
        with patch("mlflow.MlflowClient", return_value=client), patch(
            "galatea_app.integrations.project_governance.time.time", return_value=261
        ):
            fact = adapter.observe({"id": "job-123"})
            self.assertEqual(fact["status"], "failed")
            self.assertNotIn("run_id", fact)
