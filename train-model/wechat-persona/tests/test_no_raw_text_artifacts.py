import json
import stat
import tempfile
import unittest.mock
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wechat_persona.pipeline import PipelineError, build_dataset_id, run_import_pipeline
from wechat_persona.consent import ConsentError


class NoRawArtifactsTests(unittest.TestCase):
    def _consent(self, root: Path, *, purposes: list[str] | None = None) -> Path:
        now = datetime.now(timezone.utc)
        consent = root / "consent.json"
        consent.write_text(json.dumps({
            "consent_id": "c1", "subject_id": "s1", "adult_verified": True,
            "purposes": purposes or ["processing", "persona_style"],
            "scope": {"message_types": ["text"], "media_types": [],
            "time_start": None, "time_end": None, "third_party_policy": "exclude",
            "retention_until": (now + timedelta(days=1)).isoformat(), "withdrawal_key": "w"},
            "status": "verified", "ledger_version": "v1", "verified_at": now.isoformat(),
        }), encoding="utf-8")
        return consent

    def test_execute_requires_persona_style_consent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); raw = root / "raw"; raw.mkdir()
            source = raw / "chat.txt"
            source.write_text("2026-09-01 10:00\tMe: hello\n", encoding="utf-8")
            consent = self._consent(root, purposes=["processing"])
            with self.assertRaises(ConsentError):
                run_import_pipeline(source=source, consent=consent, output_root=root / "out",
                                    allowed_root=raw,
                                    speaker_map={"Me": "self", "Target": "target"},
                                    execute=True)

    def test_pipeline_requires_exact_self_and_target_binding(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); raw = root / "raw"; raw.mkdir()
            source = raw / "chat.txt"
            source.write_text("2026-09-01 10:00\tMe: hello\n", encoding="utf-8")
            with self.assertRaisesRegex(PipelineError, "exactly one self and one target"):
                run_import_pipeline(
                    source=source,
                    consent=self._consent(root),
                    output_root=root / "out",
                    allowed_root=raw,
                    speaker_map={"Me": "self"},
                    execute=False,
                )

    def test_dataset_id_binds_processing_redaction_and_scanner_versions(self):
        base = {
            "source_sha256": "a" * 64,
            "consent_digest": "b" * 64,
            "preprocessing_version": "pre-v1",
            "redaction_version": "redact-v1",
            "scanner_version": "scan-v1",
        }
        first = build_dataset_id(**base)
        for key in ("preprocessing_version", "redaction_version", "scanner_version"):
            changed = {**base, key: base[key] + "-changed"}
            self.assertNotEqual(first, build_dataset_id(**changed), key)
        selected = build_dataset_id(
            **base,
            selection_version="candidate-selection-v1",
            selection_manifest_digest="c" * 64,
        )
        self.assertNotEqual(first, selected)
        self.assertNotEqual(
            selected,
            build_dataset_id(
                **base,
                selection_version="candidate-selection-v2",
                selection_manifest_digest="c" * 64,
            ),
        )
        with self.assertRaises(PipelineError):
            build_dataset_id(
                **base,
                selection_manifest_digest="c" * 64,
            )

    def test_manifest_records_counts_versions_and_only_minimal_consent_fields(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); raw = root / "raw"; raw.mkdir()
            source = raw / "chat.json"
            source.write_text(json.dumps({"messages": [
                {"id": "m1", "timestamp": "2026-09-01T10:00:00+08:00",
                 "speaker": "Me", "text": "hello", "kind": "text"},
                {"id": "m2", "timestamp": "2026-09-01T10:01:00+08:00",
                 "speaker": "Target", "text": "reply", "kind": "text"},
                {"id": "m3", "timestamp": "2026-09-01T10:02:00+08:00",
                 "speaker": "Target", "text": "", "kind": "image"},
            ]}), encoding="utf-8")
            result = run_import_pipeline(
                source=source, consent=self._consent(root), output_root=root / "out",
                allowed_root=raw, speaker_map={"Me": "self", "Target": "target"}, execute=True,
            )
            manifest = json.loads(
                (result["dataset_root"] / "manifests/source_manifest.json").read_text(encoding="utf-8")
            )
            split_manifest = json.loads(
                (result["dataset_root"] / "manifests/split_manifest.json").read_text(encoding="utf-8")
            )
            sessions = [
                json.loads(line)
                for line in (result["dataset_root"] / "sessions/sessions.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            candidates = [
                json.loads(line)
                for line in (result["dataset_root"] / "review/candidates.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
        self.assertEqual(manifest["raw_message_count"], 3)
        self.assertEqual(manifest["filtered_message_count"], 2)
        self.assertEqual(manifest["retained_self_target_text_count"], 2)
        self.assertEqual(manifest["excluded_by_consent"]["message_type"], 1)
        self.assertEqual(manifest["unknown_role_count"], 0)
        self.assertEqual(manifest["duplicate_message_id_count"], 0)
        self.assertEqual(manifest["timestamp_parse_failure_count"], 0)
        self.assertIn("preprocessing_version", manifest)
        self.assertIn("redaction_version", manifest)
        self.assertIn("scanner_version", manifest)
        self.assertIn("consent_file_sha256", manifest)
        self.assertIn("manifest_sha256", manifest)
        self.assertEqual(manifest["split_sha256"], split_manifest["split_sha256"])
        self.assertEqual(sessions[0]["turns"][0]["message_contents"], ["hello"])
        self.assertEqual(sessions[0]["turns"][1]["message_contents"], ["reply"])
        self.assertEqual(
            candidates[0]["privacy_scan_messages"],
            [
                {"content": "hello", "source_record_index": 0},
                {"content": "reply", "source_record_index": 1},
            ],
        )
        self.assertNotIn("withdrawal_key", manifest)
        self.assertNotIn("subject_id", manifest)

    def test_duplicate_id_unknown_role_and_bad_timestamp_block_before_write(self):
        cases = [
            ([
                {"message_id": "m1", "timestamp": "2026-09-01T10:00:00+08:00", "speaker": "Me", "text": "a"},
                {"message_id": "m1", "timestamp": "2026-09-01T10:01:00+08:00", "speaker": "Target", "text": "b"},
            ], "duplicate_message_id"),
            ([
                {"message_id": "m1", "timestamp": "2026-09-01T10:00:00+08:00", "speaker": "Unmapped", "text": "a"},
            ], "unknown_role"),
            ([
                {"message_id": "m1", "timestamp": "not-a-time", "speaker": "Me", "text": "a"},
            ], "timestamp_parse_failure"),
        ]
        for raw_rows, reason in cases:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as td:
                root = Path(td); raw = root / "raw"; raw.mkdir()
                source = raw / "chat.json"; source.write_text("{}", encoding="utf-8")
                source_manifest = {"source_sha256": "a" * 64, "source_size_bytes": 2,
                                   "message_count": len(raw_rows), "importer_version": "fixture",
                                   "format": "json", "source_ref": "conversations/messages"}
                with unittest.mock.patch("wechat_persona.pipeline.import_messages",
                                         return_value=(raw_rows, source_manifest)):
                    with self.assertRaisesRegex(PipelineError, reason):
                        run_import_pipeline(
                            source=source, consent=self._consent(root), output_root=root / "out",
                            allowed_root=raw, speaker_map={"Me": "self", "Target": "target"},
                            execute=True,
                        )

    def test_plan_is_read_only_and_execute_writes_only_redacted_text(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); raw = root / "raw"; output = root / "out"; raw.mkdir()
            canary = "canary-personal-13800138000"
            source = raw / "chat.txt"
            source.write_text(f"2026-09-01 10:00\tMe: {canary}\n2026-09-01 10:01\tTarget: hello\n", encoding="utf-8")
            consent = self._consent(root)
            plan = run_import_pipeline(source=source, consent=consent, output_root=output, allowed_root=raw, speaker_map={"Me": "self", "Target": "target"}, execute=False)
            self.assertFalse(output.exists())
            self.assertFalse(plan["will_write"])
            result = run_import_pipeline(source=source, consent=consent, output_root=output, allowed_root=raw, speaker_map={"Me": "self", "Target": "target"}, execute=True)
            self.assertTrue(result["will_write"])
            emitted = "\n".join(path.read_text(encoding="utf-8") for path in result["dataset_root"].rglob("*.json*"))
            self.assertNotIn(canary, emitted)
            self.assertNotIn("13800138000", emitted)
            self.assertIn("<PII_CONTACT>", emitted)
            sessions_text = (result["dataset_root"] / "sessions/sessions.jsonl").read_text(encoding="utf-8")
            self.assertIn("message_contents", sessions_text)

    def test_manifest_records_cross_message_secret_redaction_count_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); raw = root / "raw"; raw.mkdir()
            source = raw / "chat.json"
            source.write_text(json.dumps({"messages": [
                {"id": "m1", "timestamp": "2026-09-01T10:00:00+08:00",
                 "speaker": "Me", "text": "验证码", "kind": "text"},
                {"id": "m2", "timestamp": "2026-09-01T10:01:00+08:00",
                 "speaker": "Target", "text": "123456", "kind": "text"},
            ]}), encoding="utf-8")

            result = run_import_pipeline(
                source=source,
                consent=self._consent(root),
                output_root=root / "out",
                allowed_root=raw,
                speaker_map={"Me": "self", "Target": "target"},
                execute=True,
            )
            manifest = json.loads(
                (result["dataset_root"] / "manifests/source_manifest.json")
                .read_text(encoding="utf-8")
            )
            privacy_report = json.loads(
                (result["dataset_root"] / "reports/privacy_report.json")
                .read_text(encoding="utf-8")
            )

        expected = {"cross_message_secret_redactions": 1}
        self.assertEqual(manifest["redaction_counts"], expected)
        self.assertEqual(privacy_report["redaction_counts"], expected)
        self.assertEqual(result["redaction_counts"], expected)

    def test_execute_enforces_private_directory_and_file_modes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); raw = root / "raw"; raw.mkdir()
            source = raw / "chat.txt"
            source.write_text(
                "2026-09-01 10:00\tMe: hello\n"
                "2026-09-01 10:01\tTarget: reply\n",
                encoding="utf-8",
            )
            result = run_import_pipeline(
                source=source,
                consent=self._consent(root),
                output_root=root / "out",
                allowed_root=raw,
                speaker_map={"Me": "self", "Target": "target"},
                execute=True,
            )

            directory_modes = {
                stat.S_IMODE(path.stat().st_mode)
                for path in [result["dataset_root"], *result["dataset_root"].rglob("*")]
                if path.is_dir()
            }
            file_modes = {
                stat.S_IMODE(path.stat().st_mode)
                for path in result["dataset_root"].rglob("*")
                if path.is_file()
            }

        self.assertEqual(directory_modes, {0o700})
        self.assertEqual(file_modes, {0o600})


if __name__ == "__main__":
    unittest.main()
