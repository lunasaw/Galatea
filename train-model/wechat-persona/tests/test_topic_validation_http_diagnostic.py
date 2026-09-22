from __future__ import annotations

import base64
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import test_topic_validation_recovery as fixtures
from wechat_persona import topic_validation_http_diagnostic as diagnostic
from wechat_persona import topic_validation_recovery as recovery
from wechat_persona import topic_validation_review as previous
from wechat_persona._common import digest, file_digest
from wechat_persona.topic_context import TopicContractError


class ValidationHttpDiagnosticTests(unittest.TestCase):
    def prepare(self, root):
        helper = fixtures.ValidationRecoveryTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        args, _, _ = helper.prepare(root)
        def provider(request, **kwargs):
            response = helper.fixture.helper.provider(request, **kwargs)
            if 'input' in json.loads(request.data):
                raise HTTPError('https://fixture.invalid', 400, 'bad request', {}, io.BytesIO(b'{}'))
            return response
        with patch.object(previous, 'urlopen', side_effect=provider):
            report = recovery.run_recovery(**args, execute=True)
        failed = Path(report['workspace'])
        self.assertEqual(report['unresolved_response_failures'], 1)
        config = deepcopy(diagnostic.load_config(ROOT / 'configs/topic-validation-http-diagnostic-v1.yaml'))
        config.update(failed_manifest_sha256=file_digest(failed / 'manifest.json'), missing_judgments=1)
        patcher = patch.object(diagnostic, 'load_config', return_value=config)
        patcher.start()
        self.addCleanup(patcher.stop)
        return dict(failed=failed, packet=args['packet'], consent=args['consent'],
            config_path=ROOT / 'configs/topic-validation-http-diagnostic-v1.yaml', output_root=root / 'diagnostic',
            controlled_root=root, base_url=args['base_url'], auth_file=args['auth_file'],
            authorization_reference='http-diagnostic-only')

    def test_one_request_retains_private_body_but_never_logs_message_or_adds_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.prepare(Path(tmp))
            old = {p: file_digest(p) for p in args['failed'].iterdir() if p.is_file()}
            error = {'error': {'type': 'invalid_request_error', 'code': 'unsupported_parameter',
                'param': 'input', 'message': 'unsupported parameter DO_NOT_LOG_PRIVATE_FIXTURE'}}
            with patch.object(diagnostic, 'urlopen', side_effect=lambda *a, **k: (_ for _ in ()).throw(
                    HTTPError('https://fixture.invalid', 400, 'bad request', {}, io.BytesIO(json.dumps(error).encode())))) as network:
                plan = diagnostic.run_diagnostic(**args)
                self.assertFalse(args['output_root'].exists())
                network.assert_not_called()
                report = diagnostic.run_diagnostic(**args, execute=True)
                self.assertEqual(report['http_status'], 400)
                self.assertEqual(report['error_code'], 'unsupported_parameter')
                self.assertEqual(report['message_category_hints'], ['unsupported_parameter'])
                self.assertEqual(report['judgments_added'], 0)
                self.assertFalse(report['labels_used'])
                self.assertNotIn('DO_NOT_LOG', json.dumps(report))
                self.assertEqual(diagnostic.run_diagnostic(**args, execute=True), report)
                self.assertEqual(network.call_count, 1)
            self.assertTrue(all(file_digest(p) == sha for p, sha in old.items()))
            directory = Path(plan['workspace'])
            self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in directory.iterdir()))
            (directory / 'response.json').write_text('{}')
            with self.assertRaises(TopicContractError):
                diagnostic.run_diagnostic(**args, execute=True)

    def test_interruption_is_not_redispatched(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.prepare(Path(tmp))
            with patch.object(diagnostic, 'urlopen', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    diagnostic.run_diagnostic(**args, execute=True)
            with patch.object(diagnostic, 'urlopen') as network:
                with self.assertRaisesRegex(TopicContractError, 'not redispatched'):
                    diagnostic.run_diagnostic(**args, execute=True)
                network.assert_not_called()

    def test_new_scope_same_route_and_frozen_failure_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.prepare(Path(tmp))
            with patch.object(diagnostic, 'urlopen') as network:
                for mutation in ({'authorization_reference': 'new-recovery-run'},
                                 {'base_url': 'https://different.invalid'}):
                    with self.assertRaises(TopicContractError):
                        diagnostic.run_diagnostic(**{**args, **mutation})
                (args['failed'] / 'report.json').write_text('{}')
                with self.assertRaises(TopicContractError):
                    diagnostic.run_diagnostic(**args)
                network.assert_not_called()

    def test_unrecognized_error_fields_and_malformed_usage_do_not_leak(self):
        encoded = base64.b64encode(json.dumps({'error': {'code': 'private_value', 'type': 'private_type',
            'param': 'private_parameter', 'message': 'PRIVATE_CONTENT'}, 'usage': ['invalid']}).encode()).decode()
        report, usage = diagnostic.response_summary({'body_base64': encoded, 'body_sha256': digest(encoded),
            'http_status': 400, 'transport_error': None, 'body_truncated': False, 'body_read_failed': False})
        self.assertIsNone(report['error_code'])
        self.assertIsNone(report['error_type'])
        self.assertIsNone(report['error_parameter'])
        self.assertEqual(usage, {})
        self.assertNotIn('private', json.dumps(report).lower())


if __name__ == '__main__':
    unittest.main()
