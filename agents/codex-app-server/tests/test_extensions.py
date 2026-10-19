"""Exercise extension configuration, isolation, receipt recovery and cleanup."""
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from codex_agent.app import build_host
from codex_agent.catalog import Catalog
from codex_agent.config import AgentConfig
from codex_agent.events import EventLog
from codex_agent.host import AgentHost
from codex_agent.providers import ProviderSet, ToolProvider
from codex_agent.registry import ToolContext, ToolRegistry
from codex_agent.state import AtomicJsonStore, OperationJournal, single_writer_lock


def configuration():
    return {'schema_version': 'codex-agent/v2',
            'paths': {'codex_home': 'home', 'runtime_dir': 'runtime',
                      'state_dir': 'state', 'workspace_dir': 'workspace'},
            'codex': {'binary': 'runtime/bin/codex', 'runtime_version': 'codex-cli 0.163.0'}}


class ExtensionTests(unittest.TestCase):
    def test_no_extensions_is_a_complete_host_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            config = AgentConfig.from_dict(configuration(), Path(directory))
            host, console = build_host(config, development=True)
            self.assertEqual(host.registry.dynamic_tools({'*'}), [])
            self.assertEqual(config.providers, ())
            self.assertEqual(config.skill_roots, ())
            self.assertIsNotNone(console)

    def test_old_configuration_and_embedded_business_options_are_rejected(self):
        for modify in (lambda r: r.update(schema_version='galatea.agent/v1'),
                       lambda r: r.update(galatea={}),
                       lambda r: r.update(principal={'project_ids': ['p']}),
                       lambda r: r.update(codex={**r['codex'], 'args': ['--unsafe']})):
            raw = configuration()
            modify(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                AgentConfig.from_dict(raw, ROOT)

    def test_only_selected_environment_is_visible_to_codex_and_mcp(self):
        raw = configuration()
        raw['codex']['inherit_env'] = ['EXTENSION_TOKEN']
        config = AgentConfig.from_dict(raw, ROOT)
        with patch.dict(os.environ, {'EXTENSION_TOKEN': 'selected', 'OTHER_SECRET': 'hidden'}):
            environment = config.child_environment()
        self.assertEqual(environment['EXTENSION_TOKEN'], 'selected')
        self.assertNotIn('OTHER_SECRET', environment)
        self.assertEqual(environment['CODEX_HOME'], str(config.codex_home))
        raw['codex']['inherit_env'] = ['CODEX_HOME']
        with self.assertRaises(ValueError):
            AgentConfig.from_dict(raw, ROOT)

    def test_configured_namespace_and_provider_execute_without_platform(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Catalog.load(ROOT / 'examples/echo')
            providers = ProviderSet()
            try:
                providers.load([{'path': ROOT / 'examples/echo/provider.py',
                                 'factory': 'create_provider', 'options': {'prefix': 'hello '}}], catalog)
                self.assertEqual(catalog.dynamic_tools({'echo'})[0]['name'], 'example')
                registry = ToolRegistry(catalog, OperationJournal(AtomicJsonStore(Path(directory))), providers.handlers)
                context = ToolContext('s', 't', 'turn', 'call', 'agent', frozenset({'echo'}), catalog.digest, 'config')
                self.assertEqual(registry.call(context, 'echo', {'text': 'world'})['data'], {'text': 'hello world'})
                self.assertFalse(registry.call(replace(context, call_id='invalid'), 'echo', {'unexpected': True})['ok'])
                self.assertEqual(registry.call(replace(context, actions=frozenset()), 'echo', {'text': 'world'})['error']['category'], 'forbidden')
            finally:
                providers.close()

    def test_duplicate_providers_fail_and_close_every_loaded_provider(self):
        catalog = Catalog.load(ROOT / 'examples/echo')
        providers = ProviderSet()
        entry = {'path': ROOT / 'examples/echo/provider.py', 'factory': 'create_provider', 'options': {}}
        with self.assertRaisesRegex(ValueError, 'unique'):
            providers.load([entry, entry], catalog)
        self.assertEqual(providers.handlers, {})
        self.assertEqual(providers.providers, [])
        self.assertEqual(providers._modules, [])

    def test_uncertain_mutation_is_observed_by_call_identity_without_reexecution(self):
        with tempfile.TemporaryDirectory() as directory:
            template = Catalog.load(ROOT / 'examples/echo')
            metadata = copy.deepcopy(template.metadata)
            metadata['tools']['echo']['read_only'] = False
            catalog = Catalog(template.schemas, metadata)
            calls, observations = [], []
            def execute(context, arguments):
                calls.append(context.call_id)
                raise ConnectionError('response lost after external acceptance')
            def observe(identity):
                observations.append(identity['call_id'])
                return {'external_id': identity['call_id']}
            providers = ProviderSet()
            providers.providers = [ToolProvider({'echo': execute}, observe_receipt=observe)]
            registry = ToolRegistry(catalog, OperationJournal(AtomicJsonStore(Path(directory))), {'echo': execute})
            context = ToolContext('s', 't', 'turn', 'call', 'agent', frozenset({'echo'}), catalog.digest, 'original')
            self.assertFalse(registry.call(context, 'echo', {'text': 'fixture'})['ok'])
            self.assertFalse(registry.call(context, 'echo', {'text': 'fixture'})['ok'])
            providers.reconcile(registry, config_digest='different-provider-options')
            self.assertEqual(observations, [])
            providers.reconcile(registry, config_digest='original')
            self.assertEqual(registry.call(context, 'echo', {'text': 'fixture'})['data'], {'external_id': 'call'})
            self.assertEqual(calls, ['call'])
            self.assertEqual(observations, ['call'])


class ExtensionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_failure_releases_single_writer_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            store, events = AtomicJsonStore(Path(directory)), EventLog()
            def close():
                raise RuntimeError('extension cleanup failed')
            host = AgentHost(registry=ToolRegistry(Catalog.empty(), OperationJournal(store), {}),
                event_log=events, store=store, client_factory=lambda: object(), reconciler=lambda: None,
                principal_id='fixture', actions=set(), extensions_close=close)
            await host.start()
            try:
                with self.assertRaisesRegex(RuntimeError, 'cleanup failed'):
                    await host.stop()
                with single_writer_lock(store.root / '.host.lock'):
                    pass
            finally:
                await events.close()
