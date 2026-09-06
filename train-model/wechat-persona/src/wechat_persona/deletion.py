"""Lineage-driven deletion planning and execution with an ID-only ledger."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


class DeletionError(ValueError):
    pass


def plan_deletion(lineage: Iterable[dict[str, Any]], *, source_message_ids: set[str] | None = None, session_ids: set[str] | None = None, consent_scope: str | None = None) -> dict[str, Any]:
    source_message_ids = source_message_ids or set(); session_ids = session_ids or set()
    objects: list[dict[str, Any]] = []
    candidates = [dict(item) for item in lineage]
    selected_paths: set[str] = set()
    for item in candidates:
        if source_message_ids.intersection(set(item.get("source_message_ids", []))) or session_ids.intersection(set(item.get("source_session_ids", []))) or (consent_scope and item.get("consent_scope") == consent_scope):
            if item.get("path"):
                selected_paths.add(str(item["path"]))
    for item in candidates:
        if str(item.get("path", "")) in selected_paths or source_message_ids.intersection(set(item.get("source_message_ids", []))) or session_ids.intersection(set(item.get("source_session_ids", []))) or (consent_scope and item.get("consent_scope") == consent_scope):
            objects.append({key: item.get(key) for key in ("object_id", "path", "artifact_uri", "source_message_ids", "source_session_ids") if item.get(key) is not None})
    unique = {str(item.get("object_id")): item for item in objects if item.get("object_id") is not None}
    return {"schema_version": "wechat-deletion-plan-v1", "requested_at": datetime.now(timezone.utc).isoformat(), "objects": list(unique.values()), "scope": {"source_message_ids": sorted(source_message_ids), "session_ids": sorted(session_ids), "consent_scope": consent_scope}}


def execute_deletion(plan: dict[str, Any], *, ledger_path: Path, allowed_root: Path | None = None, invalidate_artifact: Callable[[str], bool] | None = None) -> dict[str, Any]:
    deleted: list[str] = []; failures: list[str] = []
    if any(item.get("artifact_uri") for item in plan.get("objects", [])) and invalidate_artifact is None:
        raise DeletionError("artifact invalidation callback is required")
    for item in plan.get("objects", []):
        path = item.get("path")
        if path:
            try:
                target = Path(path)
                root = (allowed_root or ledger_path.parent).resolve(strict=False)
                resolved = target.resolve(strict=False)
                try:
                    resolved.relative_to(root)
                except ValueError:
                    failures.append(str(item.get("object_id")))
                    continue
                if target.is_symlink():
                    failures.append(str(item.get("object_id")))
                    continue
                if target.exists() and target.is_file(): target.unlink()
            except OSError: failures.append(str(item.get("object_id"))) ; continue
        if item.get("artifact_uri") and not invalidate_artifact(str(item["artifact_uri"])):
            failures.append(str(item.get("object_id")))
            continue
        deleted.append(str(item.get("object_id")))
    receipt = {"schema_version": "wechat-deletion-receipt-v1", "status": "completed" if not failures else "partial", "deleted_object_ids": deleted, "failed_object_ids": failures, "completed_at": datetime.now(timezone.utc).isoformat()}
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {"schema_version": "wechat-deletion-ledger-v1", "entries": []}
    if ledger_path.exists():
        try: existing = json.loads(ledger_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError: pass
    existing.setdefault("entries", []).append(receipt)
    ledger_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt
