"""One bounded recovery of response failures, preserving all valid v4 judgments."""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
from pathlib import Path

import tiktoken
import yaml

from . import topic_axes_review_v4 as base
from . import topic_axes_support_v4 as core
from ._common import digest, file_digest
from .fact_review_server import _atomic_json
from .topic_adjudication import verify_manifest
from .topic_candidates import private_path, read_json, write_jsonl
from .topic_context import TopicContractError


def load_config(path: Path) -> dict:
    policy = yaml.safe_load(path.read_text(encoding='utf-8'))
    expected = {'schema_version': 'topic-axes-recovery-config-v4',
                'review_config': 'topic-axes-review-v4.yaml', 'batch_size': 6,
                'small_repair_batch_size': 2, 'max_first_requests': 16,
                'max_second_requests': 8, 'workers': 2,
                'budget': {'max_requests': 16, 'max_input_tokens': 160000,
                           'max_total_output_tokens': 64000, 'max_output_tokens': 4000,
                           'request_timeout_seconds': 240}, 'governance': core.GOVERNANCE}
    if policy != expected:
        raise TopicContractError('invalid frozen v4 recovery policy')
    return policy


def batches_for(keys, size: int, phase: str) -> list[dict]:
    result = []
    for name in ('gpt', 'claude'):
        ids = sorted(sid for judge, sid in keys if judge == name)
        result.extend({'judge': name, 'ids': ids[i:i + size], 'phase': phase}
                      for i in range(0, len(ids), size))
    return result


def repair_batches(failed: dict, recovery: dict) -> list[dict]:
    # Empty/absent responses do not demonstrate an output-length problem. Keep
    # their batch size; isolate case-level schema failures into smaller batches.
    case_errors = {'invalid_case_axes', 'missing_or_duplicate_case'}
    whole = [key for key, reason in failed.items() if reason in base.REPAIRABLE - case_errors]
    cases = [key for key, reason in failed.items() if reason in case_errors]
    return (batches_for(whole, recovery['batch_size'], 'recovery_response')
            + batches_for(cases, recovery['small_repair_batch_size'], 'recovery_case'))


def run_recovery(*, parent: Path, config_path: Path, output_root: Path, controlled_root: Path,
                 preflight: Path, calibration: Path, confidence_audit: Path, private_inputs: dict,
                 base_url: str, auth_file: Path, authorization_reference: str, execute: bool = False) -> dict:
    for path in (parent, output_root):
        private_path(path, controlled_root)
    recovery = load_config(config_path)
    source_path = config_path.parent / recovery['review_config']
    policy = base.load_config(source_path)
    manifest = verify_manifest(parent, {'identity.json', 'report.json', 'decisions.json', 'cases.json', 'budget.json'})
    source_identity = read_json(parent / 'identity.json')
    source_report = read_json(parent / 'report.json')
    source_record = read_json(parent / 'decisions.json')
    if (parent.name != 'topic-axes-v4-private_review_' + digest(source_identity)[:20]
            or source_report['status'] != 'incomplete' or source_report['draft_exported']
            or any(manifest.get(k) != v or source_report.get(k) != v for k, v in core.GOVERNANCE.items())
            or source_report['model_identity_circuit_open']
            or set(source_report['response_failure_counts']) - base.REPAIRABLE
            or source_identity != manifest['identity'] or source_record['identity'] != source_identity
            or source_report['decisions_sha256'] != digest(source_record['decisions'])
            or (parent / 'train.draft.jsonl').exists() or not authorization_reference):
        raise TopicContractError('recovery requires sealed response-only failure with unchanged model identity')
    # Recompute the original plan, including the calibration and all private sources.
    original_plan = base.run_review(scope='private_review', config_path=source_path,
        output_root=parent.parent, controlled_root=controlled_root, preflight=preflight,
        calibration=calibration, confidence_audit=confidence_audit, private_inputs=private_inputs,
        base_url=base_url, auth_file=auth_file,
        authorization_reference=source_identity['authorization_reference'])
    if (Path(original_plan['workspace']).resolve() != parent.resolve()
            or original_plan['protocol_binding_sha256'] != digest(source_identity['protocol'])):
        raise TopicContractError('recovery parent no longer matches frozen v4 plan')
    candidates, prior, inputs = core.load_private_inputs(private_inputs, policy, controlled_root, base_url)
    inputs = dict(inputs)
    inherited = source_record['decisions']
    models = source_identity['route']['bound_returned_models']
    base.validate_v4_decisions(candidates, inherited, policy, models)
    summary = core.private_summary(candidates, prior, inherited, policy)
    cases = summary.pop('cases')
    if (any(source_report.get(k) != v for k, v in summary.items())
            or read_json(parent / 'cases.json') != {'cases': cases}):
        raise TopicContractError('recovery parent statistics changed')
    by_id = {r['sample_id']: r for r in candidates}
    inherited_keys = {(r['judge'], r['sample_id']) for r in inherited}
    missing = {(judge, sid) for judge in policy['judges'] for sid in by_id} - inherited_keys
    first = batches_for(missing, recovery['batch_size'], 'recovery_first')
    counter = tiktoken.get_encoding('o200k_base')
    budget = recovery['budget']
    estimate = sum(len(counter.encode(json.dumps(base.wire_payload([by_id[s] for s in b['ids']],
                    policy['judges'][b['judge']], budget), ensure_ascii=False))) + 256 for b in first)
    if not first or len(first) > recovery['max_first_requests'] or estimate > budget['max_input_tokens']:
        raise TopicContractError('v4 missing-response recovery exceeds frozen budget')
    inputs.update({str(parent / 'manifest.json'): file_digest(parent / 'manifest.json'),
                   str(confidence_audit / 'manifest.json'): file_digest(confidence_audit / 'manifest.json')})
    identity = {'scope': 'response_recovery', 'protocol': source_identity['protocol'],
                'parent_manifest_sha256': file_digest(parent / 'manifest.json'),
                'inherited_decisions_sha256': digest(inherited), 'inputs': inputs,
                'recovery_config_sha256': file_digest(config_path), 'source_sha256': file_digest(Path(__file__)),
                'budget': budget, 'batch_plan_sha256': digest(first),
                'authorization_reference': authorization_reference}
    workspace = output_root / ('topic-axes-v4-recovery_' + digest(identity)[:20])
    for source in [parent, preflight, calibration, confidence_audit, *private_inputs.values()]:
        if workspace.resolve() == source.resolve() or source.resolve() in workspace.resolve().parents:
            raise TopicContractError('recovery output overlaps source')
    plan = {'status': 'planned', 'workspace': str(workspace), 'population': len(candidates),
            'parent': str(parent), 'inherited_judgments': len(inherited), 'missing_judgments': len(missing),
            'planned_first_requests': len(first), 'estimated_first_input_tokens': estimate,
            'budget': budget, **core.GOVERNANCE}
    if not execute:
        return plan
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace.chmod(0o700)
    with (workspace / 'run.lock').open('a') as lock:
        (workspace / 'run.lock').chmod(0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (workspace / 'manifest.json').exists():
            saved = verify_manifest(workspace, {'identity.json', 'decisions.json', 'cases.json', 'report.json', 'budget.json'})
            if saved['identity'] != identity:
                raise TopicContractError('recovery replay identity changed')
            return read_json(workspace / 'report.json')
        if (workspace / 'identity.json').exists() and read_json(workspace / 'identity.json') != identity:
            raise TopicContractError('recovery workspace identity changed')
        _atomic_json(workspace / 'identity.json', identity)
        requests = base.Requests(workspace, identity, policy, base_url, auth_file, models)
        decisions, failed = list(inherited), {}

        def call(batch):
            if any((batch['judge'], sid) in inherited_keys for sid in batch['ids']):
                raise TopicContractError('recovery must never reask a valid parent judgment')
            return requests.call(batch, [by_id[s] for s in batch['ids']])

        with ThreadPoolExecutor(max_workers=recovery['workers']) as pool:
            for batch, (valid, errors) in zip(first, pool.map(call, first), strict=True):
                decisions.extend(valid)
                failed.update({(batch['judge'], sid): reason for sid, reason in errors.items()})
        repairs = repair_batches(failed, recovery)
        remaining = min(recovery['max_second_requests'], budget['max_requests'] - len(first))
        if not requests.circuit.is_set():
            for batch in repairs[:remaining]:
                valid, errors = call(batch)
                decisions.extend(valid)
                failed.update({(batch['judge'], sid): reason for sid, reason in errors.items()})
                for row in valid:
                    failed.pop((row['judge'], row['sample_id']), None)
        decisions.sort(key=lambda r: (r['judge'], r['sample_id']))
        base.validate_v4_decisions(candidates, decisions, policy, models)
        restored = [r for r in decisions if (r['judge'], r['sample_id']) in inherited_keys]
        if restored != sorted(inherited, key=lambda r: (r['judge'], r['sample_id'])):
            raise TopicContractError('recovery changed a valid parent judgment')
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
                or any(file_digest(Path(path)) != sha for path, sha in inputs.items())
                or base.binding_for(policy, source_path.parent / policy['synthetic_fixtures'], base_url) != identity['protocol']):
            raise TopicContractError('recovery source changed during execution')
        _atomic_json(workspace / 'decisions.json', {'identity': identity, 'decisions': decisions})
        _atomic_json(workspace / 'cases.json', {'cases': cases})
        _atomic_json(workspace / 'report.json', report)
        if complete:
            selected = set(summary['selected_sample_ids'])
            write_jsonl(workspace / 'train.draft.jsonl', [r for r in candidates if r['sample_id'] in selected])
        names = ['identity.json', 'decisions.json', 'cases.json', 'report.json', 'budget.json']
        names += [p.name for p in workspace.glob('*.json') if len(p.stem) == 64]
        if complete:
            names.append('train.draft.jsonl')
        manifest = {'schema_version': 'topic-axes-recovery-manifest-v4', 'identity': identity,
                    **core.GOVERNANCE, 'output_digests': {name: file_digest(workspace / name) for name in names}}
        manifest['manifest_sha256'] = digest(manifest)
        _atomic_json(workspace / 'manifest.json', manifest)
        return report
