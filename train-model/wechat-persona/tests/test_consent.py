import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wechat_persona.consent import ConsentError, verify_consent, consent_digest


class ConsentTests(unittest.TestCase):
    def _record(self, **overrides):
        now = datetime.now(timezone.utc)
        record = {
            "consent_id": "c1", "subject_id": "subject_a", "adult_verified": True,
            "purposes": ["processing", "persona_style", "memory_rag", "evaluation"],
            "scope": {"message_types": ["text"], "media_types": [], "time_start": None,
                      "time_end": None, "third_party_policy": "exclude",
                      "retention_until": (now + timedelta(days=2)).isoformat(), "withdrawal_key": "w1"},
            "status": "verified", "ledger_version": "v1", "verified_at": now.isoformat(),
            "evidence_ref": "ledger:c1",
        }
        record.update(overrides)
        return record

    def test_verified_scope_and_digest(self):
        record = self._record()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "consent.json"
            path.write_text(json.dumps(record), encoding="utf-8")
            verified = verify_consent(path, required_purposes={"processing", "persona_style"}, now=datetime.now(timezone.utc))
        self.assertEqual(verified["consent_id"], "c1")
        self.assertEqual(len(consent_digest(verified)), 64)

    def test_missing_purpose_expired_or_withdrawn_fail_closed(self):
        for mutation in (
            {"purposes": ["processing"]},
            {"scope": {**self._record()["scope"], "retention_until": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}},
            {"status": "withdrawn"},
        ):
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / "consent.json"
                path.write_text(json.dumps(self._record(**mutation)), encoding="utf-8")
                with self.assertRaises(ConsentError):
                    verify_consent(path, required_purposes={"persona_style"})

    def test_third_party_content_requires_separate_authorization(self):
        with self.assertRaises(ConsentError):
            verify_consent(self._record(), includes_third_party=True)
        record = self._record(scope={**self._record()["scope"], "third_party_policy": "separate_authorization"})
        self.assertEqual(verify_consent(record, includes_third_party=True)["authorization_status"], "verified")


if __name__ == "__main__":
    unittest.main()
