from __future__ import annotations

import unittest

from llm_lora_playground.data_prep import build_style_samples, redact_style_text
from llm_lora_playground.memory import (
    MemoryRecord,
    MemoryStore,
    RetrievedMemory,
    build_grounded_messages,
    build_memory_training_samples,
)


class MemoryTests(unittest.TestCase):
    def test_retrieval_is_owner_scoped_and_confirmed_only(self):
        confirmed = MemoryRecord(
            "m1", "owner-a", "姐姐在 <城市A> 上学", ("msg-1",), "2026-08-01T00:00:00Z", status="confirmed"
        )
        other_owner = MemoryRecord(
            "m2", "owner-b", "姐姐在 <城市B> 上学", ("msg-2",), "2026-08-01T00:00:00Z", status="confirmed"
        )
        candidate = MemoryRecord(
            "m3", "owner-a", "姐姐在 <城市C> 上学", ("msg-3",), "2026-08-01T00:00:00Z", status="candidate"
        )
        result = MemoryStore([confirmed, other_owner, candidate]).retrieve("姐姐 哪里 上学", owner_scope="owner-a")
        self.assertEqual([item.record.memory_id for item in result], ["m1"])

    def test_expired_records_are_not_injected(self):
        expired = MemoryRecord(
            "m1", "owner-a", "住在 <城市A>", ("msg-1",), "2025-01-01T00:00:00Z", valid_to="2026-01-01T00:00:00Z"
        )
        self.assertEqual(MemoryStore([expired]).retrieve("住在哪里", owner_scope="owner-a", now="2026-02-01T00:00:00Z"), [])

    def test_grounded_prompt_has_no_evidence_boundary(self):
        messages = build_grounded_messages("我姐在哪里上学？", [])
        self.assertEqual([message["role"] for message in messages], ["system", "user"])
        self.assertIn("无相关且已确认的记忆", messages[0]["content"])
        self.assertIn("memory_grounded_reply", messages[0]["content"])

    def test_challenge_set_covers_safety_cases(self):
        samples = build_memory_training_samples()
        scenarios = {sample["metadata"]["memory_scenario"] for sample in samples}
        self.assertTrue({"evidence", "no_evidence", "conflict", "third_party_sensitive", "prompt_injection"} <= scenarios)

    def test_style_redaction_and_group_dedup(self):
        self.assertEqual(redact_style_text("联系 138 0013 8000，日期 2026-08-01"), "联系 <电话>，日期 <日期>")
        rows = [
            {"sample_id": "a", "session_id": "s1", "messages": [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "好的"}]},
            {"sample_id": "b", "session_id": "s1", "messages": [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "好的"}]},
        ]
        self.assertEqual(len(build_style_samples(rows)), 1)


if __name__ == "__main__":
    unittest.main()
