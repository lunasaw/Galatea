import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona.capacity import CapacityContractError, compare_model_configs, validate_qlora_preflight
from wechat_persona.runtime import load_project_config


class CapacityPreflightTests(unittest.TestCase):
    def test_17b_changes_only_model_and_budget(self):
        base = load_project_config(ROOT / "configs/persona-lora-smoke.yaml")
        larger = load_project_config(ROOT / "configs/qwen3-1.7b-lora.yaml")
        report = compare_model_configs(base, larger)
        self.assertEqual({"model", "resources", "task", "run"}, set(report["changed_sections"]))

    def test_qlora_requires_capacity_bottleneck_and_complete_environment(self):
        config = load_project_config(ROOT / "configs/qlora-4b.yaml")
        with self.assertRaises(CapacityContractError):
            validate_qlora_preflight(config, {"capacity_bottleneck_confirmed": False})
        evidence = {"capacity_bottleneck_confirmed": True, "bitsandbytes_revision": "0.49.2", "torch_revision": "2.11.0", "transformers_revision": "5.16.1", "cuda_revision": "13.0", "gpu_model": "fixture", "forward_passed": True, "backward_steps": 2, "adapter_roundtrip_verified": True, "cpu_fallback": False}
        self.assertTrue(validate_qlora_preflight(config, evidence))


if __name__ == "__main__": unittest.main()
