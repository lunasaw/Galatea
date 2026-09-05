"""Read-only data identity checks and deterministic validation fixtures."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


class DataContractError(RuntimeError):
    """Raised when delivered data does not satisfy the project contract."""


@dataclass(frozen=True)
class DatasetExpectation:
    dataset_id: str
    source_sha256: str
    config_sha256: str
    pipeline_version: str


@dataclass(frozen=True)
class DatasetIdentity:
    dataset_id: str
    source_sha256: str
    config_sha256: str
    pipeline_version: str
    split_digest: str
    root: str


@dataclass(frozen=True)
class InferenceFixture:
    fixture_id: str
    sample_id: str
    session_id: str
    input_messages: list[dict[str, str]]
    reference_text: str
    reference_output_tokens: int


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DataContractError(f"invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DataContractError(f"JSON object required: {path}")
    return value


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def validate_dataset(root: Path, expected: DatasetExpectation) -> DatasetIdentity:
    root = root.resolve()
    source_path = root / "manifests/source_manifest.json"
    split_path = root / "manifests/split_manifest.json"
    for rel in ("manifests/source_manifest.json", "manifests/split_manifest.json", "reports/privacy_report.json", "reports/leakage_report.json", "work/05_candidates/candidates.jsonl"):
        path = root / rel
        if not path.is_file() or path.is_symlink():
            raise DataContractError(f"required ordinary file missing: {rel}")
        if root not in path.resolve().parents:
            raise DataContractError(f"symlink escape: {rel}")
    source = _read_json(source_path)
    for key, value in (("dataset_id", expected.dataset_id), ("source_sha256", expected.source_sha256), ("config_sha256", expected.config_sha256), ("pipeline_version", expected.pipeline_version)):
        if source.get(key) != value:
            raise DataContractError(f"{key} mismatch: expected {value}, got {source.get(key)}")
    split = _read_json(split_path)
    if split.get("strategy") != "chronological_session":
        raise DataContractError(f"split strategy mismatch: {split.get('strategy')}")
    split_digest = _sha256_json(split)
    return DatasetIdentity(expected.dataset_id, expected.source_sha256, expected.config_sha256, expected.pipeline_version, split_digest, str(root))


def build_validation_fixtures(root: Path, split: str = "validation", count: int = 20, prompt_policy_version: str = "redacted-context-v1") -> list[InferenceFixture]:
    root = root.resolve()
    split_manifest = _read_json(root / "manifests/split_manifest.json")
    session_ids = set(split_manifest.get("session_ids_by_split", {}).get(split, []))
    if not session_ids:
        raise DataContractError(f"no session IDs for split={split}")
    fixtures: list[InferenceFixture] = []
    candidate_path = root / "work/05_candidates/candidates.jsonl"
    with candidate_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DataContractError(f"invalid candidate JSON at line {line_number}") from exc
            if row.get("session_id") not in session_ids or row.get("metadata", {}).get("target_speaker") != "target":
                continue
            messages = row.get("messages")
            if not isinstance(messages, list) or not messages or messages[-1].get("role") != "assistant":
                continue
            if any(message.get("role") not in {"system", "user", "assistant"} for message in messages):
                raise DataContractError(f"unknown message role for sample {row.get('sample_id')}")
            if any("text_original_ref" in message for message in messages):
                raise DataContractError(f"restricted original text reference for sample {row.get('sample_id')}")
            target = str(messages[-1].get("content", ""))
            context = [dict(message) for message in messages[:-1]]
            sample_id = str(row.get("sample_id", ""))
            session_id = str(row.get("session_id", ""))
            fixture_id = hashlib.sha256(f"{row.get('dataset_id', 'wechat_aa807aaad90dc4463964')}:{sample_id}:{prompt_policy_version}".encode()).hexdigest()
            fixtures.append(InferenceFixture(fixture_id, sample_id, session_id, context, target, len(target)))
    fixtures.sort(key=lambda item: item.sample_id)
    return fixtures[:count]


def fixture_digest(fixtures: Sequence[InferenceFixture]) -> str:
    payload = [{"fixture_id": f.fixture_id, "sample_id": f.sample_id, "session_id": f.session_id, "input_messages": f.input_messages, "reference_output_tokens": f.reference_output_tokens} for f in fixtures]
    return _sha256_json(payload)
