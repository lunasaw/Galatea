import json
import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path

from wechat_persona._common import digest, file_digest
from wechat_persona.review import append_review_event, apply_review

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "compile_reviewed.py"
_SPEC = importlib.util.spec_from_file_location("compile_reviewed_script", _SCRIPT)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
compile_reviewed = _MODULE.compile_reviewed


class ReviewCompileTests(unittest.TestCase):
    def _row(self, sample_id: str) -> dict:
        return {
            "sample_id": sample_id,
            "session_id": "session-1",
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
            "metadata": {"review_status": "uncertain", "split": "train"},
        }

    def test_reconciles_keep_and_reject_without_overwriting_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            candidate = self._row("s1")
            rejected = self._row("s2")
            candidates = root / "candidates.jsonl"
            candidates.write_text("".join(json.dumps(row) + "\n" for row in (candidate, rejected)), encoding="utf-8")
            events = root / "events.jsonl"
            append_review_event(events, apply_review(candidate, "keep", reviewer_id="r1"))
            append_review_event(events, apply_review(rejected, "reject", reviewer_id="r1", reason="not suitable"))
            output = root / "reviewed.jsonl"
            summary = root / "review-summary.json"
            result = compile_reviewed(
                candidates,
                events,
                output,
                review_summary_path=summary,
            )
            self.assertEqual(result["exported_count"], 1)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["sample_id"], "s1")
            self.assertEqual(json.loads(candidates.read_text(encoding="utf-8").splitlines()[0])["metadata"]["review_status"], "uncertain")
            summary_value = json.loads(summary.read_text(encoding="utf-8"))
            self.assertEqual(summary_value["selected_scope"], "all")
            self.assertEqual(summary_value["status_counts"]["reject"], 1)
            self.assertEqual(
                summary_value["review_evidence_digest"],
                result["review_evidence_digest"],
            )
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(summary.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)

    def test_redact_keep_requires_separate_reviewed_row(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            candidate = self._row("s1")
            edited = {**candidate, "messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "edited"}]}
            reviewed = apply_review(candidate, "redact_keep", reviewer_id="r1", reason="remove detail", edited_messages=edited["messages"])
            candidates = root / "candidates.jsonl"; candidates.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
            events = root / "events.jsonl"; append_review_event(events, reviewed)
            reviewed_rows = root / "reviewed-rows.jsonl"; reviewed_rows.write_text(json.dumps(reviewed) + "\n", encoding="utf-8")
            output = root / "reviewed.jsonl"
            result = compile_reviewed(candidates, events, output, reviewed_rows)
            self.assertEqual(result["exported_count"], 1)
            compiled = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(compiled["messages"][-1]["content"], "edited")
            self.assertNotEqual(
                compiled["metadata"]["content_sha256"],
                compiled["metadata"]["redacted_content_sha256"],
            )

    def test_redact_keep_rejects_tampered_edited_hash(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            candidate = self._row("s1")
            reviewed = apply_review(
                candidate,
                "redact_keep",
                reviewer_id="r1",
                reason="remove detail",
                edited_messages=[
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "edited"},
                ],
            )
            candidates = root / "candidates.jsonl"
            candidates.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
            events = root / "events.jsonl"
            append_review_event(events, reviewed)
            reviewed["messages"][-1]["content"] = "tampered"
            reviewed_rows = root / "reviewed-rows.jsonl"
            reviewed_rows.write_text(json.dumps(reviewed) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(_MODULE.CompileError, "redacted content hash mismatch"):
                compile_reviewed(candidates, events, root / "reviewed.jsonl", reviewed_rows)

    def test_compile_rejects_secret_split_across_messages(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            candidate = self._row("s1")
            candidate["messages"] = [
                {"role": "user", "content": "密码"},
                {"role": "assistant", "content": "abc123"},
            ]
            candidates = root / "candidates.jsonl"
            candidates.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
            events = root / "events.jsonl"
            append_review_event(events, apply_review(candidate, "keep", reviewer_id="r1"))
            with self.assertRaises(_MODULE.CompileError):
                compile_reviewed(candidates, events, root / "reviewed.jsonl")

    def test_redact_keep_cannot_hide_edit_behind_original_boundary_view(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            candidate = self._row("s1")
            candidate["privacy_scan_messages"] = [{"content": "safe original"}]
            candidate["privacy_boundary_version"] = "wechat-privacy-boundary-v1"
            reviewed = apply_review(
                candidate,
                "redact_keep",
                reviewer_id="r1",
                reason="edit content",
                edited_messages=[
                    {"role": "user", "content": "密码"},
                    {"role": "assistant", "content": "abc123"},
                ],
            )
            candidates = root / "candidates.jsonl"
            candidates.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
            events = root / "events.jsonl"
            append_review_event(events, reviewed)
            reviewed_rows = root / "reviewed-rows.jsonl"
            reviewed_rows.write_text(json.dumps(reviewed) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(_MODULE.CompileError, "pii_scan_failed"):
                compile_reviewed(
                    candidates, events, root / "reviewed.jsonl", reviewed_rows
                )

    def test_invalid_reject_split_does_not_leave_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            candidate = self._row("s1")
            candidate["metadata"].pop("split")
            candidates = root / "candidates.jsonl"
            candidates.write_text(json.dumps(candidate) + "\n", encoding="utf-8")
            events = root / "events.jsonl"
            append_review_event(
                events,
                apply_review(
                    candidate,
                    "reject",
                    reviewer_id="r1",
                    reason="not suitable",
                ),
            )
            output = root / "reviewed.jsonl"
            summary = root / "review-summary.json"
            with self.assertRaisesRegex(_MODULE.CompileError, "split missing"):
                compile_reviewed(
                    candidates,
                    events,
                    output,
                    review_summary_path=summary,
                )
            self.assertFalse(output.exists())
            self.assertFalse(summary.exists())

    def test_subset_summary_keeps_rejected_selected_ids_in_scope(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            candidates_rows = []
            for sample_id, split in (
                ("train-keep", "train"),
                ("train-unselected", "train"),
                ("validation-reject", "validation"),
                ("test-keep", "test"),
            ):
                row = self._row(sample_id)
                row["metadata"]["split"] = split
                candidates_rows.append(row)
            candidates = root / "candidates.jsonl"
            candidates.write_text(
                "".join(json.dumps(row) + "\n" for row in candidates_rows),
                encoding="utf-8",
            )
            candidate_digest = _MODULE._candidate_manifest_digest(candidates_rows)
            selected_ids = ["train-keep", "validation-reject", "test-keep"]
            selection = root / "selection.json"
            selection.write_text(json.dumps({
                "parent_candidate_manifest_sha256": candidate_digest,
                "selection_rule": "fixture deterministic selection",
                "selection_version": "candidate-selection-v1",
                "selected_sample_ids": selected_ids,
                "selected_sample_ids_sha256": digest(selected_ids),
                "selected_count": 3,
                "split_counts": {"train": 1, "validation": 1, "test": 1},
            }) + "\n", encoding="utf-8")
            events = root / "events.jsonl"
            by_id = {row["sample_id"]: row for row in candidates_rows}
            append_review_event(
                events,
                apply_review(by_id["train-keep"], "keep", reviewer_id="r1"),
            )
            append_review_event(
                events,
                apply_review(
                    by_id["validation-reject"],
                    "reject",
                    reviewer_id="r1",
                    reason="not suitable",
                ),
            )
            append_review_event(
                events,
                apply_review(by_id["test-keep"], "keep", reviewer_id="r1"),
            )
            output = root / "reviewed.jsonl"
            summary = root / "summary.json"

            result = compile_reviewed(
                candidates,
                events,
                output,
                candidate_manifest_sha256=candidate_digest,
                review_summary_path=summary,
                selection_manifest_path=selection,
            )

            self.assertEqual(result["selected_scope"], "subset")
            self.assertEqual(result["selected_count"], 3)
            self.assertEqual(result["exported_count"], 2)
            self.assertEqual(result["rejected_counts_by_split"]["validation"], 1)
            self.assertEqual(
                result["selected_counts_by_split"],
                {"train": 1, "validation": 1, "test": 1},
            )
            self.assertEqual(result["selection_manifest_digest"], file_digest(selection))
            self.assertEqual(
                {json.loads(line)["sample_id"] for line in output.read_text().splitlines()},
                {"train-keep", "test-keep"},
            )

    def test_subset_rejects_event_for_unselected_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            candidates_rows = []
            for sample_id, split in (
                ("train-keep", "train"),
                ("validation-keep", "validation"),
                ("test-keep", "test"),
                ("not-selected", "train"),
            ):
                row = self._row(sample_id)
                row["metadata"]["split"] = split
                candidates_rows.append(row)
            candidates = root / "candidates.jsonl"
            candidates.write_text(
                "".join(json.dumps(row) + "\n" for row in candidates_rows),
                encoding="utf-8",
            )
            candidate_digest = _MODULE._candidate_manifest_digest(candidates_rows)
            selected_ids = ["train-keep", "validation-keep", "test-keep"]
            selection = root / "selection.json"
            selection.write_text(json.dumps({
                "parent_candidate_manifest_sha256": candidate_digest,
                "selection_rule": "fixture deterministic selection",
                "selection_version": "candidate-selection-v1",
                "selected_sample_ids": selected_ids,
                "selected_sample_ids_sha256": digest(selected_ids),
                "selected_count": 3,
                "split_counts": {"train": 1, "validation": 1, "test": 1},
            }) + "\n", encoding="utf-8")
            events = root / "events.jsonl"
            for row in candidates_rows:
                append_review_event(
                    events, apply_review(row, "keep", reviewer_id="r1")
                )

            with self.assertRaisesRegex(_MODULE.CompileError, "candidate/event mismatch"):
                compile_reviewed(
                    candidates,
                    events,
                    root / "reviewed.jsonl",
                    candidate_manifest_sha256=candidate_digest,
                    selection_manifest_path=selection,
                )

    def test_subset_requires_explicit_parent_candidate_digest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            candidates_rows = []
            for sample_id, split in (
                ("train-keep", "train"),
                ("validation-keep", "validation"),
                ("test-keep", "test"),
            ):
                row = self._row(sample_id)
                row["metadata"]["split"] = split
                candidates_rows.append(row)
            candidates = root / "candidates.jsonl"
            candidates.write_text(
                "".join(json.dumps(row) + "\n" for row in candidates_rows),
                encoding="utf-8",
            )
            candidate_digest = _MODULE._candidate_manifest_digest(candidates_rows)
            selected_ids = [row["sample_id"] for row in candidates_rows]
            selection = root / "selection.json"
            selection.write_text(json.dumps({
                "parent_candidate_manifest_sha256": candidate_digest,
                "selection_rule": "fixture deterministic selection",
                "selection_version": "candidate-selection-v1",
                "selected_sample_ids": selected_ids,
                "selected_sample_ids_sha256": digest(selected_ids),
                "selected_count": 3,
                "split_counts": {"train": 1, "validation": 1, "test": 1},
            }) + "\n", encoding="utf-8")
            events = root / "events.jsonl"
            for row in candidates_rows:
                append_review_event(
                    events, apply_review(row, "keep", reviewer_id="r1")
                )

            with self.assertRaisesRegex(
                _MODULE.CompileError, "explicit candidate manifest digest"
            ):
                compile_reviewed(
                    candidates,
                    events,
                    root / "reviewed.jsonl",
                    selection_manifest_path=selection,
                )

    def test_subset_rejects_reviewed_row_for_unselected_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            candidates_rows = []
            for sample_id, split in (
                ("train-keep", "train"),
                ("validation-keep", "validation"),
                ("test-keep", "test"),
                ("unselected", "train"),
            ):
                row = self._row(sample_id)
                row["metadata"]["split"] = split
                candidates_rows.append(row)
            candidates = root / "candidates.jsonl"
            candidates.write_text(
                "".join(json.dumps(row) + "\n" for row in candidates_rows),
                encoding="utf-8",
            )
            candidate_digest = _MODULE._candidate_manifest_digest(candidates_rows)
            selected_rows = candidates_rows[:3]
            selected_ids = [row["sample_id"] for row in selected_rows]
            selection = root / "selection.json"
            selection.write_text(json.dumps({
                "parent_candidate_manifest_sha256": candidate_digest,
                "selection_rule": "fixture deterministic selection",
                "selection_version": "candidate-selection-v1",
                "selected_sample_ids": selected_ids,
                "selected_sample_ids_sha256": digest(selected_ids),
                "selected_count": 3,
                "split_counts": {"train": 1, "validation": 1, "test": 1},
            }) + "\n", encoding="utf-8")
            events = root / "events.jsonl"
            for row in selected_rows:
                append_review_event(
                    events, apply_review(row, "keep", reviewer_id="r1")
                )
            reviewed_rows = root / "reviewed-rows.jsonl"
            reviewed_rows.write_text(
                json.dumps(candidates_rows[-1]) + "\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(_MODULE.CompileError, "unselected sample_id"):
                compile_reviewed(
                    candidates,
                    events,
                    root / "reviewed.jsonl",
                    reviewed_rows,
                    candidate_manifest_sha256=candidate_digest,
                    selection_manifest_path=selection,
                )


if __name__ == "__main__":
    unittest.main()
