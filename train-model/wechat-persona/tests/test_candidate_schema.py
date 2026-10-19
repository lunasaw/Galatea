import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from wechat_persona.datasets import build_review_candidates


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas/candidate.schema.json"


class CandidateSchemaTests(unittest.TestCase):
    def test_generated_candidate_allows_versioned_privacy_boundary_view(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        candidate = build_review_candidates(
            [{
                "session_id": "session-1",
                "turns": [
                    {
                        "speaker_role": "self",
                        "text_redacted": "hello",
                        "message_ids": ["m1"],
                        "message_contents": ["hello"],
                    },
                    {
                        "speaker_role": "target",
                        "text_redacted": "reply",
                        "message_ids": ["m2"],
                        "message_contents": ["reply"],
                    },
                ],
            }],
            split_by_session={"session-1": "train"},
        )[0]

        Draft202012Validator(schema).validate(candidate)

    def test_boundary_view_is_required_by_candidate_v2(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        candidate = {
            "sample_id": "sample-1",
            "session_id": "session-1",
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "reply"},
            ],
            "metadata": {
                "review_status": "uncertain",
                "content_sha256": "a" * 64,
            },
        }

        errors = list(Draft202012Validator(schema).iter_errors(candidate))
        self.assertTrue(any("privacy_scan_messages" in error.message for error in errors))
        self.assertTrue(any("privacy_boundary_version" in error.message for error in errors))


if __name__ == "__main__":
    unittest.main()
