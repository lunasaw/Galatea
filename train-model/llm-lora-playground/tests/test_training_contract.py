from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from llm_lora_playground.config import load_training_config
from llm_lora_playground.datasets import load_samples, partition_samples
from llm_lora_playground.planning import build_training_plan
from llm_lora_playground.planning import resolve_data_path, resolve_output_root
from llm_lora_playground.training import _build_update_groups
from llm_lora_playground.training import TrainingContractError, train
from llm_lora_playground.runtime import environment_digest


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DATASET = ROOT.parents[1] / "platform-data/llm-private/wechat-owner-bulk-approved-experiments/wechat_aa807aaad90dc4463964-20260905T151759Z/dataset.jsonl"


class TrainingContractTests(unittest.TestCase):
    def test_private_source_splits_are_preserved_and_test_is_isolated(self):
        config = load_training_config(ROOT / "configs/wechat-owner-bulk-approved-5k.yaml")
        samples = list(load_samples(PRIVATE_DATASET))
        splits = partition_samples(samples, config.values["data"])
        self.assertEqual({"train": 3776, "validation": 759, "test": 465}, splits.counts)
        self.assertEqual(config.values["data"]["split_sha256"], splits.digest)
        train_ids = set(splits.sample_ids_by_split["train"])
        validation_ids = set(splits.sample_ids_by_split["validation"])
        test_ids = set(splits.sample_ids_by_split["test"])
        self.assertFalse(train_ids & validation_ids)
        self.assertFalse(train_ids & test_ids)
        self.assertFalse(validation_ids & test_ids)

    def test_gradient_accumulation_changes_optimizer_step_count(self):
        groups = _build_update_groups(10, epochs=1, batch_size=2, accumulation_steps=2, seed=42)
        self.assertEqual(3, len(groups))
        self.assertEqual([4, 4, 2], [sum(len(batch) for batch in group) for group in groups])

    def test_release_runtime_uses_stable_repository_paths(self):
        config = load_training_config(ROOT / "configs/wechat-owner-bulk-approved-5k.yaml")
        previous = __import__("os").environ.get("GALATEA_REPOSITORY_ROOT")
        __import__("os").environ["GALATEA_REPOSITORY_ROOT"] = str(ROOT.parents[1])
        try:
            self.assertEqual(PRIVATE_DATASET.resolve(), resolve_data_path(config))
            self.assertTrue(resolve_output_root(config).is_relative_to(ROOT.parents[1] / "platform-data"))
        finally:
            if previous is None:
                __import__("os").environ.pop("GALATEA_REPOSITORY_ROOT", None)
            else:
                __import__("os").environ["GALATEA_REPOSITORY_ROOT"] = previous

    def test_environment_identity_ignores_transient_gpu_processes(self):
        base = {"python": "3.12.12", "packages": {"ray": "2.58.0"}, "gpu_processes": ["11, python, 10 MiB"]}
        changed = {**base, "gpu_processes": ["99, python, 20 MiB"]}
        self.assertEqual(environment_digest(base), environment_digest(changed))

    def test_plan_is_strict_json_and_never_uses_test_for_trial(self):
        config = load_training_config(ROOT / "configs/wechat-owner-bulk-approved-5k.yaml")
        plan = build_training_plan(config)
        serialized = json.dumps(plan, sort_keys=True)
        self.assertTrue(serialized)
        self.assertEqual("trial", plan["config"]["run"]["role"])
        self.assertFalse(plan["objective"]["uses_test_holdout"])
        self.assertEqual("untouched", plan["dataset"]["test_access"])

    def test_run_manifest_schema_allows_only_explicit_final_test_access(self):
        schema_path = ROOT / "schemas/training-run-manifest.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(["untouched", "enabled_once"], schema["properties"]["test_access"]["enum"])
        self.assertEqual(
            [{"type": "null"}, {"type": "string", "pattern": "^[a-f0-9]{64}$"}],
            schema["properties"]["test_evaluation_id"]["oneOf"],
        )
        self.assertEqual(2, len(schema["allOf"]))

    def test_worker_rejects_ungoverned_ray_execution(self):
        config = load_training_config(ROOT / "configs/ray-job-smoke.yaml")
        with self.assertRaisesRegex(TrainingContractError, "Galatea-governed"):
            train(config, runtime={"execution_mode": "ray_job", "ray_job_id": "job"})


if __name__ == "__main__":
    unittest.main()
