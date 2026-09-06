import ast
import importlib.util
from pathlib import Path
import unittest


class DriverWorkerBoundaryTests(unittest.TestCase):
    @staticmethod
    def _driver_module():
        path = Path(__file__).resolve().parents[1] / "scripts/submit_train.py"
        spec = importlib.util.spec_from_file_location("llm_lora_governed_driver", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_submit_entrypoint_has_one_driver_owner_boundary(self):
        path = Path(__file__).resolve().parents[1] / "scripts/submit_train.py"
        tree = ast.parse(path.read_text())
        names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
        self.assertIn("run_driver", names)
        self.assertIn("run_worker", names)
        source = path.read_text()
        driver = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "run_driver")
        worker = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "run_worker")
        driver_text = ast.get_source_segment(source, driver)
        worker_text = ast.get_source_segment(source, worker)
        self.assertIn("start_training_run", driver_text)
        self.assertNotIn("start_training_run", worker_text)

    def test_direct_training_entrypoint_is_fail_closed(self):
        path = Path(__file__).resolve().parents[1] / "scripts/train_lora.py"
        source = path.read_text()
        self.assertIn("actual training requires Galatea plan/authorization", source)

    def test_legacy_full_baseline_entrypoint_is_fail_closed(self):
        path = Path(__file__).resolve().parents[1] / "scripts/wechat_full_baseline.py"
        source = path.read_text()
        self.assertIn("local full-dataset baseline execution is disabled", source)
        self.assertNotIn("start_training_run", source)

    def test_ray_submit_includes_real_run_flag(self):
        path = Path(__file__).resolve().parents[1] / "src/llm_lora_playground/ray_runtime.py"
        source = path.read_text()
        self.assertIn('"--run"', source)
        self.assertIn("RAY_JOB_SUBMISSION_ID", source)

    def test_driver_requires_complete_galatea_authorization_metadata(self):
        path = Path(__file__).resolve().parents[1] / "scripts/submit_train.py"
        source = path.read_text()
        self.assertIn("_require_governed_ray_metadata", source)
        self.assertIn("galatea.readiness.digest", source)
        self.assertIn("galatea.execution.mode=governed-ray-job", source)
        self.assertIn("Galatea project metadata does not belong to llm-lora-playground", source)
        self.assertIn('"candidate_evidence_digest": candidate_evidence_digest', source)

    def test_ray_wrapper_rejects_real_submission_without_galatea(self):
        from llm_lora_playground.ray_runtime import submit_job
        with self.assertRaisesRegex(RuntimeError, "Galatea"):
            submit_job(
                Path("configs/ray-job-smoke.yaml"),
                "http://127.0.0.1:8265",
                {"py_executable": "/data/conda/envs/llm-lora-ray-py312/bin/python"},
                submission_id="unauthorized",
                dry_run=False,
            )

    def test_driver_metadata_gate_has_no_partial_authorization_path(self):
        path = Path(__file__).resolve().parents[1] / "scripts/submit_train.py"
        tree = ast.parse(path.read_text())
        gate = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_require_governed_ray_metadata")
        source = path.read_text()
        gate_text = ast.get_source_segment(source, gate)
        for key in (
            "galatea.execution.identity", "galatea.project", "galatea.release.id",
            "galatea.submission.id", "galatea.readiness.digest", "galatea.execution.mode",
            "galatea.promotable", "role", "attempt",
        ):
            self.assertIn(key, gate_text)

    def test_driver_rejects_each_missing_or_mismatched_binding(self):
        gate = self._driver_module()._require_governed_ray_metadata
        metadata = {
            "galatea.execution.identity": "sha256:" + "a" * 64,
            "galatea.project": "llm-lora-playground",
            "galatea.release.id": "release-1",
            "galatea.submission.id": "submission-1",
            "galatea.readiness.digest": "sha256:" + "b" * 64,
            "galatea.execution.mode": "governed-ray-job",
            "galatea.promotable": "false",
            "role": "smoke",
            "attempt": "smoke-contract-1",
        }
        for key in tuple(metadata):
            with self.subTest(missing=key), self.assertRaises(RuntimeError):
                gate(
                    {name: value for name, value in metadata.items() if name != key},
                    "submission-1",
                    expected_role="smoke",
                    expected_promotable=False,
                )
        with self.assertRaisesRegex(RuntimeError, "role"):
            gate(metadata, "submission-1", expected_role="trial", expected_promotable=False)
        with self.assertRaisesRegex(RuntimeError, "promotability"):
            gate(metadata, "submission-1", expected_role="smoke", expected_promotable=True)


if __name__ == "__main__":
    unittest.main()
