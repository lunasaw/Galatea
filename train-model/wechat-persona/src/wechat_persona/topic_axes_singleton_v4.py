"""Explicit second recovery: isolated cases, immutable parents, no label retries.

The only request change is batch membership. This checks a transport hypothesis;
it does not establish why the upstream gateway sometimes emits empty responses.
"""
from __future__ import annotations

import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
from pathlib import Path

import tiktoken
import yaml

from . import topic_axes_review_v4 as base
from . import topic_axes_recovery_v4 as previous
from . import topic_axes_support_v4 as core
from ._common import digest, file_digest
from .fact_review_server import _atomic_json
from .topic_adjudication import verify_manifest
from .topic_candidates import private_path, read_json, write_jsonl
from .topic_context import TopicContractError
from .topic_review_budget import ReviewBudget


def load_config(path: Path) -> dict:
    policy = yaml.safe_load(path.read_text(encoding='utf-8'))
    expected = {'schema_version': 'topic-axes-singleton-config-v4',
                'review_config': 'topic-axes-review-v4.yaml', 'parent_config': 'topic-axes-recovery-v4.yaml',
                'parent_manifest_sha256': 'e279e78a4bd597551146df5b31c69c5f3e6abebc504d87ab4481c7d70b0726b2',
                'inherited_judgments': 382, 'missing_judgments': 18, 'missing_judge': 'claude',
                'batch_size': 1, 'workers': 2,
                'budget': {'max_requests': 36, 'max_input_tokens': 180000,
                           'max_total_output_tokens': 144000, 'max_output_tokens': 4000,
                           'request_timeout_seconds': 240}, 'governance': core.GOVERNANCE}
    if policy != expected:
        raise TopicContractError('invalid frozen singleton recovery policy')
    return policy


def verify_responses(directory: Path, candidates: list, prior: list, policy: dict,
                     models: dict, inherited: list) -> tuple[dict, list, dict]:
    """Reconstruct all judgments and response failures from raw bytes and ledger."""
    manifest = verify_manifest(directory, {'identity.json', 'report.json', 'decisions.json', 'cases.json', 'budget.json'})
    identity, report = read_json(directory / 'identity.json'), read_json(directory / 'report.json')
    record = read_json(directory / 'decisions.json')
    if (identity != manifest['identity'] or record['identity'] != identity
            or report['decisions_sha256'] != digest(record['decisions'])
            or any(manifest.get(k) != v or report.get(k) != v for k, v in core.GOVERNANCE.items())):
        raise TopicContractError('singleton source identity mismatch')
    base.validate_v4_decisions(candidates, record['decisions'], policy, models)
    by_id = {r['sample_id']: r for r in candidates}
    reconstructed = {(r['judge'], r['sample_id']): r for r in inherited}
    errors, shapes, reservations = {}, Counter(), set()
    ledger = read_json(directory / 'budget.json')
    # Reservation order is the recorded dispatch order; file hash order is unrelated.
    for index, attempt in enumerate(ledger['attempts']):
        name = attempt['request_digest'] + '.json'
        if name not in manifest['output_digests']:
            raise TopicContractError('singleton source interrupted without raw response')
        raw = read_json(directory / name)
        batch, judge = raw['batch'], raw['batch']['judge']
        if judge not in policy['judges'] or any(sid not in by_id for sid in batch['ids']):
            raise TopicContractError('singleton source unknown request case')
        if any((judge, sid) in reconstructed for sid in batch['ids']):
            raise TopicContractError('singleton source retried a valid judgment')
        rows = [by_id[sid] for sid in batch['ids']]
        payload = base.wire_payload(rows, policy['judges'][judge], identity['budget'])
        sha = digest({'identity': identity, 'batch': batch, 'payload': payload})
        if (raw['request_digest'] != sha or attempt['request_digest'] != sha
                or raw['payload_sha256'] != digest(payload) or raw['reservation'] != index
                or raw['raw_body_sha256'] != digest(raw['raw_body_base64']) or sha in reservations):
            raise TopicContractError('singleton source raw request binding changed')
        reservations.add(sha)
        valid = []
        if raw['error_type']:
            failures = {sid: raw['error_type'] for sid in batch['ids']}
        else:
            result = core.strict_json(base64.b64decode(raw['raw_body_base64'], validate=True))
            if result.get('model') != models[judge]:
                raise TopicContractError('singleton source model identity changed')
            if judge == 'claude':
                choice = result.get('choices', [{}])[0]
                message = choice.get('message', {})
                shapes[(len(rows), choice.get('finish_reason'), bool(message.get('content')),
                        tuple(sorted(message)))] += 1
            try:
                valid, failures = core.decode_response(result, policy['judges'][judge], rows)
            except TopicContractError as exc:
                failures = {sid: str(exc) for sid in batch['ids']}
        errors.update({(judge, sid): reason for sid, reason in failures.items()})
        for row in valid:
            key = judge, row['sample_id']
            reconstructed[key] = {**row, 'judge': judge, 'request_digest': sha,
                                   'review_protocol': base.METHOD, 'prompt_sha256': digest(base.PROMPT)}
            errors.pop(key, None)
    if (sorted(reconstructed.values(), key=lambda r: (r['judge'], r['sample_id'])) != record['decisions']
            or ReviewBudget(directory, identity, identity['budget']).usage() != report['usage']):
        raise TopicContractError('singleton source raw judgments or usage mismatch')
    summary = core.private_summary(candidates, prior, record['decisions'], policy)
    cases = summary.pop('cases')
    if (any(report.get(k) != v for k, v in summary.items())
            or read_json(directory / 'cases.json') != {'cases': cases}):
        raise TopicContractError('singleton source statistics changed')
    return report, record['decisions'], {
        'raw_requests_verified': len(reservations),
        'claude_response_shapes': [{'batch_size': k[0], 'finish_reason': k[1], 'has_content': k[2],
                                    'message_fields': list(k[3]), 'count': v} for k, v in sorted(shapes.items())],
        'remaining_failures': errors,
    }


def run_recovery(*, parent: Path, config_path: Path, output_root: Path, controlled_root: Path,
                 preflight: Path, calibration: Path, confidence_audit: Path, private_inputs: dict,
                 base_url: str, auth_file: Path, authorization_reference: str, execute: bool = False) -> dict:
    for path in (parent, output_root):
        private_path(path, controlled_root)
    recovery = load_config(config_path)
    if file_digest(parent / 'manifest.json') != recovery['parent_manifest_sha256']:
        raise TopicContractError('singleton recovery requires the explicitly bound parent')
    parent_manifest = verify_manifest(parent, {'identity.json', 'report.json', 'decisions.json', 'cases.json', 'budget.json'})
    source_identity = read_json(parent / 'identity.json')
    source_report = read_json(parent / 'report.json')
    if (source_identity != parent_manifest['identity'] or source_identity['scope'] != 'response_recovery'
            or source_report['status'] != 'incomplete' or source_report['draft_exported']
            or source_report['model_identity_circuit_open']
            or source_report['response_failure_counts'] != {'incomplete axes response': recovery['missing_judgments']}
            or (parent / 'train.draft.jsonl').exists() or not authorization_reference
            or authorization_reference == source_identity['authorization_reference']):
        raise TopicContractError('singleton recovery requires sealed empty responses and new scoped authorization')
    original = Path(source_report['parent'])
    private_path(original, controlled_root)
    old_plan = previous.run_recovery(parent=original, config_path=config_path.parent / recovery['parent_config'],
        output_root=parent.parent, controlled_root=controlled_root, preflight=preflight, calibration=calibration,
        confidence_audit=confidence_audit, private_inputs=private_inputs, base_url=base_url, auth_file=auth_file,
        authorization_reference=source_identity['authorization_reference'])
    if Path(old_plan['workspace']).resolve() != parent.resolve():
        raise TopicContractError('singleton recovery chain no longer matches the old plan')
    source_path = config_path.parent / recovery['review_config']
    policy = base.load_config(source_path)
    candidates, prior, inputs = core.load_private_inputs(private_inputs, policy, controlled_root, base_url)
    inputs = dict(inputs)
    binding = base.binding_for(policy, source_path.parent / policy['synthetic_fixtures'], base_url)
    models = read_json(original / 'identity.json')['route']['bound_returned_models']
    _, old_decisions, original_audit = verify_responses(original, candidates, prior, policy, models, [])
    _, inherited, parent_audit = verify_responses(parent, candidates, prior, policy, models, old_decisions)
    by_id = {r['sample_id']: r for r in candidates}
    inherited_keys = {(r['judge'], r['sample_id']) for r in inherited}
    missing = {(judge, sid) for judge in policy['judges'] for sid in by_id} - inherited_keys
    if (len(inherited) != recovery['inherited_judgments'] or len(missing) != recovery['missing_judgments']
            or {judge for judge, _ in missing} != {recovery['missing_judge']}
            or set(parent_audit['remaining_failures']) != missing
            or set(parent_audit['remaining_failures'].values()) != {'incomplete axes response'}
            or source_identity['protocol'] != binding):
        raise TopicContractError('singleton missing population or protocol changed')
    audit = {'original': {k: v for k, v in original_audit.items() if k != 'remaining_failures'},
             'parent': {k: v for k, v in parent_audit.items() if k != 'remaining_failures'},
             'upstream_root_cause_established': False, 'hypothesis': 'single_case_request_avoids_batch_empty_response'}
    first = previous.batches_for(missing, recovery['batch_size'], 'singleton_first')
    budget = recovery['budget']
    counter = tiktoken.get_encoding('o200k_base')
    estimate = sum(len(counter.encode(json.dumps(base.wire_payload([by_id[s] for s in b['ids']],
                    policy['judges'][b['judge']], budget), ensure_ascii=False))) + 256 for b in first)
    if estimate > budget['max_input_tokens'] or len(first) > budget['max_requests']:
        raise TopicContractError('singleton plan exceeds budget')
    inputs.update({str(p / 'manifest.json'): file_digest(p / 'manifest.json')
                   for p in (parent, original, confidence_audit, preflight, calibration)})
    identity = {'scope': 'singleton_response_recovery', 'protocol': binding,
                'parent_manifest_sha256': file_digest(parent / 'manifest.json'),
                'inherited_decisions_sha256': digest(inherited), 'inputs': inputs,
                'recovery_config_sha256': file_digest(config_path), 'source_sha256': file_digest(Path(__file__)),
                'previous_source_sha256': file_digest(Path(previous.__file__)),
                'budget': budget, 'batch_plan_sha256': digest(first), 'diagnostic_sha256': digest(audit),
                'authorization_reference': authorization_reference}
    workspace = output_root / ('topic-axes-v4-singleton_' + digest(identity)[:20])
    for source in [parent, original, preflight, calibration, confidence_audit, *private_inputs.values()]:
        if workspace.resolve() == source.resolve() or source.resolve() in workspace.resolve().parents:
            raise TopicContractError('singleton output overlaps source')
    plan = {'status': 'planned', 'workspace': str(workspace), 'population': len(candidates), 'parent': str(parent),
            'inherited_judgments': len(inherited), 'missing_judgments': len(missing),
            'planned_first_requests': len(first), 'estimated_first_input_tokens': estimate,
            'budget': budget, 'batch_size': recovery['batch_size'], 'upstream_root_cause_established': False,
            **core.GOVERNANCE}
    if not execute:
        return plan
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace.chmod(0o700)
    with (workspace / 'run.lock').open('a') as lock:
        (workspace / 'run.lock').chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (workspace / 'manifest.json').exists():
            saved = verify_manifest(workspace, {'identity.json', 'decisions.json', 'cases.json', 'report.json',
                                               'budget.json', 'diagnostic.json'})
            if saved['identity'] != identity:
                raise TopicContractError('singleton replay identity changed')
            return read_json(workspace / 'report.json')
        if (workspace / 'identity.json').exists() and read_json(workspace / 'identity.json') != identity:
            raise TopicContractError('singleton workspace identity changed')
        _atomic_json(workspace / 'identity.json', identity)
        _atomic_json(workspace / 'diagnostic.json', audit)
        requests = base.Requests(workspace, identity, policy, base_url, auth_file, models)
        decisions, failed = list(inherited), {}

        def call(batch):
            if len(batch['ids']) != 1 or any((batch['judge'], sid) not in missing for sid in batch['ids']):
                raise TopicContractError('singleton recovery must only ask one missing judgment')
            return requests.call(batch, [by_id[s] for s in batch['ids']])

        def collect(batches):
            with ThreadPoolExecutor(max_workers=recovery['workers']) as pool:
                for batch, (valid, errors) in zip(batches, pool.map(call, batches), strict=True):
                    decisions.extend(valid)
                    failed.update({(batch['judge'], sid): reason for sid, reason in errors.items()})
                    for row in valid:
                        failed.pop((row['judge'], row['sample_id']), None)

        collect(first)
        if not requests.circuit.is_set():
            repairable = [key for key, reason in failed.items() if reason in base.REPAIRABLE]
            repairs = previous.batches_for(repairable, 1, 'singleton_repair')
            collect(repairs[:budget['max_requests'] - len(first)])
        decisions.sort(key=lambda r: (r['judge'], r['sample_id']))
        base.validate_v4_decisions(candidates, decisions, policy, models)
        if [r for r in decisions if (r['judge'], r['sample_id']) in inherited_keys] != inherited:
            raise TopicContractError('singleton changed a valid inherited judgment')
        summary = core.private_summary(candidates, prior, decisions, policy)
        cases = summary.pop('cases')
        complete = len(decisions) == 2 * len(candidates)
        report = {**plan, **summary, 'status': 'complete' if complete else 'incomplete',
                  'usage': requests.budget.usage(), 'new_valid_judgments': len(decisions) - len(inherited),
                  'decisions_sha256': digest(decisions), 'inherited_judgments_unchanged': True,
                  'unresolved_response_failures': len(failed), 'response_failure_counts': dict(Counter(failed.values())),
                  'model_identity_circuit_open': requests.circuit.is_set(), 'draft_exported': complete,
                  'provider_model_revision_attested': False}
        if (file_digest(config_path) != identity['recovery_config_sha256']
                or file_digest(Path(__file__)) != identity['source_sha256']
                or file_digest(Path(previous.__file__)) != identity['previous_source_sha256']
                or any(file_digest(Path(path)) != sha for path, sha in inputs.items())
                or base.binding_for(base.load_config(source_path), source_path.parent / policy['synthetic_fixtures'], base_url) != binding):
            raise TopicContractError('singleton source changed during execution')
        _atomic_json(workspace / 'decisions.json', {'identity': identity, 'decisions': decisions})
        _atomic_json(workspace / 'cases.json', {'cases': cases})
        _atomic_json(workspace / 'report.json', report)
        if complete:
            selected = set(summary['selected_sample_ids'])
            write_jsonl(workspace / 'train.draft.jsonl', [r for r in candidates if r['sample_id'] in selected])
        names = ['identity.json', 'decisions.json', 'cases.json', 'report.json', 'budget.json', 'diagnostic.json']
        names += [p.name for p in workspace.glob('*.json') if len(p.stem) == 64]
        if complete:
            names.append('train.draft.jsonl')
        manifest = {'schema_version': 'topic-axes-singleton-manifest-v4', 'identity': identity,
                    **core.GOVERNANCE, 'output_digests': {name: file_digest(workspace / name) for name in names}}
        manifest['manifest_sha256'] = digest(manifest)
        _atomic_json(workspace / 'manifest.json', manifest)
        return report
