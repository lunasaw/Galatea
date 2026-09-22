import argparse
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

import test_topic_axes_review_v3 as fixtures
from wechat_persona.topic_axes_review_v3 import run_review
from wechat_persona.topic_route_diagnostic import run
from wechat_persona.topic_context import TopicContractError


class RouteDiagnosticTests(unittest.TestCase):
    def fixture(self, root):
        fixture = fixtures.AxesV3Tests()
        fixture.setUp()
        args = fixture.args(root)

        def failing(req, **kwargs):
            if req.data is not None and 'input' in json.loads(req.data):
                raise HTTPError(req.full_url, 502, 'Bad Gateway', {}, io.BytesIO(b'{}'))
            return fixture.provider(req, **kwargs)

        with patch('wechat_persona.topic_axes_review_v3.urlopen', side_effect=failing):
            report = run_review(**args, execute=True)
        return argparse.Namespace(failed_preflight=Path(report['workspace']), output_root=root / 'diagnostic',
                                  controlled_root=root, config=fixture.config, base_url=args['base_url'],
                                  auth_file=args['auth_file'], authorization_reference='unit-test-diagnostic', execute=False)

    def test_error_bytes_are_private_summary_is_redacted_and_replay_is_free(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.fixture(Path(tmp))
            with patch('wechat_persona.topic_route_diagnostic.urlopen') as call:
                plan = run(args)
                self.assertEqual(plan['planned_requests'], 1)
                self.assertFalse(args.output_root.exists())
                call.assert_not_called()
            args.execute = True
            secret = 'private-message-MUST-NOT-BE-LOGGED'
            body = json.dumps({'error': {'type': 'upstream_error', 'message': secret}}).encode()

            def fail(req, **kwargs):
                raise HTTPError(req.full_url, 502, 'Bad Gateway', {}, io.BytesIO(body))

            with patch('wechat_persona.topic_route_diagnostic.urlopen', side_effect=fail) as call:
                report = run(args)
                self.assertEqual(call.call_count, 1)
            self.assertEqual(report['http_status'], 502)
            self.assertEqual(report['error_type'], 'upstream_error')
            self.assertNotIn(secret, json.dumps(report))
            self.assertEqual(report['usage']['requests'], 1)
            with patch('wechat_persona.topic_route_diagnostic.urlopen') as call:
                self.assertEqual(run(args), report)
                call.assert_not_called()

    def test_different_route_cannot_reinterpret_failed_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.fixture(Path(tmp))
            args.base_url = 'https://different.invalid/openai'
            with patch('wechat_persona.topic_route_diagnostic.urlopen') as call:
                with self.assertRaises(TopicContractError):
                    run(args)
                call.assert_not_called()


if __name__ == '__main__':
    unittest.main()
