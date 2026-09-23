"""Exercise the production reader, dispatcher, Host and journal with a real binary."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
from runtime_probe import MockModel, mock_config
from codex_agent.catalog import Catalog, digest
from codex_agent.compatibility import RuntimeContract
from codex_agent.config import AgentConfig
from codex_agent.events import EventLog
from codex_agent.host import AgentHost
from codex_agent.process import ManagedProcess
from codex_agent.registry import ToolRegistry
from codex_agent.state import AtomicJsonStore, OperationJournal


@unittest.skipUnless(os.environ.get('GALATEA_TEST_RUNTIME_PACKAGE'), 'real runtime opt-in required')
class RealHostTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_tool_completion_and_cross_process_resume(self):
        with tempfile.TemporaryDirectory() as directory, MockModel() as model:
            root = Path(directory)
            package = Path(os.environ['GALATEA_TEST_RUNTIME_PACKAGE'])
            (root / 'codex-home').mkdir()
            (root / 'codex-home/config.toml').write_text(mock_config(model.server.server_port))
            config = AgentConfig.from_dict({'schema_version': 'codex-agent/v1', 'paths': {
                'codex_home': 'codex-home', 'runtime_dir': str(package), 'state_dir': 'state', 'event_dir': 'events',
                'workspace_dir': 'workspace', 'contracts': str(ROOT / 'config/contracts/tools.json'),
                'catalog_metadata': str(ROOT / 'config/contracts/catalog-metadata.json')},
                'codex': {'binary': str(package / 'bin/codex'), 'runtime_version': 'codex-cli 0.153.4'},
                'galatea': {'principal': {'principal_id': 'p', 'actions': ['galatea_get_capabilities']}}}, root)
            catalog = Catalog.load(config.contracts.parent)
            compatibility = RuntimeContract.load(config.contracts.parent / 'runtime-compatibility.json', catalog)
            store, events, calls = AtomicJsonStore(config.state_dir), EventLog(config.event_dir), []
            def handler(context, args):
                calls.append(context.call_id)
                return {'protocol_version': 'galatea.tools/v1'}
            registry = ToolRegistry(catalog, OperationJournal(store), {name: handler for name in catalog.schemas})
            def build():
                process = ManagedProcess(config)
                async def start():
                    return await process.start(host.handle_tool_call, notification=host.handle_notification, on_failure=host.runtime_failed)
                host = AgentHost(registry=registry, event_log=events, store=store, client_factory=start,
                    client_stop=process.stop, reconciler=lambda: None, principal_id='p', project_ids=set(), campaign_ids=set(),
                    actions={'galatea_get_capabilities'}, workspace_dir=config.workspace_dir)
                return host
            async def completed(host):
                async with asyncio.timeout(30):
                    while host.get_session('real')['status'] != 'ready':
                        if not host.ready:
                            self.fail('Host lost runtime readiness')
                        await asyncio.sleep(.02)
            host = build()
            try:
                await host.start()
                config_digest = digest(await host.client.read_config(str(config.workspace_dir)))
                await host.create_session('real')
                await host.send_message('real', 'request-1', 'Synthetic read-only protocol fixture')
                await completed(host)
                self.assertEqual(len(calls), 1)
                self.assertEqual(store.read('requests/request-1.json')['status'], 'completed')
                self.assertEqual(len(model.requests), 2)
                self.assertIn('galatea.tools/v1', str(model.requests[1]['input']))
            finally:
                await host.stop()
            host = build()
            try:
                await host.start()
                self.assertEqual(digest(await host.client.read_config(str(config.workspace_dir))), config_digest)
                await host.resume('real')
                await host.send_message('real', 'request-2', 'Continue synthetic fixture')
                await completed(host)
                self.assertEqual(len(model.requests), 3)
                self.assertEqual(len(calls), 1)
                for request in model.requests:
                    compatibility.assert_request(request, {'galatea_get_capabilities'})
            finally:
                await host.stop()
