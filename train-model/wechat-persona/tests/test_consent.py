import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wechat_persona.consent import ConsentError, consent_digest, verify_consent


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

    def test_verified_result_is_minimal_and_file_digest_is_recorded(self):
        record = self._record()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "consent.json"
            path.write_text(json.dumps(record), encoding="utf-8")
            checked = verify_consent(path, required_purposes={"processing"})
            expected_file_digest = checked["consent_file_sha256"]
        self.assertEqual(checked["consent_id"], "c1")
        self.assertEqual(len(checked["consent_file_sha256"]), 64)
        self.assertNotIn("subject_id", checked)
        self.assertNotIn("withdrawal_key", checked.get("scope", {}))
        self.assertEqual(expected_file_digest, checked["consent_file_sha256"])

    def test_malformed_types_duplicate_keys_and_future_verification_fail_closed(self):
        with self.assertRaises(ConsentError):
            verify_consent({**self._record(), "purposes": "processing"})
        with self.assertRaises(ConsentError):
            verify_consent({**self._record(), "scope": {**self._record()["scope"], "message_types": ["text", "text"]}})
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "duplicate.json"
            path.write_text('{\"consent_id\":\"a\",\"consent_id\":\"b\"}', encoding="utf-8")
            with self.assertRaises(ConsentError):
                verify_consent(path)
        future = datetime.now(timezone.utc) + timedelta(days=1)
        with self.assertRaises(ConsentError):
            verify_consent(self._record(verified_at=future.isoformat()))

    def test_invalid_scope_dates_fail_closed(self):
        base = self._record()
        for field, value in (
            ("time_start", "not-a-date"),
            ("time_end", "not-a-date"),
            ("time_start", 0),
            ("time_end", False),
            ("retention_until", 4102444800),
        ):
            with self.subTest(field=field, value=value):
                scope = {**base["scope"], field: value}
                with self.assertRaises(ConsentError):
                    verify_consent({**base, "scope": scope})
        with self.assertRaisesRegex(ConsentError, "verified_at"):
            verify_consent(self._record(verified_at=0))

    def test_unknown_fields_fail_closed(self):
        with self.assertRaisesRegex(ConsentError, "unknown fields"):
            verify_consent({**self._record(), "unexpected": True})
        scope = {**self._record()["scope"], "unexpected": True}
        with self.assertRaisesRegex(ConsentError, "unknown scope fields"):
            verify_consent({**self._record(), "scope": scope})

    def test_required_values_reject_non_strings_and_unknown_types(self):
        with self.assertRaisesRegex(ConsentError, "required purpose"):
            verify_consent(self._record(), required_purposes="processing")
        with self.assertRaisesRegex(ConsentError, "required purpose"):
            verify_consent(self._record(), required_purposes=[1])
        with self.assertRaisesRegex(ConsentError, "required message type"):
            verify_consent(self._record(), required_message_types=[True])
        with self.assertRaisesRegex(ConsentError, "required media type"):
            verify_consent(self._record(), required_media_types=[{"image": True}])
        with self.assertRaisesRegex(ConsentError, "required message type"):
            verify_consent(self._record(), required_message_types=["future_kind"])
        with self.assertRaisesRegex(ConsentError, "required media type"):
            verify_consent(self._record(), required_media_types=["future_media"])

    def test_evidence_ref_and_declared_type_enums_fail_closed(self):
        with self.assertRaisesRegex(ConsentError, "evidence_ref"):
            verify_consent({**self._record(), "evidence_ref": {"uri": "ledger:c1"}})
        with self.assertRaisesRegex(ConsentError, "message type"):
            verify_consent({
                **self._record(),
                "scope": {
                    **self._record()["scope"],
                    "message_types": ["text", "future_kind"],
                },
            })
        with self.assertRaisesRegex(ConsentError, "media type"):
            verify_consent({
                **self._record(),
                "scope": {
                    **self._record()["scope"],
                    "media_types": ["future_media"],
                },
            })


if __name__ == "__main__":
    unittest.main()
