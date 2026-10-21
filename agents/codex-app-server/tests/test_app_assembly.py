import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from codex_agent.app import build_host
from codex_agent.config import AgentConfig
from codex_agent.stage0 import GateError


class AppAssemblyTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_assembly_keeps_principal_server_side(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = AgentConfig.from_dict({'schema_version': 'codex-agent/v2', 'paths': {'codex_home': 'home', 'runtime_dir': 'runtime', 'state_dir': 'state', 'workspace_dir': 'workspace'}, 'codex': {'binary': str(Path('/bin/true').resolve()), 'runtime_version': 'test'}, 'agent': {'id': 'p'}, 'tools': {'catalog_dir': str(ROOT / 'tests/fixtures/dynamic-tools'), 'actions': ['galatea_get_capabilities'], 'providers': [{'path': str(ROOT / 'tests/fixtures/dynamic-tools/provider.py')}]}}, root)
            host, console = build_host(config)
            self.assertEqual(host.actions, frozenset({"galatea_get_capabilities"}))
            self.assertIsNotNone(console)
            self.assertIsNone(host.extension_rebinder)
            development_host, _ = build_host(config, development=True)
            with patch('codex_agent.local.source_manifest', side_effect=GateError('files changed')):
                with self.assertRaises(ValueError):
                    await development_host.extension_rebinder({}, {})


if __name__ == "__main__": unittest.main()
