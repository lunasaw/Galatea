#!/usr/bin/env python3
"""Plan or run governed, inference-only GPT preprocessing for daily memories.

The command consumes only the authorized redacted session layer. It never reads
an SFT split, creates an MLflow Run, updates model parameters, publishes an
index, or upgrades a fact beyond ``candidate``. A complete run is published as
an immutable private snapshot; failed runs retain resumable private work state.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import threading
from typing import Any, Iterable, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from wechat_persona._common import canonical_json, digest, file_digest  # noqa: E402
from wechat_persona.consent import consent_file_digest, verify_consent  # noqa: E402
from wechat_persona.daily_memory import (  # noqa: E402
    ALIAS_REGISTRY_VERSION,
    ChunkPolicy,
    DATE_PARSER_VERSION,
    FACT_RESOLVER_VERSION,
    DailyMemoryError,
    build_daily_bundles,
    build_fact_ledger,
    chunk_daily_bundles,
    deduplicate_candidates,
    evidence_lookup_from_bundles,
    fit_neighboring_context,
    neighboring_context,
    normalize_fact_candidate,
    request_input,
)
from wechat_persona.datasets import _publish_directory_noreplace  # noqa: E402
from wechat_persona.gpt_fact_extraction import (  # noqa: E402
    DEFAULT_OPENAI_BASE_URL,
    GPTExtractionError,
    GPTSettings,
    TokenCounter,
    WIRE_SCHEMA_MODE_COMPATIBLE,
    WIRE_SCHEMA_MODE_FULL,
    extract_facts,
    extractor_version,
    load_api_key,
    output_schema_digest,
    prompt_digest,
    wire_output_schema_digest_for_mode,
)
from wechat_persona.redact import scan_atomic_text  # noqa: E402


SCHEMA_FILES = {
    "daily_bundle": PROJECT_ROOT / "schemas/daily-bundle.schema.json",
    "fact_extraction": PROJECT_ROOT / "schemas/fact-extraction.schema.json",
    "fact_ledger": PROJECT_ROOT / "schemas/fact-ledger.schema.json",
}
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
OWNER_SCOPE_RE = re.compile(r"^owner_[a-f0-9]{24}$")
PRINT_LOCK = threading.Lock()


class DailyMemoryRunError(RuntimeError):
    """Raised when preflight or snapshot publication fails closed."""


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DailyMemoryRunError(f"cannot read {label}") from exc
    if not isinstance(value, dict):
        raise DailyMemoryRunError(f"{label} must be a JSON object")
    return value


def _iter_jsonl(path: Path, label: str) -> Iterable[dict[str, Any]]:
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError as exc:
        raise DailyMemoryRunError(f"cannot open {label}") from exc
    with handle:
        for line_number, line in enumerate(handle, 1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DailyMemoryRunError(
                    f"invalid JSONL in {label} at line {line_number}"
                ) from exc
            if not isinstance(value, dict):
                raise DailyMemoryRunError(
                    f"non-object JSONL row in {label} at line {line_number}"
                )
            yield value


def _load_config(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise DailyMemoryRunError("cannot read daily memory config") from exc
    if not isinstance(value, dict):
        raise DailyMemoryRunError("daily memory config must be an object")
    required_sections = {"chunk", "extraction", "governance"}
    if not required_sections.issubset(value):
        raise DailyMemoryRunError("daily memory config is missing required sections")
    if value.get("schema_version") != "wechat-persona-memory-daily-v1":
        raise DailyMemoryRunError("unsupported daily memory config version")
    if value.get("grouping") != "calendar_day_with_session_boundaries":
        raise DailyMemoryRunError("daily memory grouping contract is invalid")
    extraction = value["extraction"]
    governance = value["governance"]
    if extraction.get("strict_json_schema") is not True:
        raise DailyMemoryRunError("strict_json_schema must be true")
    if extraction.get("wire_schema_mode") not in {
        WIRE_SCHEMA_MODE_FULL,
        WIRE_SCHEMA_MODE_COMPATIBLE,
    }:
        raise DailyMemoryRunError("unsupported wire_schema_mode")
    if extraction.get("preserve_dates") is not True:
        raise DailyMemoryRunError("preserve_dates must be true")
    if governance.get("result_status") != "candidate":
        raise DailyMemoryRunError("result_status must be candidate")
    if governance.get("training_run") is not False:
        raise DailyMemoryRunError("daily GPT preprocessing cannot be a training run")
    if governance.get("creates_mlflow_run") is not False:
        raise DailyMemoryRunError("daily GPT preprocessing cannot create an MLflow Run")
    return value


def _private_path(path: Path, allowed_root: Path, label: str) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    root = allowed_root.expanduser().resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise DailyMemoryRunError(f"{label} must stay below the private root")
    if resolved.is_symlink():
        raise DailyMemoryRunError(f"{label} must not be a symlink")
    return resolved


def _validate_source_manifest(manifest: Mapping[str, Any]) -> None:
    required = {
        "dataset_id",
        "source_sha256",
        "manifest_sha256",
        "consent_file_sha256",
        "consent_digest",
        "authorization_status",
        "preprocessing_version",
        "redaction_version",
        "scanner_version",
    }
    missing = required - set(manifest)
    if missing:
        raise DailyMemoryRunError(
            f"source manifest missing fields: {','.join(sorted(missing))}"
        )
    if manifest.get("authorization_status") != "verified":
        raise DailyMemoryRunError("source manifest authorization is not verified")
    for key in ("source_sha256", "manifest_sha256", "consent_file_sha256", "consent_digest"):
        if not SHA256_RE.fullmatch(str(manifest.get(key) or "")):
            raise DailyMemoryRunError(f"source manifest {key} is invalid")
    expected = digest({key: value for key, value in manifest.items() if key != "manifest_sha256"})
    if expected != manifest["manifest_sha256"]:
        raise DailyMemoryRunError("source manifest digest does not round-trip")


def _schemas() -> dict[str, tuple[Draft202012Validator, dict[str, Any]]]:
    values: dict[str, tuple[Draft202012Validator, dict[str, Any]]] = {}
    for name, path in SCHEMA_FILES.items():
        schema = _load_json(path, f"{name} schema")
        Draft202012Validator.check_schema(schema)
        values[name] = (
            Draft202012Validator(schema, format_checker=FormatChecker()),
            schema,
        )
    return values


def _validate_rows(
    rows: Iterable[Mapping[str, Any]],
    validator: Draft202012Validator,
    label: str,
) -> None:
    for index, row in enumerate(rows):
        errors = sorted(validator.iter_errors(row), key=lambda error: list(error.path))
        if errors:
            location = ".".join(str(part) for part in errors[0].path) or "root"
            raise DailyMemoryRunError(
                f"{label} row {index} failed schema at {location}: {errors[0].message}"
            )


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(stat.S_IRWXU)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
    os.replace(temporary, path)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(stat.S_IRWXU)
    count = 0
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")
            count += 1
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
    os.replace(temporary, path)
    return count, file_digest(path)


def _chunk_evidence_lookup(chunk: Mapping[str, Any], context: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for turn in [*chunk["primary_turns"], *context]:
        for message in turn.get("messages") or []:
            ref = str(message["evidence_ref"])
            lookup[ref] = {
                "text": str(message["text"]),
                "timestamp": str(message["timestamp"]),
            }
    return lookup


def _validate_unresolved(
    rows: Any, evidence_lookup: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, str]]:
    validated: list[dict[str, str]] = []
    for row in rows or []:
        ref = str(row.get("evidence_ref") or "")
        expression = str(row.get("expression") or "")
        reason = str(row.get("reason") or "")
        evidence = evidence_lookup.get(ref)
        if evidence is None or not expression or expression not in str(evidence["text"]):
            raise DailyMemoryRunError("unresolved reference is not bound to exact evidence")
        if scan_atomic_text(expression)["hard_leak_count"]:
            raise DailyMemoryRunError("unresolved reference failed privacy scan")
        validated.append(
            {"evidence_ref": ref, "expression": expression, "reason": reason}
        )
    return validated


def _preflight(args: argparse.Namespace) -> dict[str, Any]:
    allowed_root = args.allowed_root.expanduser().resolve(strict=True)
    sessions_path = _private_path(args.sessions, allowed_root, "sessions")
    source_manifest_path = _private_path(
        args.source_manifest, allowed_root, "source manifest"
    )
    privacy_report_path = _private_path(
        args.privacy_report, allowed_root, "privacy report"
    )
    consent_path = _private_path(args.consent, allowed_root, "consent")
    output_root = _private_path(args.output_root, allowed_root, "output root")
    for path, label in (
        (sessions_path, "sessions"),
        (source_manifest_path, "source manifest"),
        (privacy_report_path, "privacy report"),
        (consent_path, "consent"),
    ):
        if not path.is_file():
            raise DailyMemoryRunError(f"{label} file is missing")
    config = _load_config(args.config)
    source_manifest = _load_json(source_manifest_path, "source manifest")
    _validate_source_manifest(source_manifest)
    privacy_report = _load_json(privacy_report_path, "privacy report")
    if privacy_report.get("status") != "pass" or privacy_report.get("hard_leak_count") != 0:
        raise DailyMemoryRunError("source privacy report is not a zero-leak pass")
    required_purposes = set(config["governance"]["required_consent_purposes"])
    authorization = verify_consent(
        consent_path,
        required_purposes=required_purposes,
        required_message_types={"text"},
    )
    observed_consent_digest = consent_file_digest(consent_path)
    if observed_consent_digest != source_manifest["consent_file_sha256"]:
        raise DailyMemoryRunError("consent file digest does not match source manifest")
    if str(authorization.get("consent_digest")) != str(source_manifest["consent_digest"]):
        raise DailyMemoryRunError("consent record digest does not match source manifest")
    if not OWNER_SCOPE_RE.fullmatch(args.owner_scope):
        raise DailyMemoryRunError("owner_scope must be owner_<24 hex>")
    schemas = _schemas()
    tokenizer = TokenCounter(str(config["chunk"]["token_counter"]))
    sessions_sha256 = file_digest(sessions_path)
    config_sha256 = digest(config)
    implementation_files = [
        Path(__file__).resolve(),
        PROJECT_ROOT / "src/wechat_persona/daily_memory.py",
        PROJECT_ROOT / "src/wechat_persona/gpt_fact_extraction.py",
        *SCHEMA_FILES.values(),
    ]
    implementation_sha256 = digest(
        {
            str(path.relative_to(PROJECT_ROOT)): file_digest(path)
            for path in sorted(implementation_files)
        }
    )
    identity = {
        "schema_version": "daily-memory-snapshot-identity-v1",
        "dataset_id": source_manifest["dataset_id"],
        "source_manifest_sha256": source_manifest["manifest_sha256"],
        "sessions_sha256": sessions_sha256,
        "consent_file_sha256": observed_consent_digest,
        "owner_scope": args.owner_scope,
        "config_sha256": config_sha256,
        "implementation_sha256": implementation_sha256,
        "timezone": config["timezone"],
        "extractor_model": str(args.model or config["extraction"]["model"]),
        "extractor_provider": str(config["extraction"]["provider"]),
        "extractor_api_mode": str(config["extraction"]["api_mode"]),
        "endpoint_sha256": digest(
            str(args.openai_base_url or DEFAULT_OPENAI_BASE_URL).rstrip("/")
        ),
        "prompt_sha256": prompt_digest(),
        "response_schema_sha256": output_schema_digest(),
        "wire_schema_mode": str(config["extraction"]["wire_schema_mode"]),
        "wire_schema_sha256": wire_output_schema_digest_for_mode(
            str(config["extraction"]["wire_schema_mode"])
        ),
        "tokenizer_revision": tokenizer.revision,
        "authorization_id": args.authorization_id,
    }
    snapshot_id = "daily-memory_" + digest(identity)[:20]
    return {
        "allowed_root": allowed_root,
        "sessions_path": sessions_path,
        "source_manifest_path": source_manifest_path,
        "privacy_report_path": privacy_report_path,
        "consent_path": consent_path,
        "output_root": output_root,
        "config": config,
        "config_sha256": config_sha256,
        "implementation_sha256": implementation_sha256,
        "source_manifest": source_manifest,
        "authorization": authorization,
        "consent_file_sha256": observed_consent_digest,
        "schemas": schemas,
        "tokenizer": tokenizer,
        "sessions_sha256": sessions_sha256,
        "identity": identity,
        "snapshot_id": snapshot_id,
    }


def _materialize_plan(preflight: Mapping[str, Any]) -> dict[str, Any]:
    sessions = list(_iter_jsonl(preflight["sessions_path"], "sessions"))
    bundles, lineage, source_counts = build_daily_bundles(
        sessions,
        owner_scope=preflight["identity"]["owner_scope"],
        source_manifest_sha256=preflight["source_manifest"]["manifest_sha256"],
        timezone_name=preflight["config"]["timezone"],
    )
    config = preflight["config"]
    policy = ChunkPolicy(
        target_input_tokens=int(config["chunk"]["target_input_tokens"]),
        hard_input_tokens=int(config["chunk"]["hard_input_tokens"]),
        overlap_turns=int(config["chunk"]["overlap_turns"]),
    )
    chunks = chunk_daily_bundles(
        bundles,
        source_manifest_sha256=preflight["source_manifest"]["manifest_sha256"],
        policy=policy,
        count_request_tokens=preflight["tokenizer"].count_request,
    )
    bundle_validator = preflight["schemas"]["daily_bundle"][0]
    _validate_rows(bundles, bundle_validator, "daily bundle")
    token_counts = sorted(int(chunk["input_tokens_without_context"]) for chunk in chunks)
    blocked = [chunk for chunk in chunks if chunk["blocked_reason"]]
    return {
        "sessions": sessions,
        "bundles": bundles,
        "lineage": lineage,
        "chunks": chunks,
        "source_counts": source_counts,
        "policy": policy,
        "plan": {
            "status": "blocked" if blocked else "planned",
            "operation": "daily_memory_gpt_preprocessing",
            "snapshot_id": preflight["snapshot_id"],
            "dataset_id": preflight["source_manifest"]["dataset_id"],
            "source_manifest_sha256": preflight["source_manifest"]["manifest_sha256"],
            "sessions_sha256": preflight["sessions_sha256"],
            "owner_scope": preflight["identity"]["owner_scope"],
            "timezone": config["timezone"],
            "day_count": len(bundles),
            "session_count": source_counts.get("session_count", 0),
            "message_count": source_counts.get("message_count", 0),
            "chunk_count": len(chunks),
            "blocked_chunk_count": len(blocked),
            "relative_marker_chunk_count": sum(
                any(
                    marker in str(message.get("text") or "")
                    for turn in chunk["primary_turns"]
                    for message in turn["messages"]
                    for marker in ("今天", "昨天", "前天", "明天", "后天", "那天", "第二天", "当天")
                )
                for chunk in chunks
            ),
            "token_stats_without_neighbor_context": {
                "min": token_counts[0] if token_counts else 0,
                "p50": token_counts[len(token_counts) // 2] if token_counts else 0,
                "p95": token_counts[min(len(token_counts) - 1, int(len(token_counts) * 0.95))] if token_counts else 0,
                "max": token_counts[-1] if token_counts else 0,
                "sum": sum(token_counts),
            },
            "extractor_model": preflight["identity"]["extractor_model"],
            "wire_schema_mode": preflight["identity"]["wire_schema_mode"],
            "wire_schema_sha256": preflight["identity"]["wire_schema_sha256"],
            "result_status": "candidate",
            "training_run": False,
            "creates_mlflow_run": False,
            "will_read_sft_test_split": False,
            "will_build_index": False,
            "will_publish_confirmed_facts": False,
        },
    }


def _call_identity(
    preflight: Mapping[str, Any],
    chunk: Mapping[str, Any],
    actual_input: Mapping[str, Any],
) -> str:
    return digest(
        {
            "source_manifest_sha256": preflight["source_manifest"]["manifest_sha256"],
            "owner_scope": preflight["identity"]["owner_scope"],
            "day_id": chunk["day_id"],
            "session_id": chunk["session_id"],
            "chunk_ordinal": chunk["chunk_ordinal"],
            "chunk_input_digest": digest(actual_input),
            "extractor_model": preflight["identity"]["extractor_model"],
            "prompt_digest": prompt_digest(),
            "wire_schema_sha256": preflight["identity"]["wire_schema_sha256"],
            "schema_version": "fact-extraction-v1",
        }
    )


def _legacy_call_identity(
    preflight: Mapping[str, Any],
    chunk: Mapping[str, Any],
    actual_input: Mapping[str, Any],
) -> str:
    """Reproduce call IDs created before wire-schema identity was recorded."""

    return digest(
        {
            "source_manifest_sha256": preflight["source_manifest"]["manifest_sha256"],
            "owner_scope": preflight["identity"]["owner_scope"],
            "day_id": chunk["day_id"],
            "session_id": chunk["session_id"],
            "chunk_ordinal": chunk["chunk_ordinal"],
            "chunk_input_digest": digest(actual_input),
            "extractor_model": preflight["identity"]["extractor_model"],
            "prompt_digest": prompt_digest(),
            "schema_version": "fact-extraction-v1",
        }
    )


def _prepare_chunk_requests(
    preflight: Mapping[str, Any],
    chunk: Mapping[str, Any],
    bundles_by_day: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    config = preflight["config"]
    hard_tokens = int(config["chunk"]["hard_input_tokens"])

    def prepare(days: int) -> dict[str, Any]:
        context, available = neighboring_context(
            bundles_by_day,
            chunk,
            days=days,
            turns_per_day=int(config["neighbor_context_turns"]),
        )
        included, skipped = fit_neighboring_context(
            chunk,
            context,
            hard_input_tokens=hard_tokens,
            count_request_tokens=preflight["tokenizer"].count_request,
        )
        value = request_input(
            day_id=str(chunk["day_id"]),
            session_id=str(chunk["session_id"]),
            primary_turns=chunk["primary_turns"],
            context_turns=included,
        )
        tokens = preflight["tokenizer"].count_request(value)
        if tokens > hard_tokens:
            raise DailyMemoryRunError("actual request exceeds hard_input_tokens")
        return {
            "context": included,
            "available": available,
            "skipped": skipped,
            "input": value,
            "tokens": tokens,
            "call_id": _call_identity(preflight, chunk, value),
        }

    return (
        prepare(int(config["neighbor_days"])),
        prepare(int(config["unresolved_neighbor_days"])),
    )


def _load_current_cached_result(
    chunk_dir: Path,
    chunk: Mapping[str, Any],
    prepared_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    for prepared in prepared_rows:
        cached_path = chunk_dir / f"{prepared['call_id']}.json"
        if not cached_path.is_file():
            continue
        result = _load_json(cached_path, "cached chunk result")
        if (
            result.get("call_id") != prepared["call_id"]
            or result.get("status") != "succeeded"
            or result.get("chunk_id") != chunk["chunk_id"]
            or result.get("day_id") != chunk["day_id"]
            or result.get("session_id") != chunk["session_id"]
            or result.get("chunk_ordinal") != chunk["chunk_ordinal"]
            or result.get("input_digest") != digest(prepared["input"])
        ):
            raise DailyMemoryRunError("cached chunk result has invalid identity")
        return result
    return None


def _process_chunk(
    preflight: Mapping[str, Any],
    chunk: Mapping[str, Any],
    bundles_by_day: Mapping[str, Mapping[str, Any]],
    settings: GPTSettings,
    chunk_dir: Path,
) -> dict[str, Any]:
    config = preflight["config"]
    initial, expanded = _prepare_chunk_requests(preflight, chunk, bundles_by_day)
    cache_candidates = [expanded, initial] if expanded["call_id"] != initial["call_id"] else [initial]
    cached = _load_current_cached_result(chunk_dir, chunk, cache_candidates)
    if cached is not None:
        return cached

    selected = initial
    response, response_metadata = extract_facts(initial["input"], settings)
    api_usage = dict(response_metadata["usage"])
    needs_expansion = bool(response.get("unresolved_references")) or any(
        fact.get("unresolved_references") for fact in response.get("facts") or []
    )
    expansion_retry_count = 0
    if needs_expansion and expanded["call_id"] != initial["call_id"]:
        response, response_metadata = extract_facts(expanded["input"], settings)
        selected = expanded
        expansion_retry_count = 1
        for key, value in response_metadata["usage"].items():
            api_usage[key] = int(api_usage.get(key, 0)) + int(value)
    included_context = selected["context"]
    actual_input = selected["input"]
    actual_input_tokens = selected["tokens"]
    call_id = selected["call_id"]
    result_path = chunk_dir / f"{call_id}.json"
    local_evidence = _chunk_evidence_lookup(chunk, included_context)
    unresolved = _validate_unresolved(
        response.get("unresolved_references"), local_evidence
    )
    normalized: list[dict[str, Any]] = []
    rejected_inferred_count = 0
    version = extractor_version(settings.model)
    for raw_fact in response.get("facts") or []:
        try:
            normalized.append(
                normalize_fact_candidate(
                    raw_fact,
                    chunk=chunk,
                    evidence_lookup=local_evidence,
                    extractor_version=version,
                    allow_inference=bool(config["extraction"].get("allow_inference", False)),
                )
            )
        except DailyMemoryError as exc:
            if str(exc) == "inferred fact rejected by extraction policy":
                rejected_inferred_count += 1
                continue
            raise
    result = {
        "schema_version": "daily-chunk-result-v1",
        "call_id": call_id,
        "chunk_id": chunk["chunk_id"],
        "day_id": chunk["day_id"],
        "session_id": chunk["session_id"],
        "chunk_ordinal": chunk["chunk_ordinal"],
        "input_digest": digest(actual_input),
        "input_tokens_local": actual_input_tokens,
        "primary_evidence_count": len(chunk["primary_evidence_refs"]),
        "neighbor_context_turns_available": selected["available"],
        "neighbor_context_turns_included": len(included_context),
        "neighbor_context_turns_skipped_for_budget": selected["skipped"],
        "context_expansion_retry_count": expansion_retry_count,
        "facts": normalized,
        "unresolved_references": unresolved,
        "rejected_inferred_count": rejected_inferred_count,
        "response": response_metadata,
        "api_usage": api_usage,
        "status": "succeeded",
    }
    _write_json(result_path, result)
    return result


def _legacy_work_root(
    preflight: Mapping[str, Any], snapshot_id: str
) -> Path:
    if not re.fullmatch(r"daily-memory_[a-f0-9]{20}", snapshot_id):
        raise DailyMemoryRunError("import work snapshot ID is invalid")
    work_root = preflight["output_root"] / f".{snapshot_id}.work"
    if work_root.is_symlink() or not work_root.is_dir():
        raise DailyMemoryRunError("import work snapshot is missing or unsafe")
    identity = _load_json(work_root / "identity.json", "import work identity")
    if identity.get("snapshot_id") != snapshot_id:
        raise DailyMemoryRunError("import work snapshot identity mismatch")
    expected_fields = {
        "authorization_id",
        "consent_file_sha256",
        "dataset_id",
        "endpoint_sha256",
        "extractor_api_mode",
        "extractor_model",
        "extractor_provider",
        "owner_scope",
        "prompt_sha256",
        "response_schema_sha256",
        "sessions_sha256",
        "source_manifest_sha256",
        "timezone",
        "tokenizer_revision",
    }
    for key in expected_fields:
        if identity.get(key) != preflight["identity"].get(key):
            raise DailyMemoryRunError(f"import work identity differs at {key}")
    source_wire_mode = identity.get("wire_schema_mode", WIRE_SCHEMA_MODE_FULL)
    source_wire_digest = identity.get("wire_schema_sha256", output_schema_digest())
    if (
        source_wire_mode != WIRE_SCHEMA_MODE_FULL
        or source_wire_digest != output_schema_digest()
    ):
        raise DailyMemoryRunError("import work did not use the full strict wire schema")
    return work_root


def _validate_imported_result(
    preflight: Mapping[str, Any],
    chunk: Mapping[str, Any],
    prepared: Mapping[str, Any],
    result: Mapping[str, Any],
) -> None:
    expected_source_call_id = _legacy_call_identity(
        preflight, chunk, prepared["input"]
    )
    if (
        result.get("schema_version") != "daily-chunk-result-v1"
        or result.get("status") != "succeeded"
        or result.get("call_id") != expected_source_call_id
        or result.get("chunk_id") != chunk["chunk_id"]
        or result.get("day_id") != chunk["day_id"]
        or result.get("session_id") != chunk["session_id"]
        or result.get("chunk_ordinal") != chunk["chunk_ordinal"]
        or result.get("input_digest") != digest(prepared["input"])
        or result.get("input_tokens_local") != prepared["tokens"]
        or result.get("primary_evidence_count")
        != len(chunk["primary_evidence_refs"])
    ):
        raise DailyMemoryRunError("imported chunk result has invalid identity")
    response = result.get("response")
    if (
        not isinstance(response, Mapping)
        or response.get("model") != preflight["identity"]["extractor_model"]
        or not SHA256_RE.fullmatch(str(response.get("response_digest") or ""))
    ):
        raise DailyMemoryRunError("imported chunk response metadata is invalid")
    facts = result.get("facts")
    if not isinstance(facts, list):
        raise DailyMemoryRunError("imported chunk facts must be an array")
    _validate_rows(
        facts, preflight["schemas"]["fact_extraction"][0], "imported fact extraction"
    )
    evidence_lookup = _chunk_evidence_lookup(chunk, prepared["context"])
    primary_refs = set(str(value) for value in chunk["primary_evidence_refs"])
    expected_extractor_version = extractor_version(
        preflight["identity"]["extractor_model"]
    )
    for fact in facts:
        if (
            fact.get("status") != "candidate"
            or fact.get("day_id") != chunk["day_id"]
            or fact.get("session_id") != chunk["session_id"]
            or fact.get("chunk_ordinal") != chunk["chunk_ordinal"]
            or fact.get("extractor_version") != expected_extractor_version
        ):
            raise DailyMemoryRunError("imported fact lineage is invalid")
        fact_refs: set[str] = set()
        for evidence in fact["evidence"]:
            ref = str(evidence["evidence_ref"])
            quote = str(evidence["quote"])
            source = evidence_lookup.get(ref)
            if (
                source is None
                or not quote
                or quote not in str(source["text"])
                or scan_atomic_text(quote)["hard_leak_count"]
            ):
                raise DailyMemoryRunError("imported fact evidence is invalid")
            fact_refs.add(ref)
        if not primary_refs.intersection(fact_refs):
            raise DailyMemoryRunError("imported fact lacks primary evidence")
    unresolved = _validate_unresolved(
        result.get("unresolved_references"), evidence_lookup
    )
    if unresolved != list(result.get("unresolved_references") or []):
        raise DailyMemoryRunError("imported unresolved references are not canonical")


def _import_legacy_cache(
    preflight: Mapping[str, Any],
    chunks: Sequence[Mapping[str, Any]],
    bundles_by_day: Mapping[str, Mapping[str, Any]],
    chunk_dir: Path,
    source_snapshot_id: str,
) -> dict[str, int | str]:
    source_work_root = _legacy_work_root(preflight, source_snapshot_id)
    source_chunk_dir = source_work_root / "chunks"
    if source_chunk_dir.is_symlink() or not source_chunk_dir.is_dir():
        raise DailyMemoryRunError("import work chunk cache is missing or unsafe")
    by_chunk: dict[str, list[dict[str, Any]]] = {}
    source_file_count = 0
    for path in sorted(source_chunk_dir.glob("*.json")):
        if path.is_symlink() or path.stem != path.name.removesuffix(".json"):
            raise DailyMemoryRunError("import work contains an unsafe cache file")
        result = _load_json(path, "imported chunk result")
        if path.stem != result.get("call_id"):
            raise DailyMemoryRunError("imported chunk filename identity mismatch")
        by_chunk.setdefault(str(result.get("chunk_id") or ""), []).append(result)
        source_file_count += 1
    imported_count = 0
    already_present_count = 0
    for chunk in chunks:
        source_results = by_chunk.get(str(chunk["chunk_id"])) or []
        if not source_results:
            continue
        initial, expanded = _prepare_chunk_requests(
            preflight, chunk, bundles_by_day
        )
        prepared_rows = (
            [expanded, initial]
            if expanded["call_id"] != initial["call_id"]
            else [initial]
        )
        matches: list[tuple[dict[str, Any], Mapping[str, Any]]] = []
        for source_result in source_results:
            for prepared in prepared_rows:
                if (
                    source_result.get("input_digest") == digest(prepared["input"])
                    and source_result.get("call_id")
                    == _legacy_call_identity(preflight, chunk, prepared["input"])
                ):
                    matches.append((prepared, source_result))
        if len(matches) != 1:
            raise DailyMemoryRunError("imported chunk cache is ambiguous")
        prepared, source_result = matches[0]
        _validate_imported_result(
            preflight, chunk, prepared, source_result
        )
        target_path = chunk_dir / f"{prepared['call_id']}.json"
        if target_path.exists():
            _load_current_cached_result(chunk_dir, chunk, [prepared])
            already_present_count += 1
            continue
        adapted = dict(source_result)
        adapted["source_call_id"] = str(source_result["call_id"])
        adapted["source_snapshot_id"] = source_snapshot_id
        adapted["cache_origin"] = "validated_full_schema_import"
        adapted["call_id"] = prepared["call_id"]
        adapted["response"] = {
            **dict(source_result["response"]),
            "wire_schema_mode": WIRE_SCHEMA_MODE_FULL,
            "wire_schema_sha256": output_schema_digest(),
        }
        _write_json(target_path, adapted)
        imported_count += 1
    return {
        "source_snapshot_id": source_snapshot_id,
        "source_file_count": source_file_count,
        "imported_count": imported_count,
        "already_present_count": already_present_count,
    }


def _run_calls(
    preflight: Mapping[str, Any],
    materialized: Mapping[str, Any],
    args: argparse.Namespace,
    *,
    smoke: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if not args.acknowledge_external_processing:
        raise DailyMemoryRunError(
            "external GPT processing requires --acknowledge-external-processing"
        )
    config = preflight["config"]
    extraction = config["extraction"]
    settings = GPTSettings(
        api_key=load_api_key(args.auth_file),
        base_url=str(args.openai_base_url or DEFAULT_OPENAI_BASE_URL),
        model=str(args.model or extraction["model"]),
        api_mode=str(extraction.get("api_mode") or "chat_completions"),
        temperature=float(extraction.get("temperature", 0)),
        max_output_tokens=int(config["chunk"]["reserve_output_tokens"]),
        request_timeout=float(
            args.request_timeout or extraction.get("request_timeout_seconds", 120)
        ),
        max_retries=int(
            args.max_retries
            if args.max_retries is not None
            else extraction.get("max_retries", 4)
        ),
        wire_schema_mode=str(extraction["wire_schema_mode"]),
    )
    if smoke:
        selector = min if args.smoke_chunk == "smallest" else max
        chunks = [
            selector(
                materialized["chunks"],
                key=lambda row: row["input_tokens_without_context"],
            )
        ]
    else:
        chunks = list(materialized["chunks"])
    blocked = [chunk for chunk in chunks if chunk["blocked_reason"]]
    if blocked:
        raise DailyMemoryRunError(
            f"{len(blocked)} primary chunks exceed hard_input_tokens"
        )
    bundles_by_day = {str(bundle["day_id"]): bundle for bundle in materialized["bundles"]}
    if smoke:
        temporary_root = Path(tempfile.mkdtemp(prefix="daily-memory-gpt-smoke-"))
        temporary_root.chmod(stat.S_IRWXU)
        chunk_dir = temporary_root
    else:
        output_root = preflight["output_root"]
        output_root.mkdir(parents=True, exist_ok=True)
        output_root.chmod(stat.S_IRWXU)
        work_root = output_root / f".{preflight['snapshot_id']}.work"
        work_root.mkdir(mode=stat.S_IRWXU, exist_ok=True)
        chunk_dir = work_root / "chunks"
        chunk_dir.mkdir(mode=stat.S_IRWXU, exist_ok=True)
        _write_json(
            work_root / "identity.json",
            {**preflight["identity"], "snapshot_id": preflight["snapshot_id"]},
        )
        import_snapshot_id = getattr(args, "import_work_snapshot_id", None)
        if import_snapshot_id:
            import_report = _import_legacy_cache(
                preflight,
                chunks,
                bundles_by_day,
                chunk_dir,
                str(import_snapshot_id),
            )
            _write_json(work_root / "import-report.json", import_report)
            with PRINT_LOCK:
                print(
                    json.dumps(
                        {"status": "cache_import", **import_report},
                        sort_keys=True,
                    ),
                    flush=True,
                )
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    workers = 1 if smoke else int(args.workers or extraction.get("workers", 8))
    pending_chunks: list[Mapping[str, Any]] = []
    if smoke:
        pending_chunks = chunks
    else:
        for chunk in chunks:
            initial, expanded = _prepare_chunk_requests(
                preflight, chunk, bundles_by_day
            )
            prepared_rows = (
                [expanded, initial]
                if expanded["call_id"] != initial["call_id"]
                else [initial]
            )
            cached = _load_current_cached_result(
                chunk_dir, chunk, prepared_rows
            )
            if cached is None:
                pending_chunks.append(chunk)
            else:
                results.append(cached)
        with PRINT_LOCK:
            print(
                json.dumps(
                    {
                        "status": "cache_scan",
                        "cached": len(results),
                        "pending": len(pending_chunks),
                        "total": len(chunks),
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _process_chunk,
                preflight,
                chunk,
                bundles_by_day,
                settings,
                chunk_dir,
            ): chunk
            for chunk in pending_chunks
        }
        processed = len(results)
        for future in as_completed(futures):
            chunk = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - collect a resumable failure set
                failures.append(
                    {
                        "chunk_id": str(chunk["chunk_id"]),
                        "day_id": str(chunk["day_id"]),
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:300],
                    }
                )
            processed += 1
            if not smoke and (
                processed % 100 == 0 or processed == len(chunks)
            ):
                with PRINT_LOCK:
                    print(
                        json.dumps(
                            {
                                "status": "progress",
                                "processed": processed,
                                "succeeded": len(results),
                                "failed": len(failures),
                                "total": len(chunks),
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    )
    if smoke:
        shutil.rmtree(temporary_root)
    return results, failures


def _privacy_report(
    bundles: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    for bundle in bundles:
        for session in bundle["sessions"]:
            for turn in session["turns"]:
                for message in turn["messages"]:
                    for key, value in scan_atomic_text(message["text"]).items():
                        counts[f"bundles.{key}"] += value
    for candidate in candidates:
        for evidence in candidate["evidence"]:
            for key, value in scan_atomic_text(evidence["quote"]).items():
                counts[f"extractions.{key}"] += value
    hard_leaks = counts["bundles.hard_leak_count"] + counts["extractions.hard_leak_count"]
    return {
        "schema_version": "daily-memory-privacy-report-v1",
        "status": "pass" if hard_leaks == 0 else "fail",
        "hard_leak_count": hard_leaks,
        "counts": dict(sorted(counts.items())),
    }


def _publish(
    preflight: Mapping[str, Any],
    materialized: Mapping[str, Any],
    results: Sequence[Mapping[str, Any]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    all_candidates = [
        fact for result in results for fact in result.get("facts") or []
    ]
    candidates = deduplicate_candidates(all_candidates)
    ledger = build_fact_ledger(
        candidates, owner_scope=preflight["identity"]["owner_scope"]
    )
    _validate_rows(
        candidates, preflight["schemas"]["fact_extraction"][0], "fact extraction"
    )
    _validate_rows(ledger, preflight["schemas"]["fact_ledger"][0], "fact ledger")
    privacy = _privacy_report(materialized["bundles"], candidates)
    if privacy["hard_leak_count"]:
        raise DailyMemoryRunError("output privacy scan failed")
    output_root = preflight["output_root"]
    target = output_root / preflight["snapshot_id"]
    if target.exists():
        existing = _load_json(target / "manifest.json", "existing manifest")
        if existing.get("snapshot_id") != preflight["snapshot_id"]:
            raise DailyMemoryRunError("existing snapshot identity mismatch")
        return {
            "status": "already_published",
            "snapshot_id": preflight["snapshot_id"],
            "output": str(target),
            "manifest_sha256": existing.get("manifest_sha256"),
            "candidate_count": existing.get("counts", {}).get("candidate_count", 0),
            "fact_group_count": existing.get("counts", {}).get("fact_group_count", 0),
        }
    staging = Path(tempfile.mkdtemp(prefix=f".{preflight['snapshot_id']}.publish-", dir=output_root))
    staging.chmod(stat.S_IRWXU)
    try:
        output_digests: dict[str, str] = {}
        _, output_digests["daily_bundles"] = _write_jsonl(
            staging / "daily-bundles/index.jsonl", materialized["bundles"]
        )
        _, output_digests["evidence_map"] = _write_jsonl(
            staging / "manifests/evidence-map.jsonl", materialized["lineage"]
        )
        _, output_digests["daily_extractions"] = _write_jsonl(
            staging / "daily-extractions/index.jsonl", candidates
        )
        _, output_digests["review_candidates"] = _write_jsonl(
            staging / "review/candidates.jsonl", candidates
        )
        _, output_digests["fact_ledger"] = _write_jsonl(
            staging / "fact-ledger.jsonl", ledger
        )
        chunk_rows = [
            {
                key: value
                for key, value in result.items()
                if key not in {"facts", "unresolved_references"}
            }
            | {
                "fact_count": len(result.get("facts") or []),
                "unresolved_reference_count": len(result.get("unresolved_references") or []),
            }
            for result in sorted(
                results,
                key=lambda row: (row["day_id"], row["session_id"], row["chunk_ordinal"]),
            )
        ]
        _, output_digests["chunk_manifest"] = _write_jsonl(
            staging / "manifests/chunks.jsonl", chunk_rows
        )
        extraction_report = {
            "schema_version": "daily-memory-extraction-report-v1",
            "status": "complete",
            "planned_day_count": len(materialized["bundles"]),
            "completed_day_count": len({row["day_id"] for row in results}),
            "planned_chunk_count": len(materialized["chunks"]),
            "completed_chunk_count": len(results),
            "failed_chunk_count": 0,
            "candidate_count_before_deduplication": len(all_candidates),
            "candidate_count": len(candidates),
            "duplicate_candidate_count": len(all_candidates) - len(candidates),
            "fact_group_count": len(ledger),
            "conflicted_fact_group_count": sum(
                row["conflict_status"] == "conflicted" for row in ledger
            ),
            "insufficient_fact_group_count": sum(
                row["conflict_status"] == "insufficient" for row in ledger
            ),
            "unresolved_reference_count": sum(
                len(row.get("unresolved_references") or []) for row in results
            ),
            "rejected_inferred_count": sum(
                int(row.get("rejected_inferred_count") or 0) for row in results
            ),
            "validated_import_chunk_count": sum(
                row.get("cache_origin") == "validated_full_schema_import"
                for row in results
            ),
            "input_tokens_api": sum(
                int((row.get("api_usage") or {}).get("input_tokens") or 0)
                for row in results
            ),
            "output_tokens_api": sum(
                int((row.get("api_usage") or {}).get("output_tokens") or 0)
                for row in results
            ),
            "truncation_rate": 0.0,
            "day_coverage": 1.0,
            "chunk_coverage": 1.0,
            "confirmed_fact_count": 0,
        }
        _write_json(staging / "reports/extraction-report.json", extraction_report)
        output_digests["extraction_report"] = file_digest(
            staging / "reports/extraction-report.json"
        )
        _write_json(staging / "reports/privacy-report.json", privacy)
        output_digests["privacy_report"] = file_digest(
            staging / "reports/privacy-report.json"
        )
        temporal_report = {
            "schema_version": "daily-memory-temporal-report-v1",
            "date_parser_version": DATE_PARSER_VERSION,
            "date_fact_count": sum(row["value_type"] == "date" for row in candidates),
            "date_precision_counts": dict(
                sorted(Counter(row["date_precision"] for row in candidates).items())
            ),
            "date_basis_counts": dict(
                sorted(Counter(row["date_basis"] for row in candidates).items())
            ),
            "unresolved_candidate_count": sum(
                bool(row["unresolved_references"]) for row in candidates
            ),
        }
        _write_json(staging / "reports/temporal-resolution-report.json", temporal_report)
        output_digests["temporal_report"] = file_digest(
            staging / "reports/temporal-resolution-report.json"
        )
        response_models = sorted(
            {
                str(row.get("response", {}).get("model") or "")
                for row in results
                if row.get("response", {}).get("model")
            }
        )
        observed_wire_schema_modes = sorted(
            {
                str(row.get("response", {}).get("wire_schema_mode") or "")
                for row in results
                if row.get("response", {}).get("wire_schema_mode")
            }
        )
        observed_wire_schema_digests = sorted(
            {
                str(row.get("response", {}).get("wire_schema_sha256") or "")
                for row in results
                if row.get("response", {}).get("wire_schema_sha256")
            }
        )
        imported_source_snapshot_ids = sorted(
            {
                str(row.get("source_snapshot_id") or "")
                for row in results
                if row.get("source_snapshot_id")
            }
        )
        manifest = {
            "schema_version": "daily-memory-snapshot-v1",
            "snapshot_id": preflight["snapshot_id"],
            "status": "candidate_review_pending",
            "identity": preflight["identity"],
            "dataset_id": preflight["source_manifest"]["dataset_id"],
            "source_manifest_sha256": preflight["source_manifest"]["manifest_sha256"],
            "normalized_manifest_sha256": preflight["source_manifest"]["manifest_sha256"],
            "sessions_sha256": preflight["sessions_sha256"],
            "consent_file_sha256": preflight["consent_file_sha256"],
            "consent_digest": preflight["source_manifest"]["consent_digest"],
            "consent_purposes": sorted(preflight["config"]["governance"]["required_consent_purposes"]),
            "authorization_status": "verified",
            "authorization_id": args.authorization_id,
            "timezone": preflight["config"]["timezone"],
            "grouping": preflight["config"]["grouping"],
            "session_version": "wechat-session-v2",
            "message_schema_version": "message-v1",
            "preprocessing_version": preflight["source_manifest"]["preprocessing_version"],
            "redaction_version": preflight["source_manifest"]["redaction_version"],
            "scanner_version": preflight["source_manifest"]["scanner_version"],
            "config_sha256": preflight["config_sha256"],
            "implementation_sha256": preflight["implementation_sha256"],
            "chunk_policy": preflight["config"]["chunk"],
            "neighbor_days": preflight["config"]["neighbor_days"],
            "unresolved_neighbor_days": preflight["config"]["unresolved_neighbor_days"],
            "extractor_provider": preflight["config"]["extraction"]["provider"],
            "extractor_api_mode": preflight["config"]["extraction"]["api_mode"],
            "extractor_model_requested": preflight["identity"]["extractor_model"],
            "extractor_models_returned": response_models,
            "extractor_version": extractor_version(preflight["identity"]["extractor_model"]),
            "prompt_sha256": prompt_digest(),
            "response_schema_sha256": output_schema_digest(),
            "wire_schema_mode_requested": preflight["identity"]["wire_schema_mode"],
            "wire_schema_sha256_requested": preflight["identity"]["wire_schema_sha256"],
            "wire_schema_modes_observed": observed_wire_schema_modes,
            "wire_schema_digests_observed": observed_wire_schema_digests,
            "validated_import_source_snapshot_ids": imported_source_snapshot_ids,
            "temperature": preflight["config"]["extraction"].get("temperature"),
            "tokenizer_revision": preflight["tokenizer"].revision,
            "fact_resolver_version": FACT_RESOLVER_VERSION,
            "alias_registry_version": ALIAS_REGISTRY_VERSION,
            "date_parser_version": DATE_PARSER_VERSION,
            "counts": {
                **materialized["source_counts"],
                "chunk_count": len(materialized["chunks"]),
                "candidate_count": len(candidates),
                "fact_group_count": len(ledger),
                "confirmed_fact_count": 0,
                "failed_chunk_count": 0,
            },
            "output_digests": dict(sorted(output_digests.items())),
            "code_revision": args.code_revision,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "training_run": False,
            "creates_mlflow_run": False,
            "formal_training_eligible": False,
            "human_review_completed": False,
            "confirmed_index_built": False,
            "rag_index_built": False,
        }
        manifest["manifest_sha256"] = digest(manifest)
        _write_json(staging / "manifest.json", manifest)
        _publish_directory_noreplace(staging, target)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    work_root = output_root / f".{preflight['snapshot_id']}.work"
    if work_root.exists():
        shutil.rmtree(work_root)
    return {
        "status": "published",
        "snapshot_id": preflight["snapshot_id"],
        "output": str(target),
        "manifest_sha256": manifest["manifest_sha256"],
        "day_count": len(materialized["bundles"]),
        "chunk_count": len(materialized["chunks"]),
        "candidate_count": len(candidates),
        "fact_group_count": len(ledger),
        "confirmed_fact_count": 0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--privacy-report", type=Path, required=True)
    parser.add_argument("--consent", type=Path, required=True)
    parser.add_argument("--owner-scope", required=True)
    parser.add_argument("--allowed-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--model")
    parser.add_argument("--openai-base-url")
    parser.add_argument("--auth-file", type=Path, default=Path.home() / ".codex/auth.json")
    parser.add_argument("--workers", type=int)
    parser.add_argument("--request-timeout", type=float)
    parser.add_argument("--max-retries", type=int)
    parser.add_argument(
        "--import-work-snapshot-id",
        help="revalidate and import a compatible private full-schema work cache",
    )
    parser.add_argument(
        "--smoke-chunk",
        choices=("smallest", "largest"),
        default="smallest",
        help="choose the token-size boundary exercised by --smoke",
    )
    parser.add_argument("--acknowledge-external-processing", action="store_true")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--execute", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.workers is not None and args.workers < 1:
        raise SystemExit("--workers must be positive")
    if args.request_timeout is not None and args.request_timeout <= 0:
        raise SystemExit("--request-timeout must be positive")
    if args.max_retries is not None and args.max_retries < 0:
        raise SystemExit("--max-retries must be non-negative")
    try:
        preflight = _preflight(args)
        materialized = _materialize_plan(preflight)
        print(json.dumps(materialized["plan"], ensure_ascii=False, sort_keys=True), flush=True)
        if materialized["plan"]["status"] == "blocked":
            return 2
        if args.plan:
            return 0
        results, failures = _run_calls(
            preflight, materialized, args, smoke=args.smoke
        )
        if failures:
            report = {
                "status": "incomplete",
                "snapshot_id": preflight["snapshot_id"],
                "completed_chunk_count": len(results),
                "failed_chunk_count": len(failures),
                "failures": failures,
                "training_run": False,
                "creates_mlflow_run": False,
            }
            if not args.smoke:
                work_root = preflight["output_root"] / f".{preflight['snapshot_id']}.work"
                _write_json(work_root / "incomplete-report.json", report)
            print(json.dumps(report, ensure_ascii=False, sort_keys=True), file=sys.stderr)
            return 2
        if args.smoke:
            result = results[0]
            print(
                json.dumps(
                    {
                        "status": "smoke_succeeded",
                        "chunk_count": 1,
                        "candidate_count": len(result.get("facts") or []),
                        "unresolved_reference_count": len(result.get("unresolved_references") or []),
                        "model": result.get("response", {}).get("model"),
                        "usage": result.get("response", {}).get("usage"),
                        "durable_output_created": False,
                        "training_run": False,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        print(
            json.dumps(
                _publish(preflight, materialized, results, args),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except (DailyMemoryRunError, DailyMemoryError, GPTExtractionError) as exc:
        print(
            json.dumps(
                {"status": "blocked", "error_type": type(exc).__name__, "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
