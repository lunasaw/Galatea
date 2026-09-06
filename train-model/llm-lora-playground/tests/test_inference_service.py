from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from llm_lora_playground.inference_service import validate_inference_binding, validate_inference_config


ROOT = Path(__file__).resolve().parents[1]


class InferenceServiceContractTests(unittest.TestCase):
    def test_experimental_checkpoint_config_is_immutable_and_non_promotable(self):
        result = validate_inference_config(ROOT / "configs/inference-ray.yaml")
        values = result["values"]
        self.assertEqual(result["checkpoint"].step, 944)
        self.assertEqual(values["governance"]["role"], "trial")
        self.assertFalse(values["governance"]["promotable"])
        self.assertEqual(values["governance"]["test_access"], "untouched")
        self.assertEqual(values["model"]["engine"], "ray-serve-llm-vllm")
        self.assertTrue(values["model"]["lora_loading_path"].startswith("s3://"))
        self.assertEqual(len(result["model_manifest_sha256"]), 64)

    def test_config_rejects_public_bind(self):
        import yaml

        source = yaml.safe_load((ROOT / "configs/inference-ray.yaml").read_text())
        source["service"]["host"] = "0.0.0.0"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.yaml"
            path.write_text(yaml.safe_dump(source, allow_unicode=True))
            with self.assertRaisesRegex(ValueError, "loopback"):
                validate_inference_config(path)

    def test_config_rejects_adapter_base_mismatch(self):
        import yaml

        source = yaml.safe_load((ROOT / "configs/inference-ray.yaml").read_text())
        source["model"]["base_model_path"] = "/tmp"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.yaml"
            path.write_text(yaml.safe_dump(source, allow_unicode=True))
            with self.assertRaises(ValueError):
                validate_inference_config(path)

    def test_inference_requires_external_galatea_binding(self):
        with self.assertRaisesRegex(ValueError, "Galatea authorization binding"):
            validate_inference_binding(environment={})

    def test_inference_binding_is_explicit_and_typed(self):
        binding = validate_inference_binding(environment={
            "GALATEA_PROJECT": "llm-lora-playground",
            "GALATEA_RELEASE_ID": "2f43042e31363a149af2",
            "GALATEA_READINESS_DIGEST": "sha256:" + "a" * 64,
            "GALATEA_EXECUTION_IDENTITY": "sha256:" + "b" * 64,
            "GALATEA_SUBMISSION_ID": "llm-lora-inference-test",
            "GALATEA_EXECUTION_MODE": "governed-ray-serve-inference",
            "GALATEA_INFERENCE_AUTHORIZED": "true",
        })
        self.assertEqual(binding["release_id"], "2f43042e31363a149af2")


if __name__ == "__main__":
    unittest.main()
