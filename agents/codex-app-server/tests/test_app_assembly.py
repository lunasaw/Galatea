import asyncio
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from codex_agent.app import build_host
from codex_agent.config import AgentConfig
from galatea_mcp.auth import Principal


class Service:
    def call(self, principal, name, args): return {"tool": name}
    def reconcile_all(self): return None


class AppAssemblyTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_assembly_keeps_principal_server_side(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = AgentConfig.from_dict({"schema_version": "codex-agent/v1", "paths": {
                "codex_home": "home", "runtime_dir": "runtime", "state_dir": "state", "event_dir": "events",
                "workspace_dir": "workspace", "contracts": str(ROOT / "config/contracts/tools.json"),
                "catalog_metadata": str(ROOT / "config/contracts/catalog-metadata.json")},
                "codex": {"binary": str(Path("/bin/true").resolve()), "runtime_version": "test"},
                "galatea": {"principal": {"principal_id": "p", "project_ids": ["project"],
                                              "campaign_ids": ["campaign"], "actions": ["galatea_get_capabilities"]}}}, root)
            principal = Principal("p", frozenset({"project"}), frozenset({"campaign"}), frozenset({"galatea_get_capabilities"}))
            host, console = build_host(config, Service(), principal,
                                       console_token="test-token-1234567890", allowed_origins={"http://localhost"})
            self.assertEqual(host.actions, frozenset({"galatea_get_capabilities"}))
            self.assertIsNotNone(console)


if __name__ == "__main__": unittest.main()
