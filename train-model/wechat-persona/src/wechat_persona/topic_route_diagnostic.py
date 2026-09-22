#!/usr/bin/env python3
"""Capture one failed synthetic probe's HTTP error without disclosing gateway text."""
from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
from wechat_persona._common import digest, file_digest
from wechat_persona.fact_review_server import _atomic_json
from wechat_persona.topic_adjudication import verify_manifest
from wechat_persona.topic_candidates import private_path, read_json
from wechat_persona import topic_axes_review as v2
from wechat_persona.topic_axes_review_v3 import PROBES, binding_for, load_config, wire_payload
from wechat_persona.topic_review_budget import ReviewBudget
from wechat_persona.topic_context import TopicContractError

BUDGET = {'max_requests': 1, 'max_input_tokens': 12000, 'max_total_output_tokens': 3000,
          'max_output_tokens': 3000, 'request_timeout_seconds': 120}


def run(args):
    for path in (args.failed_preflight, args.output_root):
        private_path(path, args.controlled_root)
    policy = load_config(args.config)
    fixture_path, _ = v2.load_fixtures(args.config, policy)
    binding = binding_for(policy, fixture_path, args.base_url)
    source = verify_manifest(args.failed_preflight, {'identity.json', 'report.json', 'decisions.json', 'budget.json'})
    source_report = read_json(args.failed_preflight / 'report.json')
    if (source['identity']['scope'] != 'preflight' or source['identity']['protocol'] != binding
            or source_report['route_gate_passed'] or not source_report['response_failure_counts'].get('HTTPError')):
        raise TopicContractError('route diagnosis requires matching failed HTTP preflight')
    # Use an already-failed probe, never a valid judgment or an unseen calibration case.
    failed = [read_json(args.failed_preflight / name) for name in source['output_digests']
              if len(Path(name).stem) == 64]
    record = next(r for r in failed if r['batch']['judge'] == 'gpt' and r['error_type'] == 'HTTPError')
    candidates = [v2.fixture_candidate(f) for f in PROBES if f['id'] in record['batch']['ids']]
    payload = wire_payload(candidates, policy['judges']['gpt'], policy['preflight'])
    if digest(payload) != record['payload_sha256']:
        raise TopicContractError('diagnostic request differs from failed probe')
    identity = {'method': 'topic-route-http-diagnostic-v1', 'failed_manifest_sha256': file_digest(args.failed_preflight / 'manifest.json'),
                'protocol': binding, 'payload_sha256': digest(payload), 'budget': BUDGET,
                'authorization_reference': args.authorization_reference, 'implementation_sha256': file_digest(Path(__file__))}
    directory = args.output_root / ('topic-route-diagnostic_' + digest(identity)[:20])
    if not args.execute:
        return {'status': 'planned', 'workspace': str(directory), 'budget': BUDGET, 'planned_requests': 1,
                'private_samples_sent': 0, 'calibration_samples_sent': 0}
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    with (directory / 'run.lock').open('a') as lock:
        (directory / 'run.lock').chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (directory / 'manifest.json').exists():
            if verify_manifest(directory, {'identity.json', 'response.json', 'report.json', 'budget.json'})['identity'] != identity:
                raise TopicContractError('diagnostic identity changed')
            return read_json(directory / 'report.json')
        _atomic_json(directory / 'identity.json', identity)
        budget = ReviewBudget(directory, identity, BUDGET)
        response_path = directory / 'response.json'
        if response_path.exists():
            raw = read_json(response_path)
        else:
            attempt = budget.reserve(digest(identity), 4000)
            key = read_json(args.auth_file)['OPENAI_API_KEY']
            request = Request(urljoin(args.base_url.rstrip('/') + '/', 'v1/responses'),
                              data=json.dumps(payload, ensure_ascii=False).encode(), method='POST',
                              headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
            try:
                with urlopen(request, timeout=120) as response:
                    body, status = response.read(), response.status
            except HTTPError as exc:
                body, status = exc.read(), exc.code
            except URLError:
                body, status = b'', None
            raw = {'body_base64': base64.b64encode(body).decode('ascii'), 'http_status': status, 'reservation': attempt}
            _atomic_json(response_path, raw)
        body = base64.b64decode(raw['body_base64'])
        try:
            parsed = v2.strict_json(body)
        except ValueError:
            parsed = {}
        usage = parsed.get('usage', {}) if isinstance(parsed, dict) else {}
        budget.settle(raw['reservation'], {k: usage[k] for k in ('input_tokens', 'output_tokens') if k in usage})
        error = parsed.get('error', {}) if isinstance(parsed, dict) else {}
        error = error if isinstance(error, dict) else {}
        # Never echo an untrusted error message, key, URL or returned prompt to ordinary logs.
        safe_code = lambda v: v if isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', v) else None
        text = body.decode('utf-8', errors='replace').lower()
        categories = [name for name, needles in {
            'model_not_found': ('model_not_found', 'model not found', 'unknown model', '模型不存在'),
            'model_unavailable': ('no available channel', 'model is not available', '无可用渠道'),
            'unsupported_schema': ('json_schema', 'response_format', 'text.format'),
            'unsupported_parameter': ('unsupported_parameter', 'unsupported parameter'),
            'authentication': ('invalid_api_key', 'incorrect api key', 'authentication'),
            'permission': ('permission_denied', 'permission denied', 'not permitted'),
            'rate_limit': ('rate_limit', 'rate limit', 'too many requests'),
        }.items() if any(value in text for value in needles)]
        report = {'status': 'complete', 'workspace': str(directory), 'http_status': raw['http_status'],
                  'error_code': safe_code(error.get('code')), 'error_type': safe_code(error.get('type')),
                  'error_parameter': safe_code(error.get('param')), 'error_categories': categories,
                  'usage': budget.usage(), 'private_samples_sent': 0, 'calibration_samples_sent': 0, **v2.GOVERNANCE}
        _atomic_json(directory / 'report.json', report)
        names = ['identity.json', 'response.json', 'report.json', 'budget.json']
        manifest = {'schema_version': 'topic-route-http-diagnostic-v1', 'identity': identity, **v2.GOVERNANCE,
                    'output_digests': {name: file_digest(directory / name) for name in names}}
        manifest['manifest_sha256'] = digest(manifest)
        _atomic_json(directory / 'manifest.json', manifest)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('failed-preflight', 'output-root', 'controlled-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--config', type=Path, default=ROOT / 'configs/topic-axes-review-v3.yaml')
    parser.add_argument('--auth-file', type=Path, default=Path.home() / '.codex/auth.json')
    parser.add_argument('--base-url', default=os.environ.get('OPENAI_BASE_URL'))
    parser.add_argument('--authorization-reference', required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--plan', action='store_true')
    mode.add_argument('--execute', action='store_true')
    try:
        print(json.dumps(run(parser.parse_args()), ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, StopIteration) as exc:
        print(json.dumps({'status': 'blocked', 'error_type': type(exc).__name__}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
