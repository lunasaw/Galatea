"""Recover a sealed transport outage within its original cumulative budget."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import fcntl
from pathlib import Path

import yaml

from . import topic_repair_review as original
from ._common import digest, file_digest
from .fact_review_server import _atomic_json
from .topic_adjudication import verify_manifest
from .topic_candidates import private_path, read_json
from .topic_context import TopicContractError
from .topic_validation_budget import GuardedBudget


METHOD = 'topic-repair-transport-recovery-v1'


def load_config(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding='utf-8'))
    expected = {'schema_version': 'topic-repair-recovery-config-v1',
        'parent_manifest_sha256': '721703560b90045bb9bb6fc95719e56cc6665a02f0a814302fec809da45c9e64',
        'inherited_judgments': 22, 'missing_judgments': 352, 'previous_attempted_failures': 6,
        'workers': 2, 'max_total_attempts_per_judgment': 2, 'require_fresh_synthetic_route': True,
        'reuse_original_total_budget': True, 'governance': original.GOVERNANCE}
    if value != expected:
        raise TopicContractError('invalid frozen repair recovery config')
    return value


def parent_evidence(parent: Path, candidates: list, dispositions: list) -> tuple[dict, list, dict, dict]:
    manifest = verify_manifest(parent, original.REQUIRED)
    identity = read_json(parent / 'identity.json')
    if manifest['identity'] != identity or identity['scope'] != original.METHOD:
        raise TopicContractError('transport recovery requires original review')
    original.source_check(identity)
    decisions, errors, audit = original.reconstruct(parent, identity, candidates, original.request_table(identity, candidates))
    summary = original.summarize(candidates, decisions, dispositions)
    cases = summary.pop('cases')
    report = read_json(parent / 'report.json')
    if (audit['circuit_reason'] != 'repeated_transport_errors'
            or read_json(parent / 'decisions.json') != {'identity': identity, 'decisions': decisions}
            or read_json(parent / 'cases.json') != {'cases': cases}
            or any(report.get(k) != v for k, v in {**summary, **audit}.items())
            or any(error not in original.REPAIRABLE | {'not_dispatched'} for error in errors.values())):
        raise TopicContractError('parent outage evidence mismatch or nonrecoverable failure')
    return identity, decisions, errors, audit


def build_recovery_identity(parent: Path, source: dict, inherited: list, errors: dict, audit: dict,
                            route: dict, inputs: dict, config: dict, authorization_reference: str,
                            preflight: Path) -> dict:
    attempts = read_json(parent / 'budget.json')['attempts']
    # The parent journal binds each raw record; only metadata is consulted here.
    counts = Counter()
    for attempt in attempts:
        raw = read_json(parent / (attempt['request_digest'] + '.json'))
        counts[(raw['batch']['judge'], raw['batch']['ids'][0])] += 1
    if any(counts[key] >= config['max_total_attempts_per_judgment'] for key in errors):
        raise TopicContractError('recovery would exceed per-judgment retry limit')
    budget = {**source['budget'],
        'max_requests': source['budget']['max_requests'] - audit['usage']['requests'],
        'max_input_tokens': source['budget']['max_input_tokens'] - audit['usage']['charged_input_tokens'],
        'max_total_output_tokens': source['budget']['max_total_output_tokens'] - audit['usage']['charged_output_tokens'],
        'max_repair_requests': sum(counts[key] == 0 for key in errors)}
    first = [{'judge': j, 'ids': [sid], 'phase': 'first'} for j, sid in sorted(errors)]
    return {**source, 'scope': METHOD, 'inputs': inputs, 'route': route, 'budget': budget,
            'authorization_reference': authorization_reference, 'workers': config['workers'], 'first_batches': first,
            'parent': str(parent), 'recovery_preflight': str(preflight),
            'inherited_decisions_sha256': digest(inherited),
            'parent_attempt_counts': [{'judge': j, 'sample_id': sid, 'count': counts[j, sid]} for j, sid in sorted(errors)],
            'source_digests': {**source['source_digests'], Path(__file__).name: file_digest(Path(__file__))}}


def recovery_table(identity: dict, candidates: list) -> dict:
    attempted = {(r['judge'], r['sample_id']) for r in identity['parent_attempt_counts'] if r['count']}
    return {sha: item for sha, item in original.request_table(identity, candidates).items()
            if item['batch']['phase'] == 'first' or (item['batch']['judge'], item['batch']['ids'][0]) not in attempted}


def replay_complete(machine: Path, candidates: list, dispositions: list) -> tuple[list, dict, dict]:
    manifest = verify_manifest(machine, original.REQUIRED)
    identity = read_json(machine / 'identity.json')
    if manifest['identity'] != identity or identity['scope'] != METHOD:
        raise TopicContractError('unexpected repair recovery identity')
    original.source_check(identity)
    source, inherited, missing, parent_audit = parent_evidence(Path(identity['parent']), candidates, dispositions)
    config_path = Path(__file__).resolve().parents[2] / 'configs/topic-repair-recovery-v1.yaml'
    config = load_config(config_path)
    parent, preflight = Path(identity['parent']), Path(identity['recovery_preflight'])
    policy = original.base.load_config(config_path.parent / 'topic-axes-review-v4.yaml')
    route = original.base.require_preflight(preflight, source['protocol'], policy)
    inputs = {**source['inputs'], **{str(p): file_digest(p) for p in (
        parent / 'manifest.json', preflight / 'manifest.json', config_path,
        config_path.parent.parent / 'scripts/recover_topic_repairs.py')}}
    expected = build_recovery_identity(parent, source, inherited, missing, parent_audit, route, inputs,
                                       config, identity['authorization_reference'], preflight)
    if (identity != expected or file_digest(parent / 'manifest.json') != config['parent_manifest_sha256']
            or route['bound_returned_models'] != source['models']
            or read_json(preflight / 'identity.json')['authorization_reference'] != identity['authorization_reference']
            or not identity['authorization_reference'] or preflight / 'manifest.json' in map(Path, source['inputs'])):
        raise TopicContractError('recovery lineage, route, authority or remaining budget changed')
    new, errors, audit = original.reconstruct(machine, identity, candidates, recovery_table(identity, candidates))
    combined = sorted([*inherited, *new], key=lambda r: (r['judge'], r['sample_id']))
    usage = {k: parent_audit['usage'][k] + audit['usage'][k] for k in audit['usage']}
    if (usage['requests'] > source['budget']['max_requests']
            or usage['charged_input_tokens'] > source['budget']['max_input_tokens']
            or usage['charged_output_tokens'] > source['budget']['max_total_output_tokens']):
        raise TopicContractError('repair recovery exceeded original total budget')
    return combined, errors, {**audit, 'cumulative_usage': usage, 'inherited_judgments': len(inherited)}


def run_recovery(*, parent: Path, queue: Path, repair: Path, consent: Path, config_path: Path,
                 preflight: Path, output_root: Path, controlled_root: Path, base_url: str,
                 auth_file: Path, authorization_reference: str, execute: bool = False) -> dict:
    for path in (parent, queue, repair, consent, preflight, output_root):
        private_path(path, controlled_root)
    config = load_config(config_path)
    if file_digest(parent / 'manifest.json') != config['parent_manifest_sha256']:
        raise TopicContractError('recovery parent changed')
    original_config = original.load_config(config_path.parent / 'topic-repair-review-v1.yaml')
    candidates, dispositions, inputs = original.load_sources(queue, repair, consent, original_config)
    source, inherited, errors, parent_audit = parent_evidence(parent, candidates, dispositions)
    if (len(inherited) != config['inherited_judgments'] or len(errors) != config['missing_judgments']
            or sum(error != 'not_dispatched' for error in errors.values()) != config['previous_attempted_failures']):
        raise TopicContractError('recovery missing population changed')
    policy = original.base.load_config(config_path.parent / 'topic-axes-review-v4.yaml')
    route = original.base.require_preflight(preflight, source['protocol'], policy)
    if (route['bound_returned_models'] != source['models'] or not authorization_reference
            or read_json(preflight / 'identity.json')['authorization_reference'] != authorization_reference
            or source['protocol']['endpoint_sha256'] != digest(base_url)
            or preflight / 'manifest.json' in map(Path, source['inputs'])):
        raise TopicContractError('recovery route or authority mismatch')
    inputs = {**source['inputs'], **inputs, **{str(p): file_digest(p) for p in (
        parent / 'manifest.json', preflight / 'manifest.json', config_path,
        config_path.parent.parent / 'scripts/recover_topic_repairs.py')}}
    identity = build_recovery_identity(parent, source, inherited, errors, parent_audit, route, inputs,
                                       config, authorization_reference, preflight)
    table = recovery_table(identity, candidates)
    reserved = sum(item['reserve'] for item in table.values())
    if reserved > identity['budget']['max_input_tokens'] - identity['input_guard']['headroom_tokens']:
        raise TopicContractError('recovery worst-case reservation exceeds original remaining budget')
    workspace = output_root / ('topic-repair-recovery_' + digest(identity)[:20])
    if any(workspace == p or p in workspace.parents for p in (parent, queue, repair, preflight)):
        raise TopicContractError('recovery output overlaps source')
    plan = {'status': 'planned', 'workspace': str(workspace), 'planned_first_requests': len(errors),
            'budget': identity['budget'], 'worst_case_input_reservation': reserved,
            'original_total_budget_unchanged': True, **original.GOVERNANCE}
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
            raise TopicContractError('recovery sealed identity changed')
        if (workspace / 'identity.json').exists() and read_json(workspace / 'identity.json') != identity:
            raise TopicContractError('recovery interrupted identity changed')
        if not sealed:
            _atomic_json(workspace / 'identity.json', identity)
            if not (workspace / 'budget.json').exists():
                _atomic_json(workspace / 'budget.json', {'identity_sha256': digest(identity), 'attempts': []})
        new, errors, audit = original.reconstruct(workspace, identity, candidates, table, reconcile=not sealed)
        by_id = {c['sample_id']: c for c in candidates}
        if not sealed:
            budget = GuardedBudget(workspace, identity, table)

            def call(record):
                batch, payload, sha, slot = record
                endpoint = 'v1/responses' if identity['judges'][batch['judge']]['transport'] == 'responses' else 'v1/chat/completions'
                raw = {**original.capture_http(payload, base_url=base_url, endpoint=endpoint, auth_file=auth_file,
                                              timeout=identity['budget']['request_timeout_seconds']),
                       'request_digest': sha, 'batch': batch, 'payload_sha256': digest(payload), 'reservation': slot}
                _atomic_json(workspace / (sha + '.json'), raw)
                _, _, usage = original.unpack(raw, identity, by_id[batch['ids'][0]])
                budget.settle(slot, usage)

            def collect(batches):
                for start in range(0, len(batches), config['workers']):
                    _, _, state = original.reconstruct(workspace, identity, candidates, table, reconcile=True)
                    if state['circuit_reason']:
                        break
                    dispatched = {a['request_digest'] for a in read_json(workspace / 'budget.json')['attempts']}
                    wave = []
                    for batch in batches[start:start + config['workers']]:
                        payload, sha, estimate = original.payload_record(identity, batch, by_id[batch['ids'][0]])
                        if sha not in dispatched:
                            wave.append((batch, payload, sha, budget.reserve(sha, estimate)))
                    with ThreadPoolExecutor(max_workers=config['workers']) as pool:
                        list(pool.map(call, wave))
                    done, pending, state = original.reconstruct(workspace, identity, candidates, table, reconcile=True)
                    _atomic_json(workspace / 'progress.json', {'reviewed': len(inherited) + len(done), 'expected': 2 * len(candidates),
                        'pending': len(pending), 'requests': state['usage']['requests'], 'circuit_reason': state['circuit_reason']})

            collect(identity['first_batches'])
            new, errors, audit = original.reconstruct(workspace, identity, candidates, table, reconcile=True)
            already_attempted = {(r['judge'], r['sample_id']) for r in identity['parent_attempt_counts'] if r['count']}
            if not audit['circuit_reason']:
                collect([{'judge': j, 'ids': [sid], 'phase': 'repair'} for (j, sid), error in sorted(errors.items())
                         if error in original.REPAIRABLE and (j, sid) not in already_attempted])
            new, errors, audit = original.reconstruct(workspace, identity, candidates, table, reconcile=True)
        decisions = sorted([*inherited, *new], key=lambda r: (r['judge'], r['sample_id']))
        summary = original.summarize(candidates, decisions, dispositions)
        cases = summary.pop('cases')
        usage = {k: parent_audit['usage'][k] + audit['usage'][k] for k in audit['usage']}
        if (usage['requests'] > source['budget']['max_requests'] or usage['charged_input_tokens'] > source['budget']['max_input_tokens']
                or usage['charged_output_tokens'] > source['budget']['max_total_output_tokens']):
            raise TopicContractError('recovery cumulative budget exceeded')
        complete = len(decisions) == 2 * len(candidates) and not audit['circuit_reason']
        report = {**plan, **summary, **audit, 'status': 'complete' if complete else 'incomplete',
                  'cumulative_usage': usage, 'inherited_judgments': len(inherited),
                  'response_failure_counts': dict(Counter(errors.values())), 'decisions_sha256': digest(decisions),
                  'machine_review_completed': complete, 'draft_exported': False, 'inherited_judgments_unchanged': True}
        outputs = {'decisions.json': {'identity': identity, 'decisions': decisions}, 'cases.json': {'cases': cases},
                   'report.json': report, 'circuit.json': {'reason': audit['circuit_reason']}}
        original.source_check(identity)
        if sealed:
            if any(read_json(workspace / name) != value for name, value in outputs.items()):
                raise TopicContractError('recovery replay differs from raw evidence')
        else:
            for name, value in outputs.items():
                _atomic_json(workspace / name, value)
            names = original.REQUIRED | {p.name for p in workspace.glob('*.json') if len(p.stem) == 64}
            manifest = {'schema_version': METHOD + '-manifest', 'identity': identity, **original.GOVERNANCE,
                        'output_digests': {n: file_digest(workspace / n) for n in sorted(names)}}
            manifest['manifest_sha256'] = digest(manifest)
            _atomic_json(workspace / 'manifest.json', manifest)
        return report
