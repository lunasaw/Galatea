import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona.memories import MemoryCard
from wechat_persona.rag import RagContractError, build_bm25_index, build_embedding_index, evaluate_retrieval, retrieve


class RagMetricsTests(unittest.TestCase):
    def test_recall_mrr_and_no_evidence_are_reported(self):
        cards = [
            MemoryCard(
                memory_id="m1",
                owner_scope="owner-a",
                content="用户喜欢周末爬山。",
                source_session_ids=("s1",),
                created_at="2026-09-01T00:00:00Z",
                extractor_version="v1",
            ),
            MemoryCard(
                memory_id="m2",
                owner_scope="owner-a",
                content="用户在城市甲工作。",
                source_session_ids=("s2",),
                created_at="2026-09-01T00:00:00Z",
                extractor_version="v1",
            ),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            build_bm25_index(cards, temp_dir)
            report = evaluate_retrieval(
                [
                    {"query": "周末喜欢什么", "owner_scope": "owner-a", "relevant_memory_ids": ["m1"]},
                    {"query": "没有对应记忆的事情", "owner_scope": "owner-a", "relevant_memory_ids": []},
                ],
                temp_dir,
                protocol={"ks": [1, 3, 5], "now": "2026-09-02T00:00:00Z"},
            )
            self.assertEqual(report.recall_at[1], 1.0)
            self.assertEqual(report.mrr, 1.0)
            self.assertIn("no_evidence_uncertainty_rate", report.as_dict())
            self.assertGreaterEqual(report.latency_ms_p95, report.latency_ms_p50)
            self.assertEqual(report.backend, "bm25")
            self.assertEqual(len(report.query_set_digest), 64)
            self.assertEqual(len(report.protocol_digest), 64)
            self.assertEqual(len(report.index_manifest_digest), 64)

    def test_embedding_uses_supplied_local_encoder_and_checks_revision(self):
        class FixtureEncoder:
            revision = "fixture-embedding-r1"

            def encode(self, texts):
                return [[1.0, 0.0] if ("爬山" in text or "远足" in text) else [0.0, 1.0] for text in texts]

        class WrongEncoder(FixtureEncoder):
            revision = "fixture-embedding-r2"

        cards = [
            MemoryCard("hike", "owner-a", "用户喜欢爬山。", ("s1",), "2026-09-01T00:00:00Z", extractor_version="v1"),
            MemoryCard("coffee", "owner-a", "用户喜欢咖啡。", ("s2",), "2026-09-01T00:00:00Z", extractor_version="v1"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            encoder = FixtureEncoder()
            manifest = build_embedding_index(cards, encoder, temp_dir)
            self.assertEqual(manifest.embedding_model_revision, "fixture-embedding-r1")
            result = retrieve("远足", "owner-a", temp_dir, embedding_model=encoder)
            self.assertEqual(result[0].memory_id, "hike")
            with self.assertRaises(RagContractError):
                retrieve("远足", "owner-a", temp_dir, embedding_model=WrongEncoder())

    def test_evaluation_passes_local_embedding_model(self):
        class FixtureEncoder:
            revision = "fixture-eval-embedding-r1"

            def encode(self, texts):
                return [[1.0, 0.0] if ("爬山" in text or "远足" in text) else [0.0, 1.0] for text in texts]

        card = MemoryCard("hike", "owner-a", "用户喜欢爬山。", ("s1",), "2026-09-01T00:00:00Z", extractor_version="v1")
        with tempfile.TemporaryDirectory() as temp_dir:
            encoder = FixtureEncoder()
            build_embedding_index([card], encoder, temp_dir)
            report = evaluate_retrieval(
                [{"query": "远足", "owner_scope": "owner-a", "relevant_memory_ids": ["hike"]}],
                temp_dir,
                protocol={"ks": [1], "now": "2026-09-02T00:00:00Z", "embedding_model": encoder},
            )
            self.assertEqual(report.recall_at[1], 1.0)


if __name__ == "__main__":
    unittest.main()
