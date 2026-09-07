import json
import importlib.util
import tempfile
import unittest
from pathlib import Path

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
            "metadata": {"review_status": "uncertain"},
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
            result = compile_reviewed(candidates, events, output)
            self.assertEqual(result["exported_count"], 1)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["sample_id"], "s1")
            self.assertEqual(json.loads(candidates.read_text(encoding="utf-8").splitlines()[0])["metadata"]["review_status"], "uncertain")

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
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["messages"][-1]["content"], "edited")


if __name__ == "__main__":
    unittest.main()
