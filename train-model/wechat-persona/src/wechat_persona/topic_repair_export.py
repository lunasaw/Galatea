"""Compile re-reviewed v2 train candidates with atomically bound machine evidence."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path

from ._common import digest, file_digest
from .topic_adjudication import verify_manifest
from .topic_candidates import private_path, read_json, rows
from .topic_context import TopicContractError
from .topic_repair_contract import validate_repaired_rows
from .topic_repair_protocol import validate_axes, validate_candidate
from . import topic_repair_review as review
from .topic_train_repair import GOVERNANCE, publish_packet


def bind_reviewed_candidate(candidate: dict, case: dict, pair: list, review_sha: str,
                            axes_validator=validate_axes) -> dict:
    validate_candidate(candidate)
    if (case['candidate_sha256'] != candidate['candidate_sha256'] or case['sample_id'] != candidate['sample_id']
            or case['disposition'] != 'machine_consensus_keep' or case['prior_hard_risks']
            or len(pair) != 2 or {d['judge'] for d in pair} != {'gpt', 'claude'}):
        raise TopicContractError('repaired draft requires complete machine consensus')
    for decision in pair:
        expected = axes_validator(candidate, decision['axes'])
        if (any(decision.get(k) != v for k, v in expected.items()) or decision['status'] != 'keep'
                or case['decision_sha256'][decision['judge']] != digest(decision)):
            raise TopicContractError('repaired draft decision binding mismatch')
    responding = set.intersection(*(set(d['responds_to_ids']) for d in pair))
    required = set.union(*(set(d['required_context_ids']) for d in pair))
    if (not responding or responding != set(case['shared_responds_to_ids'])
            or required != set(case['required_context_ids'])):
        raise TopicContractError('repaired draft consensus evidence mismatch')
    ids = candidate['context_message_ids']
    link = {'schema_version': 'reply-link-machine-v2', 'basis': 'dual_machine_atomic_consensus',
            'responds_to_ids': [mid for mid in ids if mid in responding],
            'required_context_ids': [mid for mid in ids if mid in required],
            'target_message_ids': candidate['target_message_ids'],
            'parent_candidate_sha256': candidate['candidate_sha256'],
            'review_manifest_sha256': review_sha, 'decision_sha256': case['decision_sha256'],
            'judge_responds_to_ids': {d['judge']: d['responds_to_ids'] for d in pair},
            'source_reply_to_claimed': False, 'used_target_text_for_selection': False,
            'target_text_used_for_offline_verification': True, 'review_kind': 'machine',
            'independent_quality_validation_completed': False}
    link['evidence_sha256'] = digest(link)
    row = deepcopy(candidate)
    row.pop('fresh_review_required')
    row.update(schema_version='topic-reviewed-candidate-v2', parent_sample_id=candidate['sample_id'],
               parent_candidate_sha256=candidate['candidate_sha256'],
               sample_id='topicreviewed_' + digest({'candidate': candidate['candidate_sha256'], 'link': link})[:24],
               reply_link=link, review_status='keep', review_kind='machine', **GOVERNANCE)
    row['candidate_sha256'] = digest({k: v for k, v in row.items() if k != 'candidate_sha256'})
    validate_repaired_rows([row])
    return row


def compile_repaired_draft(*, machine: Path, queue: Path, repair: Path, consent: Path,
                           output_root: Path, controlled_root: Path, execute: bool = False) -> dict:
    for path in (machine, queue, repair, consent, output_root):
        private_path(path, controlled_root)
    if any(output_root == p or p in output_root.parents or output_root in p.parents for p in (machine, queue, repair)):
        raise TopicContractError('repaired draft output overlaps source')
    manifest = verify_manifest(machine, review.REQUIRED)
    identity = read_json(machine / 'identity.json')
    recovery_v1 = 'topic-repair-transport-recovery-v1'
    recovery_v2 = 'topic-repair-identity-recovery-v2'
    gpt6_review = 'topic-repair-axes-v4-gpt6-v1'
    if (manifest['identity'] != identity
            or identity['scope'] not in {review.METHOD, recovery_v1, recovery_v2, gpt6_review}):
        raise TopicContractError('repaired draft requires matching review identity')
    if identity['scope'] == recovery_v2:
        from . import topic_repair_recovery_v2_compat as recovery_v2_compat
        recovery_v2_compat.source_check(identity)
    else:
        review.source_check(identity)
    configs = Path(__file__).resolve().parents[2] / 'configs'
    policy = review.load_config(configs / 'topic-repair-review-v1.yaml')
    candidates, dispositions, _ = review.load_sources(queue, repair, consent, policy)
    for path in (queue / 'manifest.json', repair / 'manifest.json', consent):
        if identity['inputs'].get(str(path)) != file_digest(path):
            raise TopicContractError('repaired draft review source mismatch')
    if identity['candidate_digests'] != [c['candidate_sha256'] for c in candidates]:
        raise TopicContractError('repaired draft population mismatch')
    if identity['scope'] == review.METHOD:
        decisions, errors, audit = review.reconstruct(machine, identity, candidates, review.request_table(identity, candidates))
    elif identity['scope'] == recovery_v1:
        from .topic_repair_recovery import replay_complete
        decisions, errors, audit = replay_complete(machine, candidates, dispositions)
    elif identity['scope'] == recovery_v2:
        decisions, errors, audit = recovery_v2_compat.replay_complete(
            machine, candidates, dispositions)
    else:
        from . import topic_repair_review_gpt6 as gpt6
        decisions, errors, audit = gpt6.reconstruct(
            machine, identity, candidates, gpt6.request_table(identity, candidates))
    summary_fn = gpt6.summarize if identity['scope'] == gpt6_review else review.summarize
    axes_validator = (gpt6.validate_axes if identity['scope'] == gpt6_review else validate_axes)
    summary = summary_fn(candidates, decisions, dispositions)
    cases = summary.pop('cases')
    report = read_json(machine / 'report.json')
    if (report['status'] != 'complete' or errors or audit['circuit_reason']
            or read_json(machine / 'decisions.json') != {'identity': identity, 'decisions': decisions}
            or read_json(machine / 'cases.json') != {'cases': cases}
            or any(report.get(k) != v for k, v in {**summary, **audit}.items())):
        raise TopicContractError('repaired draft review incomplete or changed')
    by_id = {c['sample_id']: c for c in candidates}
    indexed = {(r['judge'], r['sample_id']): r for r in decisions}
    review_sha = file_digest(machine / 'manifest.json')
    selected = [c for c in cases if c['disposition'] == 'machine_consensus_keep']
    draft = [bind_reviewed_candidate(by_id[c['sample_id']], c,
              [indexed[j, c['sample_id']] for j in ('gpt', 'claude')], review_sha,
              axes_validator) for c in selected]
    original = {c['parent_sample_id']: c for c in read_json(repair / 'context-comparison.json')['cases']}
    changed = [c for c in selected if original[c['parent_sample_id']]['input_changed']]
    implementation_names = [
        'topic_repair_export.py', 'topic_repair_review.py', 'topic_repair_protocol.py',
        'topic_repair_contract.py', 'topic_repair_recovery.py']
    if identity['scope'] == recovery_v2:
        implementation_names.extend([
            'topic_repair_recovery_v2.py', 'topic_repair_recovery_v2_compat.py'])
    elif identity['scope'] == gpt6_review:
        implementation_names.extend([
            'topic_axes_review_gpt6.py', 'topic_repair_protocol_gpt6.py',
            'topic_repair_review_gpt6.py'])
    identity = {'method': 'topic-repair-reviewed-draft-v1', 'inputs': {str(p): file_digest(p) for p in (
        machine / 'manifest.json', queue / 'manifest.json', repair / 'manifest.json', consent)},
        'implementation_sha256': {name: file_digest(Path(__file__).with_name(name))
                                  for name in implementation_names},
        'candidate_digests': [c['candidate_sha256'] for c in draft], 'decisions_sha256': digest(decisions)}
    output = output_root / ('topic-repair-reviewed-draft_' + digest(identity)[:20])
    summary = {**summary, 'selected_count': len(draft), 'selected_changed_input_count': len(changed),
               'selected_previous_disposition_counts': dict(Counter(c['previous_disposition'] for c in selected)),
               'old_labels_transferred': False, 'all_reviewed_text_and_tokens_preserved': True,
               'independent_quality_validation_completed': False, 'formal_snapshot_created': False,
               'external_requests': 0, **GOVERNANCE}
    if not execute:
        return {**summary, 'status': 'planned', 'output_dir': str(output)}
    artifacts = {'train.draft.jsonl': draft, 'selection.json': {'cases': cases}, 'report.json': summary}
    if any(file_digest(Path(p)) != sha for p, sha in identity['inputs'].items()):
        raise TopicContractError('repaired draft inputs changed during compilation')
    publish_packet(output, identity, artifacts)
    return {**summary, 'status': 'built', 'output_dir': str(output), 'manifest_sha256': file_digest(output / 'manifest.json')}
