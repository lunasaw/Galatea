import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wechat_persona.pipeline import run_import_pipeline


class NoRawArtifactsTests(unittest.TestCase):
    def test_plan_is_read_only_and_execute_writes_only_redacted_text(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); raw = root / "raw"; output = root / "out"; raw.mkdir()
            canary = "canary-personal-13800138000"
            source = raw / "chat.txt"
            source.write_text(f"2026-09-01 10:00\tMe: {canary}\n2026-09-01 10:01\tTarget: hello\n", encoding="utf-8")
            now = datetime.now(timezone.utc)
            consent = root / "consent.json"
            consent.write_text(json.dumps({
                "consent_id": "c1", "subject_id": "s1", "adult_verified": True,
                "purposes": ["processing"], "scope": {"message_types": ["text"], "media_types": [],
                "time_start": None, "time_end": None, "third_party_policy": "exclude",
                "retention_until": (now + timedelta(days=1)).isoformat(), "withdrawal_key": "w"},
                "status": "verified", "ledger_version": "v1", "verified_at": now.isoformat(),
            }), encoding="utf-8")
            plan = run_import_pipeline(source=source, consent=consent, output_root=output, allowed_root=raw, speaker_map={"Me": "self", "Target": "target"}, execute=False)
            self.assertFalse(output.exists())
            self.assertFalse(plan["will_write"])
            result = run_import_pipeline(source=source, consent=consent, output_root=output, allowed_root=raw, speaker_map={"Me": "self", "Target": "target"}, execute=True)
            self.assertTrue(result["will_write"])
            emitted = "\n".join(path.read_text(encoding="utf-8") for path in result["dataset_root"].rglob("*.json*"))
            self.assertNotIn(canary, emitted)
            self.assertNotIn("13800138000", emitted)
            self.assertIn("<PII_CONTACT>", emitted)


if __name__ == "__main__":
    unittest.main()
