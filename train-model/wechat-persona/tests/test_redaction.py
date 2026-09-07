import unittest

from wechat_persona.normalize import normalize_message
from wechat_persona.redact import redact_text, scan_redacted_text


class RedactionTests(unittest.TestCase):
    def test_normalize_and_redact(self):
        msg = {"message_id": "m1", "timestamp": "2026-09-01 10:00", "speaker": "target", "kind": "text", "text": " 联系 13800138000，邮箱 a@example.com，密码: abc123 "}
        normalized = normalize_message(msg, timezone_name="Asia/Shanghai")
        redacted = redact_text(normalized["text_redacted"])
        self.assertNotIn("13800138000", redacted.text)
        self.assertNotIn("a@example.com", redacted.text)
        self.assertEqual(scan_redacted_text(redacted.text)["hard_leak_count"], 0)


if __name__ == "__main__":
    unittest.main()
