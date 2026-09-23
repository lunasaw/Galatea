"""Bridge the existing Galatea application service without importing MCP transport."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

SERVICE_ROOT = Path(__file__).resolve().parents[4] / "services" / "galatea-mcp" / "src"
# Editable repository development only. Installed deployments resolve their
# declared dependency normally, never override it with a nearby checkout.
if importlib.util.find_spec('galatea_mcp') is None and SERVICE_ROOT.is_dir():
    sys.path.insert(0, str(SERVICE_ROOT))

from galatea_mcp.auth import Principal
from galatea_mcp.envelope import call_envelope

from .registry import ToolContext, ToolResult


class GalateaAdapter:
    def __init__(self, service, *, principal: Principal):
        self.service, self.principal = service, principal
        self._reconcile_lock = __import__("threading").RLock()
        self.last_reconcile = None

    def handler(self, name: str):
        def invoke(context: ToolContext, arguments: dict):
            # Principal is immutable host configuration; model/Console arguments
            # never provide identity, scope, credentials, or budget.
            with self._reconcile_lock:
                envelope = call_envelope(self.service, self.principal, name, arguments)
            return ToolResult(envelope["ok"], data=envelope.get("data"), error=envelope.get("error"),
                              request_id=envelope["request_id"])
        return invoke

    def observe_receipt(self, identity):
        with self._reconcile_lock:
            return self.service.observe_receipt(self.principal, identity['tool'], identity['arguments'])

    def handlers(self, names):
        return {name: self.handler(name) for name in names}

    def reconcile_all(self):
        with self._reconcile_lock:
            self.service.reconcile_all()
            import time
            self.last_reconcile = time.time()

    @property
    def reconcile_lock(self):
        return self._reconcile_lock
