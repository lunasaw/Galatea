import tempfile
import unittest
from unittest import mock
from pathlib import Path
from wechat_persona.datasets import build_review_candidates, build_sft_splits, write_sft_snapshot, DatasetError
from wechat_persona.redact import scan_candidate
from wechat_persona.review import _content_hash


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
        self.assertEqual(rows[0]["metadata"]["content_sha256"], _content_hash(rows[0]))

    def test_candidate_privacy_scan_preserves_merged_source_boundaries(self):
        session = {"session_id": "sess", "turns": [
            {
                "speaker_role": "self",
                "text_redacted": "密码\nabc123",
                "message_ids": ["m1", "m2"],
                "message_contents": ["密码", "abc123"],
            },
            {
                "speaker_role": "target",
                "text_redacted": "收到",
                "message_ids": ["m3"],
                "message_contents": ["收到"],
            },
        ]}
        candidate = build_review_candidates(
            [session], split_by_session={"sess": "train"}
        )[0]

        self.assertEqual(
            candidate["privacy_scan_messages"],
            [
                {"content": "密码", "source_record_index": 0},
                {"content": "abc123", "source_record_index": 1},
                {"content": "收到", "source_record_index": 2},
            ],
        )
        self.assertEqual(
            candidate["privacy_boundary_version"], "wechat-privacy-boundary-v1"
        )
        self.assertEqual(scan_candidate(candidate)["cross_message_secret_matches"], 1)

    def test_candidate_fallback_indices_remain_monotonic_across_turns(self):
        session = {"session_id": "sess", "turns": [
            {
                "speaker_role": "self",
                "text_redacted": "first",
                "message_ids": ["m1"],
            },
            {
                "speaker_role": "self",
                "text_redacted": "second",
                "message_ids": ["m2"],
            },
            {
                "speaker_role": "target",
                "text_redacted": "reply",
                "message_ids": ["m3"],
            },
        ]}

        candidate = build_review_candidates(
            [session], split_by_session={"sess": "train"}
        )[0]

        self.assertEqual(
            [row["source_record_index"] for row in candidate["privacy_scan_messages"]],
            [0, 1, 2],
        )

    def test_build_splits_and_gates(self):
        base = {"sample_id": "s", "session_id": "sess", "messages": [{"role": "system", "content": "policy"}, {"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}], "metadata": {"review_status": "keep", "split": "train"}}
        base["metadata"]["content_sha256"] = _content_hash(base)
        out = build_sft_splits([base])
        self.assertEqual(len(out["train"]), 1)
        self.assertTrue(out["manifest"]["assistant_only_loss"])

    def test_redact_keep_binds_original_and_edited_content_separately(self):
        original = {
            "sample_id": "s",
            "session_id": "sess",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "original detail"},
            ],
            "metadata": {"review_status": "uncertain", "split": "train"},
        }
        reviewed = {
            **original,
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "edited"},
            ],
            "metadata": {
                **original["metadata"],
                "review_status": "redact_keep",
                "content_sha256": _content_hash(original),
            },
        }
        reviewed["metadata"]["redacted_content_sha256"] = _content_hash(reviewed)

        out = build_sft_splits([reviewed])

        self.assertEqual(len(out["train"]), 1)
        tampered = {
            **reviewed,
            "messages": [
                *reviewed["messages"][:-1],
                {"role": "assistant", "content": "tampered"},
            ],
        }
        with self.assertRaisesRegex(DatasetError, "redacted_content_hash_mismatch"):
            build_sft_splits([tampered])

    def test_unknown_or_empty_blocks(self):
        bad = {"sample_id": "s", "session_id": "sess", "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}], "metadata": {"review_status": "keep", "split": "train", "speaker_role": "unknown"}}
        bad["metadata"]["content_sha256"] = _content_hash(bad)
        with self.assertRaises(DatasetError):
            build_sft_splits([bad])

    def test_cross_message_secret_blocks_formal_split(self):
        bad = {
            "sample_id": "s",
            "session_id": "sess",
            "messages": [
                {"role": "user", "content": "密码"},
                {"role": "assistant", "content": "abc123"},
            ],
            "metadata": {"review_status": "keep", "split": "train"},
        }
        bad["metadata"]["content_sha256"] = _content_hash(bad)
        with self.assertRaisesRegex(DatasetError, "pii_scan_failed"):
            build_sft_splits([bad])

    def test_formal_snapshot_requires_three_nonempty_splits_and_no_overwrite(self):
        rows = []
        for split in ("train", "validation", "test"):
            row = {"sample_id": split, "session_id": f"sess-{split}", "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}], "metadata": {"review_status": "keep", "split": split, "reviewer_id": "r1", "reviewed_at": "2026-09-01T00:00:00Z"}}
            row["metadata"]["content_sha256"] = _content_hash(row)
            rows.append(row)
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "snapshot"
            manifest = write_sft_snapshot(target, rows)
            self.assertEqual(manifest["sample_counts"], {"train": 1, "validation": 1, "test": 1})
            self.assertFalse(manifest["formal_training_eligible"])
            self.assertTrue(manifest["requires_formal_dataset_ready_approval"])
            self.assertEqual(
                manifest["privacy_counts"],
                {
                    "reviewed": {"hard_leak_count": 0},
                    "formal_snapshot": {"hard_leak_count": 0},
                },
            )
            self.assertEqual(set(manifest["split_file_sha256"]), {"train", "validation", "test"})
            self.assertEqual(len(manifest["snapshot_manifest_sha256"]), 64)
            with self.assertRaises(FileExistsError):
                write_sft_snapshot(target, rows)

    def test_formal_snapshot_rejects_missing_review_evidence(self):
        rows = []
        for split in ("train", "validation", "test"):
            row = {"sample_id": split, "session_id": f"sess-{split}", "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}], "metadata": {"review_status": "keep", "split": split}}
            row["metadata"]["content_sha256"] = _content_hash(row)
            rows.append(row)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(DatasetError):
                write_sft_snapshot(Path(td) / "snapshot", rows)

    def test_manifest_has_stable_identity_and_counts(self):
        rows = []
        for split in ("train", "validation", "test"):
            row = {"sample_id": split, "session_id": f"sess-{split}",
                   "messages": [{"role": "user", "content": "hi"},
                                {"role": "assistant", "content": "hello"}],
                   "metadata": {"review_status": "keep", "split": split,
                                "reviewer_id": "r1", "reviewed_at": "2026-09-01T00:00:00Z"}}
            row["metadata"]["content_sha256"] = _content_hash(row)
            rows.append(row)
        with tempfile.TemporaryDirectory() as td:
            manifest = write_sft_snapshot(Path(td) / "snapshot", rows)
        self.assertEqual(manifest["sample_counts"], {"train": 1, "validation": 1, "test": 1})
        self.assertEqual(manifest["assistant_only_loss"], True)
        self.assertEqual(len(manifest["split_sha256"]), 64)

    def test_snapshot_publish_is_atomic_and_cleans_failed_staging(self):
        rows = []
        for split in ("train", "validation", "test"):
            row = {
                "sample_id": split,
                "session_id": f"sess-{split}",
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"},
                ],
                "metadata": {
                    "review_status": "keep",
                    "split": split,
                    "reviewer_id": "r1",
                    "reviewed_at": "2026-09-01T00:00:00Z",
                },
            }
            row["metadata"]["content_sha256"] = _content_hash(row)
            rows.append(row)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "snapshot"
            with mock.patch(
                "wechat_persona.datasets.file_digest",
                side_effect=OSError("fixture failure"),
            ):
                with self.assertRaises(OSError):
                    write_sft_snapshot(target, rows)
            self.assertFalse(target.exists())
            self.assertEqual(list(root.glob(".snapshot.staging-*")), [])

    def test_snapshot_publish_does_not_replace_racing_empty_directory(self):
        rows = []
        for split in ("train", "validation", "test"):
            row = {
                "sample_id": split,
                "session_id": f"sess-{split}",
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"},
                ],
                "metadata": {
                    "review_status": "keep",
                    "split": split,
                    "reviewer_id": "r1",
                    "reviewed_at": "2026-09-01T00:00:00Z",
                },
            }
            row["metadata"]["content_sha256"] = _content_hash(row)
            rows.append(row)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "snapshot"

            real_publish = __import__(
                "wechat_persona.datasets", fromlist=["_publish_directory_noreplace"]
            )._publish_directory_noreplace

            def create_competing_destination(staging: Path, output: Path) -> None:
                output.mkdir()
                real_publish(staging, output)

            with mock.patch(
                "wechat_persona.datasets._publish_directory_noreplace",
                side_effect=create_competing_destination,
            ):
                with self.assertRaises(FileExistsError):
                    write_sft_snapshot(target, rows)
            self.assertTrue(target.is_dir())
            self.assertEqual(list(target.iterdir()), [])
            self.assertEqual(list(root.glob(".snapshot.staging-*")), [])


if __name__ == "__main__":
    unittest.main()
