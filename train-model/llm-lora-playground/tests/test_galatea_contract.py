from __future__ import annotations

from pathlib import Path
import json
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class GalateaContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = yaml.safe_load((ROOT / "galatea.project.yaml").read_text(encoding="utf-8"))
        cls.spec = cls.manifest["spec"]

    def test_manifest_uses_fixed_ray_driver_entrypoint(self):
        self.assertEqual("ray", self.spec["executionBackend"])
        self.assertIn("train", self.spec["entrypoints"])
        self.assertNotIn("run", self.spec["entrypoints"])
        self.assertEqual("scripts/submit_train.py", self.spec["entrypoints"]["train"][1])
        self.assertEqual("--plan", self.spec["entrypoints"]["plan"][-1])

    def test_manifest_declares_full_compatibility_and_evidence(self):
        self.assertEqual(
            {"task", "datasetDigest", "splitDigest", "preprocessingVersion", "metricDefinition", "evaluationProtocol", "role"},
            set(self.spec["compatibility"]),
        )
        self.assertFalse(self.spec["capabilities"]["pauseResume"])
        evidence = self.spec["runEvidence"]
        self.assertEqual({"source": "param", "key": "data.split_sha256"}, evidence["compatibility"]["splitDigest"])
        self.assertEqual("succeeded", evidence["requiredTags"]["run.outcome"])

    def test_job_schema_requires_release_and_readiness_binding(self):
        path = ROOT.parents[1] / "doc/train-llm/2026-09-05-project-2-4-toy-lora-ray/schemas/job-metadata.schema.json"
        schema = json.loads(path.read_text(encoding="utf-8"))
        required = set(schema["required"])
        self.assertTrue({"release_id", "readiness_digest", "execution_identity"}.issubset(required))
        self.assertEqual("governed-ray-job", schema["properties"]["execution_mode"]["const"])


if __name__ == "__main__":
    unittest.main()
