from __future__ import annotations

import base64
from copy import deepcopy
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import test_topic_validation_review as fixtures
from wechat_persona import topic_validation_completion as completion
from wechat_persona import topic_validation_review as review
from wechat_persona._common import file_digest
from wechat_persona.topic_candidates import read_json
from wechat_persona.topic_context import TopicContractError


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ValidationReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def prepare(self, root):
        args, candidates = self.fixture.prepare(root)
        def provider(request, **kwargs):
            response = self.fixture.helper.provider(request, **kwargs)
            judge, ids = self.fixture.helper.calls[-1]
            if judge == 'gpt' and ids == [candidates[0]['sample_id']]:
                raise URLError('synthetic')
            return response
        with patch.object(review, 'urlopen', side_effect=provider):
            result = review.run_review(**args, execute=True)
        parent = Path(result['workspace'])
        source = read_json(parent / 'identity.json')
        decisions = read_json(parent / 'decisions.json')['decisions']
        self.assertEqual(len(decisions), 5)
        config = deepcopy(completion.load_config(ROOT / 'configs/topic-validation-completion-v1.yaml'))
        config.update(parent_manifest_sha256=file_digest(parent / 'manifest.json'),
                      inherited_judgments=5, missing_judgments=1)
        for target, value in (("load_config", config), ("load_parent", (source, candidates,
                {r['sample_id']: review.preparation.strata_for(r) for r in candidates}, self.fixture.policy, decisions))):
            patcher = patch.object(completion, target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return {k: v for k, v in {**args, 'parent': parent, 'output_root': root / 'completion',
            'config_path': ROOT / 'configs/topic-validation-completion-v1.yaml'}.items()
            if k not in {'original_preflight', 'calibration'}}

    def test_http_body_retained_repair_and_offline_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.prepare(Path(tmp))
            calls = []
            def provider(request, **kwargs):
                calls.append(1)
                if len(calls) == 1:
                    raise HTTPError(request.full_url, 400, 'synthetic', {}, io.BytesIO(b'{"error":{"code":"bad_request"}}'))
                return self.fixture.helper.provider(request, **kwargs)
            with patch.object(completion, 'urlopen', side_effect=provider):
                self.assertEqual(completion.run_completion(**args)['status'], 'planned')
                self.assertEqual(calls, [])
                report = completion.run_completion(**args, execute=True)
                self.assertEqual(report['reviewed'], 6)
                self.assertEqual(report['usage']['requests'], 2)
                self.assertEqual(report['new_valid_judgments'], 1)
                self.assertFalse(report['p3_accepted'])
            with patch.object(completion, 'urlopen', side_effect=AssertionError('network forbidden')):
                self.assertEqual(completion.run_completion(**args, execute=True), report)
            workspace = Path(report['workspace'])
            raw = [read_json(p) for p in workspace.glob('*.json') if len(p.stem) == 64]
            error = next(r for r in raw if r['http_status'] == 400)
            self.assertIn(b'bad_request', base64.b64decode(error['raw_body_base64']))
            self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in workspace.iterdir()))
            (workspace / 'report.json').write_text('{}')
            with self.assertRaises(TopicContractError):
                completion.run_completion(**args, execute=True)

    def test_identity_mismatch_stops_without_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.prepare(Path(tmp))
            self.fixture.helper.fault = lambda name, batch, model, labels: ('unexpected-model', labels)
            with patch.object(completion, 'urlopen', side_effect=self.fixture.helper.provider) as network:
                report = completion.run_completion(**args, execute=True)
            self.assertEqual(report['status'], 'incomplete')
            self.assertEqual(report['circuit_reason'], 'model_identity_mismatch')
            self.assertEqual(network.call_count, 1)

    def test_failure_retry_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.prepare(Path(tmp))
            with patch.object(completion, 'urlopen', side_effect=URLError('synthetic')) as network:
                report = completion.run_completion(**args, execute=True)
                self.assertEqual(completion.run_completion(**args, execute=True), report)
            self.assertEqual(network.call_count, 2)
            self.assertEqual(report['unresolved_response_failures'], 1)


if __name__ == '__main__':
    unittest.main()
