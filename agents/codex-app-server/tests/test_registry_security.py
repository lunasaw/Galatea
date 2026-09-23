from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from codex_agent.catalog import Catalog
from codex_agent.registry import ToolContext, ToolRegistry
from codex_agent.state import AtomicJsonStore, OperationJournal


class RegistrySecurityTests(unittest.TestCase):
    def test_secret_fields_are_removed_before_model_receives_result(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Catalog.load(ROOT / "config/contracts")
            registry = ToolRegistry(catalog, OperationJournal(AtomicJsonStore(Path(directory))), {
                name: lambda *_: {"token": "hidden", "nested": {"api_key": "hidden", "safe": "ok"}}
                for name in catalog.schemas})
            result = registry.call(ToolContext("s", "t", "turn", "call", "p", actions=frozenset({"*"})),
                                   "galatea_get_capabilities", {})
            self.assertNotIn("token", result["data"])
            self.assertNotIn("api_key", result["data"]["nested"])
            self.assertEqual(result["data"]["nested"]["safe"], "ok")


if __name__ == "__main__": unittest.main()
