from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "prelabel_fact_review_with_gpt.py"
SPEC = importlib.util.spec_from_file_location("fact_prelabel", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
fact_prelabel = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fact_prelabel
SPEC.loader.exec_module(fact_prelabel)


class FactPrelabelTests(unittest.TestCase):
    def _group(
        self,
        *,
        eligible: bool = True,
        risks: list[str] | None = None,
        conflict_status: str = "none",
    ) -> dict:
        return {
            "fact_group_id": "factgrp_" + "a" * 20,
            "lineage_digest": "b" * 64,
            "fact_key": "profile.preference",
            "aliases": [],
            "conflict_status": conflict_status,
            "candidates": [
                {
                    "candidate_sha256": "c" * 64,
                    "normalized_value": "fixture",
                    "precision": "unknown",
                    "source_days": ["2024-01-01"],
                    "support_count": 1,
                    "confirmation_eligible": eligible,
                    "risk_flags": risks or [],
                    "extractions": [],
                }
            ],
        }

    def _settings(self):
        return fact_prelabel.Settings(
            openai_base_url="https://example.invalid",
            model="fixture",
            api_key="fixture",
        )

    def test_ineligible_candidates_fail_closed_without_model_review(self):
        unresolved = self._group(
            eligible=False, risks=["unresolved_reference"]
        )
        quoted = self._group(
            eligible=False, risks=["quoted_or_hypothetical"]
        )
        negative = self._group(
            eligible=False, risks=["no_positive_evidence"]
        )
        self.assertEqual(
            fact_prelabel._deterministic_decision(unresolved)["reason_code"],
            "temporal_unresolved",
        )
        self.assertEqual(
            fact_prelabel._deterministic_decision(quoted)["reason_code"],
            "quoted_or_hypothetical",
        )
        self.assertEqual(
            fact_prelabel._deterministic_decision(negative)["reason_code"],
            "negative_or_uncertain",
        )
        self.assertIsNone(fact_prelabel._deterministic_decision(self._group()))

    def test_low_confidence_or_ineligible_confirmation_is_deferred(self):
        label = {
            "status": "confirmed",
            "selected_candidate_index": 0,
            "reason_code": "evidence_confirmed",
            "confidence": 0.7,
        }
        decision = fact_prelabel._effective_decision(
            self._group(conflict_status="conflicted"), label, self._settings()
        )
        self.assertEqual(decision["status"], "deferred")
        self.assertEqual(decision["reason_code"], "conflict_unresolved")

        decision = fact_prelabel._effective_decision(
            self._group(eligible=False),
            {**label, "confidence": 0.99},
            self._settings(),
        )
        self.assertEqual(decision["status"], "deferred")
        self.assertEqual(decision["reason_code"], "needs_more_context")

    def test_high_confidence_confirmation_selects_snapshot_hash(self):
        group = self._group()
        decision = fact_prelabel._effective_decision(
            group,
            {
                "status": "confirmed",
                "selected_candidate_index": 0,
                "reason_code": "evidence_confirmed",
                "confidence": 0.97,
            },
            self._settings(),
        )
        payload = fact_prelabel._decision_payload(
            group, decision, "gpt-fact-prelabel-v1"
        )
        self.assertEqual(payload["review_status"], "confirmed")
        self.assertEqual(
            payload["selected_candidate_sha256"],
            group["candidates"][0]["candidate_sha256"],
        )

    def test_concurrency_repair_defers_disagreeing_machine_decisions(self):
        group = self._group(conflict_status="conflicted")
        current = {
            "review_status": "rejected",
            "reason_code": "duplicate_or_malformed",
        }
        decision = fact_prelabel._concurrency_repair_decision(
            group,
            [
                {
                    "review_status": "rejected",
                    "selected_candidate_sha256": None,
                },
                {
                    "review_status": "confirmed",
                    "selected_candidate_sha256": group["candidates"][0][
                        "candidate_sha256"
                    ],
                },
            ],
            current,
        )
        self.assertEqual(decision["status"], "deferred")
        self.assertEqual(decision["reason_code"], "conflict_unresolved")

        decision = fact_prelabel._concurrency_repair_decision(
            group,
            [
                {"review_status": "rejected"},
                {"review_status": "rejected"},
            ],
            current,
        )
        self.assertEqual(decision["status"], "rejected")
        self.assertEqual(decision["reason_code"], "duplicate_or_malformed")


if __name__ == "__main__":
    unittest.main()
