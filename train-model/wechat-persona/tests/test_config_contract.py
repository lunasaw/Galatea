import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona.runtime import load_project_config, validate_project_config


class ConfigContractTests(unittest.TestCase):
    def test_all_design_configs_exist_and_are_valid(self):
        expected = {
            "import.yaml", "rag-bm25.yaml", "rag-embedding.yaml",
            "persona-lora-smoke.yaml", "persona-lora-baseline.yaml",
            "qwen3-1.7b-lora.yaml", "qlora-4b.yaml", "local-chat.yaml", "screenplay.yaml",
            "formal-sft-v2-baseline.yaml", "formal-sft-v2-trial.yaml",
            "formal-sft-v2-champion.yaml", "formal-sft-v2-champion-trial.yaml",
            "formal-sft-v2-evaluate.yaml",
        }
        self.assertEqual(expected, {p.name for p in (ROOT / "configs").glob("*.yaml")})
        for path in (ROOT / "configs").glob("*.yaml"):
            self.assertEqual([], validate_project_config(load_project_config(path)), path.name)

    def test_training_config_requires_governed_backend_resources(self):
        config = load_project_config(ROOT / "configs/persona-lora-smoke.yaml")
        with self.assertRaisesRegex(ValueError, "execution.backend"):
            validate_project_config({**config, "execution": {**config["execution"], "backend": "local"}}, raise_on_error=True)

    def test_formal_roles_and_revisions_are_explicit(self):
        for name, role in (("baseline", "baseline"), ("trial", "trial"),
                           ("champion", "champion"), ("champion-trial", "champion"),
                           ("evaluate", "evaluate")):
            config = load_project_config(ROOT / "configs" / f"formal-sft-v2-{name}.yaml")
            self.assertEqual(role, config["run"]["role"])
            self.assertRegex(config["model"]["model_revision"], r"^[a-f0-9]{40}$")
            self.assertEqual(config["model"]["model_revision"], config["model"]["tokenizer_revision"])
            self.assertEqual(
                "qwen3_5_conditional_generation",
                config["model"]["architecture"],
            )

    def test_qwen35_rejects_implicit_or_causal_lm_architecture(self):
        config = load_project_config(ROOT / "configs/formal-sft-v2-baseline.yaml")
        for architecture in (None, "qwen3_5_causal_lm"):
            model = {**config["model"]}
            if architecture is None:
                model.pop("architecture")
            else:
                model["architecture"] = architecture
            errors = validate_project_config({**config, "model": model})
            self.assertIn(
                "Qwen/Qwen3.5-0.8B requires "
                "model.architecture=qwen3_5_conditional_generation",
                errors,
            )

    def test_no_secrets_or_raw_paths_in_configs(self):
        for path in (ROOT / "configs").glob("*.yaml"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("password", text.lower())
            self.assertNotIn("token:", text.lower())
            self.assertNotIn("/data/", text)


if __name__ == "__main__":
    unittest.main()
