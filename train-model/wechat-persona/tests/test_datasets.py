import tempfile
import unittest
from pathlib import Path
from wechat_persona.datasets import build_review_candidates, build_sft_splits, write_sft_snapshot, DatasetError


class DatasetTests(unittest.TestCase):
    def test_candidate_context_has_no_future_messages(self):
        session = {"session_id": "sess", "start_time": "2026-09-01T00:00:00Z", "turns": [
            {"speaker_role": "self", "text_redacted": "before", "message_ids": ["m1"]},
            {"speaker_role": "target", "text_redacted": "reply", "message_ids": ["m2"]},
            {"speaker_role": "self", "text_redacted": "future-canary", "message_ids": ["m3"]},
        ]}
        rows = build_review_candidates([session], split_by_session={"sess": "train"})
        self.assertEqual(len(rows), 1)
        self.assertIn("before", rows[0]["messages"][-2]["content"])
        self.assertNotIn("future-canary", str(rows[0]["messages"]))
        self.assertEqual(rows[0]["metadata"]["review_status"], "uncertain")
    def test_build_splits_and_gates(self):
        base = {"sample_id": "s", "session_id": "sess", "messages": [{"role": "system", "content": "policy"}, {"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}], "metadata": {"review_status": "keep", "split": "train"}}
        out = build_sft_splits([base])
        self.assertEqual(len(out["train"]), 1)
        self.assertTrue(out["manifest"]["assistant_only_loss"])

    def test_unknown_or_empty_blocks(self):
        bad = {"sample_id": "s", "session_id": "sess", "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}], "metadata": {"review_status": "keep", "split": "train", "speaker_role": "unknown"}}
        with self.assertRaises(DatasetError):
            build_sft_splits([bad])

    def test_formal_snapshot_requires_three_nonempty_splits_and_no_overwrite(self):
        rows = []
        for split in ("train", "validation", "test"):
            rows.append({"sample_id": split, "session_id": f"sess-{split}", "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}], "metadata": {"review_status": "keep", "split": split, "reviewer_id": "r1", "reviewed_at": "2026-09-01T00:00:00Z"}})
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "snapshot"
            manifest = write_sft_snapshot(target, rows)
            self.assertEqual(manifest["sample_counts"], {"train": 1, "validation": 1, "test": 1})
            with self.assertRaises(FileExistsError):
                write_sft_snapshot(target, rows)

    def test_formal_snapshot_rejects_missing_review_evidence(self):
        rows = [{"sample_id": split, "session_id": f"sess-{split}", "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}], "metadata": {"review_status": "keep", "split": split}} for split in ("train", "validation", "test")]
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(DatasetError):
                write_sft_snapshot(Path(td) / "snapshot", rows)


if __name__ == "__main__":
    unittest.main()
