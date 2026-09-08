import unittest
from wechat_persona.sessionize import sessionize, deterministic_split


class SessionSplitTests(unittest.TestCase):
    def test_session_boundaries_merge_and_no_cross_split(self):
        rows = [
            {"message_id": "m1", "timestamp": "2026-09-01T10:00:00+08:00", "speaker_role": "self", "text_redacted": "a", "message_kind": "text"},
            {"message_id": "m2", "timestamp": "2026-09-01T10:01:00+08:00", "speaker_role": "self", "text_redacted": "b", "message_kind": "text"},
            {"message_id": "m3", "timestamp": "2026-09-01T15:00:00+08:00", "speaker_role": "target", "text_redacted": "c", "message_kind": "text"},
        ]
        sessions = sessionize(rows, inactivity_gap_minutes=60)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0]["message_ids"], ["m1", "m2"])
        self.assertEqual(sessions[0]["turns"][0]["source_record_indices"], [0, 1])
        self.assertEqual(sessions[0]["turns"][0]["message_contents"], ["a", "b"])
        self.assertEqual(sessions[0]["session_rule_version"], "wechat-session-v2")
        split = deterministic_split(sessions, ratios=(0.8, 0.1, 0.1))
        seen = [sid for values in split["session_ids_by_split"].values() for sid in values]
        self.assertEqual(len(seen), len(set(seen)))


if __name__ == "__main__":
    unittest.main()
