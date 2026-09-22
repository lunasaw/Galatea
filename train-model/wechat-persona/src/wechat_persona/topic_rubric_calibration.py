"""One bounded diagnostic pass over synthetic rubric cases; no private candidates."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import json
from pathlib import Path
import time
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import tiktoken

from ._common import digest, file_digest
from .fact_review_server import _atomic_json
from .topic_adjudication import load_fixtures, load_policy, verify_manifest
from .topic_candidates import private_path, read_json
from .topic_context import TopicContractError
from .topic_cross_review import decode_response, load_config as load_cross_policy, wire_payload
from .topic_evidence_audit import SCHEMA, SYSTEM
from .topic_review_budget import ReviewBudget


METHOD = 'topic-rubric-calibration-v1'
BUDGET = {'max_requests': 6, 'max_input_tokens': 24000, 'max_total_output_tokens': 18000,
          'max_output_tokens': 3000, 'request_timeout_seconds': 120}


def fixture_views(fixtures: list[dict]) -> list[dict]:
    return [{'sample_id': f['id'], 'candidate_sha256': digest(f), 'kind': 'selected',
             'source_ids': [str(i) for i in range(len(f['past']))],
             'case': {'reply': f['reply'], 'past_messages': [
                 {'index': i, 'speaker': m[0], 'text': m[1], 'kind': 'text', 'gap_before': False}
                 for i, m in enumerate(f['past'])]}} for f in fixtures]


def agreement_report(fixtures: list[dict], judgments: list[dict]) -> dict:
    by_id = {f['id']: f for f in fixtures}
    seen = set()
    details = []
    for row in judgments:
        key = (row['judge'], row['sample_id'])
        if key in seen or row['sample_id'] not in by_id:
            raise TopicContractError('duplicate or unknown calibration judgment')
        seen.add(key)
        expected = by_id[row['sample_id']]['expected']
        differences = [k for k in ('status', 'reply_link_correct', 'context_complete') if row[k] != expected[k]]
        details.append({'fixture_id': row['sample_id'], 'judge': row['judge'], 'rule': by_id[row['sample_id']]['rule'],
                        'differing_axes': differences, 'all_core_axes_agree': not differences,
                        'anchor_set_agrees': set(row['responds_to_indices']) == set(expected['responds_to_indices']),
                        'expected_status': expected['status'], 'observed_status': row['status'],
                        'observed_reason': row['reason'], 'confidence': row['confidence']})
    counts = {}
    for name in ('independent', 'changed_reference'):
        selected = [r for r in details if r['judge'] == name]
        counts[name] = {'population': len(fixtures), 'reviewed': len(selected),
                        'all_core_axes_agree': sum(r['all_core_axes_agree'] for r in selected),
                        'status_agrees': sum(r['expected_status'] == r['observed_status'] for r in selected),
                        'anchor_set_agrees': sum(r['anchor_set_agrees'] for r in selected),
                        'disagreement_axes': dict(Counter(k for r in selected for k in r['differing_axes']))}
    return {'fixture_count': len(fixtures), 'judges': counts, 'cases': details,
            'reference_origin': 'synthetic_specification_not_human_audit', 'true_precision_claimed': False,
            'private_samples_reviewed': 0, 'existing_labels_changed': False, 'threshold_changed': False,
            'training_run': False, 'human_review_completed': False, 'formal_training_eligible': False}


def run_calibration(*, policy_path: Path, cross_policy_path: Path, output_root: Path, controlled_root: Path,
                    base_url: str, auth_file: Path, authorization_reference: str, execute: bool = False) -> dict:
    private_path(output_root, controlled_root)
    if not authorization_reference or not base_url:
        raise TopicContractError('calibration requires endpoint and authorization reference')
    policy = load_policy(policy_path)
    fixture_path, fixtures = load_fixtures(policy_path, policy)
    cross_policy = load_cross_policy(cross_policy_path)
    views = fixture_views(fixtures)
    if len(views) != 18:
        raise TopicContractError('calibration budget is frozen to 18 synthetic cases')
    counter = tiktoken.get_encoding('o200k_base')
    batches = []
    for name, judge in cross_policy['judges'].items():
        for start in range(0, len(views), 6):
            batch = views[start:start + 6]
            wire = wire_payload(batch, cross_policy, judge)
            batches.append({'judge': name, 'views': batch, 'payload': wire,
                            'input_estimate': len(counter.encode(json.dumps(wire, ensure_ascii=False))) + 256})
    estimate = sum(b['input_estimate'] for b in batches)
    if len(batches) > BUDGET['max_requests'] or estimate > BUDGET['max_input_tokens']:
        raise TopicContractError('synthetic calibration exceeds budget')
    identity = {'method': METHOD, 'policy_sha256': digest(policy), 'cross_policy_sha256': digest(cross_policy),
                'fixture_sha256': file_digest(fixture_path), 'budget': BUDGET,
                'endpoint_sha256': digest(base_url), 'authorization_reference': authorization_reference,
                'prompt_sha256': digest(SYSTEM), 'schema_sha256': digest(SCHEMA),
                'implementation_sha256': file_digest(Path(__file__)),
                'dependencies': {name: file_digest(Path(__file__).with_name(name)) for name in (
                    'topic_adjudication.py', 'topic_cross_review.py', 'topic_evidence_audit.py', 'topic_review_budget.py')}}
    workspace = output_root / ('topic-rubric-calibration_' + digest(identity)[:20])
    plan = {'status': 'planned', 'workspace': str(workspace), 'planned_requests': len(batches),
            'estimated_input_tokens': estimate, 'budget': BUDGET, 'synthetic_fixture_count': len(fixtures),
            'private_samples_reviewed': 0, 'training_run': False, 'automatic_retries': 0}
    if not execute:
        return plan
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace.chmod(0o700)
    with (workspace / 'run.lock').open('a') as lock:
        (workspace / 'run.lock').chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (workspace / 'manifest.json').exists():
            manifest = verify_manifest(workspace, {'identity.json', 'report.json', 'decisions.json', 'cases.json', 'budget.json'})
            if manifest['identity'] != identity:
                raise TopicContractError('calibration identity changed')
            return read_json(workspace / 'report.json')
        _atomic_json(workspace / 'identity.json', identity)
        budget = ReviewBudget(workspace, identity, BUDGET)
        key = read_json(auth_file)['OPENAI_API_KEY']

        def call(batch):
            request_digest = digest({'identity': identity, 'batch': batch})
            path = workspace / (request_digest + '.json')
            judge = cross_policy['judges'][batch['judge']]
            if path.exists():
                record = read_json(path)
                parsed, _ = decode_response(record['response'], judge, batch['views'], .9)
                if record['request_digest'] != request_digest or record['response_sha256'] != digest(record['response']):
                    raise TopicContractError('calibration response cache changed')
                return [{**r, 'judge': batch['judge']} for r in parsed]
            reservation = budget.reserve(request_digest, batch['input_estimate'])
            endpoint = 'v1/responses' if judge['transport'] == 'responses' else 'v1/chat/completions'
            request = Request(urljoin(base_url.rstrip('/') + '/', endpoint), data=json.dumps(batch['payload'], ensure_ascii=False).encode(),
                              headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}, method='POST')
            with urlopen(request, timeout=BUDGET['request_timeout_seconds']) as response:
                result = json.loads(response.read())
            raw_usage = result.get('usage', {})
            usage = {target: raw_usage.get(target, raw_usage.get(other))
                     for target, other in (('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens'))
                     if target in raw_usage or other in raw_usage}
            budget.settle(reservation, usage)
            _atomic_json(path, {'request_digest': request_digest, 'response': result, 'response_sha256': digest(result),
                               'received_at_unix': time.time()})
            parsed, _ = decode_response(result, judge, batch['views'], .9)
            return [{**r, 'judge': batch['judge']} for r in parsed]

        judgments, failures = [], []
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(call, batch): i for i, batch in enumerate(batches)}
            for future in as_completed(futures):
                try:
                    judgments.extend(future.result())
                except (ValueError, OSError) as exc:
                    reason = str(exc) if isinstance(exc, TopicContractError) else type(exc).__name__
                    failures.append({'batch': futures[future], 'reason': reason})
        judgments.sort(key=lambda r: (r['judge'], r['sample_id']))
        summary = agreement_report(fixtures, judgments)
        cases = summary.pop('cases')
        report = {**plan, **summary, 'status': 'complete' if len(judgments) == 2 * len(fixtures) and not failures else 'incomplete',
                  'failures': failures, 'usage': budget.usage(), 'decisions_sha256': digest(judgments)}
        _atomic_json(workspace / 'decisions.json', {'identity': identity, 'decisions': judgments})
        _atomic_json(workspace / 'cases.json', {'cases': cases})
        _atomic_json(workspace / 'report.json', report)
        if report['status'] == 'complete':
            if file_digest(fixture_path) != identity['fixture_sha256']:
                raise TopicContractError('calibration fixtures changed during execution')
            names = ['identity.json', 'decisions.json', 'cases.json', 'report.json', 'budget.json']
            names += [p.name for p in workspace.glob('*.json') if len(p.stem) == 64]
            manifest = {'schema_version': 'topic-rubric-calibration-manifest-v1', 'identity': identity,
                        'training_run': False, 'human_review_completed': False, 'formal_training_eligible': False,
                        'output_digests': {name: file_digest(workspace / name) for name in names}}
            manifest['manifest_sha256'] = digest(manifest)
            _atomic_json(workspace / 'manifest.json', manifest)
        return report
