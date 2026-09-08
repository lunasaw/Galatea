import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "import_chat.py"
CONFIG = ROOT / "configs" / "import.yaml"


class ImportChatCheckTests(unittest.TestCase):
    def _consent(self, path: Path, *, purposes: list[str] | None = None) -> None:
        now = datetime.now(timezone.utc)
        path.write_text(json.dumps({
            "consent_id": "consent-check-1",
            "subject_id": "subject-must-not-be-printed",
            "adult_verified": True,
            "purposes": purposes or ["processing", "persona_style"],
            "scope": {
                "message_types": ["text"],
                "media_types": [],
                "time_start": None,
                "time_end": None,
                "third_party_policy": "exclude",
                "retention_until": (now + timedelta(days=1)).isoformat(),
                "withdrawal_key": "withdrawal-must-not-be-printed",
            },
            "status": "verified",
            "ledger_version": "v1",
            "verified_at": now.isoformat(),
        }), encoding="utf-8")

    def _run(self, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--config", str(CONFIG), *args],
            cwd=ROOT.parent.parent,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_check_requires_source_and_consent(self):
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        result = self._run(["--check"], env)
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "blocked")
        self.assertFalse(payload["will_create_mlflow_run"])

    def test_check_requires_explicit_controlled_source_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "chat.txt"
            source.write_text("2026-09-01 10:00\tMe: hello\n", encoding="utf-8")
            consent = root / "consent.json"
            self._consent(consent)
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
            env.pop("WECHAT_PERSONA_RAW_ROOT", None)
            result = self._run([
                "--check", "--source", str(source), "--consent", str(consent),
                "--self-speaker", "Me", "--target-speaker", "Target",
            ], env)
            self.assertEqual(result.returncode, 2)
            self.assertIn("controlled source root", result.stdout)

    def test_check_runs_full_read_only_preflight_without_raw_fields(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            raw = root / "raw"
            raw.mkdir()
            source = raw / "chat.json"
            source.write_text(json.dumps({"messages": [
                {"id": "m1", "timestamp": "2026-09-01T10:00:00+08:00", "speaker": "Me", "text": "hello", "kind": "text"},
                {"id": "m2", "timestamp": "2026-09-01T10:01:00+08:00", "speaker": "Target", "text": "reply", "kind": "text"},
            ]}), encoding="utf-8")
            consent = root / "consent.json"
            self._consent(consent)
            speaker_map = root / "speaker-map.json"
            speaker_map.write_text(json.dumps({"self_speaker": "Me", "target_speaker": "Target"}), encoding="utf-8")
            output = root / "must-not-be-created"
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "WECHAT_PERSONA_RAW_ROOT": str(raw)}
            result = self._run([
                "--check-source", "--source", str(source), "--consent", str(consent),
                "--speaker-map-file", str(speaker_map), "--output-root", str(output),
            ], env)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "planned")
            self.assertEqual(payload["raw_message_count"], 2)
            self.assertEqual(payload["filtered_message_count"], 2)
            self.assertEqual(payload["retained_self_target_text_count"], 2)
            self.assertEqual(payload["privacy_counts"]["messages"]["hard_leak_count"], 0)
            self.assertEqual(payload["privacy_counts"]["sessions"]["hard_leak_count"], 0)
            self.assertEqual(payload["privacy_counts"]["candidates"]["hard_leak_count"], 0)
            self.assertEqual(len(payload["source_sha256"]), 64)
            self.assertEqual(len(payload["consent_file_sha256"]), 64)
            self.assertNotIn("subject-must-not-be-printed", result.stdout)
            self.assertNotIn("withdrawal-must-not-be-printed", result.stdout)
            self.assertNotIn("hello", result.stdout)
            self.assertFalse(output.exists())

    def test_check_blocks_invalid_consent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            raw = root / "raw"
            raw.mkdir()
            source = raw / "chat.txt"
            source.write_text("2026-09-01 10:00\tMe: hello\n", encoding="utf-8")
            consent = root / "consent.json"
            self._consent(consent, purposes=["processing"])
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "WECHAT_PERSONA_RAW_ROOT": str(raw)}
            result = self._run([
                "--check", "--source", str(source), "--consent", str(consent),
                "--self-speaker", "Me", "--target-speaker", "Target",
            ], env)
            self.assertEqual(result.returncode, 2)
            self.assertIn("consent_scope_missing", result.stdout)

    def test_check_blocks_source_outside_configured_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            allowed = root / "allowed"
            outside = root / "outside"
            allowed.mkdir()
            outside.mkdir()
            source = outside / "chat.txt"
            source.write_text("2026-09-01 10:00\tMe: hello\n", encoding="utf-8")
            consent = root / "consent.json"
            self._consent(consent)
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "WECHAT_PERSONA_RAW_ROOT": str(allowed)}
            result = self._run([
                "--check", "--source", str(source), "--consent", str(consent),
                "--self-speaker", "Me", "--target-speaker", "Target",
            ], env)
            self.assertEqual(result.returncode, 2)
            self.assertIn("symlink_escape", result.stdout)

    def test_check_rejects_ambiguous_or_extra_speaker_map_fields(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            raw = root / "raw"
            raw.mkdir()
            source = raw / "chat.txt"
            source.write_text(
                "2026-09-01 10:00\tMe: hello\n",
                encoding="utf-8",
            )
            consent = root / "consent.json"
            self._consent(consent)
            env = {
                **os.environ,
                "PYTHONPATH": str(ROOT / "src"),
                "WECHAT_PERSONA_RAW_ROOT": str(raw),
            }
            for mapping, expected in (
                (
                    {"self_speaker": "Same", "target_speaker": "Same"},
                    "must differ",
                ),
                (
                    {
                        "self_speaker": "Me",
                        "target_speaker": "Target",
                        "raw_identity": "must-not-be-accepted",
                    },
                    "must contain only",
                ),
            ):
                with self.subTest(mapping=mapping):
                    speaker_map = root / "speaker-map.json"
                    speaker_map.write_text(json.dumps(mapping), encoding="utf-8")
                    result = self._run([
                        "--check",
                        "--source",
                        str(source),
                        "--consent",
                        str(consent),
                        "--speaker-map-file",
                        str(speaker_map),
                    ], env)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(expected, result.stdout)


if __name__ == "__main__":
    unittest.main()
