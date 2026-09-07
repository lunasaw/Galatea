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
        }
        self.assertEqual(expected, {p.name for p in (ROOT / "configs").glob("*.yaml")})
        for path in (ROOT / "configs").glob("*.yaml"):
            self.assertEqual([], validate_project_config(load_project_config(path)), path.name)

    def test_training_config_requires_governed_bindings(self):
        config = load_project_config(ROOT / "configs/persona-lora-smoke.yaml")
        with self.assertRaisesRegex(ValueError, "release_id"):
            validate_project_config({**config, "execution": {**config["execution"], "release_id": ""}}, raise_on_error=True)

    def test_no_secrets_or_raw_paths_in_configs(self):
        for path in (ROOT / "configs").glob("*.yaml"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("password", text.lower())
            self.assertNotIn("token:", text.lower())
            self.assertNotIn("/data/", text)


if __name__ == "__main__":
    unittest.main()
