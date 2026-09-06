import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona.memories import MemoryCard, MemoryContractError, build_memory_cards


class MemorySchemaTests(unittest.TestCase):
    def test_memory_card_contains_owner_lineage_revision_and_review(self):
        card = MemoryCard(
            memory_id="m1",
            owner_scope="owner-a",
            content="用户喜欢在周末爬山。",
            source_session_ids=("session-1",),
            source_message_ids=("message-1",),
            created_at="2026-09-01T00:00:00Z",
            valid_from="2026-09-01T00:00:00Z",
            confidence=0.9,
            status="confirmed",
            sensitivity="normal",
            extractor_version="extractor-v1",
            human_review={"status": "keep", "reviewer_id": "reviewer-1"},
        )
        payload = card.as_dict()
        self.assertEqual(payload["owner_scope"], "owner-a")
        self.assertEqual(payload["source_session_ids"], ["session-1"])
        self.assertEqual(payload["human_review"]["status"], "keep")
        self.assertEqual(len(payload["lineage_digest"]), 64)
        self.assertEqual(card.revision, 1)
        self.assertEqual(
            set(payload),
            {
                "memory_id", "owner_scope", "content", "source_session_ids", "source_message_ids",
                "created_at", "valid_from", "valid_to", "confidence", "status", "sensitivity",
                "fact_key", "attributes", "extractor_version", "human_review", "lineage_digest",
            },
        )

    def test_invalid_card_fails_closed(self):
        with self.assertRaises(MemoryContractError):
            MemoryCard(
                memory_id="m1",
                owner_scope="owner-a",
                content="x",
                source_session_ids=(),
                created_at="2026-09-01T00:00:00Z",
                extractor_version="v1",
            )

    def test_build_cards_ignores_unapproved_and_high_sensitive_by_default(self):
        rows = [
            {
                "memory_id": "approved",
                "owner_scope": "owner-a",
                "content": "用户喜欢爬山。",
                "session_id": "s1",
                "message_id": "msg1",
                "timestamp": "2026-09-01T00:00:00Z",
                "review_status": "keep",
            },
            {
                "memory_id": "uncertain",
                "owner_scope": "owner-a",
                "content": "未经确认的事实。",
                "session_id": "s2",
                "timestamp": "2026-09-01T00:00:00Z",
                "review_status": "uncertain",
            },
            {
                "memory_id": "secret",
                "owner_scope": "owner-a",
                "content": "高敏秘密。",
                "session_id": "s3",
                "timestamp": "2026-09-01T00:00:00Z",
                "review_status": "keep",
                "sensitivity": "high",
            },
            {
                "memory_id": "withdrawn",
                "owner_scope": "owner-a",
                "content": "已经删除的事实。",
                "session_id": "s4",
                "timestamp": "2026-09-01T00:00:00Z",
                "review_status": "keep",
                "status": "deleted",
            },
        ]
        manifest = build_memory_cards(rows, consent_scope="memory-rag-scope", extractor_version="v1")
        self.assertEqual([card.memory_id for card in manifest.cards], ["approved"])
        self.assertEqual(manifest.card_count, 1)
        self.assertEqual(len(manifest.manifest_digest), 64)


if __name__ == "__main__":
    unittest.main()
