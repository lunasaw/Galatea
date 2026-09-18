from __future__ import annotations

import http.client
import json
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from wechat_persona._common import digest, file_digest
from wechat_persona.fact_review_server import (
    FactReviewApplication,
    FactReviewServerError,
    FactReviewStore,
    create_server,
)


class FactReviewServerTests(unittest.TestCase):
    def _extraction(
        self,
        index: int,
        *,
        fact_key: str,
        value: str,
        polarity: str = "positive",
        claim_type: str = "explicit",
        unresolved: list[str] | None = None,
    ) -> dict:
        return {
            "schema_version": "fact-extraction-v1",
            "extraction_id": f"extract_{index:020x}",
            "day_id": f"2024-05-{20 + index:02d}",
            "session_id": f"session-{index}",
            "chunk_ordinal": 0,
            "fact_key": fact_key,
            "value": value,
            "normalized_value": value,
            "value_type": "string",
            "date_precision": "unknown",
            "date_basis": "unknown",
            "claim_type": claim_type,
            "polarity": polarity,
            "confidence": 0.9,
            "aliases": ["fixture alias"],
            "evidence": [
                {
                    "evidence_ref": f"e_{index:020x}",
                    "quote": f"private quote {index}",
                }
            ],
            "unresolved_references": unresolved or [],
            "status": "candidate",
            "extractor_version": "fixture-v1",
            "lineage_digest": f"{index + 1:064x}",
        }

    def _ledger_candidate(self, extraction: dict) -> dict:
        return {
            "normalized_value": extraction["normalized_value"],
            "precision": extraction["date_precision"],
            "evidence_refs": [extraction["evidence"][0]["evidence_ref"]],
            "source_days": [extraction["day_id"]],
            "extraction_ids": [extraction["extraction_id"]],
            "claim_types": [extraction["claim_type"]],
            "polarities": [extraction["polarity"]],
            "support_count": 1,
        }

    def _group(
        self,
        index: int,
        *,
        fact_key: str,
        conflict_status: str,
        candidates: list[dict],
    ) -> dict:
        return {
            "schema_version": "fact-ledger-v1",
            "fact_group_id": f"factgrp_{index:020x}",
            "owner_scope": "owner_" + "a" * 24,
            "fact_key": fact_key,
            "aliases": ["fixture alias"],
            "candidates": candidates,
            "conflict_status": conflict_status,
            "review_status": "pending",
            "confirmed_value": None,
            "lineage_digest": f"{index + 20:064x}",
        }

    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        controlled = root / "controlled"
        snapshot = controlled / "memory-daily" / "daily-memory_fixture"
        review = controlled / "memory-daily-reviews" / "daily-memory_fixture"
        (snapshot / "review").mkdir(parents=True)

        first = self._extraction(
            1, fact_key="relationship.started_at", value="private-value-a"
        )
        second = self._extraction(
            2, fact_key="relationship.started_at", value="private-value-b"
        )
        blocked = self._extraction(
            3,
            fact_key="target.location",
            value="private-value-c",
            polarity="uncertain",
            unresolved=["那里"],
        )
        candidates = [first, second, blocked]
        ledger = [
            self._group(
                1,
                fact_key="relationship.started_at",
                conflict_status="conflicted",
                candidates=[
                    self._ledger_candidate(first),
                    self._ledger_candidate(second),
                ],
            ),
            self._group(
                2,
                fact_key="target.location",
                conflict_status="insufficient",
                candidates=[self._ledger_candidate(blocked)],
            ),
        ]
        candidate_path = snapshot / "review" / "candidates.jsonl"
        ledger_path = snapshot / "fact-ledger.jsonl"
        candidate_path.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                for row in candidates
            ),
            encoding="utf-8",
        )
        ledger_path.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                for row in ledger
            ),
            encoding="utf-8",
        )
        manifest = {
            "schema_version": "daily-memory-snapshot-v1",
            "snapshot_id": "daily-memory_fixture",
            "dataset_id": "dataset-fixture",
            "status": "candidate_review_pending",
            "authorization_status": "verified",
            "human_review_completed": False,
            "formal_training_eligible": False,
            "training_run": False,
            "creates_mlflow_run": False,
            "confirmed_index_built": False,
            "rag_index_built": False,
            "counts": {"candidate_count": 3, "fact_group_count": 2},
            "output_digests": {
                "fact_ledger": file_digest(ledger_path),
                "review_candidates": file_digest(candidate_path),
            },
        }
        manifest["manifest_sha256"] = digest(manifest)
        (snapshot / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return controlled, snapshot, review

    def _request(self, server, method: str, path: str, body=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        headers = {"Accept": "application/json"}
        encoded = None
        if body is not None:
            encoded = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        status = response.status
        connection.close()
        value = json.loads(raw.decode("utf-8")) if raw else None
        return status, value

    def _server(self, controlled: Path, snapshot: Path, review: Path):
        page = Path(__file__).resolve().parents[1] / "docs" / "fact-review.html"
        store = FactReviewStore(
            snapshot_dir=snapshot,
            review_dir=review,
            controlled_root=controlled,
        )
        application = FactReviewApplication(store=store, page_path=page)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        server = create_server(application=application, host="127.0.0.1", port=port)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return server, store

    def test_check_mode_validates_without_creating_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            controlled, snapshot, review = self._fixture(Path(temporary))
            store = FactReviewStore(
                snapshot_dir=snapshot,
                review_dir=review,
                controlled_root=controlled,
                create_workspace=False,
            )
            self.assertFalse(review.exists())
            bootstrap = store.bootstrap()
            self.assertEqual(bootstrap["review"]["status_counts"]["pending"], 2)
            self.assertEqual(bootstrap["snapshot"]["eligible_group_count"], 1)

    def test_snapshot_digest_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            controlled, snapshot, review = self._fixture(Path(temporary))
            with (snapshot / "fact-ledger.jsonl").open("a", encoding="utf-8") as handle:
                handle.write("{}\n")
            with self.assertRaisesRegex(FactReviewServerError, "digest mismatch"):
                FactReviewStore(
                    snapshot_dir=snapshot,
                    review_dir=review,
                    controlled_root=controlled,
                )

    def test_confirmed_event_is_hash_only_and_reloadable(self):
        with tempfile.TemporaryDirectory() as temporary:
            controlled, snapshot, review = self._fixture(Path(temporary))
            store = FactReviewStore(
                snapshot_dir=snapshot,
                review_dir=review,
                controlled_root=controlled,
            )
            group = store.group("factgrp_00000000000000000001")
            selected = group["candidates"][0]
            event = store.save_decision(
                {
                    "fact_group_id": group["fact_group_id"],
                    "lineage_digest": group["lineage_digest"],
                    "review_status": "confirmed",
                    "selected_candidate_sha256": selected["candidate_sha256"],
                    "reason_code": "evidence_confirmed",
                    "reviewer_id": "owner",
                }
            )
            self.assertEqual(event["review_status"], "confirmed")
            audit = (review / "review-events.audit.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("private-value-a", audit)
            self.assertNotIn("private quote", audit)
            self.assertEqual(review.stat().st_mode & 0o777, 0o700)
            self.assertEqual(
                (review / "review-state.json").stat().st_mode & 0o777, 0o600
            )
            reloaded = FactReviewStore(
                snapshot_dir=snapshot,
                review_dir=review,
                controlled_root=controlled,
            )
            self.assertEqual(
                reloaded.group(group["fact_group_id"])["review_status"],
                "confirmed",
            )

    def test_batch_decisions_validate_before_one_workspace_update(self):
        with tempfile.TemporaryDirectory() as temporary:
            controlled, snapshot, review = self._fixture(Path(temporary))
            store = FactReviewStore(
                snapshot_dir=snapshot,
                review_dir=review,
                controlled_root=controlled,
            )
            first = store.group("factgrp_00000000000000000001")
            second = store.group("factgrp_00000000000000000002")
            events = store.save_decisions(
                [
                    {
                        "fact_group_id": first["fact_group_id"],
                        "lineage_digest": first["lineage_digest"],
                        "review_status": "confirmed",
                        "selected_candidate_sha256": first["candidates"][0][
                            "candidate_sha256"
                        ],
                        "reason_code": "evidence_confirmed",
                        "reviewer_id": "machine-review",
                    },
                    {
                        "fact_group_id": second["fact_group_id"],
                        "lineage_digest": second["lineage_digest"],
                        "review_status": "deferred",
                        "selected_candidate_sha256": None,
                        "reason_code": "temporal_unresolved",
                        "reviewer_id": "machine-review",
                    },
                ]
            )
            self.assertEqual([event["revision"] for event in events], [1, 1])
            state = json.loads(
                (review / "review-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(state["decisions"]), 2)
            self.assertEqual(
                len(
                    (review / "review-events.audit.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                ),
                2,
            )

            before = (review / "review-state.json").read_bytes()
            with self.assertRaisesRegex(
                FactReviewServerError, "lineage digest mismatch"
            ):
                store.save_decisions(
                    [
                        {
                            "fact_group_id": first["fact_group_id"],
                            "lineage_digest": first["lineage_digest"],
                            "review_status": "rejected",
                            "selected_candidate_sha256": None,
                            "reason_code": "not_durable_memory",
                            "reviewer_id": "machine-review",
                        },
                        {
                            "fact_group_id": second["fact_group_id"],
                            "lineage_digest": "0" * 64,
                            "review_status": "deferred",
                            "selected_candidate_sha256": None,
                            "reason_code": "needs_more_context",
                            "reviewer_id": "machine-review",
                        },
                    ]
                )
            self.assertEqual((review / "review-state.json").read_bytes(), before)
            self.assertEqual(
                len(
                    (review / "review-events.audit.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                ),
                2,
            )

    def test_ineligible_candidate_cannot_be_confirmed(self):
        with tempfile.TemporaryDirectory() as temporary:
            controlled, snapshot, review = self._fixture(Path(temporary))
            store = FactReviewStore(
                snapshot_dir=snapshot,
                review_dir=review,
                controlled_root=controlled,
            )
            group = store.group("factgrp_00000000000000000002")
            self.assertFalse(group["candidates"][0]["confirmation_eligible"])
            with self.assertRaisesRegex(
                FactReviewServerError, "blocked by the confirmation policy"
            ):
                store.save_decision(
                    {
                        "fact_group_id": group["fact_group_id"],
                        "lineage_digest": group["lineage_digest"],
                        "review_status": "confirmed",
                        "selected_candidate_sha256": group["candidates"][0][
                            "candidate_sha256"
                        ],
                        "reason_code": "evidence_confirmed",
                        "reviewer_id": "owner",
                    }
                )
            deferred = store.save_decision(
                {
                    "fact_group_id": group["fact_group_id"],
                    "lineage_digest": group["lineage_digest"],
                    "review_status": "deferred",
                    "selected_candidate_sha256": None,
                    "reason_code": "temporal_unresolved",
                    "reviewer_id": "owner",
                }
            )
            self.assertEqual(deferred["review_status"], "deferred")

    def test_http_pages_group_detail_and_decision(self):
        with tempfile.TemporaryDirectory() as temporary:
            controlled, snapshot, review = self._fixture(Path(temporary))
            server, store = self._server(controlled, snapshot, review)
            status, bootstrap = self._request(server, "GET", "/api/bootstrap")
            self.assertEqual(status, 200)
            self.assertEqual(bootstrap["snapshot"]["fact_group_count"], 2)

            status, page = self._request(
                server,
                "GET",
                "/api/groups?status=pending&conflict=conflicted&limit=10",
            )
            self.assertEqual(status, 200)
            self.assertEqual(page["total"], 1)
            group_id = page["rows"][0]["fact_group_id"]
            status, group = self._request(
                server, "GET", f"/api/group?fact_group_id={group_id}"
            )
            self.assertEqual(status, 200)
            self.assertEqual(len(group["candidates"]), 2)
            selected = group["candidates"][0]
            status, response = self._request(
                server,
                "POST",
                "/api/decision",
                {
                    "fact_group_id": group_id,
                    "lineage_digest": group["lineage_digest"],
                    "review_status": "confirmed",
                    "selected_candidate_sha256": selected["candidate_sha256"],
                    "reason_code": "evidence_confirmed",
                    "reviewer_id": "owner",
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(response["event"]["review_status"], "confirmed")
            status, page = self._request(
                server, "GET", "/api/groups?status=confirmed&limit=10"
            )
            self.assertEqual(status, 200)
            self.assertEqual(page["total"], 1)

            status, error = self._request(
                server,
                "POST",
                "/api/decision",
                {
                    "fact_group_id": group_id,
                    "lineage_digest": "0" * 64,
                    "review_status": "rejected",
                    "selected_candidate_sha256": None,
                    "reason_code": "not_durable_memory",
                    "reviewer_id": "owner",
                },
            )
            self.assertEqual(status, 400)
            self.assertIn("lineage digest mismatch", error["error"])


if __name__ == "__main__":
    unittest.main()
