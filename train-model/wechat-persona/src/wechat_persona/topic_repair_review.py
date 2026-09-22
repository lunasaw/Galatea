"""Resumable, bounded dual-model review of a frozen train repair queue."""
from __future__ import annotations

import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import fcntl
import json
from pathlib import Path

import tiktoken
import yaml

from . import topic_axes_review_v4 as base
from ._common import digest, file_digest
from .consent import verify_consent
from .fact_review_server import _atomic_json
from .topic_adjudication import verify_manifest
from .topic_candidates import private_path, read_json, rows, write_jsonl
from .topic_context import TopicContractError
from .topic_repair_protocol import METHOD, decode_response, validate_axes, validate_candidate
from .topic_repair_review_queue import partition_review_queue
from .topic_train_repair import GOVERNANCE
from .topic_validation_budget import GuardedBudget, input_reservation
from .topic_validation_completion import capture_http


REPAIRABLE = base.REPAIRABLE | {'unknown axes case index', 'body_truncated'}
REQUIRED = {'identity.json', 'budget.json', 'decisions.json', 'cases.json', 'report.json', 'circuit.json'}


def load_config(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding='utf-8'))
    if (value['schema_version'] != 'topic-repair-review-config-v1' or value['population'] != 187
            or value['original_population'] != 200 or value['governance'] != GOVERNANCE
            or value['queue_manifest_sha256'] != '93018c7e86ba537fa37161b1f5ca889bc51ea03d428dc455af8beac57882ec7f'
            or value['repair_manifest_sha256'] != 'ece03689c850b5354364f2c14d41b1e99daa8c576f6b0ce0c88d2c1a6d9db316'
            or value['review_config'] != 'topic-axes-review-v4.yaml' or value['seed'] != 47
            or value['workers'] != 4 or value['max_repairs_per_judgment'] != 1
            or value['budget'] != {'max_requests': 748, 'max_repair_requests': 374, 'max_input_tokens': 6500000,
                'max_total_output_tokens': 2992000, 'max_output_tokens': 4000, 'request_timeout_seconds': 240}
            or value['input_guard'] != {'multipliers': {'gpt': 3, 'claude': 2}, 'padding_tokens': 512,
                'headroom_tokens': 20000, 'release_unused_reservations': False, 'stop_on_reservation_exceeded': True}
            or value['transport_circuit_consecutive_errors'] != 3 or value['transport_circuit_statuses'] != [429, 502, 503]):
        raise TopicContractError('invalid frozen repair review configuration')
    return value


def load_sources(queue: Path, repair: Path, consent: Path, config: dict) -> tuple[list, list, dict]:
    if (file_digest(queue / 'manifest.json') != config['queue_manifest_sha256']
            or file_digest(repair / 'manifest.json') != config['repair_manifest_sha256']):
        raise TopicContractError('repair review population changed')
    qm = verify_manifest(queue, {'review.queue.jsonl', 'dispositions.json', 'report.json'})
    rm = verify_manifest(repair, {'selection.json', 'context-comparison.json', 'context.candidates.jsonl', 'policy.json'})
    if qm['identity']['repair_manifest_sha256'] != file_digest(repair / 'manifest.json'):
        raise TopicContractError('queue and repair do not match')
    for manifest in (qm, rm):
        if any(manifest.get(k) != v for k, v in GOVERNANCE.items()):
            raise TopicContractError('repair evidence governance mismatch')
        for name, sha in manifest['identity']['implementation_sha256'].items():
            if file_digest(Path(__file__).with_name(name)) != sha:
                raise TopicContractError('repair implementation changed')
    source_inputs = rm['identity']['inputs']
    for name, sha in source_inputs.items():
        if file_digest(Path(name)) != sha:
            raise TopicContractError('repair source input changed')
    auth = verify_consent(consent, required_purposes={'processing', 'persona_style', 'evaluation'},
                          required_message_types={'text'})
    if source_inputs.get(str(consent)) != auth['consent_file_sha256']:
        raise TopicContractError('repair consent binding changed')
    candidates, dispositions = partition_review_queue(list(rows(repair / 'context.candidates.jsonl')),
        read_json(repair / 'selection.json')['cases'], read_json(repair / 'context-comparison.json')['cases'])
    if (candidates != list(rows(queue / 'review.queue.jsonl'))
            or dispositions != read_json(queue / 'dispositions.json')['cases']
            or len(candidates) != config['population'] or len(dispositions) != config['original_population']
            or [c['candidate_sha256'] for c in candidates] != qm['identity']['queue_digests']):
        raise TopicContractError('repair review queue reconstruction mismatch')
    for candidate in candidates:
        validate_candidate(candidate)
    return candidates, dispositions, {**source_inputs,
        str(queue / 'manifest.json'): file_digest(queue / 'manifest.json'),
        str(repair / 'manifest.json'): file_digest(repair / 'manifest.json')}


def payload_record(identity: dict, batch: dict, candidate: dict) -> tuple[dict, str, int]:
    if batch['ids'] != [candidate['sample_id']] or batch['phase'] not in {'first', 'repair'}:
        raise TopicContractError('repair request must contain one frozen candidate')
    payload = base.wire_payload([candidate], identity['judges'][batch['judge']], identity['budget'])
    sha = digest({'identity': identity, 'batch': batch, 'payload': payload})
    estimate = len(tiktoken.get_encoding('o200k_base').encode(json.dumps(payload, ensure_ascii=False))) + 256
    return payload, sha, estimate


def request_table(identity: dict, candidates: list) -> dict:
    by_id = {c['sample_id']: c for c in candidates}
    table = {}
    for first in identity['first_batches']:
        for phase in ('first', 'repair'):
            batch = {**first, 'phase': phase}
            payload, sha, estimate = payload_record(identity, batch, by_id[batch['ids'][0]])
            table[sha] = {'batch': batch, 'payload_sha256': digest(payload), 'estimate': estimate,
                          'reserve': input_reservation(estimate, batch['judge'], identity['input_guard'])}
    return table


def unpack(raw: dict, identity: dict, candidate: dict) -> tuple[dict | None, str | None, dict]:
    if raw['error_type']:
        return None, raw['error_type'], {}
    usage = {}
    try:
        result = base.core.strict_json(base64.b64decode(raw['raw_body_base64'], validate=True))
        if not isinstance(result, dict):
            raise TopicContractError('invalid_provider_JSON')
        source = result.get('usage', {})
        usage = {k: source.get(k, source.get(alt)) for k, alt in
                 (('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens')) if k in source or alt in source}
        if any(type(v) is not int or v < 0 for v in usage.values()):
            raise TopicContractError('invalid_provider_usage')
        judge = raw['batch']['judge']
        decision = decode_response(result, identity['judges'][judge], candidate, identity['models'][judge])
        return {**decision, 'judge': judge, 'request_digest': raw['request_digest'],
                'review_protocol': METHOD, 'prompt_sha256': digest(base.PROMPT)}, None, usage
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        error = str(exc) if isinstance(exc, TopicContractError) else 'invalid_provider_JSON'
        return None, error, usage if all(type(v) is int and v >= 0 for v in usage.values()) else {}


def reconstruct(workspace: Path, identity: dict, candidates: list, table: dict,
                *, reconcile: bool = False) -> tuple[list, dict, dict]:
    by_id = {r['sample_id']: r for r in candidates}
    budget = GuardedBudget(workspace, identity, table)
    ledger = read_json(workspace / 'budget.json')
    if ledger['identity_sha256'] != digest(identity):
        raise TopicContractError('repair budget identity changed')
    decisions, seen, failures = {}, set(), Counter()
    errors = {(b['judge'], b['ids'][0]): 'not_dispatched' for b in identity['first_batches']}
    circuit, consecutive, repairs, raw_count, exceeded = None, 0, 0, 0, False
    for index, attempt in enumerate(ledger['attempts']):
        sha = attempt['request_digest']
        expected = table.get(sha)
        if (expected is None or sha in seen or attempt.get('input_estimate') != expected['estimate']
                or attempt['input_reserve'] != expected['reserve']
                or attempt['output_reserve'] != identity['budget']['max_output_tokens']):
            raise TopicContractError('repair reservation binding changed')
        seen.add(sha)
        batch = expected['batch']
        key = batch['judge'], batch['ids'][0]
        if key in decisions or (batch['phase'] == 'repair' and errors[key] not in REPAIRABLE):
            raise TopicContractError('repair retried a valid or nonrepairable judgment')
        repairs += batch['phase'] == 'repair'
        path = workspace / (sha + '.json')
        if not path.exists():
            if any(k in attempt for k in ('input_tokens', 'output_tokens')):
                raise TopicContractError('repair usage without raw response')
            errors[key] = 'interrupted_request_not_redispatched'
            continue
        raw = read_json(path)
        raw_count += 1
        if (raw['batch'] != batch or raw['request_digest'] != sha or raw['reservation'] != index
                or raw['payload_sha256'] != expected['payload_sha256']
                or raw['raw_body_sha256'] != digest(raw['raw_body_base64'])):
            raise TopicContractError('repair raw request binding changed')
        row, error, usage = unpack(raw, identity, by_id[key[1]])
        if (any(k in attempt and attempt[k] != v for k, v in usage.items())
                or any(k in attempt and k not in usage for k in ('input_tokens', 'output_tokens'))):
            raise TopicContractError('repair provider usage changed')
        if reconcile and usage:
            budget.settle(index, usage)
        elif any(attempt.get(k) != v for k, v in usage.items()):
            raise TopicContractError('repair usage not settled')
        exceeded |= (usage.get('input_tokens', 0) > expected['reserve']
                     or usage.get('output_tokens', 0) > attempt['output_reserve'])
        consecutive = consecutive + 1 if raw['http_status'] in identity['transport_circuit_statuses'] else 0
        if error == 'model_identity_mismatch':
            circuit = circuit or error
        if consecutive >= identity['transport_circuit_consecutive_errors']:
            circuit = circuit or 'repeated_transport_errors'
        if error:
            errors[key] = error
            failures[error] += 1
        else:
            decisions[key] = row
            errors.pop(key, None)
    ledger = read_json(workspace / 'budget.json')
    if (len(seen) > identity['budget']['max_requests'] or repairs > identity['budget']['max_repair_requests']
            or ledger.get('halt_reason') != ('provider_usage_exceeded_reservation' if exceeded else None)):
        raise TopicContractError('repair budget limit or halt reason changed')
    return sorted(decisions.values(), key=lambda r: (r['judge'], r['sample_id'])), errors, {
        'raw_requests_verified': raw_count, 'repair_requests': repairs,
        'historical_failure_counts': dict(failures), 'usage': budget._usage(ledger),
        'circuit_reason': circuit or ledger.get('halt_reason')}


def summarize(candidates: list, decisions: list, dispositions: list) -> dict:
    indexed = {(r['judge'], r['sample_id']): r for r in decisions}
    if len(indexed) != len(decisions):
        raise TopicContractError('duplicate repair review judgment')
    current = {c['parent_sample_id']: c for c in candidates}
    cases = []
    for previous in dispositions:
        case = deepcopy(previous)
        candidate = current.get(case['parent_sample_id'])
        if candidate:
            pair = [indexed.get((name, candidate['sample_id'])) for name in ('gpt', 'claude')]
            for row in pair:
                if row and any(row.get(k) != v for k, v in validate_axes(candidate, row['axes']).items()):
                    raise TopicContractError('repair decision binding changed')
            shared = sorted(set.intersection(*(set(r['responds_to_ids']) for r in pair))) if all(pair) else []
            status = ('missing_review' if not all(pair) else 'axes_keep_not_agreed' if any(r['status'] != 'keep' for r in pair)
                      else 'reply_anchor_disagreement' if not shared else 'machine_consensus_keep')
            case.update(sample_id=candidate['sample_id'], disposition=status, shared_responds_to_ids=shared,
                        required_context_ids=sorted({mid for r in pair if r for mid in r['required_context_ids']}),
                        decision_sha256={r['judge']: digest(r) for r in pair if r})
        cases.append(case)
    return {'population': len(dispositions), 'review_population': len(candidates), 'reviewed': len(decisions),
            'cases': cases, 'disposition_counts': dict(Counter(c['disposition'] for c in cases)),
            'decision_counts': {j: dict(Counter(r['status'] for r in decisions if r['judge'] == j)) for j in ('gpt', 'claude')},
            'reason_counts': {j: dict(Counter(r['reason'] for r in decisions if r['judge'] == j)) for j in ('gpt', 'claude')},
            'transitions': {s: dict(Counter(c['disposition'] for c in cases if c['previous_disposition'] == s))
                            for s in sorted({c['previous_disposition'] for c in cases})},
            'true_precision_claimed': False, 'p3_accepted': False, 'training_ready': False,
            'old_validation_changed': False, 'test_body_materialized': False}


def source_check(identity: dict) -> None:
    if (any(file_digest(Path(p)) != sha for p, sha in identity['inputs'].items())
            or any(file_digest(Path(__file__).with_name(n)) != sha for n, sha in identity['source_digests'].items())):
        raise TopicContractError('repair review immutable source changed')


def run_review(*, queue: Path, repair: Path, consent: Path, config_path: Path, preflight: Path,
               original_preflight: Path, calibration: Path, output_root: Path, controlled_root: Path,
               base_url: str, auth_file: Path, authorization_reference: str, execute: bool = False) -> dict:
    for path in (queue, repair, consent, preflight, original_preflight, calibration, output_root):
        private_path(path, controlled_root)
    if not authorization_reference:
        raise TopicContractError('repair review requires scoped authority')
    config = load_config(config_path)
    policy_path = config_path.parent / config['review_config']
    policy = base.load_config(policy_path)
    fixture_path, fixtures = base.core.load_fixtures(policy_path, policy)
    binding = base.binding_for(policy, fixture_path, base_url)
    route = base.require_preflight(preflight, binding, policy)
    old_route = base.require_preflight(original_preflight, binding, policy)
    gate = base.require_calibration(calibration, binding, fixtures, policy, old_route)
    if (read_json(preflight / 'identity.json')['authorization_reference'] != authorization_reference
            or route['bound_returned_models'] != old_route['bound_returned_models']):
        raise TopicContractError('repair review current route or authority mismatch')
    candidates, dispositions, inputs = load_sources(queue, repair, consent, config)
    inputs.update({str(p): file_digest(p) for p in (config_path, policy_path, preflight / 'manifest.json',
        original_preflight / 'manifest.json', calibration / 'manifest.json',
        config_path.parent.parent / 'scripts/review_topic_repairs.py')})
    ordered = sorted(candidates, key=lambda c: digest({'seed': config['seed'], 'sample_id': c['sample_id']}))
    identity = {'scope': METHOD, 'inputs': inputs, 'protocol': binding, 'calibration': gate, 'route': route,
                'judges': policy['judges'], 'models': route['bound_returned_models'], 'budget': config['budget'],
                'input_guard': config['input_guard'], 'authorization_reference': authorization_reference,
                'candidate_digests': [c['candidate_sha256'] for c in candidates],
                'first_batches': [{'judge': j, 'ids': [c['sample_id']], 'phase': 'first'} for c in ordered for j in policy['judges']],
                'source_digests': {**binding['source_sha256'], **{name: file_digest(Path(__file__).with_name(name)) for name in (
                    'topic_repair_protocol.py', 'topic_repair_review.py', 'topic_repair_contract.py',
                    'topic_repair_review_queue.py', 'topic_validation_budget.py', 'topic_validation_completion.py')}},
                **{k: config[k] for k in ('transport_circuit_consecutive_errors', 'transport_circuit_statuses', 'workers')}}
    table = request_table(identity, candidates)
    reserved = sum(v['reserve'] for v in table.values())
    if reserved > config['budget']['max_input_tokens'] - config['input_guard']['headroom_tokens']:
        raise TopicContractError('repair worst-case reservation exceeds budget')
    workspace = output_root / ('topic-repair-review_' + digest(identity)[:20])
    if any(workspace == p or p in workspace.parents for p in (queue, repair, preflight, original_preflight, calibration)):
        raise TopicContractError('repair review output overlaps source')
    plan = {'status': 'planned', 'workspace': str(workspace), 'planned_first_requests': len(identity['first_batches']),
            'worst_case_input_reservation': reserved, 'budget': config['budget'], **GOVERNANCE}
    source_check(identity)
    if not execute:
        return plan
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace.chmod(0o700)
    with (workspace / 'run.lock').open('a') as lock:
        (workspace / 'run.lock').chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sealed = (workspace / 'manifest.json').exists()
        if sealed and verify_manifest(workspace, REQUIRED)['identity'] != identity:
            raise TopicContractError('repair review sealed identity changed')
        if (workspace / 'identity.json').exists() and read_json(workspace / 'identity.json') != identity:
            raise TopicContractError('repair review interrupted identity changed')
        if not sealed:
            _atomic_json(workspace / 'identity.json', identity)
            if not (workspace / 'budget.json').exists():
                _atomic_json(workspace / 'budget.json', {'identity_sha256': digest(identity), 'attempts': []})
        decisions, errors, audit = reconstruct(workspace, identity, candidates, table, reconcile=not sealed)
        by_id = {c['sample_id']: c for c in candidates}
        if not sealed:
            budget = GuardedBudget(workspace, identity, table)

            def call(reservation):
                batch, payload, sha, slot = reservation
                endpoint = 'v1/responses' if identity['judges'][batch['judge']]['transport'] == 'responses' else 'v1/chat/completions'
                raw = {**capture_http(payload, base_url=base_url, endpoint=endpoint, auth_file=auth_file,
                                     timeout=config['budget']['request_timeout_seconds']),
                       'request_digest': sha, 'batch': batch, 'payload_sha256': digest(payload), 'reservation': slot}
                _atomic_json(workspace / (sha + '.json'), raw)
                _, _, usage = unpack(raw, identity, by_id[batch['ids'][0]])
                budget.settle(slot, usage)

            def collect(batches):
                # Reserve in dispatch order; only one bounded wave is in flight.
                for start in range(0, len(batches), config['workers']):
                    _, _, state = reconstruct(workspace, identity, candidates, table, reconcile=True)
                    if state['circuit_reason']:
                        break
                    dispatched = {a['request_digest'] for a in read_json(workspace / 'budget.json')['attempts']}
                    wave = []
                    for batch in batches[start:start + config['workers']]:
                        payload, sha, estimate = payload_record(identity, batch, by_id[batch['ids'][0]])
                        if sha not in dispatched:
                            wave.append((batch, payload, sha, budget.reserve(sha, estimate)))
                    with ThreadPoolExecutor(max_workers=config['workers']) as pool:
                        list(pool.map(call, wave))
                    done, pending, state = reconstruct(workspace, identity, candidates, table, reconcile=True)
                    _atomic_json(workspace / 'progress.json', {'reviewed': len(done), 'expected': len(candidates) * 2,
                        'pending': len(pending), 'requests': state['usage']['requests'], 'circuit_reason': state['circuit_reason']})

            collect(identity['first_batches'])
            decisions, errors, audit = reconstruct(workspace, identity, candidates, table, reconcile=True)
            if not audit['circuit_reason']:
                collect([{'judge': j, 'ids': [sid], 'phase': 'repair'} for (j, sid), error in sorted(errors.items())
                         if error in REPAIRABLE][:config['budget']['max_repair_requests']])
            decisions, errors, audit = reconstruct(workspace, identity, candidates, table, reconcile=True)
        summary = summarize(candidates, decisions, dispositions)
        cases = summary.pop('cases')
        complete = len(decisions) == 2 * len(candidates) and not audit['circuit_reason']
        report = {**plan, **summary, **audit, 'status': 'complete' if complete else 'incomplete',
                  'response_failure_counts': dict(Counter(errors.values())), 'decisions_sha256': digest(decisions),
                  'machine_review_completed': complete, 'provider_model_revision_attested': False,
                  'provider_usage_bound_attested': False, 'draft_exported': False}
        outputs = {'decisions.json': {'identity': identity, 'decisions': decisions}, 'cases.json': {'cases': cases},
                   'report.json': report, 'circuit.json': {'reason': audit['circuit_reason']}}
        source_check(identity)
        if sealed:
            if any(read_json(workspace / name) != value for name, value in outputs.items()):
                raise TopicContractError('repair review replay differs from raw responses')
        else:
            for name, value in outputs.items():
                _atomic_json(workspace / name, value)
            names = REQUIRED | {p.name for p in workspace.glob('*.json') if len(p.stem) == 64}
            manifest = {'schema_version': 'topic-repair-review-manifest-v1', 'identity': identity, **GOVERNANCE,
                        'output_digests': {n: file_digest(workspace / n) for n in sorted(names)}}
            manifest['manifest_sha256'] = digest(manifest)
            _atomic_json(workspace / 'manifest.json', manifest)
        return report
