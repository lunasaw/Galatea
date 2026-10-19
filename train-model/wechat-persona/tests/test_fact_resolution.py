from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import test_fact_review_server as review_fixtures
from wechat_persona._common import digest, file_digest
from wechat_persona.fact_resolution import FactCompilationError, prepare_facts, publish_facts
from wechat_persona.fact_review_server import FactReviewStore


class FactResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        helper = review_fixtures.FactReviewServerTests()
        self.root, self.snapshot, self.review = helper._fixture(Path(self.temp.name))
        self.consent = self.root / "consent.json"
        now = datetime.now(timezone.utc)
        consent = {
            "consent_id": "fixture-consent", "subject_id": "fixture-subject",
            "adult_verified": True, "purposes": ["processing", "memory_rag"],
            "status": "verified", "ledger_version": "v1", "verified_at": now.isoformat(),
            "scope": {
                "message_types": ["text"], "media_types": [], "time_start": None,
                "time_end": None, "third_party_policy": "exclude",
                "retention_until": (now + timedelta(days=1)).isoformat(),
                "withdrawal_key": "fixture-withdrawal",
            },
        }
        self.consent.write_text(json.dumps(consent), encoding="utf-8")
        (self.snapshot / "manifests").mkdir()
        evidence = self.snapshot / "manifests" / "evidence-map.jsonl"
        evidence.write_text("".join(json.dumps({
            "evidence_ref": f"e_{index:020x}", "session_id": f"session-{index}",
            "message_id": f"message-{index}", "conversation_date": "2024-05-21",
        }) + "\n" for index in (1, 2, 3)), encoding="utf-8")
        manifest_path = self.snapshot / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update({
            "consent_digest": digest(consent), "consent_file_sha256": file_digest(self.consent),
            "identity": {"owner_scope": "owner_" + "a" * 24},
        })
        manifest["output_digests"]["evidence_map"] = file_digest(evidence)
        manifest.pop("manifest_sha256")
        manifest["manifest_sha256"] = digest(manifest)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.store = FactReviewStore(
            snapshot_dir=self.snapshot, review_dir=self.review, controlled_root=self.root,
        )
        group = self.store.group("factgrp_00000000000000000001")
        self.payload = {
            "fact_group_id": group["fact_group_id"], "lineage_digest": group["lineage_digest"],
            "review_status": "confirmed",
            "selected_candidate_sha256": group["candidates"][0]["candidate_sha256"],
            "reason_code": "evidence_confirmed", "reviewer_id": "gpt-fact-prelabel-v1",
        }
        self.store.save_decision(self.payload)

    def prepare(self):
        return prepare_facts(
            snapshot_dir=self.snapshot, review_dir=self.review, consent_path=self.consent,
            controlled_root=self.root, smoke_query_count=2,
        )

    def publish(self, prepared):
        return publish_facts(
            prepared, output_root=self.root / "accepted-facts",
            selection_sha256=prepared.selection_sha256,
            acceptance_reference="fixture-user-batch-acceptance",
        )

    def test_plan_preserves_sources_and_publish_retains_machine_provenance(self):
        original = {path: file_digest(path) for path in self.root.rglob("*") if path.is_file()}
        prepared = self.prepare()
        self.assertEqual(prepared.summary()["counts"]["compiled_cards"], 1)
        self.assertEqual(prepared.cards[0].source_message_ids, ("message-1",))
        self.assertIsNone(prepared.cards[0].human_review)
        self.assertFalse((self.root / "accepted-facts").exists())
        result = self.publish(prepared)
        output = Path(result["output_dir"])
        acceptance = json.loads((output / "acceptance.json").read_text())
        self.assertTrue(acceptance["accepted_as_confirmed"])
        self.assertEqual(acceptance["source_review_kind"], "machine")
        self.assertFalse(acceptance["human_review_completed"])
        self.assertEqual(result["verification"]["status"], "passed")
        self.assertEqual(result["verification"]["self_query_top5_hits"], 1)
        self.assertTrue(result["verification"]["deletion_copy_verified"])
        self.assertEqual({path: file_digest(path) for path in original}, original)
        self.assertEqual(self.publish(prepared)["status"], "already_built")
        for path in output.rglob("*"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)

    def test_new_review_invalidates_frozen_selection(self):
        prepared = self.prepare()
        self.store.save_decision({
            **self.payload, "review_status": "deferred", "selected_candidate_sha256": None,
            "reason_code": "needs_more_context",
        })
        with self.assertRaisesRegex(FactCompilationError, "input changed"):
            self.publish(prepared)
        self.assertFalse((self.root / "accepted-facts").exists())

    def test_acceptance_cannot_bind_another_selection(self):
        prepared = self.prepare()
        with self.assertRaisesRegex(FactCompilationError, "exact selection"):
            publish_facts(prepared, output_root=self.root / "accepted-facts",
                          selection_sha256="0" * 64, acceptance_reference="fixture-acceptance")

    def test_audit_ahead_of_state_is_rejected(self):
        state_path = self.review / "review-state.json"
        previous = state_path.read_text()
        self.store.save_decision(self.payload)
        state_path.write_text(previous)
        with self.assertRaisesRegex(FactCompilationError, "terminal audit"):
            self.prepare()

    def test_evidence_map_tampering_is_rejected(self):
        evidence_path = self.snapshot / "manifests" / "evidence-map.jsonl"
        evidence_path.write_text("")
        with self.assertRaisesRegex(FactCompilationError, "evidence map digest"):
            self.prepare()

    def test_existing_index_tampering_is_not_treated_as_success(self):
        prepared = self.prepare()
        output = Path(self.publish(prepared)["output_dir"])
        (output / "index" / "cards.json").write_text("[]\n")
        with self.assertRaisesRegex(FactCompilationError, "existing output digest"):
            self.publish(prepared)

    def test_symlink_input_rejected(self):
        alias = self.root / "consent-alias.json"
        alias.symlink_to(self.consent)
        with self.assertRaisesRegex(FactCompilationError, "symlinks"):
            prepare_facts(snapshot_dir=self.snapshot, review_dir=self.review,
                          consent_path=alias, controlled_root=self.root)


if __name__ == "__main__":
    unittest.main()
