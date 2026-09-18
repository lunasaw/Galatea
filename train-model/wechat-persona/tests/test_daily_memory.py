from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona.daily_memory import (  # noqa: E402
    ChunkPolicy,
    DailyMemoryError,
    build_daily_bundles,
    build_fact_ledger,
    chunk_daily_bundles,
    normalize_fact_candidate,
    resolve_date,
)


SOURCE_DIGEST = "a" * 64
OWNER_SCOPE = "owner_" + "b" * 24


def fixture_session() -> dict:
    return {
        "session_id": "session-a",
        "start_time": "2024-05-20T23:59:00+08:00",
        "end_time": "2024-05-21T00:01:00+08:00",
        "turns": [
            {
                "speaker_role": "self",
                "message_kind": "text",
                "message_ids": ["message-a", "message-b"],
                "timestamps": [
                    "2024-05-20T23:59:00+08:00",
                    "2024-05-21T00:01:00+08:00",
                ],
                "message_contents": ["明天见。", "今天是我们在一起第一天。"],
            }
        ],
    }


class DailyMemoryTests(unittest.TestCase):
    def test_bundle_uses_business_day_and_splits_cross_midnight_turn(self):
        bundles, lineage, counts = build_daily_bundles(
            [fixture_session()],
            owner_scope=OWNER_SCOPE,
            source_manifest_sha256=SOURCE_DIGEST,
            timezone_name="Asia/Shanghai",
        )
        self.assertEqual([row["day_id"] for row in bundles], ["2024-05-20", "2024-05-21"])
        self.assertEqual(counts["message_count"], 2)
        self.assertEqual(counts["session_day_slice_count"], 2)
        self.assertEqual(len(lineage), 2)
        self.assertNotIn("message-a", str(bundles))

    def test_chunking_overlaps_complete_turns_and_marks_oversized_turn(self):
        bundles, _, _ = build_daily_bundles(
            [
                {
                    "session_id": "session-a",
                    "start_time": "2024-05-20T10:00:00+08:00",
                    "end_time": "2024-05-20T10:02:00+08:00",
                    "turns": [
                        {
                            "speaker_role": "self",
                            "message_kind": "text",
                            "message_ids": [f"message-{index}"],
                            "timestamps": [f"2024-05-20T10:0{index}:00+08:00"],
                            "message_contents": ["x" * 100],
                        }
                        for index in range(3)
                    ],
                }
            ],
            owner_scope=OWNER_SCOPE,
            source_manifest_sha256=SOURCE_DIGEST,
            timezone_name="Asia/Shanghai",
        )

        def fake_count(value):
            return 100 + sum(
                len(message["text"])
                for turn in value["primary_turns"]
                for message in turn["messages"]
            )

        chunks = chunk_daily_bundles(
            bundles,
            source_manifest_sha256=SOURCE_DIGEST,
            policy=ChunkPolicy(260, 350, 1),
            count_request_tokens=fake_count,
        )
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[1]["overlap_turn_count"], 0)
        self.assertIsNone(chunks[0]["blocked_reason"])
        self.assertIsNone(chunks[2]["blocked_reason"])

    def test_date_resolution_does_not_invent_missing_year(self):
        exact = resolve_date(
            "昨天", anchor_timestamp="2024-05-21T10:00:00+08:00", declared_basis="relative_resolved"
        )
        missing_year = resolve_date(
            "5月23日", anchor_timestamp="2024-05-21T10:00:00+08:00", declared_basis="explicit"
        )
        self.assertEqual(exact.normalized_value, "2024-05-20")
        self.assertEqual(exact.precision, "day")
        self.assertEqual(missing_year.normalized_value, "--05-23")
        self.assertEqual(missing_year.unresolved, ("year",))

    def test_candidate_requires_exact_primary_quote_and_stays_candidate(self):
        chunk = {
            "chunk_id": "chunk-a",
            "day_id": "2024-05-20",
            "session_id": "session-a",
            "chunk_ordinal": 0,
            "primary_evidence_refs": ["e_" + "1" * 20],
        }
        raw = {
            "fact_key": "relationship.started_at",
            "value": "2024-05-20",
            "value_type": "date",
            "date_precision": "day",
            "date_basis": "explicit",
            "claim_type": "explicit",
            "polarity": "positive",
            "confidence": 0.9,
            "aliases": ["确定关系"],
            "evidence": [
                {"evidence_ref": "e_" + "1" * 20, "quote": "2024年5月20日确定关系"}
            ],
            "unresolved_references": [],
        }
        lookup = {
            "e_" + "1" * 20: {
                "text": "我们在2024年5月20日确定关系。",
                "timestamp": "2024-05-21T10:00:00+08:00",
            }
        }
        candidate = normalize_fact_candidate(
            raw,
            chunk=chunk,
            evidence_lookup=lookup,
            extractor_version="fixture-v1",
        )
        self.assertEqual(candidate["status"], "candidate")
        self.assertEqual(candidate["normalized_value"], "2024-05-20")
        self.assertIn("表白", candidate["aliases"])
        with self.assertRaisesRegex(DailyMemoryError, "exact source substring"):
            normalize_fact_candidate(
                {**raw, "evidence": [{"evidence_ref": "e_" + "1" * 20, "quote": "不存在"}]},
                chunk=chunk,
                evidence_lookup=lookup,
                extractor_version="fixture-v1",
            )

    def test_inferred_fact_is_rejected_and_conflicts_are_preserved(self):
        base = {
            "schema_version": "fact-extraction-v1",
            "session_id": "session-a",
            "chunk_ordinal": 0,
            "fact_key": "relationship.started_at",
            "value_type": "date",
            "date_precision": "day",
            "date_basis": "explicit",
            "claim_type": "explicit",
            "polarity": "positive",
            "confidence": 0.9,
            "aliases": ["表白"],
            "unresolved_references": [],
            "status": "candidate",
            "extractor_version": "fixture-v1",
        }
        facts = []
        for index, value in enumerate(("2024-05-20", "2024-05-21")):
            facts.append(
                {
                    **base,
                    "extraction_id": f"extract_{index:020x}",
                    "day_id": value,
                    "value": value,
                    "normalized_value": value,
                    "evidence": [
                        {"evidence_ref": f"e_{index:020x}", "quote": "确定关系"}
                    ],
                    "lineage_digest": f"{index + 1:064x}",
                }
            )
        ledger = build_fact_ledger(facts, owner_scope=OWNER_SCOPE)
        self.assertEqual(ledger[0]["conflict_status"], "conflicted")
        self.assertIsNone(ledger[0]["confirmed_value"])
        self.assertEqual(len(ledger[0]["candidates"]), 2)


if __name__ == "__main__":
    unittest.main()
