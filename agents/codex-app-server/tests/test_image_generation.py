import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from test_console import FakeHost, call, payload
from codex_agent.config import AgentConfig
from codex_agent.console.server import ConsoleApp
from codex_agent.events import EventLog
from codex_agent.image_generation import DEFAULTS, ImageGenerationSettings, api_base, private_write


class ImageSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = self.root / 'chat-key'
        private_write(self.key, 'fixture-secret')
        self.config = SimpleNamespace(state_dir=self.root / 'state', codex_home=self.root / 'home',
                                      raw={'codex': {'model_api_key_file': str(self.key)}})
        self.settings = ImageGenerationSettings(self.config, editable=True)
        self.settings.script.parent.mkdir(parents=True)
        self.settings.script.write_text('import os,sys\nprint(os.environ["OPENAI_API_KEY"])\nprint(sys.argv[1:])\n')
        self.native = {'model_provider': 'openai', 'openai_base_url': 'https://gateway.example/v1'}

    def body(self, **changes):
        public = self.settings.public(self.native)
        return {**{key: public[key] for key in DEFAULTS}, 'revision': public['revision'], **changes}

    def test_current_key_is_referenced_and_never_returned_or_in_skill(self):
        saved = self.settings.save(self.body(enabled=True), self.native)
        self.assertTrue(saved['enabled'])
        self.assertEqual(saved['currentBaseUrl'], self.native['openai_base_url'])
        self.assertNotIn('fixture-secret', json.dumps(saved) + self.settings.skill.read_text())
        profile = json.loads(self.settings.path.read_text())
        self.assertEqual(profile['keyFile'], str(self.key))
        self.assertNotIn('apiKey', profile)
        self.assertEqual(self.settings.path.stat().st_mode & 0o777, 0o600)
        # The generated entrypoint must work in a native shell with no PYTHONPATH.
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}
        runner = Path(__file__).parents[1] / 'src/codex_agent/image_generation_cli.py'
        result = subprocess.run([sys.executable, str(runner), '--profile', str(self.settings.path),
                                 '--prompt', 'fixture', '--out', 'fixture.png', '--dry-run'],
                                cwd=self.root, env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('gpt-image-2', result.stdout)
        self.assertNotIn('fixture-secret', result.stdout + result.stderr)
        self.settings.save(self.body(enabled=False), self.native)
        self.assertFalse(self.settings.skill.exists())
        result = subprocess.run([sys.executable, str(runner), '--profile', str(self.settings.path),
                                 '--prompt', 'fixture', '--out', 'fixture.png'], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    def test_custom_key_preserved_only_for_same_endpoint(self):
        self.settings.save(self.body(connection='custom', baseUrl='https://images.example/v1', apiKey='custom-secret'), self.native)
        self.assertTrue(self.settings.public(self.native)['keyConfigured'])
        self.assertNotIn('custom-secret', json.dumps(self.settings.public(self.native)))
        self.settings.save(self.body(model='gpt-image-1.5'), self.native)
        self.assertEqual(self.settings.credential(self.settings.read()), 'custom-secret')
        with self.assertRaises(ValueError):
            self.settings.save(self.body(baseUrl='https://different.example/v1'), self.native)

    def file_config(self, **changes):
        return AgentConfig.from_dict({
            'schema_version': 'codex-agent/v2',
            'paths': {'codex_home': 'home', 'runtime_dir': 'runtime', 'state_dir': 'state', 'workspace_dir': 'workspace'},
            'codex': {'binary': '/usr/bin/true', 'runtime_version': 'codex-cli 0.163.0'},
            'image_generation': {'enabled': True, 'connection': 'custom', 'base_url': 'http://images.example/v1/',
                                 'api_key_file': 'chat-key', **changes},
        }, self.root)

    def test_file_defaults_activate_on_startup_and_preserve_web_overrides(self):
        config = self.file_config(quality='high')
        self.settings = ImageGenerationSettings(config)
        self.assertEqual(config.image_generation['keyFile'], str(self.key))
        public = self.settings.public(self.native)
        self.assertTrue(public['enabled'])
        self.assertTrue(public['keyConfigured'])
        self.assertFalse(public['editable'])
        self.assertNotIn('fixture-secret', json.dumps(public))
        self.assertTrue(self.settings.initialize(self.native))
        self.assertFalse(self.settings.path.exists())
        self.assertTrue(self.settings.skill.is_file())
        profile = json.loads(self.settings.runtime_path.read_text())
        self.assertEqual(profile['baseUrl'], 'http://images.example/v1')
        self.assertNotIn('apiKey', profile)
        self.assertNotIn('fixture-secret', self.settings.runtime_path.read_text() + self.settings.skill.read_text())
        self.assertEqual(self.settings.runtime_path.stat().st_mode & 0o777, 0o600)
        runner = Path(__file__).parents[1] / 'src/codex_agent/image_generation_cli.py'
        command = [sys.executable, str(runner), '--profile', str(self.settings.runtime_path),
                   '--prompt', 'fixture', '--out', 'fixture.png', '--dry-run']
        result = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('fixture-secret', result.stdout + result.stderr)
        # File defaults are reapplied on restart while there is no web override.
        self.settings = ImageGenerationSettings(self.file_config(model='gpt-image-1.5'))
        self.settings.initialize(self.native)
        self.assertEqual(json.loads(self.settings.runtime_path.read_text())['model'], 'gpt-image-1.5')
        self.settings.editable = True
        self.settings.save(self.body(quality='low'), self.native)
        self.assertEqual(self.settings.credential(self.settings.read()), 'fixture-secret')
        self.settings = ImageGenerationSettings(self.file_config(model='gpt-image-2', quality='high'), editable=True)
        self.settings.initialize(self.native)
        self.assertEqual(self.settings.public(self.native)['quality'], 'low')
        self.assertEqual(self.settings.public(self.native)['model'], 'gpt-image-1.5')
        # An endpoint change must never inherit the file credential.
        with self.assertRaisesRegex(ValueError, 'Key'):
            self.settings.save(self.body(baseUrl='https://other.example/v1'), self.native)
        self.settings.save(self.body(apiKey='replacement-secret'), self.native)
        self.assertNotIn('keyFile', self.settings.read())
        self.assertEqual(self.settings.credential(self.settings.read()), 'replacement-secret')
        self.settings.save(self.body(enabled=False), self.native)
        self.assertFalse(self.settings.skill.exists())
        self.assertFalse(json.loads(self.settings.runtime_path.read_text())['enabled'])
        stale = json.loads(self.settings.runtime_path.read_text())
        stale['enabled'] = True
        private_write(self.settings.runtime_path, json.dumps(stale))
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)

    def test_file_defaults_fail_early_for_invalid_or_unprotected_credentials(self):
        for changes in ({'enabled': 'true'}, {'model': 'chat-model'}, {'quality': []},
                        {'api_key': 'secret'}, {'api_key_file': ''}, {'api_key_file': '../key'},
                        {'base_url': 'file:///tmp/key'}, {'connection': 'current'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.file_config(**changes)
        settings = ImageGenerationSettings(self.file_config())
        self.key.chmod(0o644)
        with self.assertRaisesRegex(ValueError, '文件权限'):
            settings.initialize(self.native)
        self.assertFalse(settings.skill.exists())

    def test_conflict_readonly_and_credential_destination(self):
        stale = self.body()
        self.settings.save(self.body(model='gpt-image-1.5'), self.native)
        with self.assertRaisesRegex(ValueError, '其他页面'):
            self.settings.save(stale, self.native)
        self.settings.editable = False
        with self.assertRaisesRegex(ValueError, '只读'):
            self.settings.save(self.body(), self.native)
        custom = {'model_provider': 'other', 'model_providers': {'other': {'base_url': 'https://other.example/v1'}}}
        self.assertIsNone(self.settings.current_connection(custom)['keyFile'])
        invalid_native = {**self.native, 'openai_base_url': 'not-a-url'}
        self.assertEqual(self.settings.public(invalid_native)['currentBaseUrl'], '')
        self.settings.editable = True
        with self.assertRaisesRegex(ValueError, 'API 地址'):
            self.settings.save(self.body(enabled=True), invalid_native)
        for address in ('https://user:secret@example.test/v1', 'file:///etc/passwd', 'https://example.test/v1?key=x'):
            with self.assertRaises(ValueError):
                api_base(address)

    def test_http_gateway_without_model_list_checks_image_endpoint(self):
        body = self.body(connection='custom', baseUrl='http://images.example/v1/', apiKey='custom-secret', enabled=True)
        with patch('codex_agent.image_generation.request_json', side_effect=[(404, {}), (400, {})]) as request:
            result = self.settings.check(body, self.native)
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(result['models'], [])
        self.assertIn('未提供模型列表', result['message'])
        self.assertEqual(request.call_args.args[:3], ('http://images.example/v1', 'custom-secret', '/images/generations'))
        saved = self.settings.save(body, self.native)
        self.assertEqual(saved['baseUrl'], 'http://images.example/v1')
        with patch('codex_agent.image_generation.request_json', side_effect=[(404, {}), (403, {})]):
            result = self.settings.check(self.body(), self.native)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['httpStatus'], 403)

    def test_gateway_permission_is_not_confused_with_model_listing(self):
        with patch('codex_agent.image_generation.request_json', side_effect=[
            (200, {'data': [{'id': 'gpt-image-2'}, {'id': 'chat-model'}]}),
            (403, {'error': {'message': 'Image generation is not enabled for this group'}}),
        ]) as request:
            result = self.settings.check(self.body(), self.native)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['models'], ['gpt-image-2'])
        self.assertIn('分组未开启', result['message'])
        self.assertEqual(request.call_args.args[-1]['prompt'], '')
        self.assertFalse(self.settings.path.exists())
        with patch('codex_agent.image_generation.request_json', side_effect=[(200, {'data': []}), (400, {})]):
            self.assertEqual(self.settings.check(self.body(), self.native)['status'], 'unverified')


class ImageSettingsHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_reload_idempotency_origin_and_secret_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = EventLog()
            host = FakeHost(events, root)
            host.workspace_dir = root
            host.client = SimpleNamespace(read_config=AsyncMock(return_value={'config': {}}),
                                          peer=SimpleNamespace(request=AsyncMock(return_value={})))
            settings = ImageGenerationSettings(SimpleNamespace(state_dir=root / 'state', codex_home=root / 'home', raw={}), editable=True)
            app = ConsoleApp(host, events, image_settings=settings)
            public = payload(await call(app, 'GET', '/api/image-generation/settings'))
            body = {**DEFAULTS, 'connection': 'custom', 'baseUrl': 'https://images.example/v1',
                    'apiKey': 'http-test-secret', 'revision': public['revision']}
            for _ in range(2):
                response = await call(app, 'POST', '/api/image-generation/settings', body, {'idempotency-key': 'image-save'})
                self.assertEqual(response[0]['status'], 200, payload(response))
                self.assertNotIn('http-test-secret', json.dumps(payload(response)))
            host.client.peer.request.assert_awaited_once()
            self.assertNotIn('http-test-secret', host.store.read('http-requests/image-save.json').__repr__())
            self.assertEqual((await call(app, 'POST', '/api/image-generation/check', body,
                                        {'origin': 'https://evil.example'}))[0]['status'], 403)
            self.assertEqual((await call(app, 'GET', '/api/image-generation/check'))[0]['status'], 405)


@unittest.skipUnless(os.environ.get('CODEX_TEST_RUNTIME_PACKAGE'), 'native runtime opt-in required')
class ImageSettingsRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_config_only_startup_discovers_skill_without_web_save(self):
        from codex_agent.app import build_host
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = Path(os.environ['CODEX_TEST_RUNTIME_PACKAGE'])
            (root / 'home').mkdir()
            (root / 'home/config.toml').write_text('model = "fixture"\n')
            private_write(root / 'image-key', 'fixture-secret')
            config = AgentConfig.from_dict({
                'schema_version': 'codex-agent/v2',
                'paths': {'codex_home': 'home', 'runtime_dir': str(package), 'state_dir': 'state', 'workspace_dir': 'workspace'},
                'codex': {'binary': str(package / 'bin/codex'), 'runtime_version': 'codex-cli 0.163.0'},
                'image_generation': {'enabled': True, 'connection': 'custom', 'base_url': 'http://localhost/v1',
                                     'api_key_file': 'image-key'},
            }, root)
            settings = ImageGenerationSettings(config)
            private_write(settings.script, '# fixture; generation is never invoked\n')
            host, app = build_host(config, development=True)
            try:
                await host.start()
                self.assertTrue(host.ready)
                result = await host.client.peer.request('skills/list', {'cwds': [str(config.workspace_dir)], 'forceReload': True})
                skills = [skill for entry in result['data'] for skill in entry['skills']]
                self.assertIn('imagegen-api', [skill['name'] for skill in skills])
                self.assertTrue(app.image_settings.public({})['enabled'])
                self.assertFalse(settings.path.exists())
            finally:
                await host.stop()
                await host.events.close()


if __name__ == '__main__':
    unittest.main()
