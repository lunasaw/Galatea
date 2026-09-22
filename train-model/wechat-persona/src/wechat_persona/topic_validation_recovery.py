"""Recover only missing validation judgments under a new immutable budget."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import fcntl
from pathlib import Path

import yaml

from . import topic_validation_review as previous
from ._common import digest, file_digest
from .fact_review_server import _atomic_json
from .topic_adjudication import verify_manifest
from .topic_candidates import private_path, read_json
from .topic_context import TopicContractError
from .topic_validation_budget import GuardedBudget, GuardedRequests, input_reservation, request_table


METHOD = 'topic-validation-recovery-v1'
REQUIRED = {'identity.json', 'report.json', 'decisions.json', 'cases.json', 'budget.json', 'circuit.json'}


def load_config(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding='utf-8'))
    expected = {'schema_version': 'topic-validation-recovery-config-v1',
        'parent_config': 'topic-validation-review-v1.yaml',
        'parent_manifest_sha256': '232255196d89a954eaa5d0653a6fa16d64e07bc92aacbad520575bbc08c6d1b5',
        'inherited_judgments': 365, 'missing_by_judge': {'gpt': 19, 'claude': 16},
        'repair_order': 'judge_then_sample_id', 'max_repairs_per_judgment': 1,
        'budget': {'batch_size': 1, 'workers': 2, 'max_requests': 70, 'max_repair_requests': 35,
            'max_input_tokens': 700000, 'max_total_output_tokens': 280000,
            'max_output_tokens': 4000, 'request_timeout_seconds': 240},
        'input_guard': {'multipliers': {'gpt': 3, 'claude': 2}, 'padding_tokens': 512,
            'headroom_tokens': 20000, 'release_unused_reservations': False,
            'stop_on_reservation_exceeded': True}, 'governance': previous.GOVERNANCE}
    if value != expected:
        raise TopicContractError('invalid frozen validation recovery config')
    return value


def verify_parent(parent: Path, candidates: list, strata: dict, policy: dict, config: dict) -> tuple[dict, list, dict]:
    if file_digest(parent / 'manifest.json') != config['parent_manifest_sha256']:
        raise TopicContractError('recovery parent is not the frozen review')
    manifest = verify_manifest(parent, REQUIRED)
    identity, report = read_json(parent / 'identity.json'), read_json(parent / 'report.json')
    if (identity != manifest['identity'] or identity['scope'] != previous.METHOD
            or report['status'] != 'incomplete' or report['draft_exported'] or report['circuit_reason']
            or any(report.get(k) != v or manifest.get(k) != v for k, v in previous.GOVERNANCE.items())):
        raise TopicContractError('recovery requires the sealed incomplete review')
    decisions, _, audit = previous.reconstruct(parent, identity, candidates)
    summary = previous.summarize(candidates, decisions, strata, policy, identity['models'])
    cases = summary.pop('cases')
    if (read_json(parent / 'decisions.json') != {'identity': identity, 'decisions': decisions}
            or read_json(parent / 'cases.json') != {'cases': cases}
            or report['decisions_sha256'] != digest(decisions)
            or any(report.get(k) != v for k, v in {**summary, **audit}.items())
            or len(decisions) != config['inherited_judgments']):
        raise TopicContractError('recovery parent raw reconstruction changed')
    for path, sha in identity['inputs'].items():
        if file_digest(Path(path)) != sha:
            raise TopicContractError('recovery parent input changed')
    for name, sha in identity['source_digests'].items():
        if file_digest(Path(__file__).with_name(name)) != sha:
            raise TopicContractError('recovery parent source changed')
    # Check the reservation envelope against all reported parent requests, not labels.
    totals = {j: Counter() for j in identity['models']}
    for attempt in read_json(parent / 'budget.json')['attempts']:
        raw = read_json(parent / (attempt['request_digest'] + '.json'))
        judge = raw['batch']['judge']
        bound = input_reservation(attempt['input_reserve'], judge, config['input_guard'])
        if (attempt.get('input_tokens', 0) > bound
                or attempt.get('output_tokens', 0) > config['budget']['max_output_tokens']):
            raise TopicContractError('new reservation envelope does not cover parent usage')
        if 'input_tokens' in attempt:
            totals[judge].update(requests=1, estimated_input=attempt['input_reserve'],
                                 reported_input=attempt['input_tokens'])
    if any(not v['requests'] for v in totals.values()):
        raise TopicContractError('missing model-specific usage evidence')
    return identity, decisions, {k: dict(v) for k, v in totals.items()}


def reconstruct(workspace: Path, identity: dict, candidates: list, inherited: list,
                *, reconcile: bool = False) -> tuple[list, dict, dict]:
    table = request_table(identity, candidates)
    budget = GuardedBudget(workspace, identity, table)
    ledger = read_json(workspace / 'budget.json')
    if ledger['identity_sha256'] != digest(identity):
        raise TopicContractError('recovery ledger identity changed')
    by_id = {r['sample_id']: r for r in candidates}
    decisions = {(r['judge'], r['sample_id']): r for r in inherited}
    allowed = {(b['judge'], b['ids'][0]) for b in identity['first_batches']}
    if len(allowed) != len(identity['first_batches']) or allowed & decisions.keys():
        raise TopicContractError('recovery planned a valid or duplicate judgment')
    errors = {key: 'not_dispatched' for key in allowed}
    seen, failures, repairs, raw_count, exceeded = set(), Counter(), 0, 0, False
    for index, attempt in enumerate(ledger['attempts']):
        sha = attempt['request_digest']
        expected = table.get(sha)
        if (expected is None or sha in seen or attempt.get('input_estimate') != expected['estimate']
                or attempt['input_reserve'] != expected['reserve']
                or attempt['output_reserve'] != identity['budget']['max_output_tokens']):
            raise TopicContractError('recovery reservation binding changed')
        seen.add(sha)
        batch = expected['batch']
        key = batch['judge'], batch['ids'][0]
        if key in decisions or (batch['phase'] == 'repair' and errors[key] not in previous.REPAIRABLE):
            raise TopicContractError('recovery retried a valid or nonrepairable judgment')
        repairs += batch['phase'] == 'repair'
        path = workspace / (sha + '.json')
        if not path.exists():
            if any(k in attempt for k in ('input_tokens', 'output_tokens')):
                raise TopicContractError('recovery usage without raw response')
            errors[key] = 'interrupted_request_not_redispatched'
            continue
        raw_count += 1
        raw = read_json(path)
        if (raw['batch'] != batch or raw['request_digest'] != sha or raw['reservation'] != index
                or raw['payload_sha256'] != expected['payload_sha256']
                or raw['raw_body_sha256'] != digest(raw['raw_body_base64'])):
            raise TopicContractError('recovery raw response binding changed')
        row, error, usage = previous.unpack(raw, identity, by_id[key[1]])
        if (any(k in attempt and attempt[k] != v for k, v in usage.items())
                or any(k in attempt and k not in usage for k in ('input_tokens', 'output_tokens'))):
            raise TopicContractError('recovery provider usage changed')
        if reconcile and usage:
            budget.settle(index, usage)
        elif any(attempt.get(k) != v for k, v in usage.items()):
            raise TopicContractError('recovery usage not settled')
        exceeded |= (usage.get('input_tokens', 0) > expected['reserve']
                     or usage.get('output_tokens', 0) > attempt['output_reserve'])
        if error:
            errors[key] = error
            failures[error] += 1
        else:
            decisions[key] = row
            errors.pop(key, None)
    ledger = read_json(workspace / 'budget.json')
    if (len(seen) > identity['budget']['max_requests'] or repairs > identity['budget']['max_repair_requests']
            or ledger.get('halt_reason') != ('provider_usage_exceeded_reservation' if exceeded else None)):
        raise TopicContractError('recovery request limit or usage circuit changed')
    return sorted(decisions.values(), key=lambda r: (r['judge'], r['sample_id'])), errors, {
        'raw_requests_verified': raw_count, 'repair_requests': repairs,
        'historical_response_failure_counts': dict(failures),
        'usage': budget._usage(ledger), 'usage_envelope_exceeded': exceeded,
        'budget_halt_reason': ledger.get('halt_reason')}


def source_check(identity: dict) -> None:
    if (any(file_digest(Path(p)) != sha for p, sha in identity['inputs'].items())
            or any(file_digest(Path(__file__).with_name(n)) != sha for n, sha in identity['source_digests'].items())):
        raise TopicContractError('recovery immutable source changed')


def run_recovery(*, parent: Path, packet: Path, config_path: Path, consent: Path, preflight: Path,
                 original_preflight: Path, calibration: Path, output_root: Path, controlled_root: Path,
                 base_url: str, auth_file: Path, authorization_reference: str, execute: bool = False) -> dict:
    for path in (parent, packet, consent, preflight, original_preflight, calibration, output_root):
        private_path(path, controlled_root)
    config = load_config(config_path)
    if file_digest(parent / 'manifest.json') != config['parent_manifest_sha256']:
        raise TopicContractError('recovery parent is not the frozen review')
    parent_identity = read_json(parent / 'identity.json')
    if not authorization_reference or authorization_reference == parent_identity['authorization_reference']:
        raise TopicContractError('recovery requires new scoped authorization')
    # Reuse the read-only original plan to verify fresh route, consent and split.
    original_args = dict(packet=packet, config_path=config_path.parent / config['parent_config'], consent=consent,
        preflight=preflight, original_preflight=original_preflight, calibration=calibration,
        output_root=output_root, controlled_root=controlled_root, base_url=base_url, auth_file=auth_file,
        authorization_reference=authorization_reference)
    previous.run_review(**original_args)
    parent_config = previous.load_config(original_args['config_path'])
    preparation_policy = previous.preparation.load_policy(config_path.parent / parent_config['preparation_config'])
    review_policy = previous.base.load_config(config_path.parent / parent_config['review_config'])
    fixtures_path, _ = previous.base.core.load_fixtures(config_path.parent / parent_config['review_config'], review_policy)
    binding = previous.base.binding_for(review_policy, fixtures_path, base_url)
    _, candidates, strata = previous.load_packet(packet, parent_config, preparation_policy, binding, consent)
    source_identity, inherited, usage_basis = verify_parent(parent, candidates, strata, preparation_policy, config)
    if (source_identity['protocol'] != binding
            or source_identity['models'] != previous.base.require_preflight(preflight, binding, review_policy)['bound_returned_models']):
        raise TopicContractError('recovery protocol or exact model changed')
    inherited_keys = {(r['judge'], r['sample_id']) for r in inherited}
    missing = {(j, r['sample_id']) for j in source_identity['models'] for r in candidates} - inherited_keys
    if dict(Counter(j for j, _ in missing)) != config['missing_by_judge']:
        raise TopicContractError('recovery missing population changed')
    first = [{'judge': j, 'ids': [sid], 'phase': 'first'} for j, sid in sorted(missing)]
    inputs = dict(source_identity['inputs'])
    for path in (config_path, parent / 'manifest.json', preflight / 'manifest.json',
                 config_path.parent.parent / 'scripts/recover_topic_validation.py'):
        inputs[str(path)] = file_digest(path)
    sources = dict(source_identity['source_digests'])
    for name in ('topic_validation_recovery.py', 'topic_validation_budget.py', 'topic_review_budget.py'):
        sources[name] = file_digest(Path(__file__).with_name(name))
    identity = {**source_identity, 'scope': METHOD, 'inputs': inputs, 'source_digests': sources,
        'parent_manifest_sha256': config['parent_manifest_sha256'], 'inherited_decisions_sha256': digest(inherited),
        'authorization_reference': authorization_reference, 'budget': config['budget'],
        'input_guard': config['input_guard'], 'first_batches': first, 'usage_basis': usage_basis,
        'route': previous.base.require_preflight(preflight, binding, review_policy)}
    table = request_table(identity, candidates)
    first_reserved = sum(v['reserve'] for v in table.values() if v['batch']['phase'] == 'first')
    if (len(table) > config['budget']['max_requests']
            or sum(v['reserve'] for v in table.values()) > config['budget']['max_input_tokens'] - config['input_guard']['headroom_tokens']
            or len(table) * config['budget']['max_output_tokens'] > config['budget']['max_total_output_tokens']):
        raise TopicContractError('recovery worst-case reservation exceeds budget')
    workspace = output_root / (METHOD + '_' + digest(identity)[:20])
    for source in (parent, packet, preflight, original_preflight, calibration):
        if workspace.resolve() == source.resolve() or source.resolve() in workspace.resolve().parents:
            raise TopicContractError('recovery output overlaps source')
    plan = {'status': 'planned', 'workspace': str(workspace), 'parent': str(parent), 'population': len(candidates),
        'inherited_judgments': len(inherited), 'missing_by_judge': config['missing_by_judge'],
        'planned_first_requests': len(first), 'budget': config['budget'], 'input_guard': config['input_guard'],
        'first_input_reservation': first_reserved, 'worst_case_input_reservation': first_reserved * 2,
        'provider_usage_bound_attested': False, 'usage_basis': usage_basis, **previous.GOVERNANCE}
    source_check(identity)
    if not execute:
        return plan
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace.chmod(0o700)
    with (workspace / 'run.lock').open('a') as lock:
        (workspace / 'run.lock').chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sealed = (workspace / 'manifest.json').exists()
        if sealed:
            manifest = verify_manifest(workspace, REQUIRED)
            if manifest['identity'] != identity:
                raise TopicContractError('recovery sealed identity changed')
        if (workspace / 'identity.json').exists() and read_json(workspace / 'identity.json') != identity:
            raise TopicContractError('recovery interrupted identity changed')
        if not sealed:
            _atomic_json(workspace / 'identity.json', identity)
            if not (workspace / 'budget.json').exists():
                _atomic_json(workspace / 'budget.json', {'identity_sha256': digest(identity), 'attempts': []})
        decisions, errors, audit = reconstruct(workspace, identity, candidates, inherited, reconcile=not sealed)
        if not sealed:
            requests = GuardedRequests(workspace, identity, base_url, auth_file, table)
            by_id = {r['sample_id']: r for r in candidates}
            def collect(batches):
                with ThreadPoolExecutor(max_workers=config['budget']['workers']) as pool:
                    list(pool.map(lambda b: requests.call(b, by_id[b['ids'][0]]), batches))
            # Existing reservations (including interrupted calls) are never redispatched.
            collect(first)
            decisions, errors, audit = reconstruct(workspace, identity, candidates, inherited)
            if not requests.circuit.is_set():
                repairs = [{'judge': j, 'ids': [sid], 'phase': 'repair'} for (j, sid), reason in sorted(errors.items())
                           if reason in previous.REPAIRABLE]
                collect(repairs[:config['budget']['max_repair_requests']])
            decisions, errors, audit = reconstruct(workspace, identity, candidates, inherited)
        if [r for r in decisions if (r['judge'], r['sample_id']) in inherited_keys] != inherited:
            raise TopicContractError('recovery changed an inherited judgment')
        summary = previous.summarize(candidates, decisions, strata, preparation_policy, identity['models'])
        cases = summary.pop('cases')
        state = read_json(workspace / 'circuit.json')
        machine_complete = len(decisions) == 2 * len(candidates)
        report = {**plan, **summary, **audit, 'status': 'complete' if machine_complete and not state['reason'] else 'incomplete',
            'machine_review_completed': machine_complete, 'new_valid_judgments': len(decisions) - len(inherited),
            'inherited_judgments_unchanged': True, 'unresolved_response_failures': len(errors),
            'response_failure_counts': dict(Counter(errors.values())), 'circuit_reason': state['reason'],
            'decisions_sha256': digest(decisions), 'parent_usage': read_json(parent / 'report.json')['usage']}
        source_check(identity)
        if sealed:
            if (read_json(workspace / 'report.json') != report
                    or read_json(workspace / 'decisions.json') != {'identity': identity, 'decisions': decisions}
                    or read_json(workspace / 'cases.json') != {'cases': cases}):
                raise TopicContractError('recovery replay differs from raw reconstruction')
            return report
        _atomic_json(workspace / 'decisions.json', {'identity': identity, 'decisions': decisions})
        _atomic_json(workspace / 'cases.json', {'cases': cases})
        _atomic_json(workspace / 'report.json', report)
        names = REQUIRED | {p.name for p in workspace.glob('*.json') if len(p.stem) == 64}
        manifest = {'schema_version': METHOD + '-manifest', 'identity': identity, **previous.GOVERNANCE,
                    'output_digests': {n: file_digest(workspace / n) for n in sorted(names)}}
        manifest['manifest_sha256'] = digest(manifest)
        _atomic_json(workspace / 'manifest.json', manifest)
        return report
