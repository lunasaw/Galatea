"""Compile an explicitly accepted machine-confirmed fact batch into a private index.

This is a memory workflow, not an SFT compiler. Original review events remain
machine events; the separate batch acceptance binds their exact selected version.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

from ._common import canonical_json, digest, file_digest
from .consent import verify_consent
from .datasets import _publish_directory_noreplace
from .fact_review_server import FactReviewStore
from .memories import MemoryCard
from .rag import (
    _load_index,
    build_bm25_index,
    build_grounded_messages,
    delete_memory,
    retrieve,
)
from .redact import scan_redacted_text


COMPILER_VERSION = "accepted-daily-facts-v1"


class FactCompilationError(ValueError):
    """The selected facts or their authorization cannot be reproduced."""


def _private_path(path: Path, root: Path) -> Path:
    root = root.resolve()
    absolute = path.expanduser().absolute()
    if any(item.is_symlink() for item in (absolute, *absolute.parents)):
        raise FactCompilationError("controlled paths must not contain symlinks")
    resolved = absolute.resolve()
    if resolved == root or root not in resolved.parents:
        raise FactCompilationError("path must stay below the controlled root")
    return resolved


def _jsonl(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise FactCompilationError("expected a JSONL object")
            yield row


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(canonical_json(value) + "\n")
    path.chmod(0o600)


@dataclass(frozen=True)
class PreparedFacts:
    selection: dict[str, Any]
    cards: tuple[MemoryCard, ...]
    input_digests: dict[Path, str]
    controlled_root: Path
    consent_path: Path
    smoke_query_count: int

    @property
    def selection_sha256(self) -> str:
        return digest(self.selection)

    def summary(self) -> dict[str, Any]:
        return {
            "status": "planned",
            "compiler_version": COMPILER_VERSION,
            "source_snapshot_id": self.selection["source_snapshot_id"],
            "selection_sha256": self.selection_sha256,
            "counts": self.selection["counts"],
            "backend": "bm25",
            "human_review_completed": False,
            "formal_training_eligible": False,
            "training_run": False,
        }


def prepare_facts(
    *,
    snapshot_dir: Path,
    review_dir: Path,
    consent_path: Path,
    controlled_root: Path,
    smoke_query_count: int = 32,
) -> PreparedFacts:
    """Read, validate and freeze identities without creating any output."""
    if not 1 <= smoke_query_count <= 200:
        raise FactCompilationError("smoke_query_count must be between 1 and 200")
    snapshot_dir = _private_path(snapshot_dir, controlled_root)
    review_dir = _private_path(review_dir, controlled_root)
    consent_path = _private_path(consent_path, controlled_root)
    state_path = _private_path(review_dir / "review-state.json", controlled_root)
    audit_path = _private_path(review_dir / "review-events.audit.jsonl", controlled_root)
    lock_path = _private_path(review_dir / ".review-workspace.lock", controlled_root)
    # Use the review writer's lock without creating or modifying the workspace.
    with lock_path.open("rb") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
        store = FactReviewStore(
            snapshot_dir=snapshot_dir, review_dir=review_dir,
            controlled_root=controlled_root, create_workspace=False,
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if len(state["decisions"]) != len(store.latest):
            raise FactCompilationError("duplicate decisions in review state")
        replay: dict[str, dict[str, Any]] = {}
        conflicts: set[str] = set()
        for event in _jsonl(audit_path):
            for key, expected in {
                "snapshot_id": store.snapshot_id,
                "manifest_sha256": store.manifest_sha256,
                "manifest_file_sha256": store.manifest_file_sha256,
                "fact_ledger_sha256": store.ledger_sha256,
                "review_candidates_sha256": store.candidates_sha256,
            }.items():
                if event.pop(key, None) != expected:
                    raise FactCompilationError("audit event has a different snapshot binding")
            group_id = event["fact_group_id"]
            previous = replay.get(group_id)
            if previous is None or event["revision"] > previous["revision"]:
                replay[group_id] = event
                conflicts.discard(group_id)
            elif event["revision"] == previous["revision"] and event != previous:
                conflicts.add(group_id)
        if conflicts or replay != store.latest:
            raise FactCompilationError("review state differs from terminal audit revisions")
        review_digests = {state_path: file_digest(state_path), audit_path: file_digest(audit_path)}

    authorization = verify_consent(
        consent_path, required_purposes={"processing", "memory_rag"},
        required_message_types={"text"},
    )
    for key in ("consent_digest", "consent_file_sha256"):
        if authorization[key] != store.manifest.get(key):
            raise FactCompilationError("consent does not match the source snapshot")
    owner_scope = (store.manifest.get("identity") or {}).get("owner_scope")
    if not isinstance(owner_scope, str) or not owner_scope:
        raise FactCompilationError("source owner binding is missing")

    selected: list[tuple[dict, dict, dict]] = []
    needed_refs: set[str] = set()
    for group_id, event in sorted(store.latest.items()):
        if event["review_status"] != "confirmed":
            continue
        if not str(event["reviewer_id"]).startswith("gpt-fact-"):
            raise FactCompilationError("this compiler requires machine fact-review provenance")
        group = store.by_id[group_id]
        if group.get("owner_scope") != owner_scope:
            raise FactCompilationError("fact group owner does not match the source snapshot")
        candidate = next(
            row for row in group["candidates"]
            if digest(row) == event["selected_candidate_sha256"]
        )
        selected.append((group, candidate, event))
        needed_refs.update(candidate["evidence_refs"])
    if not selected:
        raise FactCompilationError("no machine-confirmed facts selected")

    evidence_path = _private_path(snapshot_dir / "manifests" / "evidence-map.jsonl", controlled_root)
    evidence_digest = file_digest(evidence_path)
    if evidence_digest != store.manifest["output_digests"].get("evidence_map"):
        raise FactCompilationError("evidence map digest mismatch")
    evidence: dict[str, dict] = {}
    for row in _jsonl(evidence_path):
        reference = row.get("evidence_ref")
        if reference in needed_refs:
            if reference in evidence:
                raise FactCompilationError("duplicate source evidence reference")
            if not row.get("session_id") or not row.get("message_id"):
                raise FactCompilationError("source evidence lacks message/session identity")
            evidence[reference] = row
    if set(evidence) != needed_refs:
        raise FactCompilationError("selected fact has missing source evidence")

    cards: list[MemoryCard] = []
    entries: list[dict] = []
    for group, candidate, event in selected:
        refs = [evidence[ref] for ref in sorted(candidate["evidence_refs"])]
        extractions = [store.extractions[key] for key in candidate["extraction_ids"]]
        for extraction in extractions:
            for item in extraction["evidence"]:
                if evidence[item["evidence_ref"]]["session_id"] != extraction["session_id"]:
                    raise FactCompilationError("extraction and evidence sessions disagree")
        value = candidate["normalized_value"]
        rendered = value if isinstance(value, str) else canonical_json(value)
        content = f"{group['fact_key']}：{rendered}"
        if scan_redacted_text(content)["hard_leak_count"]:
            raise FactCompilationError("compiled fact failed privacy scan")
        binding = {
            "fact_group_id": group["fact_group_id"],
            "candidate_sha256": event["selected_candidate_sha256"],
            "review_event_sha256": digest(event),
            "review_revision": event["revision"],
            "source_lineage_digest": group["lineage_digest"],
        }
        memory_id = "factmem_" + digest({"snapshot": store.snapshot_id, **binding})[:24]
        card = MemoryCard(
            memory_id=memory_id,
            owner_scope=owner_scope,
            content=content,
            source_session_ids=tuple(sorted({row["session_id"] for row in refs})),
            source_message_ids=tuple(sorted({row["message_id"] for row in refs})),
            created_at=event["reviewed_at"],
            status="confirmed",
            sensitivity="sensitive",
            fact_key=group["fact_key"],
            confidence=min(float(row["confidence"]) for row in extractions),
            extractor_version=COMPILER_VERSION,
            human_review=None,
            attributes={
                **binding,
                "source_snapshot_id": store.snapshot_id,
                "source_review_kind": "machine",
                "source_reviewer_id": event["reviewer_id"],
                "acceptance_basis": "owner_batch_acceptance_of_machine_confirmed_facts",
                "human_review_completed": False,
                "formal_training_eligible": False,
                "normalized_value": value,
                "source_days": candidate["source_days"],
                "available_at": event["reviewed_at"],
                "temporal_policy": "historical_evidence_no_current_validity_inferred",
                "evidence_refs": sorted(candidate["evidence_refs"]),
                "extraction_ids": sorted(candidate["extraction_ids"]),
            },
        )
        cards.append(card)
        entries.append({**binding, "memory_id": memory_id, "card_sha256": digest(card.as_dict())})
    if len({card.fact_key for card in cards}) != len(cards):
        raise FactCompilationError("duplicate fact keys would hide accepted cards during retrieval")
    counts = Counter(event["review_status"] for event in store.latest.values())
    selection = {
        "schema_version": "accepted-fact-selection-v1",
        "compiler_version": COMPILER_VERSION,
        "implementation_sha256": file_digest(Path(__file__)),
        "source_snapshot_id": store.snapshot_id,
        "source_manifest_sha256": store.manifest_sha256,
        "source_manifest_file_sha256": store.manifest_file_sha256,
        "review_state_sha256": review_digests[state_path],
        "review_audit_sha256": review_digests[audit_path],
        "consent_file_sha256": authorization["consent_file_sha256"],
        "evidence_map_sha256": evidence_digest,
        "owner_scope": owner_scope,
        "backend": "bm25",
        "smoke_query_count": smoke_query_count,
        "test_exposure_status": "historical_memory_source_not_certified_untouched",
        "counts": {
            "source_groups": len(store.groups),
            "confirmed": counts["confirmed"],
            "deferred": counts["deferred"],
            "rejected": counts["rejected"],
            "pending": len(store.groups) - len(store.latest),
            "compiled_cards": len(cards),
        },
        "entries": entries,
    }
    inputs = {
        **review_digests,
        store.manifest_path: store.manifest_file_sha256,
        store.ledger_path: store.ledger_sha256,
        store.candidates_path: store.candidates_sha256,
        consent_path: authorization["consent_file_sha256"],
        evidence_path: evidence_digest,
        Path(__file__): selection["implementation_sha256"],
    }
    return PreparedFacts(selection, tuple(cards), inputs, controlled_root, consent_path, smoke_query_count)


def verify_workflow(index_dir: Path, cards: tuple[MemoryCard, ...], query_count: int) -> dict[str, Any]:
    """Engineering smoke only: self queries do not measure factual accuracy."""
    manifest, restored = _load_index(index_dir)
    if [card.as_dict() for card in restored] != [card.as_dict() for card in cards]:
        raise FactCompilationError("index round-trip changed accepted cards")
    count = min(query_count, len(cards))
    samples = [cards[index * len(cards) // count] for index in range(count)]
    hits = 0
    prompt_checks = 0
    for card in samples:
        retrieved = retrieve(card.content, card.owner_scope, index_dir, k=5)
        hits += any(item.memory_id == card.memory_id for item in retrieved)
        messages = build_grounded_messages(card.content, retrieved)
        prompt_checks += any("<memory>" in row["content"] for row in messages)
        if retrieve(card.content, card.owner_scope + "_unrelated", index_dir, k=5):
            raise FactCompilationError("retrieval crossed owner boundary")
    if hits != count or prompt_checks != count:
        raise FactCompilationError("self-retrieval or grounded prompt smoke failed")
    if retrieve("zxqvabsentlexicalfixturezz", cards[0].owner_scope, index_dir):
        raise FactCompilationError("unmatched-query smoke failed")
    with tempfile.TemporaryDirectory(prefix=".fact-delete-check-", dir=index_dir.parent) as temporary:
        copy = Path(temporary) / "index"
        shutil.copytree(index_dir, copy)
        receipt = delete_memory(cards[0].memory_id, cards[0].owner_scope, copy)
        _, remaining = _load_index(copy)
        if not receipt.verified or len(remaining) != len(cards) - 1:
            raise FactCompilationError("deletion smoke failed")
    return {
        "schema_version": "fact-memory-workflow-check-v1",
        "status": "passed",
        "evidence_type": "engineering_smoke_not_factual_quality_evaluation",
        "index_manifest_digest": manifest.manifest_digest,
        "roundtrip_card_count": len(restored),
        "self_query_count": count,
        "self_query_top5_hits": hits,
        "grounded_prompt_checks": prompt_checks,
        "owner_isolation_failures": 0,
        "unmatched_query_passed": True,
        "deletion_copy_verified": True,
        "privacy_hard_leak_count": 0,
    }


def publish_facts(
    prepared: PreparedFacts, *, output_root: Path, selection_sha256: str,
    acceptance_reference: str,
) -> dict[str, Any]:
    if selection_sha256 != prepared.selection_sha256 or not acceptance_reference.strip():
        raise FactCompilationError("explicit acceptance must bind the exact selection digest")
    for path, expected in prepared.input_digests.items():
        if file_digest(path) != expected:
            raise FactCompilationError("input changed after planning; rebuild the selection")
    verify_consent(prepared.consent_path, required_purposes={"processing", "memory_rag"})
    root = _private_path(output_root, prepared.controlled_root)
    if any(root == path.parent or root in path.parents for path in prepared.input_digests):
        raise FactCompilationError("output root must be separate from source inputs")
    identity = {"selection_sha256": selection_sha256, "acceptance_reference": acceptance_reference}
    output = root / ("accepted-facts_" + digest(identity)[:20])
    if output.exists():
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        expected = manifest.pop("manifest_sha256", None)
        if digest(manifest) != expected or manifest.get("identity") != identity:
            raise FactCompilationError("existing output identity does not match")
        for relative, sha256 in manifest["output_digests"].items():
            if file_digest(output / relative) != sha256:
                raise FactCompilationError("existing output digest mismatch")
        return {**prepared.summary(), "status": "already_built", "output_dir": str(output)}
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    staging = Path(tempfile.mkdtemp(prefix=".accepted-facts-", dir=root))
    try:
        index = build_bm25_index(prepared.cards, staging / "index", consent_scope="memory_rag")
        report = verify_workflow(staging / "index", prepared.cards, prepared.smoke_query_count)
        acceptance = {
            "schema_version": "fact-batch-acceptance-v1", **identity,
            "accepted_at": datetime.now(timezone.utc).isoformat(),
            "accepted_as_confirmed": True,
            "acceptance_kind": "owner_batch_acceptance",
            "source_review_kind": "machine",
            "scope": ["confirmed_memory_cards", "private_memory_rag", "workflow_verification"],
            "selected_count": len(prepared.cards),
            "human_review_completed": False,
        }
        _write_json(staging / "selection.json", prepared.selection)
        _write_json(staging / "acceptance.json", acceptance)
        _write_json(staging / "verification.json", report)
        manifest = {
            "schema_version": "accepted-fact-memory-snapshot-v1", "identity": identity,
            "source_snapshot_id": prepared.selection["source_snapshot_id"],
            "counts": prepared.selection["counts"],
            "confirmed_cards_built": True, "rag_index_built": True,
            "owner_batch_accepted": True, "human_review_completed": False,
            "formal_training_eligible": False, "training_run": False,
            "creates_mlflow_run": False, "service_updated": False,
            "index_manifest_digest": index.manifest_digest,
            "output_digests": {
                str(path.relative_to(staging)): file_digest(path)
                for path in sorted(staging.rglob("*")) if path.is_file()
            },
        }
        manifest["manifest_sha256"] = digest(manifest)
        _write_json(staging / "manifest.json", manifest)
        for path, expected in prepared.input_digests.items():
            if file_digest(path) != expected:
                raise FactCompilationError("input changed during build; output not published")
        _publish_directory_noreplace(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {
        **prepared.summary(), "status": "built", "output_dir": str(output),
        "index_manifest_digest": index.manifest_digest, "verification": report,
    }
