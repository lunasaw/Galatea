import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona._common import digest, file_digest
from wechat_persona.prelabel_snapshot import (
    PrelabelSnapshotError,
    compile_prelabel_snapshot,
)
from wechat_persona.review import _content_hash


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


class PrelabelSnapshotTests(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path, Path]:
        formal = root / "formal"
        curated = root / "curated"
        review = root / "review"
        output = root / "experimental"
        for directory in (formal, curated, review):
            directory.mkdir()
        parent = {
            "dataset_id": "parent-data",
            "source_sha256": "1" * 64,
            "manifest_sha256": "2" * 64,
            "consent_digest": "3" * 64,
            "preprocessing_version": "parent-v1",
            "review_evidence_digest": "4" * 64,
            "formal_training_eligible": False,
        }
        write_json(formal / "manifest.json", parent)
        rows = {}
        for split in ("train", "validation"):
            row = {
                "sample_id": f"sample-{split}",
                "session_id": f"session-{split}",
                "messages": [
                    {"role": "system", "content": "assistant"},
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "hi"},
                ],
                "metadata": {"split": split, "assistant_only_loss": True},
            }
            row["metadata"]["content_sha256"] = _content_hash(row)
            rows[split] = row
            (curated / f"{split}.jsonl").write_text(
                json.dumps(row) + "\n", encoding="utf-8"
            )
        manifest = {
            "schema_version": "wechat-persona-curated-draft-v1",
            "curation_id": "curated-1",
            "curation_digest": "5" * 64,
            "selection_sha256": "6" * 64,
            "selected_counts": {"train": 1, "validation": 1},
            "parent": {
                "manifest_path": str(formal / "manifest.json"),
                "manifest_file_sha256": file_digest(formal / "manifest.json"),
                "splits": {"test": {"sha256": "7" * 64, "count": 1}},
            },
        }
        manifest["manifest_content_sha256"] = digest(manifest)
        write_json(curated / "selection-manifest.json", manifest)
        decisions = []
        for row in rows.values():
            decisions.append(
                {
                    "sample_id": row["sample_id"],
                    "session_id": row["session_id"],
                    "content_sha256": _content_hash(row),
                    "review_status": "keep",
                    "reviewer_id": "gpt-prelabel-v1",
                    "reviewed_at": "2026-09-12T00:00:00Z",
                    "revision": 1,
                    "labels": ["direct_response"],
                }
            )
        state = {
            "schema_version": "wechat-persona-review-workspace-v1",
            "curation_id": manifest["curation_id"],
            "curation_digest": manifest["curation_digest"],
            "selection_sha256": manifest["selection_sha256"],
            "manifest_file_sha256": file_digest(curated / "selection-manifest.json"),
            "training_eligible": False,
            "formal_approval_required": True,
            "decisions": decisions,
        }
        write_json(review / "latest-decisions.json", state)
        return curated, review / "latest-decisions.json", output

    def test_publishes_only_kept_train_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            curated, review, output = self.fixture(root)
            result = compile_prelabel_snapshot(
                curated_dataset=curated,
                review_state_path=review,
                output_root=output,
                authorization_id="user-request-20260912",
                execute=True,
                controlled_root=root,
            )
            snapshot = Path(result["output_directory"])
            self.assertTrue((snapshot / "train.jsonl").is_file())
            self.assertTrue((snapshot / "validation.jsonl").is_file())
            self.assertFalse((snapshot / "test.jsonl").exists())
            manifest = json.loads((snapshot / "manifest.json").read_text())
            self.assertFalse(manifest["governance"]["human_review_completed"])
            self.assertFalse(manifest["governance"]["formal_training_eligible"])
            self.assertTrue(manifest["governance"]["experimental_training_authorized"])
            self.assertEqual("untouched", manifest["governance"]["test_access"])

    def test_rejects_a_human_or_uncertain_effective_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            curated, review, output = self.fixture(root)
            state = json.loads(review.read_text())
            state["decisions"][0]["review_status"] = "uncertain"
            write_json(review, state)
            with self.assertRaisesRegex(PrelabelSnapshotError, "keep or reject"):
                compile_prelabel_snapshot(
                    curated_dataset=curated,
                    review_state_path=review,
                    output_root=output,
                    authorization_id="user-request-20260912",
                    controlled_root=root,
                )


if __name__ == "__main__":
    unittest.main()
