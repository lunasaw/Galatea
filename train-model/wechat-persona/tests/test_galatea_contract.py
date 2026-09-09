import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class GalateaContractTests(unittest.TestCase):
    def setUp(self):
        self.spec = yaml.safe_load((ROOT / "galatea.project.yaml").read_text(encoding="utf-8"))["spec"]

    def test_fixed_ray_driver_and_objective(self):
        self.assertEqual("ray", self.spec["executionBackend"])
        self.assertEqual(["python", "scripts/submit_train.py", "--run"], self.spec["entrypoints"]["train"])
        self.assertEqual({"metric": "val_loss", "direction": "min"}, self.spec["objective"])
        self.assertFalse(self.spec["capabilities"]["pauseResume"])

    def test_compatibility_and_artifact_gate_are_explicit(self):
        self.assertEqual({"task", "datasetDigest", "splitDigest", "preprocessingVersion", "metricDefinition", "evaluationProtocol", "role"}, set(self.spec["compatibility"]))
        evidence = self.spec["runEvidence"]
        self.assertEqual("dataset.split_sha256", evidence["compatibility"]["splitDigest"]["key"])
        self.assertEqual("true", evidence["requiredTags"]["artifact.roundtrip_verified"])
        self.assertIn("model/adapter_model.safetensors", evidence["stageArtifacts"]["training-optimization"])


if __name__ == "__main__": unittest.main()
