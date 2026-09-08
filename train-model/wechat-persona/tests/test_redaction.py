import unittest

from wechat_persona.normalize import normalize_message
from wechat_persona.redact import (
    PRIVACY_SCANNER_VERSION,
    count_adjacent_secret_redactions,
    redact_adjacent_messages,
    redact_text,
    scan_adjacent_messages,
    scan_candidate,
    scan_redacted_text,
    scan_session,
)


class RedactionTests(unittest.TestCase):
    def test_normalize_and_redact(self):
        msg = {"message_id": "m1", "timestamp": "2026-09-01 10:00", "speaker": "target", "kind": "text", "text": " 联系 13800138000，邮箱 a@example.com，密码: abc123 "}
        normalized = normalize_message(msg, timezone_name="Asia/Shanghai")
        redacted = redact_text(normalized["text_redacted"])
        self.assertNotIn("13800138000", redacted.text)
        self.assertNotIn("a@example.com", redacted.text)
        self.assertEqual(scan_redacted_text(redacted.text)["hard_leak_count"], 0)

    def test_scanner_detects_cross_message_secret_without_role_false_positive(self):
        leaked = scan_adjacent_messages([{"role": "user", "content": "密码"}, {"role": "assistant", "content": "abc123"}])
        self.assertEqual(leaked["cross_message_secret_matches"], 1)
        self.assertGreater(leaked["hard_leak_count"], 0)

        safe = scan_adjacent_messages([{"role": "self", "content": "hello"}, {"role": "target", "content": "assistant"}])
        self.assertEqual(safe["hard_leak_count"], 0)

        long_code = scan_adjacent_messages([
            {"role": "user", "content": "验证码"},
            {"role": "assistant", "content": "106946894171"},
        ])
        self.assertEqual(long_code["cross_message_secret_matches"], 1)
        phrase = scan_adjacent_messages([
            {"role": "user", "content": "屋头密码"},
            {"role": "assistant", "content": "991124"},
        ])
        self.assertEqual(phrase["cross_message_secret_matches"], 1)
        network_address = scan_adjacent_messages([
            {"role": "user", "content": "认证密码"},
            {"role": "assistant", "content": "192.168.0.1"},
        ])
        self.assertEqual(network_address["cross_message_secret_matches"], 0)
        self.assertEqual(network_address["raw_ipv4_matches"], 1)
        self.assertEqual(redact_text("192.168.0.1").text, "<PRIVATE_ID>")

    def test_atomic_scanner_covers_all_redacted_categories(self):
        report = scan_redacted_text(
            " ".join((
                "13800138000",
                "a@example.com",
                "password: abc123",
                "wxid_example",
                "11010519491231002X",
                "6222021234567890123",
                "31.2304, 121.4737",
                "192.168.0.1",
                "地址: 上海市静安区",
                "https://example.invalid/private?id=1",
                "@第三方",
            ))
        )
        for key in (
            "raw_phone_matches",
            "raw_email_matches",
            "raw_secret_matches",
            "raw_wxid_matches",
            "raw_idcard_matches",
            "raw_bank_matches",
            "raw_coordinate_matches",
            "raw_ipv4_matches",
            "raw_address_matches",
            "raw_url_matches",
            "raw_mention_matches",
        ):
            self.assertGreater(report[key], 0, key)
        self.assertEqual(scan_redacted_text("tokenizer self target")["hard_leak_count"], 0)

    def test_structured_candidate_and_session_scan_content_only(self):
        candidate = {
            "sample_id": "sample-1",
            "session_id": "session-1",
            "messages": [
                {"role": "system", "content": "policy"},
                {"role": "user", "content": "邮箱 a@example.com"},
                {"role": "assistant", "content": "<PII_CONTACT>"},
            ],
            # Metadata must not be scanned as message content.
            "metadata": {"source_message_ids": ["m1"], "timestamp": "13800138000"},
        }
        report = scan_candidate(candidate)
        self.assertEqual(report["object_id"], "sample-1")
        self.assertEqual(report["raw_email_matches"], 1)
        self.assertEqual(report["raw_phone_matches"], 0)

        session = {"session_id": "session-1", "turns": [{"speaker_role": "self", "text_redacted": "<PII_CONTACT>"}]}
        self.assertEqual(scan_session(session)["hard_leak_count"], 0)
        self.assertRegex(PRIVACY_SCANNER_VERSION, r"^wechat-privacy-scanner-v\d+$")

    def test_stale_boundary_views_cannot_hide_public_leaks(self):
        candidate = {
            "sample_id": "sample-1",
            "messages": [
                {"role": "user", "content": "password"},
                {"role": "assistant", "content": "abc123"},
            ],
            "privacy_scan_messages": [{"content": "safe"}],
        }
        self.assertGreater(scan_candidate(candidate)["hard_leak_count"], 0)

        session = {
            "session_id": "session-1",
            "turns": [{
                "speaker_role": "self",
                "text_redacted": "a@example.com",
                "message_contents": ["safe"],
                "source_record_indices": [1],
            }],
        }
        self.assertEqual(scan_session(session)["raw_email_matches"], 1)

    def test_position_without_explicit_address_marker_is_not_a_location(self):
        self.assertEqual(scan_redacted_text("位置\n普通对话")["hard_leak_count"], 0)
        self.assertEqual(scan_redacted_text("位置：上海市静安区")["raw_address_matches"], 1)
        self.assertEqual(redact_text("位置：上海市静安区").text, "<PRIVATE_LOCATION>")

    def test_adjacent_secret_redaction_requires_original_source_adjacency(self):
        rows = [
            {"content": "验证码", "source_record_index": 10},
            {"content": "123456", "source_record_index": 11},
        ]
        redacted = redact_adjacent_messages(rows)
        self.assertEqual(redacted[1]["content"], "<SECRET>")
        self.assertEqual(scan_adjacent_messages(redacted)["hard_leak_count"], 0)

        punctuated = redact_adjacent_messages([
            {"content": "验证码", "source_record_index": 12},
            {"content": "`123456`", "source_record_index": 13},
        ])
        self.assertEqual(punctuated[1]["content"], "`<SECRET>`")
        self.assertEqual(scan_adjacent_messages(punctuated)["hard_leak_count"], 0)

        filtered_gap = [
            {"content": "密码", "source_record_index": 20},
            {"content": "abc123", "source_record_index": 22},
        ]
        self.assertEqual(scan_adjacent_messages(filtered_gap)["hard_leak_count"], 0)
        self.assertEqual(redact_adjacent_messages(filtered_gap)[1]["content"], "abc123")
        self.assertEqual(count_adjacent_secret_redactions(rows), 1)
        self.assertEqual(count_adjacent_secret_redactions(filtered_gap), 0)

    def test_merged_session_preserves_message_boundaries_for_credentials(self):
        """A label and token from separate merged same-speaker messages must
        still be treated as a real cross-message credential, while a label
        plus unrelated token within one source message is not reconstructed by
        the merged newline representation.
        """
        from wechat_persona.sessionize import sessionize

        merged = sessionize([
            {"message_id": "m1", "source_record_index": 0, "timestamp": "2026-09-01T10:00:00+08:00", "speaker_role": "self", "message_kind": "text", "text_redacted": "密码"},
            {"message_id": "m2", "source_record_index": 1, "timestamp": "2026-09-01T10:00:30+08:00", "speaker_role": "self", "message_kind": "text", "text_redacted": "hello"},
            {"message_id": "m3", "source_record_index": 2, "timestamp": "2026-09-01T10:01:00+08:00", "speaker_role": "target", "message_kind": "text", "text_redacted": "abc123"},
        ])[0]
        # The first message is itself a credential label, so the original
        # adjacent boundary remains detectable despite same-role merging.
        report = scan_session(merged)
        self.assertEqual(report["raw_secret_matches"], 0)
        self.assertEqual(report["cross_message_secret_matches"], 1)

        unrelated = sessionize([
            {"message_id": "m4", "source_record_index": 3, "timestamp": "2026-09-01T10:00:00+08:00", "speaker_role": "self", "message_kind": "text", "text_redacted": "密码怎么改"},
            {"message_id": "m5", "source_record_index": 4, "timestamp": "2026-09-01T10:00:30+08:00", "speaker_role": "self", "message_kind": "text", "text_redacted": "hello"},
        ])[0]
        unrelated_report = scan_session(unrelated)
        self.assertEqual(unrelated_report["raw_secret_matches"], 0)
        self.assertEqual(unrelated_report["cross_message_secret_matches"], 0)

    def test_filtered_source_record_does_not_create_session_or_candidate_adjacency(self):
        from wechat_persona.datasets import build_review_candidates

        session = {
            "session_id": "session-gap",
            "session_rule_version": "wechat-session-v2",
            "session_rule_version": "wechat-session-v2",
            "turns": [
                {
                    "speaker_role": "self",
                    "text_redacted": "密码",
                    "message_ids": ["m1"],
                    "source_record_indices": [100],
                    "message_contents": ["密码"],
                },
                {
                    "speaker_role": "target",
                    "text_redacted": "abc123",
                    "message_ids": ["m3"],
                    "source_record_indices": [102],
                    "message_contents": ["abc123"],
                },
            ],
        }
        self.assertEqual(scan_session(session)["hard_leak_count"], 0)
        candidate = build_review_candidates(
            [session], split_by_session={"session-gap": "train"}
        )[0]
        self.assertEqual(scan_candidate(candidate)["hard_leak_count"], 0)


if __name__ == "__main__":
    unittest.main()
