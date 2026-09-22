"""Bind replayed dual-review atomic anchors without changing training text."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from . import topic_axes_review_v4 as review_v4
from . import topic_axes_support_v4 as core
from ._common import digest, file_digest
from .topic_adjudication import verify_manifest
from .topic_axes_singleton_v4 import verify_responses
from .topic_candidates import private_path, read_json
from .topic_context import TopicContractError
from .topic_review_protocol_v4 import validate_axes


def replay_train_review(*, pilot: Path, review: Path, config_path: Path,
                        controlled_root: Path, base_url: str) -> tuple[list, list, list, dict]:
    """Offline reconstruction from original requests through both recovery runs."""
    private_path(review, controlled_root)
    final = verify_manifest(review, {'identity.json', 'report.json', 'cases.json', 'decisions.json', 'train.draft.jsonl'})
    inputs = final['identity']['inputs']
    for name, sha in inputs.items():
        if file_digest(Path(name)) != sha:
            raise TopicContractError('train review input changed')

    def unique(prefix: str) -> Path:
        matches = {Path(name).parent for name in inputs if Path(name).parent.name.startswith(prefix)}
        if len(matches) != 1:
            raise TopicContractError('ambiguous train review dependency')
        return matches.pop()

    pilots = {Path(name).parent for name in inputs if Path(name).parent.name.startswith('topic-pilot_')}
    if pilot not in pilots or len(pilots) != 2:
        raise TopicContractError('train review pilot mismatch')
    policy = review_v4.load_config(config_path)
    paths = {'before': (pilots - {pilot}).pop(), 'after': pilot,
             'review': unique('topic-review_'), 'audit': unique('topic-evidence-audit_'),
             'cross': unique('topic-cross-review_'),
             'cross_policy': config_path.parent / 'topic-cross-review-v1.yaml',
             'consent': next(Path(name) for name in inputs if Path(name).parent.name == 'consent')}
    candidates, prior, _ = core.load_private_inputs(paths, policy, controlled_root, base_url)
    original, recovery = unique('topic-axes-v4-private_review_'), unique('topic-axes-v4-recovery_')
    binding = review_v4.binding_for(policy, config_path.parent / policy['synthetic_fixtures'], base_url)
    models = read_json(original / 'identity.json')['route']['bound_returned_models']
    decisions, replay_counts = [], []
    for directory in (original, recovery, review):
        private_path(directory, controlled_root)
        identity = read_json(directory / 'identity.json')
        if identity['protocol'] != binding:
            raise TopicContractError('train review frozen protocol changed')
        report, decisions, audit = verify_responses(directory, candidates, prior, policy, models, decisions)
        replay_counts.append(audit['raw_requests_verified'])
    if (report['status'] != 'complete' or len(decisions) != 2 * len(candidates)
            or not report['draft_exported'] or report['unresolved_response_failures']):
        raise TopicContractError('train atomic evidence requires complete review')
    cases = core.private_summary(candidates, prior, decisions, policy)['cases']
    receipts = {'review_manifest_sha256': file_digest(review / 'manifest.json'),
                'raw_requests_verified_by_stage': replay_counts, 'valid_judgments': len(decisions),
                'inputs': {**inputs, str(review / 'manifest.json'): file_digest(review / 'manifest.json')},
                'network_requests': 0}
    return candidates, decisions, cases, receipts


def bind_atomic_evidence(candidate: dict, pair: list[dict], case: dict, review_sha: str) -> dict:
    if (candidate['schema_version'] != 'topic-reply-candidate-v1' or candidate['split'] != 'train'
            or candidate['candidate_sha256'] != digest({k: v for k, v in candidate.items() if k != 'candidate_sha256'})
            or candidate['human_review_completed'] or candidate['formal_training_eligible']
            or case['candidate_sha256'] != candidate['candidate_sha256']
            or case['sample_id'] != candidate['sample_id'] or case['disposition'] != 'selected'
            or case['prior_hard_risks'] or len(pair) != 2 or {d['judge'] for d in pair} != {'gpt', 'claude'}):
        raise TopicContractError('atomic evidence candidate or selection mismatch')
    for decision in pair:
        expected = validate_axes(candidate, decision['axes'])
        if (any(decision.get(k) != v for k, v in expected.items()) or decision['status'] != 'keep'
                or decision['review_kind'] != 'machine'
                or not decision['reply_link_correct'] or not decision['context_complete']
                or case['decision_sha256'][decision['judge']] != digest(decision)):
            raise TopicContractError('atomic evidence decision mismatch')
    responding = set.intersection(*(set(d['responds_to_ids']) for d in pair))
    required = set.union(*(set(d['required_context_ids']) for d in pair))
    context = candidate['context_messages']
    ids = [m['message_id'] for m in context]
    if (not responding or not responding <= required <= set(ids)
            or ids != candidate['context_message_ids'] or len(set(ids)) != len(ids)
            or set(ids) & set(candidate['target_message_ids'])
            or responding != set(case['shared_responds_to_ids']) or required != set(case['required_context_ids'])
            or any(m['role'] != 'self' for m in context if m['message_id'] in responding)):
        raise TopicContractError('atomic evidence must reference visible self messages')
    for message in context:
        if ((message['owner_scope'], message['split'], message['session_id'], message['day']) != (
                candidate['owner_scope'], 'train', candidate['session_id'], candidate['day'])
                or message['order'] >= candidate['cutoff']['source_record_index']
                or message['timestamp'] > candidate['cutoff']['timestamp']):
            raise TopicContractError('atomic evidence noncausal or cross-scope')
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
    row.update(schema_version='topic-reviewed-candidate-v2',
               parent_sample_id=candidate['sample_id'], parent_candidate_sha256=candidate['candidate_sha256'],
               sample_id='topicreviewed_' + digest({'candidate': candidate['candidate_sha256'], 'link': link})[:24],
               reply_link_hypothesis=row['reply_link'], reply_link=link,
               review_status='keep', review_kind='machine', promotable=False, training_run=False)
    row['candidate_sha256'] = digest({k: v for k, v in row.items() if k != 'candidate_sha256'})
    return row
