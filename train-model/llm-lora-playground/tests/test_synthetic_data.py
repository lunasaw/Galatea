import json
from pathlib import Path
import tempfile
import unittest

from llm_lora_playground.datasets import (
    DataContractError,
    compute_dataset_digest,
    generate_dataset,
    load_samples,
    validate_sample,
)


class SyntheticDataTests(unittest.TestCase):
    def test_generation_is_deterministic_and_grouped(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            a = generate_dataset(Path(first), 12, 42, "toy-v1")
            b = generate_dataset(Path(second), 12, 42, "toy-v1")
            self.assertEqual(compute_dataset_digest(Path(first) / "dataset.jsonl"), compute_dataset_digest(Path(second) / "dataset.jsonl"))
            self.assertEqual(a.sample_count, b.sample_count)
            self.assertEqual(
                (Path(first) / "dataset.jsonl").read_bytes(),
                (Path(second) / "dataset.jsonl").read_bytes(),
            )
            rows = list(load_samples(Path(first) / "dataset.jsonl"))
            self.assertEqual(len({row.sample_id for row in rows}), 12)
            self.assertTrue(all(row.messages[-1]["role"] == "assistant" for row in rows))

    def test_invalid_roles_empty_targets_and_restricted_references_fail(self):
        sample = {
            "sample_id": "x",
            "scenario_id": "s",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "ok"},
            ],
            "metadata": {"style_label": "warm_brief", "generator_version": "v1", "seed": 1},
        }
        validate_sample(sample)
        for bad in (
            {**sample, "messages": [{"role": "system", "content": "x"}, {"role": "hacker", "content": "x"}]},
            {**sample, "messages": [{"role": "user", "content": "x"}, {"role": "assistant", "content": ""}]},
            {**sample, "messages": [{"role": "user", "content": "x", "text_original_ref": "secret"}, {"role": "assistant", "content": "ok"}]},
        ):
            with self.assertRaises(DataContractError):
                validate_sample(bad)

    def test_manifest_contains_file_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            generate_dataset(Path(directory), 3, 7, "toy-v1")
            manifest = json.loads((Path(directory) / "dataset_manifest.json").read_text())
            self.assertEqual(manifest["sample_count"], 3)
            self.assertEqual(manifest["dataset_sha256"], compute_dataset_digest(Path(directory) / "dataset.jsonl"))


if __name__ == "__main__":
    unittest.main()
