from pathlib import Path
import unittest

from llm_lora_playground.config import (
    TrainingConfig,
    canonical_training_config_digest,
    load_training_config,
    validate_training_config,
)


ROOT = Path(__file__).resolve().parents[1]


class ToyConfigTests(unittest.TestCase):
    def test_smoke_and_baseline_configs_are_explicit(self):
        smoke = load_training_config(ROOT / "configs/toy-lora-smoke.yaml")
        baseline = load_training_config(ROOT / "configs/toy-lora-baseline.yaml")
        self.assertEqual(validate_training_config(smoke), [])
        self.assertEqual(validate_training_config(baseline), [])
        self.assertEqual(smoke.values["run_kind"], "smoke")
        self.assertEqual(smoke.values["training"]["max_steps"], 10)
        self.assertEqual(baseline.values["run_kind"], "baseline")
        self.assertEqual(baseline.values["training"]["epochs"], 1)
        self.assertNotEqual(
            canonical_training_config_digest(smoke),
            canonical_training_config_digest(baseline),
        )

    def test_invalid_contract_is_rejected(self):
        values = load_training_config(ROOT / "configs/toy-lora-smoke.yaml").values
        for section, key, value in (
            ("model", "dtype", "float32"),
            ("model", "device", "cpu"),
            ("resources", "num_gpus", 2),
            ("data", "assistant_only_loss", False),
        ):
            candidate = {**values, section: {**values[section], key: value}}
            errors = validate_training_config(TrainingConfig(candidate, ROOT / "x.yaml"))
            self.assertTrue(errors, (section, key))

        candidate = {**values, "lora": {**values["lora"], "target_modules": []}}
        self.assertTrue(validate_training_config(TrainingConfig(candidate, ROOT / "x.yaml")))

    def test_config_rejects_secret_like_values(self):
        values = load_training_config(ROOT / "configs/toy-lora-smoke.yaml").values
        candidate = {**values, "tracking": {"token": "secret"}}
        errors = validate_training_config(TrainingConfig(candidate, ROOT / "x.yaml"))
        self.assertTrue(any("secret" in error.lower() for error in errors))


if __name__ == "__main__":
    unittest.main()
