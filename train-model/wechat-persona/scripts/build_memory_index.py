#!/usr/bin/env python3
"""Build an immutable, local-only memory index from authorized redacted sessions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from wechat_persona.consent import consent_file_digest, verify_consent  # noqa: E402
from wechat_persona.embeddings import LocalBgeEncoder, model_directory_digest  # noqa: E402
from wechat_persona.evidence import (  # noqa: E402
    EVIDENCE_EXTRACTOR_VERSION,
    EVENT_EXTRACTOR_VERSION,
    build_source_evidence_cards,
    extract_relationship_event_candidates,
)
from wechat_persona.rag import build_hybrid_index  # noqa: E402
from wechat_persona.redact import scan_redacted_text  # noqa: E402
from wechat_persona.runtime import load_project_config, validate_project_config  # noqa: E402


class MemoryBuildError(ValueError):
    pass


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MemoryBuildError(f"expected JSON object: {path}")
    return value


def _load_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MemoryBuildError(f"invalid JSONL at line {number}") from exc
            if not isinstance(value, dict):
                raise MemoryBuildError(f"session line {number} is not an object")
            yield value


def _require_private_path(path: Path, allowed_root: Path, *, kind: str) -> Path:
    resolved = path.expanduser().resolve()
    root = allowed_root.expanduser().resolve()
    if not resolved.is_relative_to(root):
        raise MemoryBuildError(f"{kind} must stay below the authorized private root")
    if resolved.is_symlink():
        raise MemoryBuildError(f"{kind} must not be a symlink")
    return resolved


def _write_private_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _preflight(args: argparse.Namespace) -> dict[str, Any]:
    config = load_project_config(args.config)
    errors = validate_project_config(config)
    rag = config.get("rag", {})
    if rag.get("backend") != "hybrid":
        errors.append("rag.backend must be hybrid")
    if rag.get("embedding_model_revision") != args.embedding_revision:
        errors.append("embedding revision does not match config")
    if rag.get("embedding_model_digest") != args.embedding_model_digest:
        errors.append("embedding model digest does not match config")
    if rag.get("owner_scope_required") is not True:
        errors.append("rag.owner_scope_required must be true")
    evaluation = config.get("evaluation", {})
    if evaluation.get("test_access") != "historical_test_included_in_memory_corpus":
        errors.append("RAG config must declare historical test reuse")
    if evaluation.get("future_holdout_policy") != "post_index_temporal":
        errors.append("RAG config must require a post-index temporal holdout")

    private_root = args.allowed_root.expanduser().resolve()
    sessions_path = _require_private_path(args.sessions, private_root, kind="sessions")
    source_manifest_path = _require_private_path(args.source_manifest, private_root, kind="source manifest")
    split_manifest_path = _require_private_path(args.split_manifest, private_root, kind="split manifest")
    consent_path = _require_private_path(args.consent, private_root, kind="consent")
    output_root = _require_private_path(args.output_root, private_root, kind="output root")
    if not sessions_path.is_file():
        errors.append("sessions file is missing")
    if not source_manifest_path.is_file() or not split_manifest_path.is_file():
        errors.append("source or split manifest is missing")
    source_manifest = _load_json(source_manifest_path)
    split_manifest = _load_json(split_manifest_path)
    dataset = config.get("dataset", {})
    for key in ("dataset_id", "source_sha256", "manifest_sha256", "split_sha256", "preprocessing_version"):
        if str(dataset.get(key)) != str(source_manifest.get(key)):
            errors.append(f"dataset.{key} does not match source manifest")
    if str(dataset.get("split_sha256")) != str(split_manifest.get("split_sha256")):
        errors.append("dataset.split_sha256 does not match split manifest")
    authorization = verify_consent(
        consent_path,
        required_purposes={"memory_rag"},
        required_message_types={"text"},
    )
    observed_consent_file_digest = consent_file_digest(consent_path)
    if observed_consent_file_digest != args.consent_file_sha256:
        errors.append("consent file digest does not match the authorized binding")
    observed_model_digest = model_directory_digest(args.embedding_model)
    if observed_model_digest != args.embedding_model_digest:
        errors.append("local embedding model digest does not match the authorized binding")
    if errors:
        raise MemoryBuildError("; ".join(sorted(set(errors))))
    return {
        "config": config,
        "rag": rag,
        "dataset": dataset,
        "sessions_path": sessions_path,
        "source_manifest": source_manifest,
        "split_manifest": split_manifest,
        "authorization": authorization,
        "consent_file_sha256": observed_consent_file_digest,
        "embedding_model_digest": observed_model_digest,
        "output_root": output_root,
    }


def _plan(preflight: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    return {
        "status": "planned",
        "operation": "private_memory_index_build",
        "dataset_id": preflight["dataset"]["dataset_id"],
        "dataset_manifest_sha256": preflight["dataset"]["manifest_sha256"],
        "split_sha256": preflight["dataset"]["split_sha256"],
        "consent_file_sha256": preflight["consent_file_sha256"],
        "owner_scope": args.owner_scope,
        "backend": "hybrid",
        "embedding_model_revision": args.embedding_revision,
        "embedding_model_digest": preflight["embedding_model_digest"],
        "historical_splits_included": ["train", "validation", "test"],
        "future_holdout_policy": "post_index_temporal",
        "training_run": False,
        "creates_mlflow_run": False,
        "private_network_only": True,
    }


def _build(preflight: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    sessions = list(_load_jsonl(preflight["sessions_path"]))
    evidence = build_source_evidence_cards(
        sessions,
        owner_scope=args.owner_scope,
        split_manifest=preflight["split_manifest"],
        max_chars=int(preflight["rag"].get("chunk_max_chars", 700)),
        max_messages=int(preflight["rag"].get("chunk_max_messages", 12)),
    )
    privacy_counts: dict[str, int] = {}
    for card in evidence:
        report = scan_redacted_text(card.content)
        for key, value in report.items():
            if isinstance(value, int) and not isinstance(value, bool):
                privacy_counts[key] = privacy_counts.get(key, 0) + value
    if privacy_counts.get("hard_leak_count", 0):
        raise MemoryBuildError("privacy scan failed for source evidence")
    candidates = extract_relationship_event_candidates(evidence)
    rag = preflight["rag"]
    encoder = LocalBgeEncoder(
        args.embedding_model,
        revision=args.embedding_revision,
        model_digest=args.embedding_model_digest,
        device="cpu",
        max_length=int(rag.get("embedding_max_length", 512)),
    )
    output_root = preflight["output_root"]
    output_root.mkdir(parents=True, exist_ok=True)
    output_root.chmod(stat.S_IRWXU)
    staging = Path(tempfile.mkdtemp(prefix=".memory-index-", dir=output_root))
    try:
        manifest = build_hybrid_index(
            [*evidence, *candidates],
            encoder,
            staging,
            tokenizer_revision=str(rag.get("tokenizer_revision", "tokenizer-cjk-char-bigram-v2")),
            pooling=str(rag.get("pooling", "cls-normalized")),
            lexical_weight=float(rag.get("lexical_weight", 0.35)),
            semantic_weight=float(rag.get("semantic_weight", 0.65)),
            min_score=float(rag.get("min_score", 0.45)),
            consent_scope="memory_rag",
        )
        candidate_payload = [card.as_dict() for card in candidates]
        candidate_digest = _digest(candidate_payload)
        cutoff = max((str(session.get("end_time") or "") for session in sessions), default="")
        identity = {
            "schema_version": "private-memory-build-v1",
            "dataset_id": preflight["dataset"]["dataset_id"],
            "dataset_manifest_sha256": preflight["dataset"]["manifest_sha256"],
            "split_sha256": preflight["dataset"]["split_sha256"],
            "consent_file_sha256": preflight["consent_file_sha256"],
            "owner_scope": args.owner_scope,
            "index_manifest_digest": manifest.manifest_digest,
            "candidate_digest": candidate_digest,
            "embedding_model_revision": args.embedding_revision,
            "embedding_model_digest": args.embedding_model_digest,
            "source_evidence_extractor": EVIDENCE_EXTRACTOR_VERSION,
            "event_candidate_extractor": EVENT_EXTRACTOR_VERSION,
            "historical_splits_included": ["train", "validation", "test"],
            "historical_test_status": "included_in_memory_corpus",
            "future_holdout": {
                "strategy": "post_index_temporal",
                "starts_after": cutoff,
                "status": "awaiting_future_data",
            },
        }
        index_id = "memory_" + _digest(identity)[:20]
        build_manifest = {
            **identity,
            "index_id": index_id,
            "session_count": len(sessions),
            "evidence_card_count": len(evidence),
            "candidate_count": len(candidates),
            "privacy_counts": privacy_counts,
        }
        _write_private_json(staging / "memory_candidates.json", candidate_payload)
        _write_private_json(staging / "build_manifest.json", build_manifest)
        target = output_root / index_id
        if target.exists():
            existing = _load_json(target / "build_manifest.json")
            if existing != build_manifest:
                raise FileExistsError(f"refusing to overwrite different memory index: {target}")
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        return {
            "status": "built",
            "index_id": index_id,
            "index_dir": str(target),
            "index_manifest_digest": manifest.manifest_digest,
            "card_count": manifest.card_count,
            "candidate_count": len(candidates),
            "embedding_model_revision": manifest.embedding_model_revision,
            "future_holdout_status": "awaiting_future_data",
            "training_run": False,
            "private_network_only": True,
        }
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a local-only hybrid memory index")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--consent", type=Path, required=True)
    parser.add_argument("--consent-file-sha256", required=True)
    parser.add_argument("--owner-scope", required=True)
    parser.add_argument("--embedding-model", type=Path, required=True)
    parser.add_argument("--embedding-revision", required=True)
    parser.add_argument("--embedding-model-digest", required=True)
    parser.add_argument("--allowed-root", type=Path, default=Path("/srv/galatea-private/wechat-persona"))
    parser.add_argument("--output-root", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--build", action="store_true")
    args = parser.parse_args()
    try:
        preflight = _preflight(args)
        result = _build(preflight, args) if args.build else _plan(preflight, args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (MemoryBuildError, ValueError, FileNotFoundError, OSError) as exc:
        print(json.dumps({"status": "blocked", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
