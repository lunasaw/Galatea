"""Exclude known context regressions before any new semantic review request."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from ._common import digest, file_digest
from .topic_adjudication import verify_manifest
from .topic_candidates import private_path, read_json, rows
from .topic_context import TopicContractError
from .topic_repair_contract import validate_repaired_rows
from .topic_train_repair import GOVERNANCE, publish_packet


def partition_review_queue(candidates: list[dict], previous_cases: list[dict],
                           comparison: list[dict]) -> tuple[list, list]:
    validate_repaired_rows(candidates)
    old = {c['sample_id']: c for c in previous_cases}
    records = {c['parent_sample_id']: c for c in comparison}
    current = {c['parent_sample_id']: c for c in candidates}
    if (len(old) != len(previous_cases) or len(records) != len(comparison) or len(current) != len(candidates)
            or set(old) != set(records) or not set(current) <= set(old)):
        raise TopicContractError('repair review queue population mismatch')
    queue, dispositions = [], []
    for sid, prior in old.items():
        new, record = current.get(sid), records[sid]
        if new is None:
            if record['status'] != 'quarantined':
                raise TopicContractError('repair candidate missing without quarantine')
            reason, missing = 'construction_quarantined', []
        else:
            missing = sorted(set(prior['required_context_ids']) - set(new['context_message_ids']))
            if (new['parent_candidate_sha256'] != prior['candidate_sha256']
                    or new['candidate_sha256'] != record['candidate_sha256']
                    or new['prior_hard_risks'] != prior['prior_hard_risks']
                    or missing != record['old_review_required_context_missing']
                    or record['status'] != 'built' or not new['fresh_review_required']):
                raise TopicContractError('repair review queue evidence mismatch')
            if prior['prior_hard_risks']:
                reason = 'prior_hard_risk_requires_adjudication'
            elif missing:
                reason = 'known_required_context_regression'
            else:
                reason = 'ready_for_fresh_machine_review'
                queue.append(new)
        dispositions.append({'parent_sample_id': sid, 'parent_candidate_sha256': prior['candidate_sha256'],
                             'candidate_sha256': new['candidate_sha256'] if new else None,
                             'disposition': reason, 'missing_required_context_ids': missing,
                             'previous_disposition': prior['disposition'],
                             'prior_hard_risks': prior['prior_hard_risks'], **GOVERNANCE})
    return queue, dispositions


def prepare_queue(*, repair: Path, output_root: Path, controlled_root: Path, execute: bool = False) -> dict:
    for path in (repair, output_root):
        private_path(path, controlled_root)
    if repair == output_root or repair in output_root.parents or output_root in repair.parents:
        raise TopicContractError('review queue output overlaps repair source')
    manifest = verify_manifest(repair, {'selection.json', 'context-comparison.json', 'context.candidates.jsonl', 'report.json'})
    if (manifest['schema_version'] != 'topic-train-repair-manifest-v1'
            or any(manifest.get(k) != v for k, v in GOVERNANCE.items())):
        raise TopicContractError('review queue requires machine-only train repair packet')
    queue, dispositions = partition_review_queue(list(rows(repair / 'context.candidates.jsonl')),
        read_json(repair / 'selection.json')['cases'], read_json(repair / 'context-comparison.json')['cases'])
    identity = {'method': 'topic-repair-review-queue-v1', 'repair_manifest_sha256': file_digest(repair / 'manifest.json'),
                'implementation_sha256': {name: file_digest(Path(__file__).with_name(name)) for name in (
                    'topic_repair_review_queue.py', 'topic_repair_contract.py', 'topic_train_repair.py')},
                'queue_digests': [c['candidate_sha256'] for c in queue], 'dispositions_sha256': digest(dispositions)}
    output = output_root / ('topic-repair-review-queue_' + digest(identity)[:20])
    report = {'schema_version': 'topic-repair-review-queue-report-v1', 'population': len(dispositions),
              'disposition_counts': dict(Counter(d['disposition'] for d in dispositions)),
              'queued_for_review': len(queue), 'semantic_review_completed': False,
              'quality_improvement_claimed': False, 'old_validation_changed': False,
              'external_requests': 0, 'training_ready': False, **GOVERNANCE}
    if not execute:
        return {**report, 'status': 'planned', 'output_dir': str(output)}
    if file_digest(repair / 'manifest.json') != identity['repair_manifest_sha256']:
        raise TopicContractError('repair changed during queue preparation')
    publish_packet(output, identity, {'review.queue.jsonl': queue, 'dispositions.json': {'cases': dispositions},
                                      'report.json': report})
    return {**report, 'status': 'built', 'output_dir': str(output),
            'manifest_sha256': file_digest(output / 'manifest.json')}
