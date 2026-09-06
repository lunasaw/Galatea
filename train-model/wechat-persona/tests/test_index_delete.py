import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona.memories import MemoryCard
from wechat_persona.rag import (
    RagContractError,
    build_bm25_index,
    build_embedding_index,
    delete_consent_scope,
    delete_memory,
    delete_session,
    retrieve,
)


class IndexDeleteTests(unittest.TestCase):
    def test_delete_creates_receipt_changes_manifest_and_leaves_no_residue(self):
        card = MemoryCard(
            memory_id="m-delete",
            owner_scope="owner-a",
            content="删除验证 canary 记忆。",
            source_session_ids=("session-delete",),
            created_at="2026-09-01T00:00:00Z",
            extractor_version="v1",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_before = build_bm25_index([card], temp_dir)
            receipt = delete_memory("m-delete", "owner-a", temp_dir)
            self.assertTrue(receipt.verified)
            self.assertEqual(receipt.residual_count, 0)
            self.assertNotEqual(receipt.manifest_digest_before, receipt.manifest_digest_after)
            self.assertEqual(retrieve("canary", owner_scope="owner-a", index_ref=temp_dir, k=5), [])

    def test_delete_session_and_consent_scope_remove_all_affected_cards(self):
        cards = [
            MemoryCard("m1", "owner-a", "session canary one", ("shared-session",), "2026-09-01T00:00:00Z", extractor_version="v1"),
            MemoryCard("m2", "owner-a", "session canary two", ("shared-session",), "2026-09-01T00:00:00Z", extractor_version="v1"),
            MemoryCard("m3", "owner-b", "scope canary", ("other-session",), "2026-09-01T00:00:00Z", extractor_version="v1"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            build_bm25_index(cards, temp_dir, consent_scope="scope-v1")
            session_receipts = delete_session("shared-session", "owner-a", temp_dir)
            self.assertEqual(len(session_receipts), 2)
            self.assertTrue(all(receipt.verified for receipt in session_receipts))
            self.assertEqual(retrieve("session canary", "owner-a", temp_dir), [])
            scope_receipts = delete_consent_scope("scope-v1", temp_dir)
            self.assertEqual(len(scope_receipts), 1)
            self.assertTrue(scope_receipts[0].verified)
            self.assertEqual(retrieve("scope canary", "owner-b", temp_dir), [])

    def test_manifest_tampering_is_rejected(self):
        card = MemoryCard("m1", "owner-a", "tamper canary", ("s1",), "2026-09-01T00:00:00Z", extractor_version="v1")
        with tempfile.TemporaryDirectory() as temp_dir:
            build_bm25_index([card], temp_dir)
            manifest_path = Path(temp_dir) / "index_manifest.json"
            text = manifest_path.read_text(encoding="utf-8")
            manifest_path.write_text(text.replace("tokenizer-cjk-char-v1", "tampered-tokenizer"), encoding="utf-8")
            with self.assertRaises(RagContractError):
                retrieve("canary", "owner-a", temp_dir)

    def test_embedding_delete_preserves_remaining_vectors(self):
        class FixtureEncoder:
            revision = "fixture-delete-embedding-r1"

            def encode(self, texts):
                return [[1.0, 0.0] if "one" in text else [0.0, 1.0] for text in texts]

        cards = [
            MemoryCard("m1", "owner-a", "canary one", ("s1",), "2026-09-01T00:00:00Z", extractor_version="v1"),
            MemoryCard("m2", "owner-a", "canary two", ("s2",), "2026-09-01T00:00:00Z", extractor_version="v1"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            encoder = FixtureEncoder()
            build_embedding_index(cards, encoder, temp_dir)
            self.assertTrue(delete_memory("m1", "owner-a", temp_dir).verified)
            result = retrieve("canary two", "owner-a", temp_dir, embedding_model=encoder)
            self.assertEqual([item.memory_id for item in result], ["m2"])


if __name__ == "__main__":
    unittest.main()
