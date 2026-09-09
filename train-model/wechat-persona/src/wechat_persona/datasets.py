"""Formal SFT export gates; output contains redacted text only."""
from __future__ import annotations

from collections import defaultdict
import ctypes
import errno
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any, Iterable

from ._common import digest, file_digest
from .redact import scan_candidate
from .review import _content_hash


class DatasetError(ValueError): pass


DATASET_SCHEMA_VERSION = "wechat-sft-v2"
PRIVACY_BOUNDARY_VERSION = "wechat-privacy-boundary-v1"


def _publish_directory_noreplace(staging: Path, output: Path) -> None:
    """Publish ``staging`` atomically without replacing any existing path.

    Linux ``renameat2(RENAME_NOREPLACE)`` closes the check-then-rename race
    that plain ``os.rename`` leaves for an empty destination directory.
    Galatea's supported deployment platform is Linux; fail closed when the
    kernel/libc cannot provide this no-overwrite primitive.
    """

    renameat2 = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "atomic no-replace directory publish unavailable")
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    at_fdcwd = -100
    rename_noreplace = 1
    result = renameat2(
        at_fdcwd,
        os.fsencode(staging),
        at_fdcwd,
        os.fsencode(output),
        rename_noreplace,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(
                error_number,
                "formal dataset snapshot already exists",
                output,
            )
        raise OSError(error_number, os.strerror(error_number), output)


def _turn_message_contents(turn: dict[str, Any], content: str) -> list[str]:
    """Return the source-message contents represented by one merged turn."""

    values = turn.get("message_contents")
    if isinstance(values, list) and len(values) == len(turn.get("message_ids") or []):
        return [str(value or "") for value in values]
    return [content]


def _candidate_privacy_messages(
    context: list[dict[str, Any]],
    reply_contents: list[str],
    reply_indices: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Keep source boundaries for scanning without parsing serialized prompts."""

    messages: list[dict[str, Any]] = []
    fallback_index = 0
    for turn in context:
        indices = turn.get("source_record_indices") or []
        for position, value in enumerate(turn["message_contents"]):
            item: dict[str, Any] = {"content": value}
            if position < len(indices):
                item["source_record_index"] = indices[position]
            else:
                item["source_record_index"] = fallback_index
            messages.append(item)
            fallback_index = max(fallback_index + 1, int(item["source_record_index"]) + 1)
    for position, value in enumerate(reply_contents):
        item = {"content": value}
        if reply_indices and position < len(reply_indices):
            item["source_record_index"] = reply_indices[position]
        else:
            item["source_record_index"] = fallback_index
        messages.append(item)
        fallback_index = max(fallback_index + 1, int(item["source_record_index"]) + 1)

    return messages


def build_review_candidates(
    sessions: Iterable[dict[str, Any]],
    *,
    split_by_session: dict[str, str],
    target_role: str = "target",
    max_context_turns: int = 8,
    system_prompt: str = "仅基于已提供的脱敏对话生成回复；不猜测或复述私人事实。",
) -> list[dict[str, Any]]:
    """Build causal review candidates using only turns preceding a reply."""
    candidates: list[dict[str, Any]] = []
    for session in sessions:
        candidate_fallback_index = 0
        sid = str(session.get("session_id", ""))
        split = split_by_session.get(sid)
        if split not in {"train", "validation", "test"}:
            raise DatasetError("split missing")
        prior: list[dict[str, Any]] = []
        for index, turn in enumerate(session.get("turns", [])):
            role = str(turn.get("speaker_role", "unknown"))
            content = str(turn.get("text_redacted") or "").strip()
            message_contents = _turn_message_contents(turn, content)
            source_record_indices = list(turn.get("source_record_indices") or [])
            if len(source_record_indices) != len(message_contents):
                source_record_indices = list(
                    range(
                        candidate_fallback_index,
                        candidate_fallback_index + len(message_contents),
                    )
                )
            if source_record_indices:
                candidate_fallback_index = max(
                    candidate_fallback_index,
                    max(int(value) for value in source_record_indices) + 1,
                )
            if role == target_role and content and prior:
                context = prior[-max_context_turns:]
                context_text = "\n".join(
                    f"{item['speaker_role']}: {item['text_redacted']}" for item in context
                )
                candidate = {
                    "sample_id": f"{sid}_{index}",
                    "session_id": sid,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": context_text},
                        {"role": "assistant", "content": content},
                    ],
                    "metadata": {
                        "review_status": "uncertain",
                        "content_sha256": "",
                        "split": split,
                        "source_message_ids": list(turn.get("message_ids") or []),
                        "context_message_ids": [mid for item in context for mid in item.get("message_ids", [])],
                    },
                    # Candidate display/training messages intentionally retain
                    # the existing schema. This versioned privacy view keeps
                    # atomic source-message boundaries so later review and
                    # snapshot gates need not parse the serialized prompt.
                    "privacy_scan_messages": _candidate_privacy_messages(
                        context,
                        message_contents,
                        source_record_indices,
                    ),
                    "privacy_boundary_version": PRIVACY_BOUNDARY_VERSION,
                }
                candidate["metadata"]["content_sha256"] = _content_hash(candidate)
                candidates.append(candidate)
            if role in {"self", "target"} and content:
                prior.append({
                    "speaker_role": role,
                    "text_redacted": content,
                    "message_ids": list(turn.get("message_ids") or []),
                    "message_contents": message_contents,
                    "source_record_indices": list(
                        source_record_indices
                    ),
                })
    return candidates


def build_sft_splits(
    rows: Iterable[dict[str, Any]],
    *,
    require_nonempty: bool = False,
    require_human_evidence: bool = False,
) -> dict[str, Any]:
    splits: dict[str, list[dict[str, Any]]] = defaultdict(list); sessions: dict[str, str] = {}; duplicate_groups: dict[str, str] = {}
    seen_sample_ids: set[str] = set()
    for row in rows:
        sample_id = str(row.get("sample_id") or "")
        if not sample_id:
            raise DatasetError("sample_id required")
        if sample_id in seen_sample_ids:
            raise DatasetError("duplicate_sample_id")
        seen_sample_ids.add(sample_id)
        meta = dict(row.get("metadata") or {}); status = meta.get("review_status")
        if status not in {"keep", "redact_keep"}:
            continue
        if require_human_evidence and (not meta.get("reviewer_id") or not meta.get("reviewed_at")):
            raise DatasetError("manual_review_pending")
        split = meta.get("split")
        if split not in {"train", "validation", "test"}: raise DatasetError("split missing")
        if meta.get("speaker_role") == "unknown" or any(item.get("role") == "unknown" for item in row.get("messages", [])): raise DatasetError("unknown_role")
        messages = row.get("messages") or []
        if (
            len(messages) < 2
            or messages[-1].get("role") != "assistant"
            or not messages[-1].get("content", "").strip()
        ):
            raise DatasetError("assistant_reply_required")
        current_content_hash = _content_hash(row)
        if status == "keep":
            if meta.get("content_sha256") != current_content_hash:
                raise DatasetError("content_hash_mismatch")
            if meta.get("redacted_content_sha256"):
                raise DatasetError("unexpected_redacted_content_hash")
        else:
            # ``content_sha256`` remains the immutable original-candidate
            # identity. The reviewed edit is independently bound here.
            if not meta.get("content_sha256"):
                raise DatasetError("content_hash_missing")
            if not meta.get("redacted_content_sha256"):
                raise DatasetError("redacted_content_hash_missing")
            if meta.get("redacted_content_sha256") != current_content_hash:
                raise DatasetError("redacted_content_hash_mismatch")
        if scan_candidate(row)["hard_leak_count"]:
            raise DatasetError("pii_scan_failed")
        session_id = str(row.get("session_id", ""))
        prior = sessions.get(session_id)
        if prior and prior != split: raise DatasetError("cross_split_session")
        sessions[session_id] = split
        group = str(meta.get("near_duplicate_group") or "")
        if group:
            prior_group_split = duplicate_groups.get(group)
            if prior_group_split and prior_group_split != split:
                raise DatasetError("duplicate_group_cross_split")
            duplicate_groups[group] = split
        splits[split].append({**row, "metadata": {**meta, "assistant_only_loss": True}})
    if require_nonempty and any(not splits[name] for name in ("train", "validation", "test")): raise DatasetError("empty_split")
    result = {name: list(splits[name]) for name in ("train", "validation", "test")}
    result["manifest"] = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "assistant_only_loss": True,
        "sample_counts": {
            name: len(result[name]) for name in ("train", "validation", "test")
        },
        "split_sha256": digest(
            {name: [row.get("sample_id") for row in result[name]]
             for name in ("train", "validation", "test")}
        ),
        "sample_ids_sha256": digest(
            {name: [row.get("sample_id") for row in result[name]]
             for name in ("train", "validation", "test")}
        ),
    }
    return result


def write_sft_snapshot(
    output: Path,
    rows: Iterable[dict[str, Any]],
    *,
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically create a non-overwriting formal snapshot after all gates."""
    data = build_sft_splits(rows, require_nonempty=True, require_human_evidence=True)
    manifest = {**data["manifest"], **dict(identity or {})}
    manifest["privacy_counts"] = {
        "reviewed": {"hard_leak_count": 0},
        "formal_snapshot": {"hard_leak_count": 0},
    }
    manifest["formal_training_eligible"] = False
    manifest["requires_formal_dataset_ready_approval"] = True
    if output.exists():
        raise FileExistsError("formal dataset snapshot already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
    staging.chmod(stat.S_IRWXU)
    try:
        for split in ("train", "validation", "test"):
            with (staging / f"{split}.jsonl").open("x", encoding="utf-8") as handle:
                for row in data[split]:
                    handle.write(
                        json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                        + "\n"
                    )
            (staging / f"{split}.jsonl").chmod(stat.S_IRUSR | stat.S_IWUSR)
        split_file_sha256 = {
            split: file_digest(staging / f"{split}.jsonl")
            for split in ("train", "validation", "test")
        }
        manifest["split_file_sha256"] = split_file_sha256
        manifest["snapshot_content_sha256"] = digest(split_file_sha256)
        manifest["snapshot_manifest_sha256"] = digest(manifest)
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (staging / "manifest.json").chmod(stat.S_IRUSR | stat.S_IWUSR)
        _publish_directory_noreplace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest
