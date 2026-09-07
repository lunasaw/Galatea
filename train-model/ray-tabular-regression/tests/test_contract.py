import base64
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ray_tabular_regression.binding import (BindingError, verify_execution_binding,
                                            verify_runtime_origin)
from ray_tabular_regression.config import load_bound_config
from ray_tabular_regression.data import load_role_views
from ray_tabular_regression.data import download_json_view
from ray_tabular_regression.driver import execute
from ray_tabular_regression.evidence import build_evidence
from ray_tabular_regression.model import RidgeModel, predict
from ray_tabular_regression.release import build_release, write_registration_examples
from ray_tabular_regression.workload import (run, require_integrity,
                                             download_proxy_artifact)
from ray_tabular_regression.model import fit_ridge


class FakeContext:
    def get_job_id(self):
        return "ray-job-1"

    def get_job_submission_id(self):
        return "submission-1"


def packet(role="trial"):
    return {
        "schema_version": "galatea.execution/v1", "project_id": "ray-tabular-regression",
        "campaign_id": "campaign-1", "operation_id": "op-1", "submission_id": "submission-1",
        "step_id": "trial", "attempt": 1, "role": role, "config_id": "baseline",
        "release_id": "release-1", "readiness_digest": "a" * 64, "deadline_at": 4102444800,
        "candidate_id": "candidate-1" if role == "evaluate" else None,
        "champion_run_id": "run-champion" if role == "evaluate" else None,
        "metadata": {"galatea.submission": "submission-1", "galatea.operation": "op-1"},
        "ray_address": "https://ray.example",
        "config_path": "configs/baseline.yaml", "config_digest": "b" * 64,
        "release_digest": "c" * 64, "code_revision": "deadbeef",
        "environment_digest": "d" * 64, "dataset_digest": "e" * 64,
        "split_digest": "f" * 64, "preprocessing": "standardize-v1",
        "metric_definition": "rmse-v1", "evaluation_protocol": "frozen-json-v1",
        "objective": {"metric": "val_rmse", "direction": "min"}, "seed": 42,
        "resources": {"cpus": 1, "gpus": 0, "memory_bytes": 536870912, "workers": 1,
                      "seconds": 60, "cleanup_seconds": 10}, "experiment_id": "12",
        "tracking_uri": "https://mlflow.invalid", "clean_start": role != "evaluate",
        "views": ({"test": {"bucket": "approved", "key": "test.json", "version_id": "v1",
                              "sha256": "1" * 64, "size_bytes": 10}} if role == "evaluate" else
                  {"train": {"bucket": "approved", "key": "train.json", "version_id": "v1",
                              "sha256": "2" * 64, "size_bytes": 10},
                   "validation": {"bucket": "approved", "key": "validation.json", "version_id": "v1",
                                  "sha256": "3" * 64, "size_bytes": 10}}),
    }


def _issue_admission(binding):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from ray_tabular_regression.admission import issue
    from types import SimpleNamespace
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    raw = json.dumps(binding, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    environment = {'GALATEA_EXECUTION_BINDING': raw,
                   'GALATEA_EXECUTION_SIGNATURE': base64.b64encode(key.sign(raw.encode())).decode()}
    class Client:
        def get_job_info(self, submission):
            return SimpleNamespace(job_id='ray-job-1', metadata=binding['metadata'])
    return issue(binding, public_key_pem=public, ray_context=FakeContext(),
                 client_factory=lambda _: Client(), environ=environment)


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        cls.key = Ed25519PrivateKey.generate()
        cls.public = cls.key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

    def signed(self, value):
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return raw.decode(), base64.b64encode(self.key.sign(raw)).decode()

    def test_missing_forged_and_local_bindings_fail_before_fit(self):
        fit_calls = []
        for env in ({}, {"GALATEA_EXECUTION_BINDING": "{}", "GALATEA_EXECUTION_SIGNATURE": "bad"}):
            with self.assertRaises(BindingError), patch.dict(os.environ, env, clear=True):
                execute(self.public, FakeContext(), fit=lambda *_: fit_calls.append(True))
        raw, sig = self.signed(packet())
        with self.assertRaises(BindingError), patch.dict(os.environ, {
                "GALATEA_EXECUTION_BINDING": raw, "GALATEA_EXECUTION_SIGNATURE": sig}, clear=True):
            execute(self.public, None, fit=lambda *_: fit_calls.append(True))
        self.assertEqual([], fit_calls)

    def test_binding_matches_real_ray_context_and_role_views(self):
        raw, sig = self.signed(packet())
        binding = verify_execution_binding(raw, sig, self.public, FakeContext(), now=1)
        self.assertEqual({"train", "validation"}, set(binding["views"]))
        altered = packet(); altered["submission_id"] = "other"
        raw, sig = self.signed(altered)
        verified = verify_execution_binding(raw, sig, self.public, now=1)
        with self.assertRaises(BindingError):
            verify_runtime_origin(verified,
                type("Context", (), {"get_job_id": lambda self: "0a0b"})(),
                lambda _: type("Client", (), {"get_job_info": lambda self, submission:
                    (_ for _ in ()).throw(KeyError(submission))})(),
                now=lambda: 1, sleep=lambda _: None)

    def test_official_ray_job_record_must_match_runtime_id_and_signed_metadata(self):
        class Job:
            job_id = "0a0b"
            metadata = packet()["metadata"]
        binding = packet()
        context = type("Context", (), {"get_job_id": lambda self: "0a0b"})()
        verify_runtime_origin(binding, context, lambda address: type("Client", (), {
            "get_job_info": lambda self, submission: Job()})(), now=lambda: 1, sleep=lambda _: None)
        Job.metadata = {"galatea.operation": "forged"}
        with self.assertRaises(BindingError):
            verify_runtime_origin(binding, context, lambda address: type("Client", (), {
                "get_job_info": lambda self, submission: Job()})(), now=lambda: 1, sleep=lambda _: None)

    def test_runtime_origin_retries_pending_job_id_without_fitting(self):
        records = [type("Job", (), {"job_id": None, "metadata": packet()["metadata"]})(),
                   type("Job", (), {"job_id": "0a0b", "metadata": packet()["metadata"]})()]
        context = type("Context", (), {"get_job_id": lambda self: "0a0b"})()
        verify_runtime_origin(packet(), context, lambda _: type("Client", (), {
            "get_job_info": lambda self, submission: records.pop(0)})(), now=lambda: 1,
            sleep=lambda _: None)
        self.assertEqual([], records)

    def test_bound_config_digest_rejects_modified_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.yaml"; path.write_text("alpha: 1\n")
            binding = packet() | {"config_digest": hashlib.sha256(path.read_bytes()).hexdigest()}
            self.assertEqual(1, load_bound_config(path, binding)["alpha"])
            path.write_text("alpha: 2\n")
            with self.assertRaises(BindingError):
                load_bound_config(path, binding)

    def test_expired_binding_rejected_before_runtime_query(self):
        raw, sig = self.signed(packet() | {"deadline_at": 2})
        queried = []
        with self.assertRaises(BindingError):
            verify_execution_binding(raw, sig, self.public, now=2)
        self.assertEqual([], queried)

    def test_binding_accepts_mcp_unicode_canonical_json(self):
        value = packet() | {"project_id": "项目"}
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                         allow_nan=False).encode()
        verified = verify_execution_binding(raw.decode(), base64.b64encode(self.key.sign(raw)).decode(),
                                            self.public, now=1)
        self.assertEqual("项目", verified["project_id"])

    def test_data_download_is_bounded_versioned_closed_and_finite(self):
        raw = b'[{"x":NaN}]'
        class Body:
            closed = False
            def read(self, limit): self.limit = limit; return raw
            def close(self): self.closed = True
        body = Body()
        ref = {"bucket": "b", "key": "k", "version_id": "v", "sha256": hashlib.sha256(raw).hexdigest(),
               "size_bytes": len(raw)}
        class S3:
            def get_object(self, **kwargs): return {"VersionId": "v", "ContentLength": len(raw), "Body": body}
        with self.assertRaises(ValueError):
            download_json_view(ref, S3())
        self.assertEqual(len(raw) + 1, body.limit)
        self.assertTrue(body.closed)

    def test_data_loader_never_crosses_role_boundary(self):
        calls = []
        load_role_views(packet(), lambda ref: calls.append(ref["key"]) or [])
        self.assertEqual(["train.json", "validation.json"], calls)
        calls.clear(); load_role_views(packet("evaluate"), lambda ref: calls.append(ref["key"]) or [])
        self.assertEqual(["test.json"], calls)

    def test_predict_is_forward_only_and_deterministic(self):
        model = RidgeModel([2.0, -1.0], 0.5, [1.0, 2.0], [2.0, 4.0])
        self.assertEqual([2.5], predict(model, [[3.0, 2.0]]))

    def test_evidence_matches_service_schema(self):
        model = b'{"weights":[1.0],"intercept":0.0}'
        report = build_evidence(packet(), "run-1", 0.2, model, roundtrip=True, load_verified=True)
        self.assertEqual("galatea.evidence/v1", report["schema_version"])
        self.assertEqual({"val_rmse": 0.2}, report["metrics"])
        self.assertEqual(hashlib.sha256(model).hexdigest(), report["artifacts"][0]["sha256"])
        self.assertEqual("not-run", report["final_test_status"])

    def test_evaluator_downloads_champion_without_fitting_and_binds_digest(self):
        from types import SimpleNamespace
        from ray_tabular_regression.model import dumps
        model_bytes = dumps(RidgeModel([1.0, 0.0, 0.0], 0.0, [0.0] * 3, [1.0] * 3))
        binding = packet("evaluate")
        binding["champion_model_sha256"] = hashlib.sha256(model_bytes).hexdigest()

        class Body:
            def read(self, limit=None):
                return json.dumps([{"feature_1": 1, "feature_2": 0, "feature_3": 0,
                                    "target": 1}]).encode()
            def close(self): pass
        test_raw = Body().read()
        binding["views"]["test"].update(sha256=hashlib.sha256(test_raw).hexdigest(),
                                         size_bytes=len(test_raw))
        class S3:
            def get_object(self, **kwargs): return {"VersionId": "v1", "ContentLength": len(test_raw), "Body": Body()}
        class MLflow:
            def __init__(self, root): self.root = root; self.terminated = []
            def create_run(self, *args, **kwargs): return SimpleNamespace(info=SimpleNamespace(run_id="eval-run"))
            def get_run(self, run_id): return SimpleNamespace(info=SimpleNamespace(artifact_uri="mlflow-artifacts:/x"))
            def list_artifacts(self, run_id, parent):
                path = self.root / parent / "model.json"
                return [SimpleNamespace(path=f"{parent}/model.json", is_dir=False, file_size=path.stat().st_size)]
            def log_param(self, *args): pass
            def log_metric(self, *args): pass
            def log_artifact(self, run_id, source, destination):
                target = self.root / destination / Path(source).name
                target.parent.mkdir(exist_ok=True); target.write_bytes(Path(source).read_bytes())
            def download_artifacts(self, run_id, artifact, dst_path):
                source = self.root / artifact; target = Path(dst_path) / artifact
                target.parent.mkdir(parents=True); target.write_bytes(source.read_bytes()); return str(target)
            def set_terminated(self, run_id, status): self.terminated.append(status)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / "model").mkdir(); (root / "model/model.json").write_bytes(model_bytes)
            with patch("ray_tabular_regression.workload.fit_ridge", side_effect=AssertionError("fit forbidden")):
                report = run(binding, {"features": ["feature_1", "feature_2", "feature_3"],
                                       "target": "target", "alpha": 1}, S3(), MLflow(root),
                             admission=_issue_admission(binding))
        self.assertEqual(binding["champion_model_sha256"],
                         report["lineage"]["model_sha256"])

    def test_missing_artifact_proxy_fails_evaluation_run(self):
        class MLflow:
            def create_run(self, *args, **kwargs):
                return type("Run", (), {"info": type("Info", (), {"run_id": "eval-run"})()})()
            def log_param(self, *args): pass
            def get_run(self, run_id): raise ConnectionError("proxy unavailable")
            def set_terminated(self, run_id, status): self.status = status
        client = MLflow()
        with self.assertRaises(ConnectionError):
            run(packet("evaluate"), {"features": [], "target": "target", "alpha": 1}, object(), client,
                admission=_issue_admission(packet("evaluate")))
        self.assertEqual("FAILED", client.status)

    def test_corrupt_roundtrip_cannot_be_marked_verified(self):
        with self.assertRaises(ValueError):
            require_integrity(b"expected", b"corrupt", load_verified=True)
        with self.assertRaises(ValueError):
            require_integrity(b"expected", b"expected", load_verified=False)

    def test_direct_fit_and_run_require_driver_admission(self):
        with self.assertRaises(PermissionError):
            fit_ridge([[1.0]], [1.0], 1.0)
        with self.assertRaises(PermissionError):
            run(packet(), {}, object(), object(), admission=None)

    def test_nondefault_model_path_rejected_before_mlflow_run(self):
        class Client:
            created = False
            def create_run(self, *args, **kwargs): self.created = True
        client = Client()
        with self.assertRaises(ValueError):
            bound = packet() | {"model_artifact_path": "other/model.json"}
            run(bound, {}, object(), client, admission=_issue_admission(bound))
        self.assertFalse(client.created)

    def test_model_download_requires_mlflow_artifact_proxy(self):
        from types import SimpleNamespace
        class Client:
            def get_run(self, run_id):
                return SimpleNamespace(info=SimpleNamespace(artifact_uri="s3://private/model"))
        with self.assertRaises(ValueError):
            download_proxy_artifact(Client(), "run", "model/model.json", 1024)

    def test_release_build_is_deterministic_and_external(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "release.zip"
            first = build_release(ROOT, output, self.public)
            second = build_release(ROOT, Path(temp) / "release-second.zip", self.public)
            self.assertEqual(first["sha256"], second["sha256"])
            self.assertTrue(output.is_file())
            with self.assertRaises(FileExistsError):
                build_release(ROOT, output, self.public)
            with self.assertRaises(ValueError):
                build_release(ROOT, ROOT / "release.zip", self.public)

    def test_release_excludes_caches_symlinks_and_has_registry_json_config(self):
        import zipfile
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "release.zip"
            release = build_release(ROOT, output, self.public)
            with zipfile.ZipFile(output) as archive:
                self.assertIn("configs/baseline.json", archive.namelist())
                self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in archive.namelist()))

    def test_registration_generation_binds_real_release_and_config_digests(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "build" / "release.zip"
            release = build_release(ROOT, output, self.public)
            registration_root = Path(temp) / "registration"
            project, campaign = write_registration_examples(ROOT, registration_root, release, "env-1")
            project_data = json.loads(project.read_text())
            campaign_data = json.loads(campaign.read_text())
            self.assertEqual(release["sha256"], project_data["releases"]["trainer-v1"]["sha256"])
            self.assertEqual(2, len(project_data["releases"]))
            self.assertEqual("evaluator-v1", campaign_data["slots"][-1]["release_ids"][0])

            service_src = ROOT.parents[1] / "services" / "galatea-mcp" / "src"
            sys.path.insert(0, str(service_src))
            try:
                from galatea_mcp.projects import Registry
                class Objects:
                    verify = verify_metadata = lambda self, ref: True
                registry = Registry({"schema_version": "galatea.registry/v1",
                                     "projects": [project_data]}, Objects())
                project_obj, config_obj, release_obj, _ = registry.verify(
                    "ray-tabular-regression", "baseline", "trainer-v1", "trial")
                from galatea_mcp.backends.binding import BindingSigner
                operation = {"campaign_id": "c", "operation_id": "op", "submission_id": "sub",
                             "step_id": "evaluate", "attempt": 1, "role": "evaluate",
                             "config_id": "baseline", "release_id": "evaluator-v1",
                             "readiness_digest": "a" * 64, "deadline_at": 4102444800,
                             "candidate_id": "candidate", "champion_run_id": "run",
                             "champion_model_sha256": "b" * 64, "metadata": {}}
                signed = BindingSigner(self.key, tracking_uri="https://mlflow.invalid", role_env={},
                                       ray_addresses={"trainer": "http://trainer", "evaluator": "http://evaluator"})(
                                           operation, project_obj, config_obj,
                                           project_obj.releases["evaluator-v1"])
                packet_from_signer = json.loads(signed["GALATEA_EXECUTION_BINDING"])
                self.assertEqual("b" * 64, packet_from_signer["champion_model_sha256"])
                self.assertNotIn("champion_model_sha256", packet_from_signer["metadata"])
            finally:
                sys.path.remove(str(service_src))


if __name__ == "__main__":
    unittest.main()
