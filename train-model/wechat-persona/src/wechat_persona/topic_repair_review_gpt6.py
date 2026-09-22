"""From-scratch repaired-candidate review under the calibrated GPT-6 protocol."""
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

from . import topic_axes_review_gpt6 as base
from . import topic_repair_review as legacy
from ._common import digest, file_digest
from .fact_review_server import _atomic_json
from .topic_adjudication import verify_manifest
from .topic_candidates import private_path, read_json
from .topic_context import TopicContractError
from .topic_repair_protocol_gpt6 import METHOD, decode_response, validate_axes
from .topic_validation_budget import GuardedBudget, input_reservation
from .topic_validation_completion import capture_http


GOVERNANCE = legacy.GOVERNANCE
REPAIRABLE = legacy.REPAIRABLE
REQUIRED = legacy.REQUIRED


def load_config(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding='utf-8'))
    expected = {
        'schema_version': 'topic-repair-review-gpt6-config-v1',
        'queue_manifest_sha256': '93018c7e86ba537fa37161b1f5ca889bc51ea03d428dc455af8beac57882ec7f',
        'repair_manifest_sha256': 'ece03689c850b5354364f2c14d41b1e99daa8c576f6b0ce0c88d2c1a6d9db316',
        'review_config': 'topic-axes-review-v4-gpt6.yaml',
        'population': 187,
        'original_population': 200,
        'seed': 47,
        'required_consecutive_route_preflights': 3,
        'inherited_judgments': 0,
        'require_exact_models': True,
        'budget': {'max_requests': 748, 'max_repair_requests': 374,
                   'max_input_tokens': 6500000, 'max_total_output_tokens': 2992000,
                   'max_output_tokens': 4000, 'request_timeout_seconds': 240},
        'input_guard': {'multipliers': {'gpt': 3, 'claude': 2}, 'padding_tokens': 512,
                        'headroom_tokens': 20000, 'release_unused_reservations': False,
                        'stop_on_reservation_exceeded': True},
        'workers': 2,
        'max_repairs_per_judgment': 1,
        'transport_circuit_consecutive_errors': 3,
        'transport_circuit_statuses': [429, 502, 503],
        'governance': GOVERNANCE,
    }
    if value != expected:
        raise TopicContractError('invalid frozen GPT-6 repair review configuration')
    return value


def payload_record(identity: dict, batch: dict, candidate: dict) -> tuple[dict, str, int]:
    if batch['ids'] != [candidate['sample_id']] or batch['phase'] not in {'first', 'repair'}:
        raise TopicContractError('GPT-6 repair request must contain one frozen candidate')
    payload = base.legacy.wire_payload([candidate], identity['judges'][batch['judge']], identity['budget'])
    sha = digest({'identity': identity, 'batch': batch, 'payload': payload})
    estimate = len(tiktoken.get_encoding('o200k_base').encode(
        json.dumps(payload, ensure_ascii=False))) + 256
    return payload, sha, estimate


def request_table(identity: dict, candidates: list) -> dict:
    by_id = {candidate['sample_id']: candidate for candidate in candidates}
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
        usage = {key: source.get(key, source.get(alias)) for key, alias in
                 (('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens'))
                 if key in source or alias in source}
        if any(type(value) is not int or value < 0 for value in usage.values()):
            raise TopicContractError('invalid_provider_usage')
        judge = raw['batch']['judge']
        decision = decode_response(result, identity['judges'][judge], candidate,
                                   identity['models'][judge])
        return {**decision, 'judge': judge, 'request_digest': raw['request_digest'],
                'review_protocol': METHOD, 'prompt_sha256': digest(base.PROMPT)}, None, usage
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        error = str(exc) if isinstance(exc, TopicContractError) else 'invalid_provider_JSON'
        return None, error, usage if all(type(value) is int and value >= 0
                                         for value in usage.values()) else {}


def reconstruct(workspace: Path, identity: dict, candidates: list, table: dict,
                *, reconcile: bool = False) -> tuple[list, dict, dict]:
    by_id = {candidate['sample_id']: candidate for candidate in candidates}
    budget = GuardedBudget(workspace, identity, table)
    ledger = read_json(workspace / 'budget.json')
    if ledger['identity_sha256'] != digest(identity):
        raise TopicContractError('GPT-6 repair budget identity changed')
    decisions, seen, failures = {}, set(), Counter()
    errors = {(batch['judge'], batch['ids'][0]): 'not_dispatched'
              for batch in identity['first_batches']}
    circuit, consecutive, repairs, raw_count, exceeded = None, 0, 0, 0, False
    for index, attempt in enumerate(ledger['attempts']):
        sha = attempt['request_digest']
        expected = table.get(sha)
        if (expected is None or sha in seen or attempt.get('input_estimate') != expected['estimate']
                or attempt['input_reserve'] != expected['reserve']
                or attempt['output_reserve'] != identity['budget']['max_output_tokens']):
            raise TopicContractError('GPT-6 repair reservation binding changed')
        seen.add(sha)
        batch = expected['batch']
        key = batch['judge'], batch['ids'][0]
        if key in decisions or (batch['phase'] == 'repair' and errors[key] not in REPAIRABLE):
            raise TopicContractError('GPT-6 repair retried a valid or nonrepairable judgment')
        repairs += batch['phase'] == 'repair'
        path = workspace / (sha + '.json')
        if not path.exists():
            if any(name in attempt for name in ('input_tokens', 'output_tokens')):
                raise TopicContractError('GPT-6 repair usage without raw response')
            errors[key] = 'interrupted_request_not_redispatched'
            continue
        raw = read_json(path)
        raw_count += 1
        if (raw['batch'] != batch or raw['request_digest'] != sha
                or raw['reservation'] != index
                or raw['payload_sha256'] != expected['payload_sha256']
                or raw['raw_body_sha256'] != digest(raw['raw_body_base64'])):
            raise TopicContractError('GPT-6 raw request binding changed')
        row, error, usage = unpack(raw, identity, by_id[key[1]])
        if (any(name in attempt and attempt[name] != value for name, value in usage.items())
                or any(name in attempt and name not in usage
                       for name in ('input_tokens', 'output_tokens'))):
            raise TopicContractError('GPT-6 provider usage changed')
        if reconcile and usage:
            budget.settle(index, usage)
        elif any(attempt.get(name) != value for name, value in usage.items()):
            raise TopicContractError('GPT-6 provider usage not settled')
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
    if (len(seen) > identity['budget']['max_requests']
            or repairs > identity['budget']['max_repair_requests']
            or ledger.get('halt_reason') != ('provider_usage_exceeded_reservation' if exceeded else None)):
        raise TopicContractError('GPT-6 repair budget limit or halt reason changed')
    return sorted(decisions.values(), key=lambda row: (row['judge'], row['sample_id'])), errors, {
        'raw_requests_verified': raw_count, 'repair_requests': repairs,
        'historical_failure_counts': dict(failures), 'usage': budget._usage(ledger),
        'circuit_reason': circuit or ledger.get('halt_reason')}


def summarize(candidates: list, decisions: list, dispositions: list) -> dict:
    indexed = {(row['judge'], row['sample_id']): row for row in decisions}
    if len(indexed) != len(decisions):
        raise TopicContractError('duplicate GPT-6 repair judgment')
    current = {candidate['parent_sample_id']: candidate for candidate in candidates}
    cases = []
    for previous in dispositions:
        case = deepcopy(previous)
        candidate = current.get(case['parent_sample_id'])
        if candidate:
            pair = [indexed.get((name, candidate['sample_id'])) for name in ('gpt', 'claude')]
            for row in pair:
                if row and any(row.get(key) != value
                               for key, value in validate_axes(candidate, row['axes']).items()):
                    raise TopicContractError('GPT-6 repair decision binding changed')
            shared = sorted(set.intersection(*(set(row['responds_to_ids']) for row in pair))) if all(pair) else []
            status = ('missing_review' if not all(pair)
                      else 'axes_keep_not_agreed' if any(row['status'] != 'keep' for row in pair)
                      else 'reply_anchor_disagreement' if not shared else 'machine_consensus_keep')
            case.update(sample_id=candidate['sample_id'], disposition=status,
                        shared_responds_to_ids=shared,
                        required_context_ids=sorted({message_id for row in pair if row
                                                     for message_id in row['required_context_ids']}),
                        decision_sha256={row['judge']: digest(row) for row in pair if row})
        cases.append(case)
    return {'population': len(dispositions), 'review_population': len(candidates),
            'reviewed': len(decisions), 'cases': cases,
            'disposition_counts': dict(Counter(case['disposition'] for case in cases)),
            'decision_counts': {judge: dict(Counter(row['status'] for row in decisions
                                                   if row['judge'] == judge))
                                for judge in ('gpt', 'claude')},
            'reason_counts': {judge: dict(Counter(row['reason'] for row in decisions
                                                 if row['judge'] == judge))
                              for judge in ('gpt', 'claude')},
            'transitions': {status: dict(Counter(case['disposition'] for case in cases
                                                 if case['previous_disposition'] == status))
                            for status in sorted({case['previous_disposition'] for case in cases})},
            'true_precision_claimed': False, 'p3_accepted': False, 'training_ready': False,
            'old_validation_changed': False, 'test_body_materialized': False}


def source_check(identity: dict) -> None:
    if (any(file_digest(Path(path)) != sha for path, sha in identity['inputs'].items())
            or any(file_digest(Path(__file__).with_name(name)) != sha
                   for name, sha in identity['source_digests'].items())):
        raise TopicContractError('GPT-6 repair immutable source changed')


def require_route_stability(preflights: list[Path], binding: dict, policy: dict,
                            authorization_reference: str) -> tuple[dict, dict]:
    required = policy['required_consecutive_route_preflights']
    if len(preflights) != required or len({path.resolve() for path in preflights}) != required:
        raise TopicContractError('GPT-6 repair requires distinct consecutive route preflights')
    receipts = []
    for path in preflights:
        receipt = base.require_preflight(path, binding, policy)
        identity = read_json(path / 'identity.json')
        if identity['authorization_reference'] != authorization_reference:
            raise TopicContractError('GPT-6 route preflight authority mismatch')
        receipts.append(receipt)
    if ([receipt['route_probe_sequence'] for receipt in receipts] != list(range(1, required + 1))
            or any(receipt['bound_returned_models'] != receipts[0]['bound_returned_models']
                   for receipt in receipts)
            or receipts[0]['bound_returned_models'] != {
                name: judge['model'] for name, judge in policy['judges'].items()}):
        raise TopicContractError('GPT-6 route preflights are not one exact stable sequence')
    route = {'preflight_manifest_sha256s': [receipt['manifest_sha256'] for receipt in receipts],
             'bound_returned_models': receipts[0]['bound_returned_models'],
             'route_gate_passed': True, 'consecutive_passes': required}
    return route, receipts[-1]


def run_review(*, queue: Path, repair: Path, consent: Path, config_path: Path,
               preflights: list[Path], calibration: Path, output_root: Path,
               controlled_root: Path, base_url: str, auth_file: Path,
               authorization_reference: str, execute: bool = False) -> dict:
    for path in (queue, repair, consent, calibration, output_root, *preflights):
        private_path(path, controlled_root)
    if not authorization_reference:
        raise TopicContractError('GPT-6 repair review requires scoped authority')
    config = load_config(config_path)
    policy_path = config_path.parent / config['review_config']
    policy = base.load_config(policy_path)
    fixture_path, fixtures = base.core.load_fixtures(policy_path, policy)
    binding = base.binding_for(policy, fixture_path, base_url)
    route, calibration_route = require_route_stability(
        preflights, binding, policy, authorization_reference)
    calibration_receipt = base.require_calibration(
        calibration, binding, fixtures, policy, calibration_route)
    if read_json(calibration / 'identity.json')['authorization_reference'] != authorization_reference:
        raise TopicContractError('GPT-6 calibration authority mismatch')
    candidates, dispositions, inputs = legacy.load_sources(queue, repair, consent, config)
    inputs.update({str(path): file_digest(path) for path in (
        config_path, policy_path, calibration / 'manifest.json',
        config_path.parent.parent / 'scripts/review_topic_repairs_gpt6.py',
        *(preflight / 'manifest.json' for preflight in preflights))})
    ordered = sorted(candidates, key=lambda candidate: digest(
        {'seed': config['seed'], 'sample_id': candidate['sample_id']}))
    source_names = ('topic_repair_protocol.py', 'topic_repair_protocol_gpt6.py',
                    'topic_repair_review.py', 'topic_repair_review_gpt6.py',
                    'topic_repair_contract.py', 'topic_repair_review_queue.py',
                    'topic_validation_budget.py', 'topic_validation_completion.py')
    identity = {'scope': METHOD, 'inputs': inputs, 'protocol': binding,
                'calibration': calibration_receipt, 'route': route,
                'judges': policy['judges'], 'models': route['bound_returned_models'],
                'budget': config['budget'], 'input_guard': config['input_guard'],
                'authorization_reference': authorization_reference,
                'candidate_digests': [candidate['candidate_sha256'] for candidate in candidates],
                'first_batches': [{'judge': judge, 'ids': [candidate['sample_id']], 'phase': 'first'}
                                  for candidate in ordered for judge in policy['judges']],
                'inherited_judgments': 0,
                'source_digests': {**binding['source_sha256'],
                    **{name: file_digest(Path(__file__).with_name(name)) for name in source_names}},
                **{key: config[key] for key in ('transport_circuit_consecutive_errors',
                                                 'transport_circuit_statuses', 'workers')}}
    table = request_table(identity, candidates)
    reserved = sum(item['reserve'] for item in table.values())
    if reserved > config['budget']['max_input_tokens'] - config['input_guard']['headroom_tokens']:
        raise TopicContractError('GPT-6 repair worst-case reservation exceeds budget')
    workspace = output_root / ('topic-repair-review-gpt6_' + digest(identity)[:20])
    if any(workspace == path or path in workspace.parents
           for path in (queue, repair, calibration, *preflights)):
        raise TopicContractError('GPT-6 repair review output overlaps source')
    plan = {'status': 'planned', 'workspace': str(workspace),
            'planned_first_requests': len(identity['first_batches']),
            'worst_case_input_reservation': reserved, 'budget': config['budget'],
            'inherited_judgments': 0, 'route_stability_passes': len(preflights),
            **GOVERNANCE}
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
            raise TopicContractError('GPT-6 repair sealed identity changed')
        if (workspace / 'identity.json').exists() and read_json(workspace / 'identity.json') != identity:
            raise TopicContractError('GPT-6 repair interrupted identity changed')
        if not sealed:
            _atomic_json(workspace / 'identity.json', identity)
            if not (workspace / 'budget.json').exists():
                _atomic_json(workspace / 'budget.json', {
                    'identity_sha256': digest(identity), 'attempts': []})
        decisions, errors, audit = reconstruct(workspace, identity, candidates, table,
                                               reconcile=not sealed)
        by_id = {candidate['sample_id']: candidate for candidate in candidates}
        if not sealed:
            budget = GuardedBudget(workspace, identity, table)

            def call(reservation):
                batch, payload, sha, slot = reservation
                endpoint = ('v1/responses' if identity['judges'][batch['judge']]['transport'] == 'responses'
                            else 'v1/chat/completions')
                raw = {**capture_http(payload, base_url=base_url, endpoint=endpoint,
                                      auth_file=auth_file,
                                      timeout=config['budget']['request_timeout_seconds']),
                       'request_digest': sha, 'batch': batch,
                       'payload_sha256': digest(payload), 'reservation': slot}
                _atomic_json(workspace / (sha + '.json'), raw)
                _, _, usage = unpack(raw, identity, by_id[batch['ids'][0]])
                budget.settle(slot, usage)

            def collect(batches):
                for start in range(0, len(batches), config['workers']):
                    _, _, state = reconstruct(workspace, identity, candidates, table,
                                              reconcile=True)
                    if state['circuit_reason']:
                        break
                    dispatched = {attempt['request_digest']
                                  for attempt in read_json(workspace / 'budget.json')['attempts']}
                    wave = []
                    for batch in batches[start:start + config['workers']]:
                        payload, sha, estimate = payload_record(
                            identity, batch, by_id[batch['ids'][0]])
                        if sha not in dispatched:
                            wave.append((batch, payload, sha, budget.reserve(sha, estimate)))
                    with ThreadPoolExecutor(max_workers=config['workers']) as pool:
                        list(pool.map(call, wave))
                    done, pending, state = reconstruct(workspace, identity, candidates, table,
                                                       reconcile=True)
                    _atomic_json(workspace / 'progress.json', {
                        'reviewed': len(done), 'expected': len(candidates) * 2,
                        'pending': len(pending), 'requests': state['usage']['requests'],
                        'circuit_reason': state['circuit_reason']})

            collect(identity['first_batches'])
            decisions, errors, audit = reconstruct(workspace, identity, candidates, table,
                                                   reconcile=True)
            if not audit['circuit_reason']:
                repairs = [{'judge': judge, 'ids': [sample_id], 'phase': 'repair'}
                           for (judge, sample_id), error in sorted(errors.items())
                           if error in REPAIRABLE]
                collect(repairs[:config['budget']['max_repair_requests']])
            decisions, errors, audit = reconstruct(workspace, identity, candidates, table,
                                                   reconcile=True)
        summary = summarize(candidates, decisions, dispositions)
        cases = summary.pop('cases')
        complete = len(decisions) == 2 * len(candidates) and not audit['circuit_reason']
        report = {**plan, **summary, **audit,
                  'status': 'complete' if complete else 'incomplete',
                  'response_failure_counts': dict(Counter(errors.values())),
                  'decisions_sha256': digest(decisions),
                  'machine_review_completed': complete,
                  'provider_model_revision_attested': False,
                  'provider_usage_bound_attested': False,
                  'model_protocol_recalibrated': True,
                  'old_model_judgments_inherited': False,
                  'draft_exported': False}
        outputs = {'decisions.json': {'identity': identity, 'decisions': decisions},
                   'cases.json': {'cases': cases}, 'report.json': report,
                   'circuit.json': {'reason': audit['circuit_reason']}}
        source_check(identity)
        if sealed:
            if any(read_json(workspace / name) != value for name, value in outputs.items()):
                raise TopicContractError('GPT-6 repair replay differs from raw responses')
        else:
            for name, value in outputs.items():
                _atomic_json(workspace / name, value)
            names = REQUIRED | {path.name for path in workspace.glob('*.json')
                                if len(path.stem) == 64}
            manifest = {'schema_version': 'topic-repair-review-gpt6-manifest-v1',
                        'identity': identity, **GOVERNANCE,
                        'output_digests': {name: file_digest(workspace / name)
                                           for name in sorted(names)}}
            manifest['manifest_sha256'] = digest(manifest)
            _atomic_json(workspace / 'manifest.json', manifest)
        return report
