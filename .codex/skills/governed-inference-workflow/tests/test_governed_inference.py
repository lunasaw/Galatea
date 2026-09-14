from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import yaml


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "governed_inference.py"
SPEC = importlib.util.spec_from_file_location("governed_inference", SCRIPT)
assert SPEC and SPEC.loader
governed_inference = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(governed_inference)


class GovernedInferenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "train-model" / "fixture"
        (self.project / "configs").mkdir(parents=True)
        (self.project / "scripts").mkdir()
        (self.project / "configs" / "inference.yaml").write_text(
            "service: fixture\n", encoding="utf-8"
        )
        checker = (
            "import json\n"
            "print(json.dumps({'status':'ok','config_digest':'a'*64,"
            "'model_manifest_sha256':'b'*64,'checkpoint_step':7}))\n"
        )
        (self.project / "scripts" / "serve.py").write_text(checker, encoding="utf-8")
        manifest = {
            "apiVersion": "galatea/v1",
            "kind": "TrainingProject",
            "metadata": {"name": "fixture"},
            "spec": {
                "executionBackend": "ray",
                "entrypoints": {
                    "inferenceCheck": [
                        "/usr/bin/python3",
                        "scripts/serve.py",
                        "--config",
                        "{config}",
                        "--check-config",
                    ],
                    "inference": [
                        "/usr/bin/python3",
                        "scripts/serve.py",
                        "--config",
                        "{config}",
                        "--run",
                    ],
                },
            },
        }
        (self.project / "galatea.project.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
        )
        self.release_dir = self.root / "releases" / "fixture-release"
        self.release_dir.mkdir(parents=True)
        archive = self.release_dir / "working-dir.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.write(
                self.project / "configs" / "inference.yaml",
                "configs/inference.yaml",
            )
            bundle.write(self.project / "scripts" / "serve.py", "scripts/serve.py")
        wheel = self.release_dir / "fixture.whl"
        wheel.write_bytes(b"wheel")
        release = {
            "schema_version": 1,
            "project": "fixture",
            "release_id": "fixture-release",
            "runtime_env": {
                "working_dir": "s3://training-data/releases/working-dir.zip",
                "py_modules": ["s3://training-data/releases/fixture.whl"],
                "py_executable": "/usr/bin/python3",
                "env_vars": {"RAYLLM_ENABLE_REQUEST_PROMPT_LOGS": "0"},
            },
            "files": {
                "working_dir": {
                    "filename": archive.name,
                    "key": "releases/working-dir.zip",
                    "sha256": governed_inference._sha256_file(archive),
                    "size_bytes": archive.stat().st_size,
                },
                "py_module": {
                    "filename": wheel.name,
                    "key": "releases/fixture.whl",
                    "sha256": governed_inference._sha256_file(wheel),
                    "size_bytes": wheel.stat().st_size,
                },
            },
        }
        self.release_manifest = self.release_dir / "release.json"
        self.release_manifest.write_text(json.dumps(release), encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def build_plan(self):
        return governed_inference.build_plan(
            project_root=self.project,
            config_relative="configs/inference.yaml",
            release_manifest_path=self.release_manifest,
            attempt="fixture-v1",
            ray_address="http://127.0.0.1:8265",
        )

    def test_plan_binds_release_preflight_and_submission(self):
        plan = self.build_plan()
        self.assertEqual(plan["schema_version"], governed_inference.PLAN_SCHEMA)
        self.assertEqual(plan["release_id"], "fixture-release")
        self.assertEqual(plan["preflight"]["config_digest"], "a" * 64)
        self.assertTrue(plan["execution_identity"].startswith("sha256:"))
        self.assertTrue(plan["readiness_evidence_digest"].startswith("sha256:"))
        self.assertIn(plan["execution_identity"][7:19], plan["submission_id"])

    def test_plan_rejects_config_different_from_release(self):
        (self.project / "configs" / "inference.yaml").write_text(
            "service: changed\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(governed_inference.GovernedInferenceError, "differs"):
            self.build_plan()

    def test_plan_rejects_tampered_release_file(self):
        with (self.release_dir / "working-dir.zip").open("ab") as handle:
            handle.write(b"tampered")
        with self.assertRaisesRegex(governed_inference.GovernedInferenceError, "verification failed"):
            self.build_plan()

    def test_plan_rejects_release_without_wheel_runtime_binding(self):
        release = json.loads(self.release_manifest.read_text(encoding="utf-8"))
        del release["runtime_env"]["py_modules"]
        self.release_manifest.write_text(json.dumps(release), encoding="utf-8")
        with self.assertRaisesRegex(governed_inference.GovernedInferenceError, "py_modules"):
            self.build_plan()

    def test_existing_job_requires_exact_governed_metadata(self):
        plan = self.build_plan()
        job = {
            "submission_id": plan["submission_id"],
            "metadata": governed_inference._expected_metadata(plan),
        }
        governed_inference._verify_existing_job(job, plan)
        job["metadata"]["galatea.release.id"] = "other"
        with self.assertRaisesRegex(governed_inference.GovernedInferenceError, "release.id"):
            governed_inference._verify_existing_job(job, plan)

    def test_authorization_digest_requires_prefix(self):
        with self.assertRaisesRegex(governed_inference.GovernedInferenceError, "prefix"):
            governed_inference._require_sha256("a" * 64, "authorization", prefixed=True)


if __name__ == "__main__":
    unittest.main()
