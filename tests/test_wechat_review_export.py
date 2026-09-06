from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "doc" / "train-llm"))

from export_review_candidates import ExportContractError, export_review_dataset


class ReviewExportTests(unittest.TestCase):
    def _fixture(self, root: Path) -> None:
        for relative in (
            "manifests/source_manifest.json",
            "manifests/split_manifest.json",
            "reports/quality_report.json",
            "work/05_candidates/candidates.jsonl",
        ):
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / "manifests/source_manifest.json").write_text(
            json.dumps(
                {
                    "dataset_id": "wechat_test",
                    "source_sha256": "a" * 64,
                    "config_sha256": "b" * 64,
                    "pipeline_version": "wechat-preprocess-v1.2",
                }
            ),
            encoding="utf-8",
        )
        (root / "manifests/split_manifest.json").write_text(
            json.dumps(
                {
                    "session_ids_by_split": {
                        "train": ["session_train"],
                        "validation": ["session_validation"],
                        "test": ["session_test"],
                    }
                }
            ),
            encoding="utf-8",
        )
        (root / "reports/quality_report.json").write_text(
            json.dumps({"status": "blocked_for_formal_training"}), encoding="utf-8"
        )
        rows = []
        for index, split in enumerate(("train", "validation", "test")):
            rows.append(
                {
                    "sample_id": f"sample-{index}",
                    "session_id": f"session_{split}",
                    "messages": [
                        {"role": "system", "content": "boundary"},
                        {"role": "user", "content": "context"},
                        {"role": "assistant", "content": "reply"},
                    ],
                    "metadata": {
                        "review_status": "uncertain",
                        "source_session_id": f"session_{split}",
                    },
                }
            )
        (root / "work/05_candidates/candidates.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )

    def test_exports_review_only_split_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "dataset"
            self._fixture(root)
            output = export_review_dataset(root)
            manifest = json.loads((output / "review_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual("review_only", manifest["export_status"])
            self.assertFalse(manifest["formal_training_eligible"])
            self.assertEqual({"train": 1, "validation": 1, "test": 1}, manifest["split_counts"])
            row = json.loads((output / "validation_candidates.jsonl").read_text(encoding="utf-8"))
            self.assertEqual("validation", row["metadata"]["split"])
            self.assertEqual("uncertain", row["metadata"]["review_status"])
            self.assertFalse((root / "datasets/train.jsonl").exists())

    def test_refuses_missing_session_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "dataset"
            self._fixture(root)
            path = root / "work/05_candidates/candidates.jsonl"
            row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            row["session_id"] = "not-in-manifest"
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            with self.assertRaises(ExportContractError):
                export_review_dataset(root)


if __name__ == "__main__":
    unittest.main()
