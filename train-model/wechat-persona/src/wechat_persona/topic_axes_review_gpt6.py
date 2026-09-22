"""Independent GPT-6 route and synthetic-calibration protocol for repair review."""
from __future__ import annotations

import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import tiktoken
import yaml

from . import topic_axes_review_v4 as legacy
from . import topic_axes_support_v4 as core
from ._common import digest, file_digest
from .fact_review_server import _atomic_json
from .topic_adjudication import verify_manifest
from .topic_candidates import private_path, read_json
from .topic_context import TopicContractError
from .topic_review_budget import ReviewBudget
from .topic_review_protocol_v4 import AXES_CONTRACT


METHOD = 'topic-reply-axes-v4-gpt6-v1'
PROMPT = legacy.PROMPT
PROBES = [
    {'id': 'route-gpt6-v1-01', 'past': [['self', '午间的讲座几点开始？']], 'reply': '中午十二点开始。'},
    {'id': 'route-gpt6-v1-02', 'past': [['self', '你落下的围巾我放门卫了。']], 'reply': '谢啦，等会去取。'},
]


def load_config(path: Path) -> dict:
    policy = yaml.safe_load(path.read_text(encoding='utf-8'))
    expected_judges = {
        'gpt': {'model': 'gpt-6-sol', 'family': 'gpt',
                'returned_model_prefix': 'gpt-6-sol', 'transport': 'responses'},
        'claude': {'model': 'claude-sonnet-4-6', 'family': 'claude',
                   'returned_model_prefix': 'claude-sonnet-4-6', 'transport': 'chat_completions'},
    }
    if (policy.get('schema_version') != 'topic-axes-review-gpt6-config-v1'
            or policy.get('method') != METHOD or policy.get('axes_contract') != AXES_CONTRACT
            or policy.get('synthetic_fixtures') != 'fixtures/topic-axes-validation-v4.json'
            or policy.get('seed') != 47 or policy.get('batch_size') != 6
            or policy.get('workers') != 2 or policy.get('required_consecutive_route_preflights') != 3
            or policy.get('confidence_role') != 'uncalibrated_diagnostic_only'
            or policy.get('basis_confidence_audit_manifest_sha256')
            != '2b1d287aadcf1791087e1e4ab70ce9836f04cb11122f4473271d5e1f98d26512'
            or policy.get('judges') != expected_judges or policy.get('governance') != core.GOVERNANCE
            or {key: policy['calibration'][key] for key in core.GATE} != core.GATE
            or policy['calibration'].get('population') != 24):
        raise TopicContractError('invalid frozen GPT-6 axes policy')
    for scope, ceilings in (('preflight', (4, 32000, 16000)),
                            ('calibration', (8, 80000, 32000))):
        limits = dict(zip(('max_requests', 'max_input_tokens', 'max_total_output_tokens'), ceilings))
        limits.update(max_output_tokens=4000, request_timeout_seconds=120)
        for key, maximum in limits.items():
            if type(policy[scope].get(key)) is not int or not 1 <= policy[scope][key] <= maximum:
                raise TopicContractError('invalid GPT-6 axes budget')
    return policy


def binding_for(policy: dict, fixture_path: Path, base_url: str) -> dict:
    binding = core.protocol_binding(policy, fixture_path, base_url)
    binding.update(method=METHOD, axes_contract=AXES_CONTRACT,
                   prompt_sha256=digest(PROMPT), probes_sha256=digest(PROBES))
    binding['source_sha256'][Path(__file__).name] = file_digest(Path(__file__))
    return binding


def validate_decisions(candidates: list[dict], decisions: list[dict], policy: dict,
                       models: dict | None = None) -> None:
    core.validate_decisions(candidates, decisions, policy)
    for row in decisions:
        if (row.get('review_protocol') != METHOD or row.get('prompt_sha256') != digest(PROMPT)
                or not legacy.re_digest(row.get('request_digest'))
                or (models is not None and row['model_returned'] != models[row['judge']])):
            raise TopicContractError('GPT-6 decision or exact model binding mismatch')


def route_summary(candidates: list[dict], decisions: list[dict], policy: dict,
                  catalog: dict) -> dict:
    expected = {name: judge['model'] for name, judge in policy['judges'].items()}
    validate_decisions(candidates, decisions, policy, expected)
    ids = {row.get('id') for row in catalog.get('data', []) if isinstance(row, dict)}
    counts = {name: dict(Counter(row['model_returned'] for row in decisions
                                if row['judge'] == name)) for name in policy['judges']}
    passed = all(model in ids and counts[name] == {model: len(PROBES)}
                 for name, model in expected.items())
    return {'route_gate_passed': passed, 'returned_model_counts': counts,
            'requested_models_listed': {name: model in ids for name, model in expected.items()},
            'bound_returned_models': expected, 'catalog_request_count': 1,
            'route_stability_proven': False, 'quality_claimed': False}


def read_package(directory: Path, binding: dict, scope: str) -> tuple[dict, dict, list]:
    required = {'identity.json', 'report.json', 'decisions.json', 'cases.json', 'budget.json'}
    if scope == 'preflight':
        required.add('catalog.json')
    manifest = verify_manifest(directory, required)
    identity = read_json(directory / 'identity.json')
    report = read_json(directory / 'report.json')
    record = read_json(directory / 'decisions.json')
    if (manifest['identity'] != identity or identity['scope'] != scope
            or identity['protocol'] != binding or record['identity'] != identity
            or digest(record['decisions']) != report['decisions_sha256']
            or report['status'] != 'complete'
            or any(manifest.get(key) != value for key, value in core.GOVERNANCE.items())):
        raise TopicContractError('GPT-6 prerequisite incomplete or identity mismatch')
    return identity, report, record['decisions']


def require_preflight(directory: Path, binding: dict, policy: dict) -> dict:
    identity, report, decisions = read_package(directory, binding, 'preflight')
    sequence = identity.get('route_probe_sequence')
    if type(sequence) is not int or not 1 <= sequence <= policy['required_consecutive_route_preflights']:
        raise TopicContractError('GPT-6 route probe sequence invalid')
    candidates = [core.fixture_candidate(fixture) for fixture in PROBES]
    summary = route_summary(candidates, decisions, policy, read_json(directory / 'catalog.json'))
    if not summary['route_gate_passed'] or any(report.get(key) != value for key, value in summary.items()):
        raise TopicContractError('GPT-6 route preflight failed')
    return {'manifest_sha256': file_digest(directory / 'manifest.json'),
            'bound_returned_models': summary['bound_returned_models'],
            'route_gate_passed': True, 'route_probe_sequence': sequence}


def require_calibration(directory: Path, binding: dict, fixtures: list, policy: dict,
                        route: dict) -> dict:
    identity, report, decisions = read_package(directory, binding, 'calibration')
    candidates = [core.fixture_candidate(fixture) for fixture in fixtures]
    validate_decisions(candidates, decisions, policy, route['bound_returned_models'])
    summary = core.calibration_summary(fixtures, decisions, policy)
    cases = summary.pop('cases')
    if (identity['route'] != route or not summary['protocol_gate_passed']
            or any(report.get(key) != value for key, value in summary.items())
            or read_json(directory / 'cases.json') != {'cases': cases}):
        raise TopicContractError('GPT-6 synthetic calibration failed')
    return {'manifest_sha256': file_digest(directory / 'manifest.json'),
            'protocol_gate_passed': True, 'kind': 'synthetic_protocol_readiness_only'}


class Requests(legacy.Requests):
    """Use the frozen transport while binding decisions to the GPT-6 protocol."""

    def call(self, batch: dict, candidates: list[dict]) -> tuple[list, dict]:
        judge = self.policy['judges'][batch['judge']]
        payload = legacy.wire_payload(candidates, judge, self.identity['budget'])
        sha = digest({'identity': self.identity, 'batch': batch, 'payload': payload})
        path = self.workspace / (sha + '.json')
        ledger = read_json(self.workspace / 'budget.json') if (self.workspace / 'budget.json').exists() else {'attempts': []}
        reservations = [index for index, row in enumerate(ledger['attempts'])
                        if row['request_digest'] == sha]
        if path.exists():
            record = read_json(path)
            if (record['request_digest'] != sha or record['batch'] != batch
                    or record['payload_sha256'] != digest(payload)
                    or record['raw_body_sha256'] != digest(record['raw_body_base64'])
                    or reservations != [record['reservation']]):
                raise TopicContractError('GPT-6 raw cache or budget reservation changed')
        elif self.circuit.is_set():
            return [], {sample_id: 'model_identity_circuit_open' for sample_id in batch['ids']}
        elif reservations:
            return [], {sample_id: 'interrupted_request_not_redispatched' for sample_id in batch['ids']}
        else:
            estimate = len(self.counter.encode(json.dumps(payload, ensure_ascii=False))) + 256
            try:
                attempt = self.budget.reserve(sha, estimate)
            except TopicContractError as exc:
                if str(exc) != 'review_budget_or_circuit_breaker':
                    raise
                return [], {sample_id: 'budget_exhausted' for sample_id in batch['ids']}
            endpoint = 'v1/responses' if judge['transport'] == 'responses' else 'v1/chat/completions'
            request = Request(urljoin(self.base_url.rstrip('/') + '/', endpoint),
                              data=json.dumps(payload, ensure_ascii=False).encode(), method='POST',
                              headers={'Content-Type': 'application/json',
                                       'Authorization': 'Bearer ' + self.key})
            try:
                with urlopen(request, timeout=self.identity['budget']['request_timeout_seconds']) as response:
                    raw, error = base64.b64encode(response.read()).decode('ascii'), None
            except OSError as exc:
                raw, error = None, type(exc).__name__
            record = {'request_digest': sha, 'batch': batch, 'payload_sha256': digest(payload),
                      'reservation': attempt, 'raw_body_base64': raw,
                      'raw_body_sha256': digest(raw), 'error_type': error}
            _atomic_json(path, record)
        if record['error_type']:
            return [], {sample_id: record['error_type'] for sample_id in batch['ids']}
        try:
            result = core.strict_json(base64.b64decode(record['raw_body_base64'], validate=True))
            if not isinstance(result, dict):
                raise TopicContractError('invalid_provider_JSON')
            usage = result.get('usage', {})
            self.budget.settle(record['reservation'], {key: usage.get(key, usage.get(alias))
                for key, alias in (('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens'))
                if key in usage or alias in usage})
            model = result.get('model')
            if model != self.models[batch['judge']]:
                self.circuit.set()
                return [], {sample_id: 'model_identity_mismatch' for sample_id in batch['ids']}
            decisions, failures = core.decode_response(result, judge, candidates)
        except (ValueError, TypeError, AttributeError) as exc:
            reason = str(exc) if isinstance(exc, TopicContractError) else 'invalid_provider_JSON'
            return [], {sample_id: reason for sample_id in batch['ids']}
        return [{**row, 'judge': batch['judge'], 'request_digest': sha,
                 'review_protocol': METHOD, 'prompt_sha256': digest(PROMPT)}
                for row in decisions], failures


def run_review(*, scope: str, config_path: Path, output_root: Path, controlled_root: Path,
               base_url: str, auth_file: Path, authorization_reference: str,
               confidence_audit: Path, execute: bool = False, preflight: Path | None = None,
               route_probe_sequence: int | None = None) -> dict:
    private_path(output_root, controlled_root)
    if scope not in {'preflight', 'calibration'} or not base_url or not authorization_reference:
        raise TopicContractError('GPT-6 scope, endpoint and authorization required')
    policy = load_config(config_path)
    if ((scope == 'preflight' and (type(route_probe_sequence) is not int
            or not 1 <= route_probe_sequence <= policy['required_consecutive_route_preflights']))
            or (scope == 'calibration' and route_probe_sequence is not None)):
        raise TopicContractError('GPT-6 route probe sequence required only for preflight')
    private_path(confidence_audit, controlled_root)
    audit_receipt = legacy.require_confidence_audit(confidence_audit, policy)
    fixture_path, fixtures = core.load_fixtures(config_path, policy)
    binding = binding_for(policy, fixture_path, base_url)
    route = None
    if scope == 'calibration':
        if preflight is None:
            raise TopicContractError('GPT-6 calibration requires route preflight')
        private_path(preflight, controlled_root)
        route = require_preflight(preflight, binding, policy)
        if read_json(preflight / 'identity.json')['authorization_reference'] != authorization_reference:
            raise TopicContractError('GPT-6 route authority mismatch')
    candidates = [core.fixture_candidate(fixture)
                  for fixture in (PROBES if scope == 'preflight' else fixtures)]
    inputs = {str(confidence_audit / 'manifest.json'): audit_receipt['manifest_sha256']}
    ordered = sorted(candidates, key=lambda row: digest({'seed': policy['seed'],
                                                         'sample_id': row['sample_id']}))
    size = 1 if scope == 'preflight' else policy['batch_size']
    batches = [{'judge': name, 'ids': [row['sample_id'] for row in ordered[start:start + size]],
                'phase': 'first'} for start in range(0, len(ordered), size)
               for name in policy['judges']]
    by_id = {row['sample_id']: row for row in candidates}
    budget = policy[scope]
    counter = tiktoken.get_encoding('o200k_base')
    estimate = sum(len(counter.encode(json.dumps(legacy.wire_payload(
        [by_id[sample_id] for sample_id in batch['ids']], policy['judges'][batch['judge']], budget),
        ensure_ascii=False))) + 256 for batch in batches)
    if (len(batches) > budget['max_requests'] or estimate > budget['max_input_tokens']
            or len(batches) * budget['max_output_tokens'] > budget['max_total_output_tokens']):
        raise TopicContractError('GPT-6 plan exceeds budget')
    identity = {'scope': scope, 'protocol': binding, 'inputs': inputs, 'route': route,
                'route_probe_sequence': route_probe_sequence,
                'population_sha256': digest([row['candidate_sha256'] for row in candidates]),
                'confidence_audit': audit_receipt, 'authorization_reference': authorization_reference,
                'budget': budget, 'batch_plan_sha256': digest(batches)}
    workspace = output_root / ('topic-axes-gpt6-' + scope + '_' + digest(identity)[:20])
    plan = {'status': 'planned', 'workspace': str(workspace), 'scope': scope,
            'population': len(candidates), 'planned_requests': len(batches),
            'estimated_input_tokens': estimate, 'budget': budget, 'route': route,
            'protocol_binding_sha256': digest(binding), **core.GOVERNANCE}
    if not execute:
        return plan
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace.chmod(0o700)
    with (workspace / 'run.lock').open('a') as lock:
        (workspace / 'run.lock').chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (workspace / 'manifest.json').exists():
            manifest = verify_manifest(workspace, {'identity.json', 'report.json', 'decisions.json',
                                                   'cases.json', 'budget.json'})
            if manifest['identity'] != identity:
                raise TopicContractError('GPT-6 replay identity changed')
            return read_json(workspace / 'report.json')
        if (workspace / 'identity.json').exists() and read_json(workspace / 'identity.json') != identity:
            raise TopicContractError('GPT-6 workspace identity changed')
        _atomic_json(workspace / 'identity.json', identity)
        exact_models = route['bound_returned_models'] if route else {
            name: judge['model'] for name, judge in policy['judges'].items()}
        requests = Requests(workspace, identity, policy, base_url, auth_file, exact_models)
        catalog = legacy.catalog_snapshot(workspace, base_url, requests.key) if scope == 'preflight' else None
        if catalog is not None and any(model not in {row.get('id') for row in catalog['data']
                                                      if isinstance(row, dict)}
                                       for model in exact_models.values()):
            raise TopicContractError('GPT-6 reviewer absent from catalog')
        decisions, failed = [], {}

        def call(batch):
            return requests.call(batch, [by_id[sample_id] for sample_id in batch['ids']])

        with ThreadPoolExecutor(max_workers=policy['workers']) as pool:
            for batch, (valid, missing) in zip(batches, pool.map(call, batches), strict=True):
                decisions.extend(valid)
                failed.update({(batch['judge'], sample_id): reason
                               for sample_id, reason in missing.items()})
        decisions.sort(key=lambda row: (row['judge'], row['sample_id']))
        validate_decisions(candidates, decisions, policy, exact_models)
        if scope == 'preflight':
            summary = route_summary(candidates, decisions, policy, catalog)
        else:
            summary = core.calibration_summary(fixtures, decisions, policy)
        cases = summary.pop('cases', [])
        complete = len(decisions) == 2 * len(candidates)
        report = {**plan, **summary, 'status': 'complete' if complete else 'incomplete',
                  'usage': requests.budget.usage(), 'unresolved_response_failures': len(failed),
                  'response_failure_counts': dict(Counter(failed.values())),
                  'decisions_sha256': digest(decisions),
                  'provider_model_revision_attested': False,
                  'model_identity_circuit_open': requests.circuit.is_set(),
                  'draft_exported': False}
        if (binding_for(policy, fixture_path, base_url) != binding
                or load_config(config_path) != policy
                or any(file_digest(Path(path)) != sha for path, sha in inputs.items())):
            raise TopicContractError('GPT-6 bound source changed during execution')
        _atomic_json(workspace / 'decisions.json', {'identity': identity, 'decisions': decisions})
        _atomic_json(workspace / 'cases.json', {'cases': cases})
        _atomic_json(workspace / 'report.json', report)
        names = ['identity.json', 'report.json', 'decisions.json', 'cases.json', 'budget.json']
        names += [path.name for path in workspace.glob('*.json') if len(path.stem) == 64]
        if catalog is not None:
            names.append('catalog.json')
        manifest = {'schema_version': 'topic-axes-review-gpt6-manifest-v1',
                    'identity': identity, **core.GOVERNANCE,
                    'output_digests': {name: file_digest(workspace / name) for name in names}}
        manifest['manifest_sha256'] = digest(manifest)
        _atomic_json(workspace / 'manifest.json', manifest)
        return report
