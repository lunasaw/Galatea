"""Bounded review of the immutable validation packet, without training evidence."""
from __future__ import annotations

import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import tiktoken
import yaml

from . import topic_axes_review_v4 as base
from . import topic_validation as preparation
from ._common import digest, file_digest
from .consent import verify_consent
from .fact_review_server import _atomic_json
from .topic_adjudication import verify_manifest
from .topic_candidates import private_path, read_json, rows
from .topic_context import TopicContractError
from .topic_review_budget import ReviewBudget
from .topic_validation_protocol import METHOD, decode_response, validate_axes, validate_candidate


GOVERNANCE = base.core.GOVERNANCE
REPAIRABLE = base.REPAIRABLE | {'unknown axes case index'}


def load_config(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding='utf-8'))
    expected = {'schema_version': 'topic-validation-review-config-v1',
        'preparation_config': 'topic-validation-v1.yaml', 'review_config': 'topic-axes-review-v4.yaml',
        'packet_manifest_sha256': 'afe3e682a9da34007921991c3f34ad228d398612d9a74c4dfee7bb66558be335',
        'population': 200, 'repair_order': 'judge_then_sample_id', 'max_repairs_per_judgment': 1,
        'transport_circuit_consecutive_errors': 3, 'transport_circuit_statuses': [429, 502],
        'governance': GOVERNANCE}
    if value != expected:
        raise TopicContractError('invalid frozen validation review config')
    return value


def load_packet(packet: Path, config: dict, preparation_policy: dict, binding: dict,
                consent: Path) -> tuple[dict, list, dict]:
    if file_digest(packet / 'manifest.json') != config['packet_manifest_sha256']:
        raise TopicContractError('validation packet is not the frozen population')
    manifest = verify_manifest(packet, {'identity.json', 'report.json', 'policy.json', 'candidate-schema.json',
        'validation.candidates.jsonl', 'selection.json', 'source-audit.json', 'protocol.md', 'evidence.jsonl'})
    identity = manifest['identity']
    if (identity != read_json(packet / 'identity.json') or identity['review_protocol'] != binding
            or read_json(packet / 'policy.json') != preparation_policy
            or read_json(packet / 'candidate-schema.json') != preparation.validation_schema()
            or any(manifest.get(k) != v for k, v in GOVERNANCE.items())):
        raise TopicContractError('validation packet protocol or governance mismatch')
    for name, sha in identity['source_digests'].items():
        if file_digest(Path(__file__).with_name(name)) != sha:
            raise TopicContractError('validation preparation source changed')
    for path, sha in identity['input_digests'].items():
        if file_digest(Path(path)) != sha:
            raise TopicContractError('validation preparation input changed')
    auth = verify_consent(consent, required_purposes={'processing', 'persona_style', 'evaluation'},
                          required_message_types={'text'})
    if read_json(packet / 'source-audit.json')['consent_file_sha256'] != auth['consent_file_sha256']:
        raise TopicContractError('validation consent binding changed')
    candidates = list(rows(packet / 'validation.candidates.jsonl'))
    selection = read_json(packet / 'selection.json')
    if (len(candidates) != config['population'] or len({r['sample_id'] for r in candidates}) != len(candidates)
            or [r['sample_id'] for r in candidates] != selection['sample_ids']
            or [r['candidate_sha256'] for r in candidates] != identity['candidate_digests']):
        raise TopicContractError('validation population/order changed')
    for candidate in candidates:
        validate_candidate(candidate)
        if preparation.strata_for(candidate) != selection['strata_membership'][candidate['sample_id']]:
            raise TopicContractError('validation stratum membership changed')
    return manifest, candidates, selection['strata_membership']


def summarize(candidates: list, decisions: list, strata: dict, policy: dict, models: dict) -> dict:
    by_id = {r['sample_id']: r for r in candidates}
    indexed = {}
    for row in decisions:
        key = row['judge'], row['sample_id']
        if key in indexed or key[0] not in models or key[1] not in by_id:
            raise TopicContractError('unexpected validation judgment population')
        expected = validate_axes(by_id[key[1]], row['axes'])
        if (any(row.get(k) != v for k, v in expected.items()) or row['model_returned'] != models[key[0]]
                or row['model_requested'] != models[key[0]] or row['requested_family'] != key[0]
                or row['review_protocol'] != METHOD or row['prompt_sha256'] != digest(base.PROMPT)
                or not base.re_digest(row.get('request_digest'))):
            raise TopicContractError('validation judgment/source binding changed')
        indexed[key] = row
    cases = []
    for candidate in candidates:
        sid = candidate['sample_id']
        pair = [indexed.get((judge, sid)) for judge in models]
        shared = sorted(set.intersection(*(set(r['responds_to_ids']) for r in pair))) if all(pair) else []
        if not all(pair):
            disposition = 'missing_review'
        elif any(r['status'] != 'keep' for r in pair):
            disposition = 'axes_keep_not_agreed'
        elif not shared:
            disposition = 'reply_anchor_disagreement'
        else:
            disposition = 'machine_consensus_keep'
        names = set(strata[sid])
        if any(r and r['axes']['confidence'] < policy['strata']['low_confidence_threshold'] for r in pair):
            names.add('low_confidence')
        cases.append({'sample_id': sid, 'candidate_sha256': candidate['candidate_sha256'], 'strata': sorted(names),
            'disposition': disposition, 'shared_responds_to_ids': shared,
            'decision_sha256': {name: digest(indexed[(name, sid)]) for name in models if (name, sid) in indexed},
            **GOVERNANCE})
    names = sorted(set(policy['strata']['required']) | {n for r in cases for n in r['strata']})
    stats = {name: {'population': sum(name in r['strata'] for r in cases),
                   'disposition_counts': dict(Counter(r['disposition'] for r in cases if name in r['strata']))}
             for name in names}
    return {'population': len(candidates), 'reviewed': len(decisions), 'cases': cases,
        'decision_counts': {name: dict(Counter(r['status'] for r in decisions if r['judge'] == name)) for name in models},
        'reason_counts': {name: dict(Counter(r['reason'] for r in decisions if r['judge'] == name)) for name in models},
        'disposition_counts': dict(Counter(r['disposition'] for r in cases)), 'strata': stats,
        'insufficient_strata': [n for n in policy['strata']['required'] if stats[n]['population'] < policy['strata']['minimum_count']],
        'low_confidence_membership_complete': len(decisions) == len(candidates) * len(models),
        'reference_review_completed': False, 'near_duplicate_audit_completed': False,
        'true_precision_claimed': False, 'quality_gate_passed': False, 'p3_accepted': False,
        'training_ready': False, 'draft_exported': False, 'test_body_materialized': False,
        'provider_model_revision_attested': False}


def unpack(raw: dict, identity: dict, candidate: dict) -> tuple[dict | None, str | None, dict]:
    if raw['error_type']:
        return None, raw['error_type'], {}
    usage = {}
    try:
        result = base.core.strict_json(base64.b64decode(raw['raw_body_base64'], validate=True))
        if not isinstance(result, dict):
            raise TopicContractError('invalid_provider_JSON')
        provider_usage = result.get('usage', {})
        usage = {key: provider_usage.get(key, provider_usage.get(alt)) for key, alt in
                 (('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens'))
                 if key in provider_usage or alt in provider_usage}
        if any(type(v) is not int or v < 0 for v in usage.values()):
            raise TopicContractError('invalid_provider_usage')
        judge = raw['batch']['judge']
        decision = decode_response(result, identity['judges'][judge], candidate, identity['models'][judge])
        return {**decision, 'judge': judge, 'request_digest': raw['request_digest'],
                'review_protocol': METHOD, 'prompt_sha256': digest(base.PROMPT)}, None, usage
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        reason = str(exc) if isinstance(exc, TopicContractError) else 'invalid_provider_JSON'
        return None, reason, usage if all(type(v) is int and v >= 0 for v in usage.values()) else {}


def payload_record(identity: dict, batch: dict, candidate: dict) -> tuple[dict, str, int]:
    if batch['ids'] != [candidate['sample_id']] or batch['phase'] not in {'first', 'repair'}:
        raise TopicContractError('validation requests must have one frozen candidate')
    payload = preparation.validation_payload(candidate, identity['judges'][batch['judge']], identity['budget'])
    sha = digest({'identity': identity, 'batch': batch, 'payload': payload})
    estimate = len(tiktoken.get_encoding('o200k_base').encode(json.dumps(payload, ensure_ascii=False))) + 256
    return payload, sha, estimate


class Requests:
    def __init__(self, workspace: Path, identity: dict, base_url: str, auth_file: Path):
        self.workspace, self.identity, self.base_url = workspace, identity, base_url
        self.key = read_json(auth_file)['OPENAI_API_KEY']
        self.budget = ReviewBudget(workspace, identity, identity['budget'])
        self.lock, self.circuit = threading.Lock(), threading.Event()
        self.state = {'reason': None, 'consecutive_transport_errors': 0}
        if (workspace / 'circuit.json').exists():
            self.state = read_json(workspace / 'circuit.json')
        # A crash may happen after raw persistence but before circuit persistence.
        for path in workspace.glob('*.json'):
            if len(path.stem) != 64:
                continue
            raw = read_json(path)
            if raw['error_type']:
                continue
            try:
                result = base.core.strict_json(base64.b64decode(raw['raw_body_base64'], validate=True))
            except (ValueError, TypeError):
                continue
            if isinstance(result, dict) and result.get('model') != identity['models'][raw['batch']['judge']]:
                self.state['reason'] = 'model_identity_mismatch'
        if self.state['reason']:
            self.circuit.set()
        _atomic_json(workspace / 'circuit.json', self.state)

    def call(self, batch: dict, candidate: dict) -> tuple[dict | None, str | None]:
        payload, sha, estimate = payload_record(self.identity, batch, candidate)
        path = self.workspace / (sha + '.json')
        ledger = read_json(self.workspace / 'budget.json') if (self.workspace / 'budget.json').exists() else {'attempts': []}
        reservations = [i for i, r in enumerate(ledger['attempts']) if r['request_digest'] == sha]
        cached = path.exists()
        if cached:
            raw = read_json(path)
            if (raw['request_digest'] != sha or raw['batch'] != batch or raw['payload_sha256'] != digest(payload)
                    or raw['raw_body_sha256'] != digest(raw['raw_body_base64']) or reservations != [raw['reservation']]):
                raise TopicContractError('validation raw response/reservation changed')
        elif self.circuit.is_set():
            return None, 'circuit_open'
        elif reservations:
            return None, 'interrupted_request_not_redispatched'
        else:
            try:
                reservation = self.budget.reserve(sha, estimate)
            except TopicContractError as exc:
                if str(exc) != 'review_budget_or_circuit_breaker':
                    raise
                return None, 'budget_exhausted'
            endpoint = 'v1/responses' if self.identity['judges'][batch['judge']]['transport'] == 'responses' else 'v1/chat/completions'
            request = Request(urljoin(self.base_url.rstrip('/') + '/', endpoint),
                data=json.dumps(payload, ensure_ascii=False).encode(), method='POST',
                headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.key})
            status, error, body = None, None, None
            try:
                with urlopen(request, timeout=self.identity['budget']['request_timeout_seconds']) as response:
                    body = base64.b64encode(response.read()).decode('ascii')
            except HTTPError as exc:
                status, error = exc.code, 'HTTPError'
            except OSError as exc:
                error = type(exc).__name__
            raw = {'request_digest': sha, 'batch': batch, 'payload_sha256': digest(payload), 'reservation': reservation,
                   'raw_body_base64': body, 'raw_body_sha256': digest(body), 'error_type': error, 'http_status': status}
            _atomic_json(path, raw)
        decision, error, usage = unpack(raw, self.identity, candidate)
        if usage:
            self.budget.settle(raw['reservation'], usage)
        with self.lock:
            if error == 'model_identity_mismatch':
                self.state['reason'] = error
            if not cached:
                if raw['http_status'] in self.identity['transport_circuit_statuses']:
                    self.state['consecutive_transport_errors'] += 1
                else:
                    self.state['consecutive_transport_errors'] = 0
                if self.state['consecutive_transport_errors'] >= self.identity['transport_circuit_consecutive_errors']:
                    self.state['reason'] = 'repeated_transport_errors'
            if self.state['reason']:
                self.circuit.set()
            _atomic_json(self.workspace / 'circuit.json', self.state)
        return decision, error


def reconstruct(workspace: Path, identity: dict, candidates: list) -> tuple[list, dict, dict]:
    """Read-only reconstruction from raw responses and dispatch reservations."""
    by_id = {r['sample_id']: r for r in candidates}
    ledger = read_json(workspace / 'budget.json')
    if ledger['identity_sha256'] != digest(identity):
        raise TopicContractError('validation budget identity mismatch')
    decisions, errors, counts, seen, phases = {}, {}, Counter(), set(), {}
    allowed = {(r['judge'], r['ids'][0]) for r in identity['first_batches']}
    expected_requests = {}
    for batch in identity['first_batches']:
        for phase in ('first', 'repair'):
            current = {**batch, 'phase': phase}
            _, sha, estimate = payload_record(identity, current, by_id[batch['ids'][0]])
            expected_requests[sha] = current, estimate
    repair_count = 0
    for i, attempt in enumerate(ledger['attempts']):
        sha = attempt['request_digest']
        if sha not in expected_requests or sha in seen:
            raise TopicContractError('unknown or duplicate validation reservation')
        reserved_batch, reserved_estimate = expected_requests[sha]
        if attempt['input_reserve'] != reserved_estimate or attempt['output_reserve'] != identity['budget']['max_output_tokens']:
            raise TopicContractError('validation reservation amount changed')
        if reserved_batch['phase'] == 'repair':
            repair_count += 1
        seen.add(sha)
        path = workspace / (sha + '.json')
        if not path.exists():
            # Reserved calls interrupted before persistence are not sent again.
            errors[(reserved_batch['judge'], reserved_batch['ids'][0])] = 'interrupted_request_not_redispatched'
            continue
        raw = read_json(path)
        batch = raw['batch']
        key = batch['judge'], batch['ids'][0]
        if key not in allowed or key in decisions or (key, batch['phase']) in phases:
            raise TopicContractError('validation requested unknown or already valid judgment')
        if batch['phase'] == 'repair' and (key not in errors or errors[key] not in REPAIRABLE):
            raise TopicContractError('validation repaired a nonrepairable judgment')
        payload, expected_sha, estimate = payload_record(identity, batch, by_id[key[1]])
        if (sha != expected_sha or raw['request_digest'] != sha or raw['payload_sha256'] != digest(payload)
                or raw['raw_body_sha256'] != digest(raw['raw_body_base64']) or raw['reservation'] != i
                or attempt['input_reserve'] != estimate or attempt['output_reserve'] != identity['budget']['max_output_tokens']):
            raise TopicContractError('validation raw request binding changed')
        phases[(key, batch['phase'])] = True
        row, error, usage = unpack(raw, identity, by_id[key[1]])
        if any(attempt.get(k) != v for k, v in usage.items()) or any(k in attempt and k not in usage for k in ('input_tokens', 'output_tokens')):
            raise TopicContractError('validation provider usage changed')
        if error:
            errors[key] = error
            counts[error] += 1
        else:
            decisions[key] = row
            errors.pop(key, None)
    if len(ledger['attempts']) > identity['budget']['max_requests'] or repair_count > identity['budget']['max_repair_requests']:
        raise TopicContractError('validation request count exceeds frozen budget')
    return sorted(decisions.values(), key=lambda r: (r['judge'], r['sample_id'])), errors, {
        'raw_requests_verified': sum((workspace / (sha + '.json')).exists() for sha in seen),
        'historical_response_failure_counts': dict(counts),
        'usage': ReviewBudget._usage(ledger)}


def run_review(*, packet: Path, config_path: Path, consent: Path, preflight: Path, original_preflight: Path,
               calibration: Path, output_root: Path, controlled_root: Path, base_url: str,
               auth_file: Path, authorization_reference: str, execute: bool = False) -> dict:
    for path in (packet, consent, preflight, original_preflight, calibration, output_root):
        private_path(path, controlled_root)
    if not authorization_reference or not base_url:
        raise TopicContractError('validation review requires explicit route and authority')
    config = load_config(config_path)
    preparation_path = config_path.parent / config['preparation_config']
    policy = preparation.load_policy(preparation_path)
    review_path = config_path.parent / config['review_config']
    review_policy = base.load_config(review_path)
    fixture_path, fixtures = base.core.load_fixtures(review_path, review_policy)
    binding = base.binding_for(review_policy, fixture_path, base_url)
    route = base.require_preflight(preflight, binding, review_policy)
    preflight_identity = read_json(preflight / 'identity.json')
    if preflight_identity['authorization_reference'] != authorization_reference:
        raise TopicContractError('validation requires current authorization-bound route preflight')
    old_route = base.require_preflight(original_preflight, binding, review_policy)
    gate = base.require_calibration(calibration, binding, fixtures, review_policy, old_route)
    if route['bound_returned_models'] != old_route['bound_returned_models']:
        raise TopicContractError('validation route changed from calibrated models')
    manifest, candidates, strata = load_packet(packet, config, policy, binding, consent)
    budget = policy['prospective_review']
    models = route['bound_returned_models']
    first = [{'judge': name, 'ids': [row['sample_id']], 'phase': 'first'}
             for row in candidates for name in sorted(models)]
    inputs = {str(p): file_digest(p) for p in (config_path, preparation_path, review_path, consent,
        packet / 'manifest.json', preflight / 'manifest.json', original_preflight / 'manifest.json', calibration / 'manifest.json')}
    sources = {name: file_digest(Path(__file__).with_name(name)) for name in
               ('topic_validation_review.py', 'topic_validation_protocol.py', 'topic_validation.py')}
    sources.update(manifest['identity']['source_digests'])
    identity = {'scope': METHOD, 'protocol': binding, 'inputs': inputs, 'source_digests': sources,
        'budget': budget, 'judges': review_policy['judges'], 'models': models, 'route': route, 'calibration': gate,
        'first_batches': first, 'population_sha256': digest([r['candidate_sha256'] for r in candidates]),
        'authorization_reference': authorization_reference,
        'transport_circuit_consecutive_errors': config['transport_circuit_consecutive_errors'],
        'transport_circuit_statuses': config['transport_circuit_statuses']}
    by_id = {row['sample_id']: row for row in candidates}
    estimate = sum(payload_record(identity, batch, by_id[batch['ids'][0]])[2] for batch in first)
    if len(first) > budget['max_requests'] or estimate > budget['max_input_tokens']:
        raise TopicContractError('validation first pass exceeds budget')
    workspace = output_root / ('topic-validation-review_' + digest(identity)[:20])
    for parent in (packet, preflight, original_preflight, calibration):
        if workspace.resolve() == parent.resolve() or parent.resolve() in workspace.resolve().parents:
            raise TopicContractError('validation output overlaps sources')
    plan = {'status': 'planned', 'workspace': str(workspace), 'population': len(candidates),
            'planned_first_requests': len(first), 'estimated_first_input_tokens': estimate, 'budget': budget,
            'packet_manifest_sha256': config['packet_manifest_sha256'], **GOVERNANCE}
    if not execute:
        return plan
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace.chmod(0o700)
    with (workspace / 'run.lock').open('a') as lock:
        (workspace / 'run.lock').chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (workspace / 'manifest.json').exists():
            saved = verify_manifest(workspace, {'identity.json', 'report.json', 'decisions.json', 'cases.json', 'budget.json', 'circuit.json'})
            if saved['identity'] != identity:
                raise TopicContractError('validation replay identity mismatch')
            restored, _, audit = reconstruct(workspace, identity, candidates)
            report = read_json(workspace / 'report.json')
            summary = summarize(candidates, restored, strata, policy, models)
            cases = summary.pop('cases')
            if (restored != read_json(workspace / 'decisions.json')['decisions']
                    or any(report.get(k) != v for k, v in {**summary, **audit}.items())
                    or read_json(workspace / 'cases.json') != {'cases': cases}):
                raise TopicContractError('validation replay report/raw mismatch')
            return report
        if (workspace / 'identity.json').exists() and read_json(workspace / 'identity.json') != identity:
            raise TopicContractError('validation interrupted workspace changed')
        _atomic_json(workspace / 'identity.json', identity)
        requests = Requests(workspace, identity, base_url, auth_file)
        by_id = {r['sample_id']: r for r in candidates}
        decisions, failed = [], {}

        def collect(batches):
            def call(batch):
                return requests.call(batch, by_id[batch['ids'][0]])
            with ThreadPoolExecutor(max_workers=budget['workers']) as pool:
                for batch, (row, error) in zip(batches, pool.map(call, batches), strict=True):
                    key = batch['judge'], batch['ids'][0]
                    if row:
                        decisions.append(row)
                        failed.pop(key, None)
                    else:
                        failed[key] = error

        collect(first)
        if not requests.circuit.is_set():
            repair = [{'judge': name, 'ids': [sid], 'phase': 'repair'} for (name, sid), reason in sorted(failed.items())
                      if reason in REPAIRABLE][:budget['max_repair_requests']]
            collect(repair)
        decisions.sort(key=lambda r: (r['judge'], r['sample_id']))
        restored, _, audit = reconstruct(workspace, identity, candidates)
        if restored != decisions:
            raise TopicContractError('validation execution differs from raw reconstruction')
        summary = summarize(candidates, decisions, strata, policy, models)
        cases = summary.pop('cases')
        complete = len(decisions) == len(first)
        report = {**plan, **summary, **audit, 'status': 'complete' if complete else 'incomplete',
            'machine_review_completed': complete, 'unresolved_response_failures': len(failed),
            'response_failure_counts': dict(Counter(failed.values())), 'decisions_sha256': digest(decisions),
            'circuit_reason': requests.state['reason']}
        if (any(file_digest(Path(p)) != sha for p, sha in inputs.items())
                or any(file_digest(Path(__file__).with_name(n)) != sha for n, sha in sources.items())):
            raise TopicContractError('validation review source changed during execution')
        _atomic_json(workspace / 'decisions.json', {'identity': identity, 'decisions': decisions})
        _atomic_json(workspace / 'cases.json', {'cases': cases})
        _atomic_json(workspace / 'report.json', report)
        names = ['identity.json', 'report.json', 'decisions.json', 'cases.json', 'budget.json', 'circuit.json']
        names += [p.name for p in workspace.glob('*.json') if len(p.stem) == 64]
        saved = {'schema_version': 'topic-validation-review-manifest-v1', 'identity': identity, **GOVERNANCE,
                 'output_digests': {name: file_digest(workspace / name) for name in names}}
        saved['manifest_sha256'] = digest(saved)
        _atomic_json(workspace / 'manifest.json', saved)
        return report
