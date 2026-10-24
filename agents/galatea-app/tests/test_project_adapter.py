import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace
from galatea_app.integrations.project_governance import (
    ProjectGovernance,
    ProjectGovernanceRegistry,
)
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


class ProjectAdapterRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.configs = {
            project_id: {
                "project_id": project_id,
                "profile": "dev",
                "ray_api": "http://ray:8265",
                "tracking_uri": "http://mlflow:5000",
                "receipts_root": self.root / project_id,
                "project_root": self.root / project_id,
                "user_authorization": {
                    "principal_ids": ["alice"],
                    "roles": ["trial"],
                    "allow_final_test": False,
                    "resources": {"gpu": 1, "cpu": 8},
                    "hard_limits": {
                        "attempts": 1,
                        "retries": 0,
                        "wall_seconds": 43200,
                        "memory_bytes": None,
                    },
                    "expires_at": "2099-01-01T00:00:00Z",
                    "allow_unbounded_memory": True,
                },
            }
            for project_id in ("cats", "ray-llm")
        }
        for settings in self.configs.values():
            Path(settings["receipts_root"]).mkdir()
        self.registry = ProjectGovernanceRegistry(self.configs)
        self.addCleanup(self.registry.close)

    def tearDown(self):
        self.tmp.cleanup()

    def test_dispatches_each_project_to_its_own_adapter(self):
        cats, llm = self.registry.adapters.values()
        cats.reconcile = Mock(return_value={"id": "cats-job"})
        llm.reconcile = Mock(return_value={"id": "llm-job"})

        cats_result = self.registry.reconcile(
            {"immutable_inputs": {"project_id": "cats"}}
        )
        llm_result = self.registry.reconcile(
            {"immutable_inputs": {"project_id": "ray-llm"}}
        )

        self.assertEqual(cats_result["id"], "cats-job")
        self.assertEqual(llm_result["id"], "llm-job")
        cats.reconcile.assert_called_once()
        llm.reconcile.assert_called_once()

    def test_authorization_is_scoped_to_registered_project(self):
        proposal = {
            "immutable_inputs": {
                "route": "project_governance",
                "project_id": "ray-llm",
                "profile": "dev",
                "role": "trial",
                "final_test": False,
            },
            "resources": {"gpu": 1, "cpu": 8},
            "hard_limits": {
                "attempts": 1,
                "retries": 0,
                "wall_seconds": 43200,
                "memory_bytes": None,
            },
            "expires_at": "2098-01-01T00:00:00Z",
        }
        self.assertTrue(self.registry.can_authorize("alice", proposal))
        proposal["immutable_inputs"]["project_id"] = "unregistered"
        self.assertFalse(self.registry.can_authorize("alice", proposal))

    def test_unknown_project_does_not_fall_back_to_another_adapter(self):
        with self.assertRaises(TaskError):
            self.registry.reconcile(
                {"immutable_inputs": {"project_id": "unregistered"}}
            )

    def test_conflicting_project_identity_is_rejected(self):
        with self.assertRaises(TaskError):
            self.registry.reconcile(
                {
                    "project_id": "cats",
                    "immutable_inputs": {"project_id": "ray-llm"},
                }
            )
        self.assertFalse(
            self.registry.owns(
                {"immutable_inputs": {"project_id": "cats"}},
                {"id": "job", "project_id": "ray-llm"},
            )
        )
