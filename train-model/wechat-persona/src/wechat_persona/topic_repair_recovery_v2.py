"""Continue a sealed identity-drift recovery without changing its reviewers."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import fcntl
from pathlib import Path

import yaml

from . import topic_repair_recovery as previous
from . import topic_repair_review as original
from ._common import digest, file_digest
from .fact_review_server import _atomic_json
from .topic_adjudication import verify_manifest
from .topic_candidates import private_path, read_json
from .topic_context import TopicContractError
from .topic_validation_budget import GuardedBudget


METHOD = 'topic-repair-identity-recovery-v2'


def load_config(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding='utf-8'))
    expected = {
        'schema_version': 'topic-repair-recovery-config-v2',
        'parent_manifest_sha256': 'f9566fd1df7a64c96aaf41579a1cdc0a3752f8ba271411916e08bd62b956e363',
        'inherited_judgments': 196,
        'missing_judgments': 178,
        'ancestor_attempt_counts': {'zero': 171, 'one': 7, 'two': 0},
        'workers': 2,
        'max_total_attempts_per_judgment': 2,
        'required_consecutive_route_preflights': 3,
        'require_same_exact_models': True,
        'reuse_original_total_budget': True,
        'governance': original.GOVERNANCE,
    }
    if value != expected:
        raise TopicContractError('invalid frozen repair recovery v2 config')
    return value


def parent_evidence(parent: Path, candidates: list, dispositions: list) -> tuple[dict, list, dict, dict]:
    manifest = verify_manifest(parent, original.REQUIRED)
    identity = read_json(parent / 'identity.json')
    if manifest['identity'] != identity or identity['scope'] != previous.METHOD:
        raise TopicContractError('identity recovery requires the sealed v1 recovery')
    decisions, errors, audit = previous.replay_complete(parent, candidates, dispositions)
    summary = original.summarize(candidates, decisions, dispositions)
    cases = summary.pop('cases')
    report = read_json(parent / 'report.json')
    allowed = original.REPAIRABLE | {'model_identity_mismatch', 'not_dispatched'}
    if (audit['circuit_reason'] != 'model_identity_mismatch'
            or read_json(parent / 'decisions.json') != {'identity': identity, 'decisions': decisions}
            or read_json(parent / 'cases.json') != {'cases': cases}
            or any(report.get(key) != value for key, value in {**summary, **audit}.items())
            or any(error not in allowed for error in errors.values())):
        raise TopicContractError('v1 recovery evidence mismatch or nonrecoverable failure')
    return identity, decisions, errors, audit


def ancestor_attempt_counts(parent: Path, missing: dict) -> tuple[dict, int]:
    current_identity = read_json(parent / 'identity.json')
    ancestors = [Path(current_identity['parent']), parent]
    counts = Counter()
    repair_requests = 0
    for directory in ancestors:
        for attempt in read_json(directory / 'budget.json')['attempts']:
            raw = read_json(directory / (attempt['request_digest'] + '.json'))
            batch = raw['batch']
            key = batch['judge'], batch['ids'][0]
            counts[key] += 1
            repair_requests += batch['phase'] == 'repair'
    if any(counts[key] > 2 for key in counts):
        raise TopicContractError('ancestor exceeded frozen per-judgment attempt limit')
    return {key: counts[key] for key in missing}, repair_requests


def require_route_stability(preflights: list[Path], source: dict, policy: dict,
                            authorization_reference: str, required: int) -> dict:
    if len(preflights) != required or len({path.resolve() for path in preflights}) != required:
        raise TopicContractError('recovery requires distinct consecutive route preflights')
    manifests, routes = [], []
    for path in preflights:
        route = original.base.require_preflight(path, source['protocol'], policy)
        if read_json(path / 'identity.json')['authorization_reference'] != authorization_reference:
            raise TopicContractError('route preflight authority mismatch')
        manifests.append(file_digest(path / 'manifest.json'))
        routes.append(route)
    models = source['models']
    if any(route['bound_returned_models'] != models for route in routes):
        raise TopicContractError('route preflight exact model mismatch')
    return {'preflight_manifest_sha256s': manifests, 'bound_returned_models': models,
            'route_gate_passed': True, 'consecutive_passes': required}


def build_identity(parent: Path, source: dict, inherited: list, errors: dict, audit: dict,
                   attempt_counts: dict, ancestor_repairs: int, route: dict, inputs: dict,
                   config: dict, authorization_reference: str, preflights: list[Path]) -> dict:
    original_parent = Path(source['parent'])
    original_identity = read_json(original_parent / 'identity.json')
    total = original_identity['budget']
    cumulative = audit['cumulative_usage']
    budget = {**total,
        'max_requests': total['max_requests'] - cumulative['requests'],
        'max_input_tokens': total['max_input_tokens'] - cumulative['charged_input_tokens'],
        'max_total_output_tokens': total['max_total_output_tokens'] - cumulative['charged_output_tokens'],
        'max_repair_requests': total['max_repair_requests'] - ancestor_repairs}
    if any(value < 0 for key, value in budget.items() if key.startswith('max_')):
        raise TopicContractError('no cumulative review budget remains')
    first = [{'judge': judge, 'ids': [sample_id], 'phase': 'first'}
             for judge, sample_id in sorted(errors)]
    preserved = {key: source[key] for key in (
        'protocol', 'calibration', 'judges', 'models', 'input_guard', 'candidate_digests',
        'transport_circuit_consecutive_errors', 'transport_circuit_statuses')}
    return {**preserved, 'scope': METHOD, 'inputs': inputs, 'route': route, 'budget': budget,
            'authorization_reference': authorization_reference, 'workers': config['workers'],
            'first_batches': first, 'parent': str(parent),
            'recovery_preflights': [str(path) for path in preflights],
            'inherited_decisions_sha256': digest(inherited),
            'ancestor_attempt_counts': [
                {'judge': judge, 'sample_id': sample_id, 'count': attempt_counts[judge, sample_id]}
                for judge, sample_id in sorted(errors)],
            'attempt_policy': {'max_total_attempts_per_judgment': config['max_total_attempts_per_judgment'],
                               'valid_judgments_reasked': False},
            'original_total_budget': total,
            'source_digests': {**source['source_digests'], Path(__file__).name: file_digest(Path(__file__))}}


def recovery_table(identity: dict, candidates: list) -> dict:
    return original.request_table(identity, candidates)


def replay_complete(machine: Path, candidates: list, dispositions: list) -> tuple[list, dict, dict]:
    manifest = verify_manifest(machine, original.REQUIRED)
    identity = read_json(machine / 'identity.json')
    if manifest['identity'] != identity or identity['scope'] != METHOD:
        raise TopicContractError('unexpected repair recovery v2 identity')
    original.source_check(identity)
    parent = Path(identity['parent'])
    source, inherited, missing, parent_audit = parent_evidence(parent, candidates, dispositions)
    config_path = Path(__file__).resolve().parents[2] / 'configs/topic-repair-recovery-v2.yaml'
    config = load_config(config_path)
    preflights = [Path(path) for path in identity['recovery_preflights']]
    policy = original.base.load_config(config_path.parent / 'topic-axes-review-v4.yaml')
    route = require_route_stability(preflights, source, policy, identity['authorization_reference'],
                                    config['required_consecutive_route_preflights'])
    attempt_counts, ancestor_repairs = ancestor_attempt_counts(parent, missing)
    inputs = {**source['inputs'], **{str(path): file_digest(path) for path in (
        parent / 'manifest.json', config_path,
        config_path.parent.parent / 'scripts/recover_topic_repairs_v2.py',
        *(preflight / 'manifest.json' for preflight in preflights))}}
    expected = build_identity(parent, source, inherited, missing, parent_audit, attempt_counts,
                              ancestor_repairs, route, inputs, config,
                              identity['authorization_reference'], preflights)
    if (identity != expected or file_digest(parent / 'manifest.json') != config['parent_manifest_sha256']):
        raise TopicContractError('recovery v2 lineage, route, authority or budget changed')
    new, errors, audit = original.reconstruct(machine, identity, candidates,
                                              recovery_table(identity, candidates))
    combined = sorted([*inherited, *new], key=lambda row: (row['judge'], row['sample_id']))
    usage = {key: parent_audit['cumulative_usage'][key] + audit['usage'][key]
             for key in audit['usage']}
    total = identity['original_total_budget']
    if (usage['requests'] > total['max_requests']
            or usage['charged_input_tokens'] > total['max_input_tokens']
            or usage['charged_output_tokens'] > total['max_total_output_tokens']):
        raise TopicContractError('repair recovery v2 exceeded original total budget')
    return combined, errors, {**audit, 'cumulative_usage': usage,
                              'inherited_judgments': len(inherited)}


def run_recovery(*, parent: Path, queue: Path, repair: Path, consent: Path, config_path: Path,
                 preflights: list[Path], output_root: Path, controlled_root: Path, base_url: str,
                 auth_file: Path, authorization_reference: str, execute: bool = False) -> dict:
    for path in (parent, queue, repair, consent, output_root, *preflights):
        private_path(path, controlled_root)
    if not authorization_reference:
        raise TopicContractError('repair recovery v2 requires scoped authority')
    config = load_config(config_path)
    if file_digest(parent / 'manifest.json') != config['parent_manifest_sha256']:
        raise TopicContractError('recovery v2 parent changed')
    review_config = original.load_config(config_path.parent / 'topic-repair-review-v1.yaml')
    candidates, dispositions, inputs = original.load_sources(queue, repair, consent, review_config)
    source, inherited, errors, parent_audit = parent_evidence(parent, candidates, dispositions)
    attempt_counts, ancestor_repairs = ancestor_attempt_counts(parent, errors)
    distribution = Counter(attempt_counts.values())
    expected_distribution = {0: config['ancestor_attempt_counts']['zero'],
                             1: config['ancestor_attempt_counts']['one'],
                             2: config['ancestor_attempt_counts']['two']}
    if (len(inherited) != config['inherited_judgments'] or len(errors) != config['missing_judgments']
            or any(distribution[count] != expected for count, expected in expected_distribution.items())
            or any(count >= config['max_total_attempts_per_judgment'] for count in attempt_counts.values())):
        raise TopicContractError('recovery v2 missing population or attempt history changed')
    policy = original.base.load_config(config_path.parent / 'topic-axes-review-v4.yaml')
    route = require_route_stability(preflights, source, policy, authorization_reference,
                                    config['required_consecutive_route_preflights'])
    if source['protocol']['endpoint_sha256'] != digest(base_url):
        raise TopicContractError('recovery v2 endpoint changed')
    inputs = {**source['inputs'], **inputs, **{str(path): file_digest(path) for path in (
        parent / 'manifest.json', config_path,
        config_path.parent.parent / 'scripts/recover_topic_repairs_v2.py',
        *(preflight / 'manifest.json' for preflight in preflights))}}
    identity = build_identity(parent, source, inherited, errors, parent_audit, attempt_counts,
                              ancestor_repairs, route, inputs, config,
                              authorization_reference, preflights)
    table = recovery_table(identity, candidates)
    prior = {(row['judge'], row['sample_id']): row['count'] for row in identity['ancestor_attempt_counts']}
    planned = [item for item in table.values() if item['batch']['phase'] == 'first'
               or prior[item['batch']['judge'], item['batch']['ids'][0]] == 0]
    reserved = sum(item['reserve'] for item in planned)
    if reserved > identity['budget']['max_input_tokens'] - identity['input_guard']['headroom_tokens']:
        raise TopicContractError('recovery v2 worst-case reservation exceeds original remaining budget')
    workspace = output_root / ('topic-repair-recovery-v2_' + digest(identity)[:20])
    if any(workspace == path or path in workspace.parents for path in (parent, queue, repair, *preflights)):
        raise TopicContractError('recovery v2 output overlaps source')
    plan = {'status': 'planned', 'workspace': str(workspace),
            'planned_first_requests': len(errors),
            'planned_repair_capacity': sum(count == 0 for count in attempt_counts.values()),
            'budget': identity['budget'], 'worst_case_input_reservation': reserved,
            'original_total_budget_unchanged': True, 'route_stability_passes': len(preflights),
            **original.GOVERNANCE}
    original.source_check(identity)
    if not execute:
        return plan
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace.chmod(0o700)
    with (workspace / 'run.lock').open('a') as lock:
        (workspace / 'run.lock').chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sealed = (workspace / 'manifest.json').exists()
        if sealed and verify_manifest(workspace, original.REQUIRED)['identity'] != identity:
            raise TopicContractError('recovery v2 sealed identity changed')
        if (workspace / 'identity.json').exists() and read_json(workspace / 'identity.json') != identity:
            raise TopicContractError('recovery v2 interrupted identity changed')
        if not sealed:
            _atomic_json(workspace / 'identity.json', identity)
            if not (workspace / 'budget.json').exists():
                _atomic_json(workspace / 'budget.json', {'identity_sha256': digest(identity), 'attempts': []})
        new, current_errors, audit = original.reconstruct(
            workspace, identity, candidates, table, reconcile=not sealed)
        by_id = {candidate['sample_id']: candidate for candidate in candidates}
        if not sealed:
            budget = GuardedBudget(workspace, identity, table)

            def call(record):
                batch, payload, sha, slot = record
                endpoint = ('v1/responses' if identity['judges'][batch['judge']]['transport'] == 'responses'
                            else 'v1/chat/completions')
                raw = {**original.capture_http(
                    payload, base_url=base_url, endpoint=endpoint, auth_file=auth_file,
                    timeout=identity['budget']['request_timeout_seconds']),
                    'request_digest': sha, 'batch': batch, 'payload_sha256': digest(payload),
                    'reservation': slot}
                _atomic_json(workspace / (sha + '.json'), raw)
                _, _, usage = original.unpack(raw, identity, by_id[batch['ids'][0]])
                budget.settle(slot, usage)

            def collect(batches):
                for start in range(0, len(batches), config['workers']):
                    _, _, state = original.reconstruct(
                        workspace, identity, candidates, table, reconcile=True)
                    if state['circuit_reason']:
                        break
                    dispatched = {attempt['request_digest']
                                  for attempt in read_json(workspace / 'budget.json')['attempts']}
                    wave = []
                    for batch in batches[start:start + config['workers']]:
                        payload, sha, estimate = original.payload_record(
                            identity, batch, by_id[batch['ids'][0]])
                        if sha not in dispatched:
                            wave.append((batch, payload, sha, budget.reserve(sha, estimate)))
                    with ThreadPoolExecutor(max_workers=config['workers']) as pool:
                        list(pool.map(call, wave))
                    done, pending, state = original.reconstruct(
                        workspace, identity, candidates, table, reconcile=True)
                    _atomic_json(workspace / 'progress.json', {
                        'reviewed': len(inherited) + len(done), 'expected': 2 * len(candidates),
                        'pending': len(pending), 'requests': state['usage']['requests'],
                        'circuit_reason': state['circuit_reason']})

            collect(identity['first_batches'])
            new, current_errors, audit = original.reconstruct(
                workspace, identity, candidates, table, reconcile=True)
            current_counts = Counter()
            for attempt in read_json(workspace / 'budget.json')['attempts']:
                batch = table[attempt['request_digest']]['batch']
                current_counts[batch['judge'], batch['ids'][0]] += 1
            if not audit['circuit_reason']:
                repairs = [{'judge': judge, 'ids': [sample_id], 'phase': 'repair'}
                           for (judge, sample_id), error in sorted(current_errors.items())
                           if error in original.REPAIRABLE
                           and prior[judge, sample_id] + current_counts[judge, sample_id]
                           < config['max_total_attempts_per_judgment']]
                collect(repairs)
            new, current_errors, audit = original.reconstruct(
                workspace, identity, candidates, table, reconcile=True)
        decisions = sorted([*inherited, *new], key=lambda row: (row['judge'], row['sample_id']))
        summary = original.summarize(candidates, decisions, dispositions)
        cases = summary.pop('cases')
        cumulative = {key: parent_audit['cumulative_usage'][key] + audit['usage'][key]
                      for key in audit['usage']}
        total = identity['original_total_budget']
        if (cumulative['requests'] > total['max_requests']
                or cumulative['charged_input_tokens'] > total['max_input_tokens']
                or cumulative['charged_output_tokens'] > total['max_total_output_tokens']):
            raise TopicContractError('recovery v2 cumulative budget exceeded')
        complete = len(decisions) == 2 * len(candidates) and not audit['circuit_reason']
        report = {**plan, **summary, **audit, 'status': 'complete' if complete else 'incomplete',
                  'cumulative_usage': cumulative, 'inherited_judgments': len(inherited),
                  'response_failure_counts': dict(Counter(current_errors.values())),
                  'decisions_sha256': digest(decisions), 'machine_review_completed': complete,
                  'draft_exported': False, 'inherited_judgments_unchanged': True,
                  'valid_judgments_reasked': False}
        outputs = {'decisions.json': {'identity': identity, 'decisions': decisions},
                   'cases.json': {'cases': cases}, 'report.json': report,
                   'circuit.json': {'reason': audit['circuit_reason']}}
        original.source_check(identity)
        if sealed:
            if any(read_json(workspace / name) != value for name, value in outputs.items()):
                raise TopicContractError('recovery v2 replay differs from raw evidence')
        else:
            for name, value in outputs.items():
                _atomic_json(workspace / name, value)
            names = original.REQUIRED | {
                path.name for path in workspace.glob('*.json') if len(path.stem) == 64}
            manifest = {'schema_version': METHOD + '-manifest', 'identity': identity,
                        **original.GOVERNANCE,
                        'output_digests': {name: file_digest(workspace / name)
                                           for name in sorted(names)}}
            manifest['manifest_sha256'] = digest(manifest)
            _atomic_json(workspace / 'manifest.json', manifest)
        return report
