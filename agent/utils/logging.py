"""Structured logging for Galatea agents."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def setup_logging(level: int = logging.INFO, format_json: bool = False) -> None:
    """Set up root logging configuration."""
    if format_json:
        formatter = _JsonFormatter()
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        logging.basicConfig(level=level, handlers=[handler], force=True)
    else:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
            force=True,
        )


class StructuredLogger:
    """Structured logger with consistent event payloads."""

    def __init__(self, name: str, context: Optional[Dict[str, Any]] = None) -> None:
        self.logger = logging.getLogger(name)
        self.context = context or {}

    def log_event(
        self,
        event: str,
        level: int = logging.INFO,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload = self._payload(event, metadata)
        self.logger.log(level, json.dumps(payload, ensure_ascii=False, default=str))

    def log_tool_use(self, tool_name: str, tool_input: Dict[str, Any], session_id: str) -> None:
        self.log_event(
            "tool_use",
            metadata={"tool_name": tool_name, "tool_input": tool_input, "session_id": session_id},
        )

    def log_tool_result(
        self,
        tool_name: str,
        success: bool,
        duration_ms: float,
        session_id: str,
        error: Optional[str] = None,
    ) -> None:
        self.log_event(
            "tool_result",
            level=logging.INFO if success else logging.ERROR,
            metadata={
                "tool_name": tool_name,
                "success": success,
                "duration_ms": duration_ms,
                "session_id": session_id,
                "error": error,
            },
        )

    def log_stage_transition(self, from_stage: str, to_stage: str, workflow_id: str) -> None:
        self.log_event(
            "stage_transition",
            metadata={"from_stage": from_stage, "to_stage": to_stage, "workflow_id": workflow_id},
        )

    def log_api_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        session_id: str,
    ) -> None:
        self.log_event(
            "api_call",
            metadata={
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost_usd,
                "session_id": session_id,
            },
        )

    def _payload(self, event: str, metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **self.context,
            **(metadata or {}),
        }


class AuditLogger:
    """Audit logger for compliance and approval trails."""

    def __init__(self, audit_file: Optional[str] = None) -> None:
        self.audit_file = Path(audit_file) if audit_file else None
        self.logger = logging.getLogger("galatea.audit")

    def log_action(
        self,
        action: str,
        actor: str,
        resource: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload = {
            "event": "audit_action",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "actor": actor,
            "resource": resource,
            "metadata": metadata or {},
        }
        self._write(payload)

    def log_approval(
        self,
        approval_id: str,
        action: str,
        approved: bool,
        approver: str,
    ) -> None:
        self.log_action(
            action="approval_decision",
            actor=approver,
            resource=approval_id,
            metadata={"requested_action": action, "approved": approved},
        )

    def _write(self, payload: Dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, default=str)
        self.logger.info(line)
        if self.audit_file:
            self.audit_file.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_file.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def get_logger(name: str, context: Optional[Dict[str, Any]] = None) -> StructuredLogger:
    """Get a structured logger."""
    return StructuredLogger(name, context)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            },
            ensure_ascii=False,
        )
