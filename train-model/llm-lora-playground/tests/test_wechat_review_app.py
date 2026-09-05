from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from wechat_review_app import init_workspace, update_state  # noqa: E402


class WeChatReviewAppTests(unittest.TestCase):
    def _source(self, root: Path) -> None:
        (root / "review_manifest.json").write_text(
            json.dumps({"dataset_id": "wechat_test", "source_sha256": "a" * 64}), encoding="utf-8"
        )
        for split in ("train", "validation", "test"):
            row = {
                "sample_id": f"sample-{split}",
                "session_id": f"session-{split}",
                "messages": [
                    {"role": "system", "content": "boundary"},
                    {"role": "user", "content": "context"},
                    {"role": "assistant", "content": "reply"},
                ],
                "metadata": {"review_status": "uncertain", "content_sha256": "c" * 64},
            }
            (root / f"{split}_candidates.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    def test_init_creates_default_keep_baseline_without_touching_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            runtime = Path(temporary) / "runtime"
            source.mkdir()
            self._source(source)
            before = (source / "train_candidates.jsonl").read_bytes()
            state = init_workspace(source, runtime)
            self.assertEqual(3, state["candidate_count"])
            self.assertEqual({"keep": 3}, state["status_counts"])
            self.assertEqual(before, (source / "train_candidates.jsonl").read_bytes())
            manifest = json.loads((runtime / "baseline/baseline_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["baseline_only"])
            self.assertFalse(manifest["formal_training_eligible"])
            with (runtime / "baseline/datasets/train.jsonl").open(encoding="utf-8") as handle:
                self.assertEqual(1, sum(1 for _ in handle))

    def test_update_is_immediate_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            runtime = Path(temporary) / "runtime"
            source.mkdir()
            self._source(source)
            init_workspace(source, runtime)
            state = update_state(runtime, "sample-validation", "reject", "third-party detail")
            self.assertEqual({"keep": 2, "reject": 1}, state["status_counts"])
            events = (runtime / "review_events.jsonl").read_text(encoding="utf-8")
            self.assertIn("third-party detail", events)


if __name__ == "__main__":
    unittest.main()
