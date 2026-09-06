import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona.memories import MemoryCard, MemoryContractError
from wechat_persona.rag import build_bm25_index, retrieve


def card(memory_id, owner, content):
    return MemoryCard(
        memory_id=memory_id,
        owner_scope=owner,
        content=content,
        source_session_ids=(f"session-{memory_id}",),
        source_message_ids=(f"message-{memory_id}",),
        created_at="2026-09-01T00:00:00Z",
        extractor_version="fixture-v1",
    )


class OwnerIsolationTests(unittest.TestCase):
    def test_owner_is_required_and_cross_owner_cards_never_leak(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            index = build_bm25_index(
                [
                    card("a", "owner-a", "用户住在城市甲。"),
                    card("b", "owner-b", "用户住在城市乙。"),
                ],
                temp_dir,
            )
            with self.assertRaises(MemoryContractError):
                retrieve("住在哪里", owner_scope="", index_ref=temp_dir, k=5)
            result = retrieve("住在哪里", owner_scope="owner-a", index_ref=temp_dir, k=5)
            self.assertEqual([item.memory_id for item in result], ["a"])
            self.assertTrue(all(item.record.owner_scope == "owner-a" for item in result))
            self.assertEqual(index.owner_scopes, ("owner-a", "owner-b"))

    def test_ineligible_status_sensitivity_and_expiry_are_filtered(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            build_bm25_index(
                [
                    card("ok", "owner-a", "用户喜欢咖啡。"),
                    MemoryCard(
                        memory_id="candidate",
                        owner_scope="owner-a",
                        content="用户喜欢茶。",
                        source_session_ids=("s-c",),
                        created_at="2026-09-01T00:00:00Z",
                        status="candidate",
                        extractor_version="v1",
                    ),
                    MemoryCard(
                        memory_id="high",
                        owner_scope="owner-a",
                        content="用户的高敏信息。",
                        source_session_ids=("s-h",),
                        created_at="2026-09-01T00:00:00Z",
                        sensitivity="high",
                        extractor_version="v1",
                    ),
                    MemoryCard(
                        memory_id="expired",
                        owner_scope="owner-a",
                        content="用户以前喜欢汽水。",
                        source_session_ids=("s-e",),
                        created_at="2025-01-01T00:00:00Z",
                        valid_to="2026-01-01T00:00:00Z",
                        extractor_version="v1",
                    ),
                ],
                temp_dir,
            )
            ids = [item.memory_id for item in retrieve("用户喜欢", owner_scope="owner-a", index_ref=temp_dir, k=10, now="2026-09-02T00:00:00Z")]
            self.assertEqual(ids, ["ok"])

    def test_latest_revision_of_same_fact_is_the_only_default_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            old = MemoryCard(
                memory_id="old",
                owner_scope="owner-a",
                content="用户住在城市甲。",
                source_session_ids=("s-old",),
                created_at="2025-01-01T00:00:00Z",
                valid_from="2025-01-01T00:00:00Z",
                fact_key="home_city",
                extractor_version="v1",
            )
            new = MemoryCard(
                memory_id="new",
                owner_scope="owner-a",
                content="用户住在城市乙。",
                source_session_ids=("s-new",),
                created_at="2026-01-01T00:00:00Z",
                valid_from="2026-01-01T00:00:00Z",
                fact_key="home_city",
                extractor_version="v1",
            )
            build_bm25_index([old, new], temp_dir)
            result = retrieve("住在城市", owner_scope="owner-a", index_ref=temp_dir, k=5, now="2026-09-02T00:00:00Z")
            self.assertEqual([item.memory_id for item in result], ["new"])


if __name__ == "__main__":
    unittest.main()
