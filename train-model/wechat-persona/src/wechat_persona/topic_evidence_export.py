"""Publish unchanged draft rows with separately verified semantic reply links."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import shutil
import tempfile

from ._common import digest, file_digest
from .datasets import _publish_directory_noreplace
from .topic_candidates import private_path, read_json, verified_pilot, write_json, write_jsonl
from .topic_comparison import verified_review
from .topic_context import TopicContractError


METHOD = 'topic-reply-evidence-audit-v1'


def bind_reply_evidence(candidate: dict, decision: dict, audit_sha256: str) -> dict:
    if (candidate['schema_version'] != 'topic-reply-candidate-v1'
            or candidate['candidate_sha256'] != digest({k: v for k, v in candidate.items() if k != 'candidate_sha256'})
            or candidate['split'] != 'train' or candidate['human_review_completed'] or candidate['formal_training_eligible']):
        raise TopicContractError('invalid evidence draft candidate')
    if (decision['sample_id'] != candidate['sample_id'] or decision['candidate_sha256'] != candidate['candidate_sha256']
            or decision['method'] != METHOD or decision['review_kind'] != 'machine'
            or decision['view_kind'] != 'selected' or decision['status'] != 'keep'
            or decision['reason'] != 'usable_reply' or not decision['reply_link_correct']
            or not decision['context_complete'] or not .9 <= decision['confidence'] <= 1):
        raise TopicContractError('reply evidence does not qualify for draft binding')
    context = candidate['context_messages']
    source_ids = [row['message_id'] for row in context]
    if source_ids != candidate['context_message_ids'] or set(source_ids) & set(candidate['target_message_ids']):
        raise TopicContractError('invalid evidence context identity')
    for name, index_key in (('responds_to_ids', 'responds_to_indices'), ('required_context_ids', 'required_context_indices')):
        indices = decision[index_key]
        ids = decision[name]
        if (not ids or len(set(ids)) != len(ids)
                or any(type(i) is not int or not 0 <= i < len(context) for i in indices)
                or [source_ids[i] for i in indices] != ids):
            raise TopicContractError('reply evidence indices disagree with selected context')
    responding = set(decision['responds_to_ids'])
    required = set(decision['required_context_ids'])
    if not responding <= required or any(row['role'] != 'self' for row in context if row['message_id'] in responding):
        raise TopicContractError('reply evidence requires real self messages')
    for row in context:
        if ((row['owner_scope'], row['split'], row['session_id'], row['day']) != (
                candidate['owner_scope'], 'train', candidate['session_id'], candidate['day'])
                or row['order'] >= candidate['cutoff']['source_record_index']
                or row['timestamp'] > candidate['cutoff']['timestamp']):
            raise TopicContractError('reply evidence is outside causal scope')
    result = {'schema_version': 'reply-link-evidence-v1', 'sample_id': candidate['sample_id'],
              'candidate_sha256': candidate['candidate_sha256'], 'audit_manifest_sha256': audit_sha256,
              'method': METHOD, 'review_kind': 'machine', 'confidence': decision['confidence'],
              'responds_to_ids': decision['responds_to_ids'], 'required_context_ids': decision['required_context_ids'],
              'target_message_ids': candidate['target_message_ids'],
              'original_basis': candidate['reply_link']['basis'],
              'original_responds_to_ids': candidate['reply_link']['responds_to_ids'],
              'basis': 'machine_semantic_verification_within_existing_context',
              'source_reply_to_claimed': False, 'target_text_used_for_context_selection': False,
              'target_text_used_for_offline_verification': True, 'expanded_diagnostic_used': False,
              'human_review_completed': False, 'formal_training_eligible': False}
    result['evidence_sha256'] = digest(result)
    return result


def compile_evidence_draft(*, pilot: Path, review: Path, audit: Path,
                           output_root: Path, controlled_root: Path) -> dict:
    for path in (pilot, review, audit, output_root):
        private_path(path, controlled_root)
    if any(output_root == path or path in output_root.parents for path in (pilot, review, audit)):
        raise TopicContractError('evidence draft output must be outside inputs')
    _, candidates = verified_pilot(pilot)
    _, previous = verified_review(pilot, review, candidates)
    manifest = read_json(audit / 'manifest.json')
    if (manifest['manifest_sha256'] != digest({k: v for k, v in manifest.items() if k != 'manifest_sha256'})
            or any(Path(name).name != name or file_digest(audit / name) != sha
                   for name, sha in manifest['output_digests'].items())):
        raise TopicContractError('evidence audit manifest or outputs changed')
    identity = manifest['identity']
    if (identity['method'] != METHOD or identity['pilot_manifest_sha256'] != file_digest(pilot / 'manifest.json')
            or identity['prior_review_sha256'] != file_digest(review / 'decisions.json')):
        raise TopicContractError('evidence audit binds a different population or review')
    report = read_json(audit / 'report.json')
    payload = read_json(audit / 'decisions.json')
    decisions = payload['decisions']
    if report['status'] != 'complete' or payload['identity'] != identity or digest(decisions) != report['decisions_sha256']:
        raise TopicContractError('incomplete or changed evidence audit')
    selected = {row['sample_id']: row for row in decisions if row['view_kind'] == 'selected'}
    if (len(selected) != sum(row['view_kind'] == 'selected' for row in decisions)
            or set(selected) != {row['sample_id'] for row in candidates}):
        raise TopicContractError('evidence audit must cover the full frozen population')
    kept, links = [], []
    audit_sha256 = file_digest(audit / 'manifest.json')
    for candidate in candidates:
        decision = selected[candidate['sample_id']]
        if decision['candidate_sha256'] != candidate['candidate_sha256']:
            raise TopicContractError('evidence audit candidate changed')
        if previous[candidate['sample_id']]['status'] == 'keep' and decision['status'] == 'keep':
            links.append(bind_reply_evidence(candidate, decision, audit_sha256))
            kept.append(candidate)
    if [row['sample_id'] for row in kept] != report['consensus_keep_ids']:
        raise TopicContractError('audit consensus selection does not match bound evidence')
    identity = {'pilot_manifest_sha256': file_digest(pilot / 'manifest.json'),
                'prior_review_sha256': file_digest(review / 'decisions.json'),
                'audit_manifest_sha256': audit_sha256, 'implementation_sha256': file_digest(Path(__file__))}
    output = output_root / ('topic-evidence-draft_' + digest(identity)[:20])
    link_changes = Counter()
    for row in links:
        old, new = set(row['original_responds_to_ids']), set(row['responds_to_ids'])
        if old == new:
            link_changes['unchanged'] += 1
        elif new < old:
            link_changes['narrowed_within_incoming_turn'] += 1
        elif old & new:
            link_changes['additional_earlier_anchors'] += 1
        else:
            link_changes['redirected_to_earlier_self'] += 1
    result = {'status': 'built', 'output_dir': str(output), 'kept_count': len(kept), 'population': len(candidates),
              'new_link_count': sum(set(row['responds_to_ids']) != set(row['original_responds_to_ids']) for row in links),
              'link_change_counts': dict(link_changes),
              'training_ready': False, 'expanded_context_used': False, 'human_review_completed': False}
    if output.exists():
        existing = read_json(output / 'manifest.json')
        if (existing['identity'] != identity or existing['manifest_sha256'] != digest({k: v for k, v in existing.items() if k != 'manifest_sha256'})
                or any(file_digest(output / name) != sha for name, sha in existing['output_digests'].items())):
            raise TopicContractError('existing evidence draft changed')
        return {**result, 'status': 'already_built'}
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_root.chmod(0o700)
    staging = Path(tempfile.mkdtemp(prefix='.topic-evidence-draft-', dir=output_root))
    try:
        write_jsonl(staging / 'train.draft.jsonl', kept)
        write_jsonl(staging / 'reply-link-evidence.jsonl', links)
        write_json(staging / 'selection.json', {
            'population_sample_ids': [row['sample_id'] for row in candidates],
            'selected_sample_ids': [row['sample_id'] for row in kept],
            'rule': 'prior_keep_and_blind_selected_view_keep_with_valid_bound_evidence',
            'previous_counts': dict(Counter(row['status'] for row in previous.values())),
            'new_counts': dict(Counter(row['status'] for row in selected.values())),
            'all_other_rows_remain_excluded_or_deferred': True})
        manifest = {'schema_version': 'topic-evidence-reviewed-draft-v1', 'identity': identity,
                    'counts': {'train': len(kept), 'population': len(candidates)},
                    'human_review_completed': False, 'formal_training_eligible': False,
                    'promotable': False, 'training_run': False,
                    'context_and_target_unchanged': True, 'machine_evidence_is_not_source_reply_to': True,
                    'output_digests': {path.name: file_digest(path) for path in staging.iterdir()}}
        manifest['manifest_sha256'] = digest(manifest)
        write_json(staging / 'manifest.json', manifest)
        if audit_sha256 != file_digest(audit / 'manifest.json'):
            raise TopicContractError('audit changed during draft compilation')
        _publish_directory_noreplace(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return result
