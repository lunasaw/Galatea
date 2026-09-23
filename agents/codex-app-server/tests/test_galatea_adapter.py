from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from codex_agent.galatea_adapter import GalateaAdapter
from codex_agent.registry import ToolContext


class Service:
    def __init__(self, result=None, error=None): self.result, self.error = result, error
    def call(self, principal, name, arguments):
        if self.error: raise self.error
        return self.result
    def reconcile_all(self): pass


class GalateaAdapterTests(unittest.TestCase):
    def test_domain_error_is_safe_envelope_and_identity_is_not_model_controlled(self):
        from galatea_mcp.errors import DomainError
        adapter = GalateaAdapter(Service(error=DomainError("forbidden")), principal=object())
        result = adapter.handler("galatea_get_capabilities")(
            ToolContext("s", "t", "turn", "call", "p", actions=frozenset({"*"})),
            {"protocol_version": "galatea.tools/v1"})
        self.assertFalse(result.ok)
        self.assertEqual(result.error["category"], "forbidden")

    def test_unknown_exception_is_unknown_not_retry_success(self):
        adapter = GalateaAdapter(Service(error=RuntimeError("secret backend details")), principal=object())
        result = adapter.handler("galatea_get_capabilities")(
            ToolContext("s", "t", "turn", "call", "p", actions=frozenset({"*"})), {})
        self.assertFalse(result.ok)
        self.assertEqual(result.error["state_changed"], "unknown")
        self.assertNotIn("secret", str(result.error))


if __name__ == "__main__": unittest.main()
