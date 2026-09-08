import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from wechat_persona._common import digest, file_digest
from wechat_persona.datasets import DatasetError
from wechat_persona.review import _content_hash


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_dataset.py"
_SPEC = importlib.util.spec_from_file_location("build_dataset_script", _SCRIPT)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
validate_formal_inputs = _MODULE.validate_formal_inputs


class FormalInputValidationTests(unittest.TestCase):
    def _write_json(self, path: Path, value: dict) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _fixture(self, root: Path) -> dict:
        reviewed = root / "reviewed.jsonl"
        rows = []
        split_sessions = {
            "train": ["session-train"],
            "validation": ["session-validation"],
            "test": ["session-test"],
        }
        lineage_rows = []
        for split, session_ids in split_sessions.items():
            sample_id = f"sample-{split}"
            session_id = session_ids[0]
            target_id = f"target-{split}"
            context_id = f"context-{split}"
            row = {
                "sample_id": sample_id,
                "session_id": session_id,
                "messages": [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "reply"},
                ],
                "metadata": {
                    "review_status": "keep",
                    "split": split,
                    "reviewer_id": "reviewer-1",
                    "reviewed_at": "2026-09-07T00:00:00+00:00",
                    "source_message_ids": [target_id],
                    "context_message_ids": [context_id],
                },
            }
            row["metadata"]["content_sha256"] = _content_hash(row)
            rows.append(row)
            lineage_rows.append({
                "object_id": sample_id,
                "stage": "candidate",
                "source_message_ids": [context_id, target_id],
                "target_message_ids": [target_id],
                "context_message_ids": [context_id],
                "source_session_ids": [session_id],
                "consent_scope": "processing+persona_style",
            })
        reviewed.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )

        candidate_manifest_sha256 = "c" * 64
        split_manifest = {
            "strategy": "chronological_session",
            "session_ids_by_split": split_sessions,
            "session_counts": {split: 1 for split in split_sessions},
            "candidate_counts": {split: 1 for split in split_sessions},
            "candidate_manifest_sha256": candidate_manifest_sha256,
            "split_sha256": digest(split_sessions),
        }
        split_manifest["manifest_sha256"] = digest({
            key: value
            for key, value in split_manifest.items()
            if key not in {"split_sha256", "manifest_sha256"}
        })

        zero_layers = {
            layer: {"hard_leak_count": 0}
            for layer in ("messages", "sessions", "candidates")
        }
        source_manifest = {
            "dataset_id": "wechat-fixture",
            "source_sha256": "a" * 64,
            "consent_id": "consent-fixture",
            "consent_file_sha256": "d" * 64,
            "consent_digest": "b" * 64,
            "authorization_status": "verified",
            "raw_message_count": 3,
            "filtered_message_count": 3,
            "retained_self_target_text_count": 3,
            "excluded_by_consent": {
                "message_type": 0,
                "time_scope": 0,
                "third_party": 0,
            },
            "unknown_role_count": 0,
            "duplicate_message_id_count": 0,
            "timestamp_parse_failure_count": 0,
            "redaction_counts": {
                "cross_message_secret_redactions": 0,
            },
            "preprocessing_version": "pre-v2",
            "redaction_version": "wechat-redaction-v2",
            "scanner_version": "wechat-privacy-scanner-v3",
            "split_sha256": split_manifest["split_sha256"],
            "candidate_manifest_sha256": candidate_manifest_sha256,
            "privacy_counts": copy.deepcopy(zero_layers),
        }
        source_manifest["manifest_sha256"] = digest(source_manifest)

        paths = {
            "reviewed_jsonl": reviewed,
            "source_manifest_path": root / "source-manifest.json",
            "split_manifest_path": root / "split-manifest.json",
            "privacy_report_path": root / "privacy-report.json",
            "lineage_path": root / "lineage.jsonl",
            "review_summary_path": root / "review-summary.json",
        }
        self._write_json(paths["source_manifest_path"], source_manifest)
        self._write_json(paths["split_manifest_path"], split_manifest)
        self._write_json(paths["privacy_report_path"], {
            "schema_version": "wechat-privacy-report-v2",
            "status": "pass",
            "hard_leak_count": 0,
            "layers": copy.deepcopy(zero_layers),
            "message_count": 3,
            "session_count": 3,
            "candidate_count": 3,
            "excluded_by_consent": copy.deepcopy(
                source_manifest["excluded_by_consent"]
            ),
            "scanner_version": source_manifest["scanner_version"],
            "redaction_counts": copy.deepcopy(source_manifest["redaction_counts"]),
        })
        paths["lineage_path"].write_text(
            "".join(json.dumps(row) + "\n" for row in lineage_rows),
            encoding="utf-8",
        )
        review_digest = file_digest(reviewed)
        self._write_json(paths["review_summary_path"], {
            "selected_scope": "all",
            "selected_count": 3,
            "candidate_count": 3,
            "event_count": 3,
            "exported_count": 3,
            "uncertain_count": 0,
            "status_counts": {
                "keep": 3,
                "redact_keep": 0,
                "reject": 0,
                "uncertain": 0,
            },
            "rejected_counts_by_split": {
                "train": 0,
                "validation": 0,
                "test": 0,
            },
            "reviewed_hard_leak_count": 0,
            "human_review_completed": True,
            "review_evidence_digest": review_digest,
            "candidate_manifest_sha256": candidate_manifest_sha256,
        })
        config = {"dataset": {
            "dataset_id": source_manifest["dataset_id"],
            "source_sha256": source_manifest["source_sha256"],
            "manifest_sha256": source_manifest["manifest_sha256"],
            "split_sha256": split_manifest["split_sha256"],
            "consent_digest": source_manifest["consent_digest"],
            "preprocessing_version": source_manifest["preprocessing_version"],
            "redaction_version": source_manifest["redaction_version"],
            "scanner_version": source_manifest["scanner_version"],
            "review_evidence_digest": review_digest,
        }}
        return {"config": config, "rows": rows, **paths}

    def test_accepts_consistent_formal_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fixture = self._fixture(Path(td))
            rows, identity = validate_formal_inputs(
                **{key: value for key, value in fixture.items() if key not in {"rows"}}
            )
        self.assertEqual(len(rows), 3)
        self.assertEqual(identity["dataset_id"], "wechat-fixture")

    def test_rejects_manifest_split_privacy_lineage_and_review_tampering(self) -> None:
        cases = (
            ("source_manifest_path", "source_sha256", "d" * 64, "source"),
            ("split_manifest_path", "split_sha256", "e" * 64, "split"),
            ("privacy_report_path", "hard_leak_count", 1, "privacy"),
            ("review_summary_path", "uncertain_count", 1, "uncertain"),
        )
        for path_key, field, value, message in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as td:
                fixture = self._fixture(Path(td))
                path = fixture[path_key]
                document = json.loads(path.read_text(encoding="utf-8"))
                document[field] = value
                self._write_json(path, document)
                with self.assertRaisesRegex(DatasetError, message):
                    validate_formal_inputs(
                        **{key: child for key, child in fixture.items() if key != "rows"}
                    )

        with tempfile.TemporaryDirectory() as td:
            fixture = self._fixture(Path(td))
            fixture["lineage_path"].write_text("", encoding="utf-8")
            with self.assertRaisesRegex(DatasetError, "lineage"):
                validate_formal_inputs(
                    **{key: child for key, child in fixture.items() if key != "rows"}
                )

    def test_rejects_unverified_consent_and_nonzero_import_anomalies(self) -> None:
        cases = (
            ("authorization_status", "blocked", "authorization"),
            ("unknown_role_count", 1, "unknown_role_count"),
            ("duplicate_message_id_count", 1, "duplicate_message_id_count"),
            ("timestamp_parse_failure_count", 1, "timestamp_parse_failure_count"),
        )
        for field, value, message in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as td:
                fixture = self._fixture(Path(td))
                manifest = json.loads(
                    fixture["source_manifest_path"].read_text(encoding="utf-8")
                )
                manifest[field] = value
                manifest["manifest_sha256"] = digest({
                    key: child
                    for key, child in manifest.items()
                    if key != "manifest_sha256"
                })
                self._write_json(fixture["source_manifest_path"], manifest)
                fixture["config"]["dataset"]["manifest_sha256"] = manifest[
                    "manifest_sha256"
                ]
                with self.assertRaisesRegex(DatasetError, message):
                    validate_formal_inputs(
                        **{
                            key: child
                            for key, child in fixture.items()
                            if key != "rows"
                        }
                    )

    def test_rejects_source_manifest_count_reconciliation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fixture = self._fixture(Path(td))
            manifest = json.loads(
                fixture["source_manifest_path"].read_text(encoding="utf-8")
            )
            manifest["excluded_by_consent"]["message_type"] = 1
            manifest["manifest_sha256"] = digest({
                key: child
                for key, child in manifest.items()
                if key != "manifest_sha256"
            })
            self._write_json(fixture["source_manifest_path"], manifest)
            fixture["config"]["dataset"]["manifest_sha256"] = manifest[
                "manifest_sha256"
            ]
            with self.assertRaisesRegex(DatasetError, "counts do not reconcile"):
                validate_formal_inputs(
                    **{key: child for key, child in fixture.items() if key != "rows"}
                )

    def test_rejects_privacy_report_not_bound_to_manifest_evidence(self) -> None:
        cases = (
            ("scanner_version", "wechat-privacy-scanner-v999", "scanner version"),
            ("message_count", 2, "message_count"),
            ("hard_leak_count", False, "not passing"),
        )
        for field, value, message in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as td:
                fixture = self._fixture(Path(td))
                report = json.loads(
                    fixture["privacy_report_path"].read_text(encoding="utf-8")
                )
                report[field] = value
                self._write_json(fixture["privacy_report_path"], report)
                with self.assertRaisesRegex(DatasetError, message):
                    validate_formal_inputs(
                        **{
                            key: child
                            for key, child in fixture.items()
                            if key != "rows"
                        }
                    )

        with tempfile.TemporaryDirectory() as td:
            fixture = self._fixture(Path(td))
            report = json.loads(
                fixture["privacy_report_path"].read_text(encoding="utf-8")
            )
            report["layers"]["messages"]["cross_message_secret_matches"] = 1
            report["layers"]["messages"]["hard_leak_count"] = 0
            self._write_json(fixture["privacy_report_path"], report)
            with self.assertRaisesRegex(DatasetError, "manifest mismatch: messages"):
                validate_formal_inputs(
                    **{key: child for key, child in fixture.items() if key != "rows"}
                )

        with tempfile.TemporaryDirectory() as td:
            fixture = self._fixture(Path(td))
            report = json.loads(
                fixture["privacy_report_path"].read_text(encoding="utf-8")
            )
            report["redaction_counts"]["cross_message_secret_redactions"] = 1
            self._write_json(fixture["privacy_report_path"], report)
            with self.assertRaisesRegex(DatasetError, "redaction counts"):
                validate_formal_inputs(
                    **{key: child for key, child in fixture.items() if key != "rows"}
                )

    def test_all_review_allows_rejects_when_per_split_counts_reconcile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixture = self._fixture(root)
            summary = json.loads(
                fixture["review_summary_path"].read_text(encoding="utf-8")
            )
            extra = copy.deepcopy(fixture["rows"][2])
            extra["sample_id"] = "sample-test-kept"
            extra["metadata"]["source_message_ids"] = ["target-test-kept"]
            extra["metadata"]["context_message_ids"] = ["context-test-kept"]
            fixture["rows"].append(extra)
            with fixture["lineage_path"].open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "object_id": extra["sample_id"],
                    "stage": "candidate",
                    "source_message_ids": [
                        *extra["metadata"]["context_message_ids"],
                        *extra["metadata"]["source_message_ids"],
                    ],
                    "target_message_ids": extra["metadata"]["source_message_ids"],
                    "context_message_ids": extra["metadata"]["context_message_ids"],
                    "source_session_ids": [extra["session_id"]],
                    "consent_scope": "processing+persona_style",
                }) + "\n")
            split_manifest = json.loads(
                fixture["split_manifest_path"].read_text(encoding="utf-8")
            )
            split_manifest["candidate_counts"]["test"] = 2
            split_manifest["manifest_sha256"] = digest({
                key: value
                for key, value in split_manifest.items()
                if key not in {"split_sha256", "manifest_sha256"}
            })
            self._write_json(fixture["split_manifest_path"], split_manifest)
            privacy_report = json.loads(
                fixture["privacy_report_path"].read_text(encoding="utf-8")
            )
            privacy_report["candidate_count"] = 4
            self._write_json(fixture["privacy_report_path"], privacy_report)
            summary.update({
                "selected_count": 4,
                "candidate_count": 4,
                "event_count": 4,
                "exported_count": 3,
                "status_counts": {
                    "keep": 3,
                    "redact_keep": 0,
                    "reject": 1,
                    "uncertain": 0,
                },
                "rejected_counts_by_split": {
                    "train": 0,
                    "validation": 0,
                    "test": 1,
                },
            })
            rows = fixture["rows"][0:2] + [extra]
            fixture["reviewed_jsonl"].write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            review_digest = file_digest(fixture["reviewed_jsonl"])
            summary["review_evidence_digest"] = review_digest
            fixture["config"]["dataset"]["review_evidence_digest"] = review_digest
            self._write_json(fixture["review_summary_path"], summary)
            validated, _ = validate_formal_inputs(
                **{key: value for key, value in fixture.items() if key != "rows"}
            )
        self.assertEqual([row["sample_id"] for row in validated], [
            "sample-train",
            "sample-validation",
            "sample-test-kept",
        ])

    def test_rejects_review_candidate_count_not_bound_to_split_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            fixture = self._fixture(Path(td))
            summary = json.loads(
                fixture["review_summary_path"].read_text(encoding="utf-8")
            )
            summary.update({
                "selected_count": 4,
                "candidate_count": 4,
                "event_count": 4,
                "status_counts": {
                    "keep": 3,
                    "redact_keep": 0,
                    "reject": 1,
                    "uncertain": 0,
                },
                "rejected_counts_by_split": {
                    "train": 1,
                    "validation": 0,
                    "test": 0,
                },
            })
            self._write_json(fixture["review_summary_path"], summary)

            with self.assertRaisesRegex(DatasetError, "candidate count"):
                validate_formal_inputs(
                    **{key: value for key, value in fixture.items() if key != "rows"}
                )

    def test_subset_selection_counts_include_rejected_selected_id(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixture = self._fixture(root)
            review_digest = file_digest(fixture["reviewed_jsonl"])
            split_manifest = json.loads(
                fixture["split_manifest_path"].read_text(encoding="utf-8")
            )
            candidate_digest = split_manifest["candidate_manifest_sha256"]
            split_manifest["candidate_counts"]["validation"] = 2
            split_manifest["manifest_sha256"] = digest({
                key: value
                for key, value in split_manifest.items()
                if key not in {"split_sha256", "manifest_sha256"}
            })
            self._write_json(fixture["split_manifest_path"], split_manifest)
            privacy_report = json.loads(
                fixture["privacy_report_path"].read_text(encoding="utf-8")
            )
            privacy_report["candidate_count"] = 4
            self._write_json(fixture["privacy_report_path"], privacy_report)
            selected_ids = [
                "sample-train",
                "sample-validation",
                "sample-validation-reject",
                "sample-test",
            ]
            selection_path = root / "selection.json"
            self._write_json(selection_path, {
                "parent_candidate_manifest_sha256": candidate_digest,
                "selection_rule": "fixture deterministic selection",
                "selection_version": "candidate-selection-v1",
                "selected_sample_ids": selected_ids,
                "selected_sample_ids_sha256": digest(selected_ids),
                "selected_count": 4,
                "split_counts": {"train": 1, "validation": 2, "test": 1},
            })
            selection_digest = file_digest(selection_path)
            summary = json.loads(
                fixture["review_summary_path"].read_text(encoding="utf-8")
            )
            summary.update({
                "selected_scope": "subset",
                "selected_count": 4,
                "candidate_count": 4,
                "event_count": 4,
                "exported_count": 3,
                "status_counts": {
                    "keep": 3,
                    "redact_keep": 0,
                    "reject": 1,
                    "uncertain": 0,
                },
                "rejected_counts_by_split": {
                    "train": 0,
                    "validation": 1,
                    "test": 0,
                },
                "review_evidence_digest": review_digest,
                "selection_manifest_digest": selection_digest,
                "selection_version": "candidate-selection-v1",
                "selected_sample_ids_sha256": digest(selected_ids),
                "selected_counts_by_split": {
                    "train": 1,
                    "validation": 2,
                    "test": 1,
                },
            })
            self._write_json(fixture["review_summary_path"], summary)
            fixture["config"]["dataset"].update({
                "review_evidence_digest": review_digest,
                "selection_manifest_digest": selection_digest,
                "selection_version": "candidate-selection-v1",
            })
            fixture["selection_manifest_path"] = selection_path

            validated, identity = validate_formal_inputs(
                **{key: value for key, value in fixture.items() if key != "rows"}
            )

        self.assertEqual(
            [row["sample_id"] for row in validated],
            ["sample-train", "sample-validation", "sample-test"],
        )
        self.assertEqual(identity["selection_manifest_digest"], selection_digest)

    def test_subset_rejects_exported_unselected_id(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fixture = self._fixture(root)
            candidate_digest = json.loads(
                fixture["split_manifest_path"].read_text(encoding="utf-8")
            )["candidate_manifest_sha256"]
            selected_ids = [
                "sample-train",
                "sample-validation",
                "sample-test-other",
            ]
            selection_path = root / "selection.json"
            self._write_json(selection_path, {
                "parent_candidate_manifest_sha256": candidate_digest,
                "selection_rule": "invalid fixture selection",
                "selection_version": "candidate-selection-v1",
                "selected_sample_ids": selected_ids,
                "selected_sample_ids_sha256": digest(selected_ids),
                "selected_count": 3,
                "split_counts": {"train": 1, "validation": 1, "test": 1},
            })
            selection_digest = file_digest(selection_path)
            summary = json.loads(
                fixture["review_summary_path"].read_text(encoding="utf-8")
            )
            summary.update({
                "selected_scope": "subset",
                "selected_count": 3,
                "event_count": 3,
                "selection_manifest_digest": selection_digest,
                "selection_version": "candidate-selection-v1",
                "selected_sample_ids_sha256": digest(selected_ids),
                "selected_counts_by_split": {
                    "train": 1,
                    "validation": 1,
                    "test": 1,
                },
            })
            self._write_json(fixture["review_summary_path"], summary)
            fixture["config"]["dataset"].update({
                "selection_manifest_digest": selection_digest,
                "selection_version": "candidate-selection-v1",
            })
            fixture["selection_manifest_path"] = selection_path

            with self.assertRaisesRegex(DatasetError, "unselected|split counts"):
                validate_formal_inputs(
                    **{key: value for key, value in fixture.items() if key != "rows"}
                )


if __name__ == "__main__":
    unittest.main()
