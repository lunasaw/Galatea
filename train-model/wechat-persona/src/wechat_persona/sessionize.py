"""Session construction and deterministic, session-level chronological splits."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable

from ._common import digest, parse_datetime


SESSION_VERSION = "wechat-session-v1"


def _merge_turns(rows: list[dict[str, Any]], merge_gap_seconds: int) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for row in rows:
        role = str(row.get("speaker_role", "unknown")); ts = parse_datetime(row.get("timestamp"))
        if turns:
            prior = turns[-1]; previous_ts = prior.get("_last_ts")
            gap = (ts - previous_ts).total_seconds() if ts and previous_ts else None
            if prior["speaker_role"] == role and gap is not None and 0 <= gap <= merge_gap_seconds and row.get("message_kind") not in {"system", "payment", "call"}:
                if row.get("text_redacted"): prior["text_redacted"] = (prior.get("text_redacted") or "") + "\n" + str(row["text_redacted"])
                prior["message_ids"].append(str(row["message_id"])); prior["_last_ts"] = ts; continue
        turns.append({"speaker_role": role, "message_kind": row.get("message_kind", "text"), "text_redacted": row.get("text_redacted"), "message_ids": [str(row["message_id"])], "timestamps": [row.get("timestamp")], "_last_ts": ts})
    for turn in turns: turn.pop("_last_ts", None)
    return turns


def sessionize(rows: Iterable[dict[str, Any]], *, inactivity_gap_minutes: int = 120, max_duration_minutes: int = 720, merge_gap_seconds: int = 120) -> list[dict[str, Any]]:
    ordered = sorted((dict(row) for row in rows), key=lambda row: (row.get("timestamp") or "", int(row.get("source_record_index", 0))))
    sessions: list[dict[str, Any]] = []; current: list[dict[str, Any]] = []; start: datetime | None = None; previous: datetime | None = None
    gap_limit = inactivity_gap_minutes * 60; duration_limit = max_duration_minutes * 60

    def finish() -> None:
        nonlocal current
        if not current: return
        sid = "session_" + digest([row["message_id"] for row in current])[:16]
        timestamps = [row.get("timestamp") for row in current]
        sessions.append({"session_id": sid, "start_time": timestamps[0], "end_time": timestamps[-1], "message_ids": [str(row["message_id"]) for row in current], "participants": sorted({str(row.get("speaker_role", "unknown")) for row in current}), "turns": _merge_turns(current, merge_gap_seconds), "session_rule_version": SESSION_VERSION})
        current = []

    for row in ordered:
        ts = parse_datetime(row.get("timestamp"))
        boundary = bool(current and ts and previous and ((ts - previous).total_seconds() > gap_limit or (start and (ts - start).total_seconds() > duration_limit)))
        if boundary: finish(); start = None
        if not current: start = ts
        current.append(row); previous = ts or previous
    finish()
    return sessions


def deterministic_split(sessions: Iterable[dict[str, Any]], *, ratios: tuple[float, float, float] = (0.8, 0.1, 0.1)) -> dict[str, Any]:
    if len(ratios) != 3 or any(value < 0 for value in ratios) or abs(sum(ratios) - 1.0) > 1e-6: raise ValueError("split ratios must sum to 1")
    ordered = sorted((dict(session) for session in sessions), key=lambda session: (session.get("start_time") or "", session["session_id"]))
    n = len(ordered); train_end = int(n * ratios[0]); val_end = train_end + int(n * ratios[1])
    mapping = {"train": [session["session_id"] for session in ordered[:train_end]], "validation": [session["session_id"] for session in ordered[train_end:val_end]], "test": [session["session_id"] for session in ordered[val_end:]]}
    return {"strategy": "chronological_session", "session_ids_by_split": mapping, "session_counts": {key: len(value) for key, value in mapping.items()}, "split_sha256": digest(mapping)}
