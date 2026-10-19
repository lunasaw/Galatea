from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona.evidence import build_source_evidence_cards, extract_relationship_event_candidates
from wechat_persona.rag import RagContractError, build_hybrid_index, retrieve


class FixtureEncoder:
    revision = "BAAI/bge-small-zh-v1.5@fixture"

    def encode_documents(self, texts, batch_size=32):
        return [[1.0, 0.0] if "在一起" in text else [0.0, 1.0] for text in texts]

    def encode_query(self, text):
        return [1.0, 0.0] if "在一起" in text else [0.0, 1.0]


class MemoryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.sessions = [{
            "session_id": "session-a",
            "start_time": "2024-05-20T20:00:00+08:00",
            "end_time": "2024-05-20T20:01:00+08:00",
            "turns": [{
                "speaker_role": "self",
                "message_ids": ["message-a"],
                "timestamps": ["2024-05-20T20:00:00+08:00"],
                "message_contents": ["今天是我们在一起第一天。"],
            }],
        }]
        self.split = {"session_ids_by_split": {"train": [], "validation": [], "test": ["session-a"]}}

    def test_source_evidence_is_eligible_but_derived_event_requires_confirmation(self):
        evidence = build_source_evidence_cards(
            self.sessions,
            owner_scope="owner_aaaaaaaaaaaaaaaaaaaaaaaa",
            split_manifest=self.split,
        )
        candidates = extract_relationship_event_candidates(evidence)
        self.assertEqual(evidence[0].status, "confirmed")
        self.assertEqual(evidence[0].attributes["truth_status"], "unverified_conversation_evidence")
        self.assertEqual(evidence[0].attributes["original_split"], "test")
        self.assertEqual(candidates[0].status, "candidate")
        self.assertEqual(candidates[0].fact_key, "relationship.started_at")
        self.assertEqual(candidates[0].attributes["event_date"], "2024-05-20")

        with tempfile.TemporaryDirectory() as directory:
            index = build_hybrid_index(
                [*evidence, *candidates],
                FixtureEncoder(),
                directory,
                min_score=0.1,
            )
            self.assertEqual(index.card_count, 1)
            self.assertEqual(index.excluded_counts["candidate"], 1)
            result = retrieve(
                "我们第一次在一起是什么日子？",
                "owner_aaaaaaaaaaaaaaaaaaaaaaaa",
                directory,
                embedding_model=FixtureEncoder(),
            )
            self.assertEqual([item.memory_id for item in result], [evidence[0].memory_id])

    def test_vector_tampering_is_rejected(self):
        evidence = build_source_evidence_cards(
            self.sessions,
            owner_scope="owner_aaaaaaaaaaaaaaaaaaaaaaaa",
            split_manifest=self.split,
        )
        with tempfile.TemporaryDirectory() as directory:
            build_hybrid_index(evidence, FixtureEncoder(), directory, min_score=0.1)
            vectors_path = Path(directory) / "vectors.json"
            vectors_path.write_text("[[0.0,1.0]]\n", encoding="utf-8")
            with self.assertRaisesRegex(RagContractError, "vector digest"):
                retrieve(
                    "在一起",
                    "owner_aaaaaaaaaaaaaaaaaaaaaaaa",
                    directory,
                    embedding_model=FixtureEncoder(),
                )


if __name__ == "__main__":
    unittest.main()
