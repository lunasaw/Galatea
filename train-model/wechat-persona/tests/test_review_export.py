import tempfile
import unittest
from pathlib import Path
from wechat_persona.review import ReviewError, apply_review, export_approved
from wechat_persona.review import append_review_event


class ReviewExportTests(unittest.TestCase):
    def test_initial_uncertain_and_approved_export_requires_reviewer(self):
        row = {"sample_id": "s1", "session_id": "sess1", "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}], "metadata": {"review_status": "uncertain", "content_sha256": ""}}
        with self.assertRaises(ReviewError):
            apply_review(row, "keep")
        approved = apply_review(row, "keep", reviewer_id="r1")
        self.assertEqual(approved["metadata"]["review_status"], "keep")
        self.assertEqual(len(export_approved([approved], split="train")), 1)

    def test_uncertain_never_exports(self):
        row = {"sample_id": "s1", "session_id": "sess1", "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}], "metadata": {"review_status": "uncertain", "content_sha256": ""}}
        self.assertEqual(export_approved([row], split="train"), [])

    def test_redact_keep_requires_edit_and_reason(self):
        row = {"sample_id": "s1", "session_id": "sess1", "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}], "metadata": {"review_status": "uncertain"}}
        with self.assertRaises(ReviewError):
            apply_review(row, "redact_keep", reviewer_id="r1", edited_messages=row["messages"])

    def test_event_log_is_append_only_and_omits_text(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "events.jsonl"
            row = {"sample_id": "s1", "session_id": "sess1", "messages": [{"role": "user", "content": "private-canary"}, {"role": "assistant", "content": "reply"}], "metadata": {"review_status": "uncertain"}}
            approved = apply_review(row, "keep", reviewer_id="r1")
            append_review_event(path, approved)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("private-canary", text)
            self.assertIn("s1", text)


if __name__ == "__main__":
    unittest.main()
