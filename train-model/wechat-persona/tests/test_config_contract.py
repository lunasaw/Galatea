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
            "formal-sft-v2-evaluate.yaml", "rag-private-hybrid.yaml",
            "gpt-prelabel-v1-baseline.yaml", "daily-memory-gpt-v1.yaml", "daily-topic-sft-v1.yaml",
            "daily-topic-sft-v2.yaml",
            "daily-topic-sft-v3.yaml",
            "topic-evidence-audit-v1.yaml",
            "topic-cross-review-v1.yaml",
            "topic-adjudication-v1.yaml",
            "topic-axes-review-v2.yaml",
            "topic-axes-review-v3.yaml",
            "topic-axes-review-v3-sol.yaml",
            "topic-axes-review-v4.yaml",
            "topic-axes-review-v4-gpt6.yaml",
            "topic-axes-recovery-v4.yaml",
            "topic-axes-singleton-v4.yaml",
            "topic-validation-v1.yaml",
            "topic-validation-review-v1.yaml",
            "topic-validation-duplicates-v1.yaml",
            "topic-validation-recovery-v1.yaml",
            "topic-validation-http-diagnostic-v1.yaml",
            "topic-validation-completion-v1.yaml",
            "topic-blind-reference-v1.yaml",
            "topic-blind-reference-opus-v1.yaml",
            "topic-blind-reference-index-recovery-v1.yaml",
            "topic-train-repair-v1.yaml",
            "topic-repair-review-v1.yaml",
            "topic-repair-recovery-v1.yaml",
            "topic-repair-recovery-v2.yaml",
            "topic-repair-review-gpt6-v1.yaml",
        }
        self.assertEqual(expected, {p.name for p in (ROOT / "configs").glob("*.yaml")})
        for path in (ROOT / "configs").glob("*.yaml"):
            if path.name == "topic-repair-review-gpt6-v1.yaml":
                from wechat_persona.topic_repair_review_gpt6 import load_config
                self.assertFalse(load_config(path)["governance"]["training_run"])
                continue
            if path.name == "topic-repair-recovery-v2.yaml":
                from wechat_persona.topic_repair_recovery_v2 import load_config
                self.assertFalse(load_config(path)["governance"]["training_run"])
                continue
            if path.name == "topic-repair-recovery-v1.yaml":
                from wechat_persona.topic_repair_recovery import load_config
                self.assertFalse(load_config(path)["governance"]["training_run"])
                continue
            if path.name == "topic-repair-review-v1.yaml":
                from wechat_persona.topic_repair_review import load_config
                self.assertFalse(load_config(path)["governance"]["training_run"])
                continue
            if path.name == "topic-train-repair-v1.yaml":
                from wechat_persona.topic_train_repair import load_config
                self.assertFalse(load_config(path)["governance"]["training_run"])
                continue
            if path.name == "topic-blind-reference-index-recovery-v1.yaml":
                from wechat_persona.topic_blind_reference_recovery import load_config
                self.assertFalse(load_config(path)["governance"]["training_run"])
                continue
            if path.name == "topic-blind-reference-opus-v1.yaml":
                from wechat_persona.topic_blind_reference_opus import load_config
                self.assertFalse(load_config(path)["governance"]["training_run"])
                continue
            if path.name == "topic-blind-reference-v1.yaml":
                from wechat_persona.topic_blind_reference import load_config
                self.assertFalse(load_config(path)["governance"]["training_run"])
                continue
            if path.name == "topic-validation-completion-v1.yaml":
                from wechat_persona.topic_validation_completion import load_config
                self.assertFalse(load_config(path)["governance"]["training_run"])
                continue
            if path.name == "topic-validation-http-diagnostic-v1.yaml":
                from wechat_persona.topic_validation_http_diagnostic import load_config
                self.assertFalse(load_config(path)["governance"]["training_run"])
                continue
            if path.name == "topic-validation-recovery-v1.yaml":
                from wechat_persona.topic_validation_recovery import load_config
                self.assertFalse(load_config(path)["governance"]["training_run"])
                continue
            if path.name == "topic-validation-duplicates-v1.yaml":
                from wechat_persona.topic_validation_duplicates import load_policy
                self.assertFalse(load_policy(path)["governance"]["training_run"])
                continue
            if path.name == "topic-validation-review-v1.yaml":
                from wechat_persona.topic_validation_review import load_config
                self.assertFalse(load_config(path)["governance"]["training_run"])
                continue
            if path.name == "topic-validation-v1.yaml":
                from wechat_persona.topic_validation import load_policy
                self.assertFalse(load_policy(path)["governance"]["training_run"])
                continue
            if path.name == "topic-axes-singleton-v4.yaml":
                from wechat_persona.topic_axes_singleton_v4 import load_config
                self.assertFalse(load_config(path)["governance"]["training_run"])
                continue
            if path.name == "topic-axes-recovery-v4.yaml":
                from wechat_persona.topic_axes_recovery_v4 import load_config
                self.assertFalse(load_config(path)["governance"]["training_run"])
                continue
            if path.name == "topic-axes-review-v4.yaml":
                from wechat_persona.topic_axes_review_v4 import load_config
                self.assertFalse(load_config(path)["governance"]["training_run"])
                continue
            if path.name == "topic-axes-review-v4-gpt6.yaml":
                from wechat_persona.topic_axes_review_gpt6 import load_config
                self.assertFalse(load_config(path)["governance"]["training_run"])
                continue
            if path.name in {"topic-axes-review-v3.yaml", "topic-axes-review-v3-sol.yaml"}:
                from wechat_persona.topic_axes_review_v3 import load_config
                self.assertFalse(load_config(path)["governance"]["training_run"])
                continue
            if path.name == "topic-axes-review-v2.yaml":
                from wechat_persona.topic_axes_review import load_config
                self.assertFalse(load_config(path)["governance"]["training_run"])
                continue
            if path.name == "topic-adjudication-v1.yaml":
                from wechat_persona.topic_adjudication import load_policy
                self.assertFalse(load_policy(path)["governance"]["training_run"])
                continue
            if path.name == "topic-cross-review-v1.yaml":
                from wechat_persona.topic_cross_review import load_config
                self.assertFalse(load_config(path)["governance"]["training_run"])
                continue
            if path.name == "topic-evidence-audit-v1.yaml":
                from wechat_persona.topic_evidence_audit import load_config
                self.assertFalse(load_config(path)["governance"]["training_run"])
                continue
            if path.name in {"daily-topic-sft-v1.yaml", "daily-topic-sft-v2.yaml", "daily-topic-sft-v3.yaml"}:
                from wechat_persona.topic_candidates import load_policy
                self.assertEqual(["train"], load_policy(path)["allowed_splits"])
                continue
            if path.name == "daily-memory-gpt-v1.yaml":
                config = load_project_config(path)
                self.assertEqual("wechat-persona-memory-daily-v1", config["schema_version"])
                self.assertEqual("candidate", config["governance"]["result_status"])
                self.assertFalse(config["governance"]["training_run"])
                self.assertEqual(
                    "compatible_constraints_v1",
                    config["extraction"]["wire_schema_mode"],
                )
                continue
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
