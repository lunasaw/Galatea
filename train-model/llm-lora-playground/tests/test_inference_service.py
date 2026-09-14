from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from llm_lora_playground.inference_service import validate_inference_binding, validate_inference_config, validate_model_adapter_compatibility


ROOT = Path(__file__).resolve().parents[1]


class InferenceServiceContractTests(unittest.TestCase):
    def test_experimental_checkpoint_config_is_immutable_and_non_promotable(self):
        result = validate_inference_config(ROOT / "configs/inference-ray.yaml")
        values = result["values"]
        self.assertEqual(result["checkpoint"].step, 9353)
        self.assertEqual(values["model"]["source_mlflow_run_id"], "fd1509c6b1824132941f9909b022fa68")
        self.assertEqual(values["governance"]["role"], "trial")
        self.assertFalse(values["governance"]["promotable"])
        self.assertEqual(values["governance"]["test_access"], "historical_test_included_in_memory_corpus")
        self.assertEqual(values["governance"]["future_holdout_policy"], "post_index_temporal")
        self.assertEqual(values["model"]["engine"], "ray-serve-llm-vllm")
        self.assertEqual(values["model"]["expected_architecture"], "qwen3_5_conditional_generation")
        self.assertIn("像女友一样聊天", values["prompt"]["persona_system_prompt"])
        self.assertTrue(values["model"]["lora_loading_path"].startswith("s3://"))
        self.assertEqual(values["memory"]["backend"], "hybrid-bm25-bge")
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

    def test_conditional_adapter_is_compatible_with_conditional_serving(self):
        preflight = validate_inference_config(ROOT / "configs/inference-ray.yaml")
        compatibility = validate_model_adapter_compatibility(preflight)
        self.assertEqual(compatibility["base_architecture"], "Qwen3_5ForConditionalGeneration")
        self.assertEqual(compatibility["adapter_architecture"], "qwen3_5_conditional_generation")

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
            "RAYLLM_ENABLE_REQUEST_PROMPT_LOGS": "0",
        })
        self.assertEqual(binding["release_id"], "2f43042e31363a149af2")

    def test_inference_rejects_prompt_logging(self):
        environment = {
            "GALATEA_PROJECT": "llm-lora-playground",
            "GALATEA_RELEASE_ID": "2f43042e31363a149af2",
            "GALATEA_READINESS_DIGEST": "sha256:" + "a" * 64,
            "GALATEA_EXECUTION_IDENTITY": "sha256:" + "b" * 64,
            "GALATEA_SUBMISSION_ID": "llm-lora-inference-test",
            "GALATEA_EXECUTION_MODE": "governed-ray-serve-inference",
            "GALATEA_INFERENCE_AUTHORIZED": "true",
            "RAYLLM_ENABLE_REQUEST_PROMPT_LOGS": "1",
        }
        with self.assertRaisesRegex(ValueError, "RAYLLM_ENABLE_REQUEST_PROMPT_LOGS"):
            validate_inference_binding(environment=environment)


if __name__ == "__main__":
    unittest.main()
