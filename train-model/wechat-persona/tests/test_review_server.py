import http.client
import json
import socket
import sys
import tempfile
import threading
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from wechat_persona._common import file_digest
from wechat_persona.review import _content_hash
from wechat_persona.review_server import ReviewApplication, ReviewStore, create_server


_PRELABEL_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "prelabel_review_with_gpt.py"
)
_PRELABEL_SPEC = spec_from_file_location("prelabel_review_with_gpt", _PRELABEL_PATH)
_PRELABEL_MODULE = module_from_spec(_PRELABEL_SPEC)
assert _PRELABEL_SPEC and _PRELABEL_SPEC.loader
sys.modules[_PRELABEL_SPEC.name] = _PRELABEL_MODULE
_PRELABEL_SPEC.loader.exec_module(_PRELABEL_MODULE)


class ReviewServerTests(unittest.TestCase):
    def test_gpt_prelabel_batch_contract_and_conservative_resolution(self):
        raw = {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                {
                                    "results": [
                                        {
                                            "index": 1,
                                            "status": "keep",
                                            "labels": ["relationship_context"],
                                            "confidence": 0.91,
                                            "reason_code": "relationship_signal",
                                            "rationale": "关系语境对回复有解释作用",
                                            "risk_flags": ["none"],
                                        },
                                        {
                                            "index": 0,
                                            "status": "keep",
                                            "labels": ["direct_response"],
                                            "confidence": 0.70,
                                            "reason_code": "low_confidence",
                                            "rationale": "信心不足",
                                            "risk_flags": ["none"],
                                        },
                                    ]
                                },
                                ensure_ascii=False,
                            ),
                        }
                    ]
                }
            ]
        }
        labels = _PRELABEL_MODULE._parse_labels(raw, 2)
        self.assertEqual(labels[0]["reason_code"], "low_confidence")
        self.assertEqual(labels[1]["labels"], ["relationship_context"])
        self.assertEqual(
            _PRELABEL_MODULE._effective_decision(labels[0], 0.78)["status"],
            "uncertain",
        )
        self.assertEqual(
            _PRELABEL_MODULE._effective_decision(
                labels[0], 0.78, resolve_uncertain=True
            )["status"],
            "reject",
        )

    def test_gpt_prelabel_payload_excludes_candidate_identifiers(self):
        row = self._row("private-sample", "train", "先休息一下吧。")
        messages = _PRELABEL_MODULE._candidate_messages(row)
        self.assertEqual(set(messages[0]), {"role", "content"})
        serialized = json.dumps(messages, ensure_ascii=False)
        self.assertNotIn("private-sample", serialized)
        self.assertNotIn("session-private-sample", serialized)

    def _row(self, sample_id: str, split: str, answer: str) -> dict:
        row = {
            "sample_id": sample_id,
            "session_id": f"session-{sample_id}",
            "messages": [
                {"role": "system", "content": "只提供温和、诚实的回复"},
                {"role": "user", "content": "今天有点累"},
                {"role": "assistant", "content": answer},
            ],
            "metadata": {
                "split": split,
                "review_status": "keep",
                "content_sha256": "",
            },
        }
        row["metadata"]["content_sha256"] = _content_hash(row)
        return row

    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        controlled = root / "controlled"
        dataset = controlled / "draft"
        review = controlled / "review"
        dataset.mkdir(parents=True)
        train = self._row("train-1", "train", "抱抱，慢慢来，我陪你。")
        validation = self._row("validation-1", "validation", "要不要先休息一下？")
        for split, row in (("train", train), ("validation", validation)):
            (dataset / f"{split}.jsonl").write_text(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        manifest = {
            "schema_version": "wechat-persona-curated-draft-v1",
            "parent_test_untouched": True,
            "test_artifact_created": False,
            "formal_training_eligible": False,
            "curation_id": "fixture-curation",
            "curation_digest": "a" * 64,
            "selection_sha256": "b" * 64,
            "selected_counts": {"train": 1, "validation": 1},
            "artifacts_sha256": {
                "train.jsonl": file_digest(dataset / "train.jsonl"),
                "validation.jsonl": file_digest(dataset / "validation.jsonl"),
            },
        }
        (dataset / "selection-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        return controlled, dataset, review

    def _client(self, server):
        return http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)

    def _request(self, server, method, path, body=None):
        connection = self._client(server)
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

    def _server(self, controlled: Path, dataset: Path, review: Path):
        page = Path(__file__).resolve().parents[1] / "docs" / "review.html"
        store = ReviewStore(
            dataset_dir=dataset, review_dir=review, controlled_root=controlled
        )
        application = ReviewApplication(store=store, page_path=page)
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

    def test_bootstrap_dataset_and_test_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            controlled, dataset, review = self._fixture(Path(temporary))
            server, store = self._server(controlled, dataset, review)
            status, bootstrap = self._request(server, "GET", "/api/bootstrap")
            self.assertEqual(status, 200)
            self.assertEqual(bootstrap["dataset"]["counts"], {"train": 1, "validation": 1})
            self.assertEqual(bootstrap["review"]["decision_count"], 0)
            self.assertEqual(bootstrap["review"]["status_counts"]["uncertain"], 2)

            status, page = self._request(
                server, "GET", "/api/dataset?split=all&offset=0&limit=1"
            )
            self.assertEqual(status, 200)
            self.assertEqual(page["total"], 2)
            self.assertEqual(len(page["rows"]), 1)
            self.assertEqual(page["rows"][0]["sample_id"], "train-1")
            status, page = self._request(
                server, "GET", "/api/dataset?split=all&offset=1&limit=1"
            )
            self.assertEqual(status, 200)
            self.assertEqual(page["rows"][0]["sample_id"], "validation-1")

            status, error = self._request(server, "GET", "/api/dataset?split=test")
            self.assertEqual(status, 403)
            self.assertIn("only train and validation", error["error"])

    def test_keep_and_reject_decisions_are_persisted(self):
        with tempfile.TemporaryDirectory() as temporary:
            controlled, dataset, review = self._fixture(Path(temporary))
            server, store = self._server(controlled, dataset, review)
            row = store.by_id["train-1"]
            status, body = self._request(
                server,
                "POST",
                "/api/decision",
                {
                    "sample_id": "train-1",
                    "reviewer_id": "owner",
                    "review_status": "keep",
                    "content_sha256": row["metadata"]["content_sha256"],
                    "labels": ["emotional_attunement", "relationship_context"],
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(body["event"]["review_status"], "keep")
            self.assertIn("relationship_context", body["event"]["labels"])
            self.assertTrue((review / "latest-decisions.json").exists())
            status, page = self._request(
                server,
                "GET",
                "/api/dataset?split=all&status=keep&limit=100",
            )
            self.assertEqual(status, 200)
            self.assertEqual(page["total"], 1)
            self.assertEqual(page["rows"][0]["sample_id"], "train-1")

            status, error = self._request(
                server,
                "POST",
                "/api/decision",
                {
                    "sample_id": "validation-1",
                    "reviewer_id": "owner",
                    "review_status": "reject",
                    "content_sha256": store.by_id["validation-1"]["metadata"]["content_sha256"],
                },
            )
            self.assertEqual(status, 400)
            self.assertIn("require a reason", error["error"])

            status, error = self._request(
                server,
                "POST",
                "/api/decision",
                {
                    "sample_id": "validation-1",
                    "reviewer_id": "owner",
                    "review_status": "keep",
                    "content_sha256": "0" * 64,
                },
            )
            self.assertEqual(status, 400)
            self.assertIn("digest mismatch", error["error"])

    def test_redact_keep_runs_privacy_scan_and_writes_reviewed_row(self):
        with tempfile.TemporaryDirectory() as temporary:
            controlled, dataset, review = self._fixture(Path(temporary))
            server, store = self._server(controlled, dataset, review)
            row = store.by_id["train-1"]
            status, body = self._request(
                server,
                "POST",
                "/api/decision",
                {
                    "sample_id": "train-1",
                    "reviewer_id": "owner",
                    "review_status": "redact_keep",
                    "review_reason": "remove private detail",
                    "content_sha256": row["metadata"]["content_sha256"],
                    "edited_reply": "抱抱，先休息一下，我陪你。",
                    "labels": ["emotional_attunement"],
                },
            )
            self.assertEqual(status, 200)
            self.assertTrue(body["event"]["redacted_content_sha256"])
            reviewed = (review / "latest-reviewed-rows.jsonl").read_text(encoding="utf-8")
            self.assertIn("先休息一下", reviewed)


if __name__ == "__main__":
    unittest.main()
