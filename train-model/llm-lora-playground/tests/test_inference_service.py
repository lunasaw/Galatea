from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from llm_lora_playground.inference_service import validate_inference_config


ROOT = Path(__file__).resolve().parents[1]


class InferenceServiceContractTests(unittest.TestCase):
    def test_experimental_checkpoint_config_is_immutable_and_non_promotable(self):
        result = validate_inference_config(ROOT / "configs/inference-ray.yaml")
        values = result["values"]
        self.assertEqual(result["checkpoint"].step, 944)
        self.assertEqual(values["governance"]["role"], "trial")
        self.assertFalse(values["governance"]["promotable"])
        self.assertEqual(values["governance"]["test_access"], "untouched")
        self.assertEqual(values["model"]["engine"], "transformers-peft-ray-serve")
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


if __name__ == "__main__":
    unittest.main()
