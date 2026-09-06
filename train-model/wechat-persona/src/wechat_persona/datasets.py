"""Formal SFT export gates; output contains redacted text only."""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
from typing import Any, Iterable

from ._common import digest
from .redact import scan_redacted_text


class DatasetError(ValueError): pass


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
        sid = str(session.get("session_id", ""))
        split = split_by_session.get(sid)
        if split not in {"train", "validation", "test"}:
            raise DatasetError("split missing")
        prior: list[dict[str, Any]] = []
        for index, turn in enumerate(session.get("turns", [])):
            role = str(turn.get("speaker_role", "unknown"))
            content = str(turn.get("text_redacted") or "").strip()
            if role == target_role and content and prior:
                context = prior[-max_context_turns:]
                context_text = "\n".join(
                    f"{item['speaker_role']}: {item['text_redacted']}" for item in context
                )
                assistant_hash = digest(content)
                candidates.append({
                    "sample_id": f"{sid}_{index}",
                    "session_id": sid,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": context_text},
                        {"role": "assistant", "content": content},
                    ],
                    "metadata": {
                        "review_status": "uncertain",
                        "content_sha256": assistant_hash,
                        "split": split,
                        "source_message_ids": list(turn.get("message_ids") or []),
                        "context_message_ids": [mid for item in context for mid in item.get("message_ids", [])],
                    },
                })
            if role in {"self", "target"} and content:
                prior.append({"speaker_role": role, "text_redacted": content, "message_ids": list(turn.get("message_ids") or [])})
    return candidates


def build_sft_splits(rows: Iterable[dict[str, Any]], *, require_nonempty: bool = False, require_human_evidence: bool = False) -> dict[str, Any]:
    splits: dict[str, list[dict[str, Any]]] = defaultdict(list); sessions: dict[str, str] = {}; duplicate_groups: dict[str, str] = {}
    for row in rows:
        meta = dict(row.get("metadata") or {}); status = meta.get("review_status")
        if status not in {"keep", "redact_keep"}: continue
        if require_human_evidence and (not meta.get("reviewer_id") or not meta.get("reviewed_at")):
            raise DatasetError("manual_review_pending")
        split = meta.get("split")
        if split not in {"train", "validation", "test"}: raise DatasetError("split missing")
        if meta.get("speaker_role") == "unknown" or any(item.get("role") == "unknown" for item in row.get("messages", [])): raise DatasetError("unknown_role")
        messages = row.get("messages") or []
        if len(messages) < 2 or messages[-1].get("role") != "assistant" or not messages[-1].get("content", "").strip(): raise DatasetError("assistant_reply_required")
        for item in messages:
            if scan_redacted_text(item.get("content", ""))["hard_leak_count"]: raise DatasetError("pii_scan_failed")
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
    result["manifest"] = {"schema_version": "wechat-sft-v1", "assistant_only_loss": True, "sample_counts": {name: len(result[name]) for name in ("train", "validation", "test")}, "split_sha256": digest({name: [row.get("sample_id") for row in result[name]] for name in ("train", "validation", "test")})}
    return result


def write_sft_snapshot(output: Path, rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Atomically create a non-overwriting formal snapshot after all gates."""
    data = build_sft_splits(rows, require_nonempty=True, require_human_evidence=True)
    if output.exists():
        raise FileExistsError("formal dataset snapshot already exists")
    output.mkdir(parents=True, exist_ok=False)
    for split in ("train", "validation", "test"):
        with (output / f"{split}.jsonl").open("x", encoding="utf-8") as handle:
            for row in data[split]:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    (output / "manifest.json").write_text(json.dumps(data["manifest"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data["manifest"]
