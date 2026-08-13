"""Tests for patrol session persistence, resume/fork, and memory compaction."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.patrol.compaction import compact_patrol_memory, validate_compaction_fidelity
from agent.schemas.patrol import (
    EvidenceRecord,
    Finding,
    PatrolMemory,
    RawRef,
    SummaryVersion,
)
from agent.state.patrol import FilePatrolSessionStore, PatrolSession


class TestPatrolMemory(unittest.IsolatedAsyncioTestCase):
    def _memory_with_secret_and_long_log(self) -> PatrolMemory:
        raw_ref = RawRef(uri="state://patrol/s1/r1/raw/mlflow.json", digest="sha256:" + "a" * 64)
        evidence = EvidenceRecord(
            evidence_id="ev_mlflow",
            kind="mlflow_run",
            source_tool="inspect_mlflow_experiment",
            source_uri="mlflow://experiments/cats/runs/run-1",
            raw_ref=raw_ref,
            summary="MLflow run run-1 is missing manifest digest. token=SECRET should not leak.",
            metadata={
                "mlflow_run_id": "run-1",
                "experiment_name": "cats",
                "artifact_uri": "mlflow-artifacts:/exp/run-1/artifacts",
                "manifest_digest": "sha256:" + "b" * 64,
            },
        )
        finding = Finding(
            finding_id="fd_missing_digest",
            target={"kind": "mlflow_run", "id": "run-1"},
            type="evidence_missing",
            severity="warning",
            summary="Run run-1 is missing required dataset digest.",
            evidence_ids=["ev_mlflow"],
            metadata={"mlflow_run_id": "run-1"},
        )
        return PatrolMemory(
            patrol_run_id="pr_1",
            project_name="cats-and-dogs",
            window={"started_at": "2026-08-13T00:00:00+00:00", "ended_at": "2026-08-13T00:05:00+00:00"},
            summary="A" * 5000 + " password=hunter2",
            open_findings=[finding],
            evidence_index=[evidence],
            next_check_at="2026-08-13T01:00:00+00:00",
            unresolved_errors=["inspect_mlflow_experiment timed out with password=hunter2"],
            metadata={"long_log": "line\n" * 2000},
        )

    async def test_file_store_round_trip_resume_and_fork_isolated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FilePatrolSessionStore(Path(tmpdir))
            session = PatrolSession(
                session_id="s1",
                project_scope=["cats-and-dogs"],
                memory=self._memory_with_secret_and_long_log(),
            )
            await store.save_session(session)

            loaded = await store.load_session("s1")
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.memory.open_findings[0].finding_id, "fd_missing_digest")

            forked = await store.fork_session("s1", "s1-fork")
            forked.open_findings[0].summary = "changed only in fork"
            await store.save_session(forked)

            original = await store.load_session("s1")
            self.assertIsNotNone(original)
            assert original is not None
            self.assertEqual(original.open_findings[0].summary, "Run run-1 is missing required dataset digest.")
            self.assertEqual(forked.forked_from, "s1")

    def test_compaction_preserves_traceable_fields_and_redacts_sensitive_text(self):
        original = self._memory_with_secret_and_long_log()
        compacted = compact_patrol_memory(original, max_summary_chars=800)
        validate_compaction_fidelity(original, compacted)

        self.assertIsInstance(compacted.summary_version, SummaryVersion)
        self.assertLessEqual(len(compacted.summary), 800)
        self.assertNotIn("hunter2", compacted.summary)
        self.assertNotIn("SECRET", compacted.summary)
        self.assertNotIn("line\nline\nline", compacted.summary)
        self.assertEqual(compacted.evidence_index[0].raw_ref.digest, "sha256:" + "a" * 64)
        self.assertEqual(compacted.open_findings[0].evidence_ids, ["ev_mlflow"])

    def test_compaction_fidelity_detects_lost_evidence(self):
        original = self._memory_with_secret_and_long_log()
        compacted = compact_patrol_memory(original)
        compacted.evidence_index = []
        with self.assertRaises(ValueError):
            validate_compaction_fidelity(original, compacted)


if __name__ == "__main__":
    unittest.main()
