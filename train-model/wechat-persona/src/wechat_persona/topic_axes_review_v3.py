"""Versioned v3 rubric runner with route probes and per-response identity binding.

The v2 schema, deterministic status calculation, source validation and statistics
remain shared. New decisions explicitly bind the v3 prompt; old evidence is not adopted.
"""
from __future__ import annotations

import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
from pathlib import Path
import threading
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import tiktoken
import yaml

from . import topic_axes_review as v2
from ._common import digest, file_digest
from .fact_review_server import _atomic_json
from .topic_adjudication import verify_manifest
from .topic_candidates import private_path, read_json, write_jsonl
from .topic_context import TopicContractError
from .topic_review_budget import ReviewBudget
from .topic_review_protocol_v3 import AXES_CONTRACT, METHOD, SYSTEM


PROMPT = SYSTEM + v2.LEGEND
PROBES = [
    {'id': 'route-v3-01', 'past': [['self', '展览明天几点开门？']], 'reply': '上午十点开门。'},
    {'id': 'route-v3-02', 'past': [['self', '这盒彩纸留给你做手工。']], 'reply': '谢谢，正好用得上。'},
]
REPAIRABLE = {'invalid_case_axes', 'missing_or_duplicate_case', 'invalid axes JSON',
              'invalid axes envelope', 'incomplete axes response', 'invalid_provider_JSON',
              'TimeoutError', 'URLError', 'HTTPError'}


def load_config(path: Path) -> dict:
    policy = yaml.safe_load(path.read_text(encoding='utf-8'))
    if (policy['schema_version'] != 'topic-axes-review-config-v3' or policy['method'] != METHOD
            or policy['axes_contract'] != AXES_CONTRACT or policy['governance'] != v2.GOVERNANCE
            or policy['batch_size'] != 6 or policy['workers'] != 2
            or {k: policy['calibration'][k] for k in v2.GATE} != v2.GATE
            or {k: policy['private_review'][k] for k in ('population', 'previous_selected', 'previous_pending', 'previous_hard_risks')}
               != {'population': 200, 'previous_selected': 58, 'previous_pending': 142, 'previous_hard_risks': 12}
            or policy['private_review']['max_repair_requests'] != 4 or set(policy['judges']) != {'gpt', 'claude'}):
        raise TopicContractError('invalid frozen v3 policy')
    for name, transport in (('gpt', 'responses'), ('claude', 'chat_completions')):
        judge = policy['judges'][name]
        if (judge['family'] != name or judge['transport'] != transport
                or not judge['model'].startswith(judge['returned_model_prefix'])
                or not judge['returned_model_prefix'].startswith(name + '-')):
            raise TopicContractError('invalid v3 judge')
    for scope, ceilings in (('preflight', (4, 24000, 12000)), ('calibration', (8, 60000, 24000)),
                            ('private_review', (72, 500000, 216000))):
        limits = dict(zip(('max_requests', 'max_input_tokens', 'max_total_output_tokens'), ceilings))
        limits.update(max_output_tokens=3000, request_timeout_seconds=120)
        for key, maximum in limits.items():
            if type(policy[scope][key]) is not int or not 1 <= policy[scope][key] <= maximum:
                raise TopicContractError('invalid v3 budget')
    return policy


def wire_payload(candidates: list[dict], judge: dict, budget: dict) -> dict:
    payload = v2.wire_payload(candidates, judge, budget)
    messages = payload['input' if judge['transport'] == 'responses' else 'messages']
    messages[0]['content'] = PROMPT + json.dumps(v2.wire_schema(v2.BATCH_SCHEMA), ensure_ascii=False)
    return payload


def binding_for(policy: dict, fixture_path: Path, base_url: str) -> dict:
    binding = v2.protocol_binding(policy, fixture_path, base_url)
    binding.update(method=METHOD, axes_contract=AXES_CONTRACT, prompt_sha256=digest(PROMPT), probes_sha256=digest(PROBES))
    for name in ('topic_axes_review_v3.py', 'topic_review_protocol_v3.py'):
        binding['source_sha256'][name] = file_digest(Path(__file__).with_name(name))
    return binding


def validate_v3_decisions(candidates: list[dict], decisions: list[dict], policy: dict, models: dict | None = None) -> None:
    v2.validate_decisions(candidates, decisions, policy)
    for row in decisions:
        if (row.get('review_protocol') != METHOD or row.get('prompt_sha256') != digest(PROMPT)
                or not re_digest(row.get('request_digest'))
                or (models is not None and row['model_returned'] != models[row['judge']])):
            raise TopicContractError('v3 decision prompt or exact model binding mismatch')


def re_digest(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def route_summary(candidates: list[dict], decisions: list[dict], policy: dict, catalog: dict) -> dict:
    validate_v3_decisions(candidates, decisions, policy)
    ids = {row.get('id') for row in catalog.get('data', []) if isinstance(row, dict)}
    counts = {name: dict(Counter(r['model_returned'] for r in decisions if r['judge'] == name)) for name in policy['judges']}
    passed = all(judge['model'] in ids and len(counts[name]) == 1 and sum(counts[name].values()) == 2
                 for name, judge in policy['judges'].items())
    return {'route_gate_passed': passed, 'returned_model_counts': counts,
            'requested_models_listed': {name: judge['model'] in ids for name, judge in policy['judges'].items()},
            'bound_returned_models': {name: next(iter(values)) for name, values in counts.items() if len(values) == 1},
            'catalog_request_count': 1, 'route_stability_proven': False, 'quality_claimed': False}


def read_package(directory: Path, binding: dict, scope: str) -> tuple[dict, dict, list]:
    required = {'identity.json', 'report.json', 'decisions.json', 'cases.json', 'budget.json'}
    if scope == 'preflight':
        required.add('catalog.json')
    manifest = verify_manifest(directory, required)
    identity = read_json(directory / 'identity.json')
    report = read_json(directory / 'report.json')
    record = read_json(directory / 'decisions.json')
    if (manifest['identity'] != identity or identity['scope'] != scope or identity['protocol'] != binding
            or record['identity'] != identity or digest(record['decisions']) != report['decisions_sha256']
            or report['status'] != 'complete' or any(manifest.get(k) != v for k, v in v2.GOVERNANCE.items())):
        raise TopicContractError('v3 prerequisite incomplete or identity mismatch')
    return identity, report, record['decisions']


def require_preflight(directory: Path, binding: dict, policy: dict) -> dict:
    _, report, decisions = read_package(directory, binding, 'preflight')
    summary = route_summary([v2.fixture_candidate(f) for f in PROBES], decisions, policy, read_json(directory / 'catalog.json'))
    if not summary['route_gate_passed'] or any(report.get(k) != v for k, v in summary.items()):
        raise TopicContractError('v3 route preflight failed')
    return {'manifest_sha256': file_digest(directory / 'manifest.json'),
            'bound_returned_models': summary['bound_returned_models'], 'route_gate_passed': True}


def require_calibration(directory: Path, binding: dict, fixtures: list, policy: dict, route: dict) -> dict:
    identity, report, decisions = read_package(directory, binding, 'calibration')
    candidates = [v2.fixture_candidate(f) for f in fixtures]
    validate_v3_decisions(candidates, decisions, policy, route['bound_returned_models'])
    summary = v2.calibration_summary(fixtures, decisions, policy)
    cases = summary.pop('cases')
    if (identity['route'] != route or not summary['protocol_gate_passed']
            or any(report.get(k) != v for k, v in summary.items())
            or read_json(directory / 'cases.json') != {'cases': cases}):
        raise TopicContractError('v3 synthetic gate failed; private review blocked')
    return {'manifest_sha256': file_digest(directory / 'manifest.json'), 'protocol_gate_passed': True,
            'kind': 'synthetic_protocol_readiness_only'}


class Requests:
    """Reserve before dispatch, retain raw bytes, never retry identity drift."""

    def __init__(self, workspace: Path, identity: dict, policy: dict, base_url: str, auth_file: Path, models: dict | None):
        self.workspace, self.identity, self.policy = workspace, identity, policy
        self.base_url, self.models = base_url, models
        self.key = read_json(auth_file)['OPENAI_API_KEY']
        self.budget = ReviewBudget(workspace, identity, identity['budget'])
        self.circuit = threading.Event()
        self.counter = tiktoken.get_encoding('o200k_base')

    def call(self, batch: dict, candidates: list[dict]) -> tuple[list, dict]:
        judge = self.policy['judges'][batch['judge']]
        payload = wire_payload(candidates, judge, self.identity['budget'])
        sha = digest({'identity': self.identity, 'batch': batch, 'payload': payload})
        path = self.workspace / (sha + '.json')
        ledger = read_json(self.workspace / 'budget.json') if (self.workspace / 'budget.json').exists() else {'attempts': []}
        reservations = [i for i, r in enumerate(ledger['attempts']) if r['request_digest'] == sha]
        if path.exists():
            record = read_json(path)
            if (record['request_digest'] != sha or record['batch'] != batch or record['payload_sha256'] != digest(payload)
                    or record['raw_body_sha256'] != digest(record['raw_body_base64'])
                    or reservations != [record['reservation']]):
                raise TopicContractError('v3 raw cache or budget reservation changed')
        elif self.circuit.is_set():
            return [], {sid: 'model_identity_circuit_open' for sid in batch['ids']}
        elif reservations:
            return [], {sid: 'interrupted_request_not_redispatched' for sid in batch['ids']}
        else:
            estimate = len(self.counter.encode(json.dumps(payload, ensure_ascii=False))) + 256
            try:
                attempt = self.budget.reserve(sha, estimate)
            except TopicContractError as exc:
                if str(exc) != 'review_budget_or_circuit_breaker':
                    raise
                return [], {sid: 'budget_exhausted' for sid in batch['ids']}
            endpoint = 'v1/responses' if judge['transport'] == 'responses' else 'v1/chat/completions'
            request = Request(urljoin(self.base_url.rstrip('/') + '/', endpoint),
                              data=json.dumps(payload, ensure_ascii=False).encode(), method='POST',
                              headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.key})
            try:
                with urlopen(request, timeout=self.identity['budget']['request_timeout_seconds']) as response:
                    raw, error = base64.b64encode(response.read()).decode('ascii'), None
            except OSError as exc:
                raw, error = None, type(exc).__name__
            record = {'request_digest': sha, 'batch': batch, 'payload_sha256': digest(payload), 'reservation': attempt,
                      'raw_body_base64': raw, 'raw_body_sha256': digest(raw), 'error_type': error}
            _atomic_json(path, record)
        if record['error_type']:
            return [], {sid: record['error_type'] for sid in batch['ids']}
        try:
            result = v2.strict_json(base64.b64decode(record['raw_body_base64'], validate=True))
            if not isinstance(result, dict):
                raise TopicContractError('invalid_provider_JSON')
            usage = result.get('usage', {})
            self.budget.settle(record['reservation'], {k: usage.get(k, usage.get(alt))
                for k, alt in (('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens')) if k in usage or alt in usage})
            model = result.get('model')
            if (not isinstance(model, str) or not model.startswith(judge['returned_model_prefix'])
                    or (self.models is not None and model != self.models[batch['judge']])):
                self.circuit.set()
                return [], {sid: 'model_identity_mismatch' for sid in batch['ids']}
            decisions, failures = v2.decode_response(result, judge, candidates)
        except (ValueError, TypeError, AttributeError) as exc:
            reason = str(exc) if isinstance(exc, TopicContractError) else 'invalid_provider_JSON'
            return [], {sid: reason for sid in batch['ids']}
        return [{**r, 'judge': batch['judge'], 'request_digest': sha, 'review_protocol': METHOD,
                 'prompt_sha256': digest(PROMPT)} for r in decisions], failures


def catalog_snapshot(workspace: Path, base_url: str, key: str) -> dict:
    path = workspace / 'catalog.json'
    if path.exists():
        return read_json(path)
    request = Request(urljoin(base_url.rstrip('/') + '/', 'v1/models'), headers={'Authorization': 'Bearer ' + key})
    with urlopen(request, timeout=30) as response:
        catalog = v2.strict_json(response.read())
    if not isinstance(catalog, dict) or not isinstance(catalog.get('data'), list):
        raise TopicContractError('invalid gateway catalog')
    _atomic_json(path, catalog)
    return catalog


def run_review(*, scope: str, config_path: Path, output_root: Path, controlled_root: Path,
               base_url: str, auth_file: Path, authorization_reference: str, execute: bool = False,
               preflight: Path | None = None, calibration: Path | None = None, private_inputs: dict | None = None) -> dict:
    private_path(output_root, controlled_root)
    if scope not in {'preflight', 'calibration', 'private_review'} or not base_url or not authorization_reference:
        raise TopicContractError('v3 scope, endpoint and authorization required')
    policy = load_config(config_path)
    fixture_path, fixtures = v2.load_fixtures(config_path, policy)
    binding = binding_for(policy, fixture_path, base_url)
    route, calibration_receipt, inputs, prior = None, None, {}, []
    if scope != 'preflight':
        if preflight is None:
            raise TopicContractError('v3 requires route preflight')
        private_path(preflight, controlled_root)
        route = require_preflight(preflight, binding, policy)
    if scope == 'private_review':
        if calibration is None or private_inputs is None:
            raise TopicContractError('v3 requires calibration and private source bindings')
        private_path(calibration, controlled_root)
        calibration_receipt = require_calibration(calibration, binding, fixtures, policy, route)
        candidates, prior, inputs = v2.load_private_inputs(private_inputs, policy, controlled_root, base_url)
    else:
        candidates = [v2.fixture_candidate(f) for f in (PROBES if scope == 'preflight' else fixtures)]
    for source in [preflight, calibration, *((private_inputs or {}).values())]:
        if source is not None and (output_root.resolve() == source.resolve() or source.resolve() in output_root.resolve().parents):
            raise TopicContractError('v3 output must be separate from sources')
    ordered = sorted(candidates, key=lambda r: digest({'seed': policy['seed'], 'sample_id': r['sample_id']}))
    size = 1 if scope == 'preflight' else policy['batch_size']
    batches = [{'judge': name, 'ids': [r['sample_id'] for r in ordered[start:start + size]], 'phase': 'first'}
               for start in range(0, len(ordered), size) for name in policy['judges']]
    by_id = {r['sample_id']: r for r in candidates}
    budget = policy[scope]
    counter = tiktoken.get_encoding('o200k_base')
    estimate = sum(len(counter.encode(json.dumps(wire_payload([by_id[sid] for sid in b['ids']],
                    policy['judges'][b['judge']], budget), ensure_ascii=False))) + 256 for b in batches)
    if (len(batches) > budget['max_requests'] or estimate > budget['max_input_tokens']
            or len(batches) * budget['max_output_tokens'] > budget['max_total_output_tokens']):
        raise TopicContractError('v3 plan exceeds budget')
    identity = {'scope': scope, 'protocol': binding, 'inputs': inputs, 'route': route, 'calibration': calibration_receipt,
                'population_sha256': digest([r['candidate_sha256'] for r in candidates]), 'prior_sha256': digest(prior),
                'authorization_reference': authorization_reference, 'budget': budget, 'batch_plan_sha256': digest(batches)}
    workspace = output_root / ('topic-axes-v3-' + scope + '_' + digest(identity)[:20])
    plan = {'status': 'planned', 'workspace': str(workspace), 'scope': scope, 'population': len(candidates),
            'planned_requests': len(batches), 'estimated_input_tokens': estimate, 'budget': budget,
            'route': route, 'calibration': calibration_receipt, 'protocol_binding_sha256': digest(binding), **v2.GOVERNANCE}
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
                raise TopicContractError('v3 replay identity changed')
            return read_json(workspace / 'report.json')
        if (workspace / 'identity.json').exists() and read_json(workspace / 'identity.json') != identity:
            raise TopicContractError('v3 workspace identity changed')
        _atomic_json(workspace / 'identity.json', identity)
        requests = Requests(workspace, identity, policy, base_url, auth_file, route['bound_returned_models'] if route else None)
        catalog = catalog_snapshot(workspace, base_url, requests.key) if scope == 'preflight' else None
        if catalog is not None and any(j['model'] not in {r.get('id') for r in catalog['data'] if isinstance(r, dict)}
                                       for j in policy['judges'].values()):
            raise TopicContractError('requested reviewer absent from catalog')
        decisions, failed = [], {}

        def call(batch):
            return requests.call(batch, [by_id[sid] for sid in batch['ids']])

        with ThreadPoolExecutor(max_workers=policy['workers']) as pool:
            for batch, (valid, missing) in zip(batches, pool.map(call, batches), strict=True):
                decisions.extend(valid)
                failed.update({(batch['judge'], sid): reason for sid, reason in missing.items()})
        if scope == 'private_review' and failed and not requests.circuit.is_set():
            repairs = []
            for name in policy['judges']:
                ids = sorted(sid for (judge_name, sid), reason in failed.items() if judge_name == name and reason in REPAIRABLE)
                repairs.extend({'judge': name, 'ids': ids[start:start + size], 'phase': 'repair'} for start in range(0, len(ids), size))
            for batch in repairs[:budget['max_repair_requests']]:
                valid, _ = call(batch)
                decisions.extend(valid)
                for row in valid:
                    failed.pop((row['judge'], row['sample_id']), None)
        decisions.sort(key=lambda r: (r['judge'], r['sample_id']))
        validate_v3_decisions(candidates, decisions, policy, route['bound_returned_models'] if route else None)
        if scope == 'preflight':
            summary = route_summary(candidates, decisions, policy, catalog)
        elif scope == 'calibration':
            summary = v2.calibration_summary(fixtures, decisions, policy)
        else:
            summary = v2.private_summary(candidates, prior, decisions, policy)
        cases = summary.pop('cases', [])
        complete = len(decisions) == 2 * len(candidates)
        report = {**plan, **summary, 'status': 'complete' if complete else 'incomplete', 'usage': requests.budget.usage(),
                  'unresolved_response_failures': len(failed), 'response_failure_counts': dict(Counter(failed.values())),
                  'decisions_sha256': digest(decisions), 'provider_model_revision_attested': False,
                  'model_identity_circuit_open': requests.circuit.is_set(), 'draft_exported': scope == 'private_review' and complete}
        if (binding_for(policy, fixture_path, base_url) != binding or load_config(config_path) != policy
                or any(file_digest(Path(path)) != sha for path, sha in inputs.items())):
            raise TopicContractError('v3 bound source changed during execution')
        _atomic_json(workspace / 'decisions.json', {'identity': identity, 'decisions': decisions})
        _atomic_json(workspace / 'cases.json', {'cases': cases})
        _atomic_json(workspace / 'report.json', report)
        if report['draft_exported']:
            selected = set(summary['selected_sample_ids'])
            write_jsonl(workspace / 'train.draft.jsonl', [r for r in candidates if r['sample_id'] in selected])
        names = ['identity.json', 'report.json', 'decisions.json', 'cases.json', 'budget.json']
        names += [p.name for p in workspace.glob('*.json') if len(p.stem) == 64]
        if catalog is not None:
            names.append('catalog.json')
        if report['draft_exported']:
            names.append('train.draft.jsonl')
        manifest = {'schema_version': 'topic-axes-review-manifest-v3', 'identity': identity, **v2.GOVERNANCE,
                    'output_digests': {name: file_digest(workspace / name) for name in names}}
        manifest['manifest_sha256'] = digest(manifest)
        _atomic_json(workspace / 'manifest.json', manifest)
        return report
