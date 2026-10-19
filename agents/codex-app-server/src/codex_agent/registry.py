from __future__ import annotations

from dataclasses import dataclass, field
import json
import threading
import time
import uuid
from typing import Any, Protocol

from jsonschema import Draft202012Validator

from .catalog import Catalog
from .events import _sensitive
from .state import OperationJournal

def redact_result(value):
    # Redact secret fields without changing successful result types or truncating data.
    if isinstance(value, dict):
        return {k: redact_result(v) for k, v in value.items() if not _sensitive(k)}
    if isinstance(value, list):
        return [redact_result(v) for v in value]
    return value


MAX_RESPONSE_BYTES = 256 * 1024


@dataclass(frozen=True)
class ToolContext:
    session_id: str
    thread_id: str
    turn_id: str
    call_id: str
    principal_id: str
    actions: frozenset[str] = frozenset()
    catalog_digest: str = ""
    config_digest: str = ""
    deadline: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event, compare=False)
    audit: Any = field(default=None, compare=False)


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: Any = None
    error: dict[str, Any] | None = None
    request_id: str = ""

    def envelope(self):
        result = {"schema_version": "codex-agent.tools/v1", "request_id": self.request_id, "ok": self.ok}
        result["data" if self.ok else "error"] = self.data if self.ok else self.error
        return result


class ToolHandler(Protocol):
    def __call__(self, context: ToolContext, arguments: dict[str, Any]): ...


class ToolRegistry:
    def __init__(self, catalog: Catalog, journal: OperationJournal, handlers: dict[str, ToolHandler] | None = None, *, max_response_bytes=MAX_RESPONSE_BYTES):
        self.catalog, self.journal, self.handlers = catalog, journal, {}
        self.max_response_bytes, self._lock = max_response_bytes, threading.RLock()
        if handlers is not None:
            self.bind_handlers(handlers)

    def bind_handlers(self, handlers):
        if set(self.catalog.schemas) != set(handlers):
            raise ValueError("handlers must cover exactly the configured tool catalog")
        self.handlers = dict(handlers)

    def dynamic_tools(self, actions):
        return self.catalog.dynamic_tools(actions)

    def call(self, context: ToolContext, name: str, arguments: dict[str, Any]):
        request_id = "req-" + uuid.uuid4().hex
        if name not in self.catalog.schemas:
            return self._error(request_id, "unknown-tool", "review-catalog")
        if name not in context.actions and "*" not in context.actions:
            return self._error(request_id, "forbidden", "review-authorization")
        if not isinstance(arguments, dict):
            return self._error(request_id, "invalid-arguments", "fix-input")
        try:
            Draft202012Validator(self.catalog.schemas[name]).validate(arguments)
        except Exception:
            return self._error(request_id, "invalid-arguments", "fix-input")
        if context.cancel.is_set() or (context.deadline is not None and time.monotonic() >= context.deadline):
            return self._error(request_id, "cancelled", "observe")
        identity = {"call_id": context.call_id, "session_id": context.session_id, "thread_id": context.thread_id, "turn_id": context.turn_id,
                    "tool": name, "arguments": arguments, "read_only": self.catalog.metadata["tools"][name]["read_only"], "catalog_digest": context.catalog_digest,
                    "principal_id": context.principal_id, "config_digest": context.config_digest}
        with self._lock:
            try:
                existing = self.journal.get(context.call_id)
                record = self.journal.begin(context.call_id, identity)
            except ValueError:
                return self._error(request_id, "conflict", "review-request-id")
            if record.get("state") == "receipt_saved":
                return record["receipt"]
            if existing is not None:
                return self._error(request_id, "operation-unknown", "reconcile", state_changed="unknown")
            if not self.catalog.metadata['tools'][name]['read_only']:
                unresolved = [r for r in self.journal.records() if r['call_id'] != context.call_id
                              and r['state'] != 'receipt_saved'
                              and not r['identity'].get('read_only', False)]
                if unresolved:
                    envelope = self._error('req-' + uuid.uuid4().hex, 'operation-unknown', 'observe-existing')
                    self.journal.transition(context.call_id, 'receipt_saved', receipt=envelope)
                    return envelope
            if context.cancel.is_set() or (context.deadline is not None and time.monotonic() >= context.deadline):
                envelope = self._error('req-' + uuid.uuid4().hex, 'cancelled', 'observe')
                self.journal.transition(context.call_id, 'receipt_saved', receipt=envelope)
                return envelope
            self.journal.transition(context.call_id, "executing", request_id=request_id)
            try:
                value = self.handlers[name](context, arguments)
                envelope = value.envelope() if isinstance(value, ToolResult) else ToolResult(True, data=value, request_id=self.journal.get(context.call_id)["request_id"]).envelope()
                envelope = redact_result(envelope)
                if len(json.dumps(envelope, ensure_ascii=False, allow_nan=False).encode()) > self.max_response_bytes:
                    envelope = self._error(request_id, "response-too-large", "reduce-page-size-and-reconcile", state_changed="unknown")
                state = "unknown" if envelope.get("error", {}).get("state_changed") == "unknown" else "receipt_saved"
                self.journal.transition(context.call_id, state, receipt=envelope)
                return envelope
            except Exception:
                self.journal.transition(context.call_id, "unknown")
                return self._error(request_id, "backend-or-state-unavailable", "reconcile", state_changed="unknown")

    def reconcile_unknown(self, observe):
        with self._lock:
            for record in self.journal.records():
                if record['state'] == 'receipt_saved':
                    continue
                try:
                    data = observe(record['identity'])
                except Exception:
                    continue
                if data is None:
                    continue
                receipt = ToolResult(True, data=redact_result(data),
                    request_id=record.get('request_id') or 'req-' + uuid.uuid4().hex).envelope()
                if len(json.dumps(receipt, ensure_ascii=False, allow_nan=False).encode()) <= self.max_response_bytes:
                    self.journal.transition(record['call_id'], 'receipt_saved', receipt=receipt, recovered_by='operation-observation')

    @staticmethod
    def _error(request_id, category, next_action, *, state_changed=False):
        return ToolResult(False, error={"category": category, "retryable": False, "state_changed": state_changed,
            "operation_id": None, "next_action": next_action}, request_id=request_id).envelope()
