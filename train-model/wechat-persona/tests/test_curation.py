import hashlib
import json
import stat
import tempfile
import unittest
from pathlib import Path

from wechat_persona._common import file_digest
from wechat_persona.curation import CurationError, curate_snapshot
from wechat_persona.review import _content_hash


class CurationTests(unittest.TestCase):
    def _row(self, sample_id: str, session_id: str, split: str, answer: str) -> dict:
        row = {
            "sample_id": sample_id,
            "session_id": session_id,
            "messages": [
                {"role": "system", "content": "policy"},
                {"role": "user", "content": "self: context"},
                {"role": "assistant", "content": answer},
            ],
            "metadata": {
                "review_status": "keep",
                "content_sha256": "",
                "split": split,
                "source_message_ids": [f"target-{sample_id}"],
                "context_message_ids": [f"context-{sample_id}-{i}" for i in range(6)],
                "reviewer_id": "reviewer-hash",
                "reviewed_at": "2026-09-01T00:00:00+00:00",
                "review_reason": None,
                "assistant_only_loss": True,
            },
        }
        row["metadata"]["content_sha256"] = _content_hash(row)
        return row

    def _fixture(self, root: Path) -> tuple[Path, Path]:
        parent = root / "formal-snapshot" / "parent"
        parent.mkdir(parents=True)
        train = [
            self._row("train-care", "session-train-a", "train", "抱抱，慢慢来，我陪你"),
            self._row("train-plain", "session-train-b", "train", "收到内容"),
            self._row("train-short", "session-train-c", "train", "嗯"),
        ]
        validation = [
            self._row("validation-care", "session-validation-a", "validation", "要不要先休息一下？"),
            self._row("validation-plain", "session-validation-b", "validation", "收到内容"),
        ]
        for split, rows in (("train", train), ("validation", validation)):
            path = parent / f"{split}.jsonl"
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
                encoding="utf-8",
            )
        (parent / "test.jsonl").write_bytes(b"opaque-holdout-bytes-only\n")
        review = root / "review-v2" / "review-summary.json"
        review.parent.mkdir(parents=True)
        review.write_text(
            json.dumps(
                {
                    "status": "completed",
                    "human_review_completed": True,
                    "uncertain_count": 0,
                    "reviewed_hard_leak_count": 0,
                    "review_evidence_digest": "a" * 64,
                }
            ),
            encoding="utf-8",
        )
        split_hashes = {
            split: file_digest(parent / f"{split}.jsonl")
            for split in ("train", "validation", "test")
        }
        manifest = {
            "schema_version": "wechat-sft-v2",
            "assistant_only_loss": True,
            "dataset_id": "fixture-dataset",
            "split_sha256": "b" * 64,
            "sample_counts": {"train": 3, "validation": 2, "test": 99},
            "split_file_sha256": split_hashes,
            "review_evidence_digest": "a" * 64,
            "privacy_counts": {
                "reviewed": {"hard_leak_count": 0},
                "formal_snapshot": {"hard_leak_count": 0},
            },
        }
        (parent / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return parent, review

    def test_plan_is_read_only_and_never_parses_test(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controlled = Path(temp_dir) / "controlled"
            parent, review = self._fixture(controlled)
            output_root = controlled / "curated-drafts"

            result = curate_snapshot(
                parent_snapshot=parent,
                review_summary_path=review,
                output_root=output_root,
                controlled_root=controlled,
            )

            self.assertFalse(output_root.exists())
            self.assertFalse(result["will_write"])
            self.assertTrue(result["parent_test_untouched"])
            self.assertEqual(result["parent_counts"]["test"], 99)
            self.assertGreater(result["selected_counts"]["train"], 0)

    def test_execute_writes_private_hash_only_evidence_and_no_test_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controlled = Path(temp_dir) / "controlled"
            parent, review = self._fixture(controlled)
            output_root = controlled / "curated-drafts"

            result = curate_snapshot(
                parent_snapshot=parent,
                review_summary_path=review,
                output_root=output_root,
                execute=True,
                controlled_root=controlled,
            )
            output = Path(result["output_directory"])
            manifest = json.loads((output / "selection-manifest.json").read_text())
            decisions = [
                json.loads(line)
                for line in (output / "selection-decisions.jsonl").read_text().splitlines()
            ]

            self.assertFalse((output / "test.jsonl").exists())
            self.assertTrue(manifest["parent_test_untouched"])
            self.assertFalse(manifest["parent"]["splits"]["test"]["content_parsed"])
            self.assertFalse(manifest["training_eligible"])
            self.assertTrue(manifest["human_review_required"])
            self.assertTrue(manifest["formal_approval_required"])
            self.assertTrue(all("messages" not in row and "content" not in row for row in decisions))
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            for path in output.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                curate_snapshot(
                    parent_snapshot=parent,
                    review_summary_path=review,
                    output_root=output_root,
                    execute=True,
                    controlled_root=controlled,
                )

    def test_blocks_parent_digest_and_review_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            controlled = Path(temp_dir) / "controlled"
            parent, review = self._fixture(controlled)
            manifest_path = parent / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["split_file_sha256"]["validation"] = hashlib.sha256(b"changed").hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(CurationError, "digest mismatch"):
                curate_snapshot(
                    parent_snapshot=parent,
                    review_summary_path=review,
                    output_root=controlled / "curated-drafts",
                    controlled_root=controlled,
                )


if __name__ == "__main__":
    unittest.main()
