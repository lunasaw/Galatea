"""Frozen v4 semantics for actual v2 train candidates, with no schema relabeling."""
from __future__ import annotations

import re

import jsonschema

from ._common import digest
from .topic_axes_support_v4 import strict_json
from .topic_context import TopicContractError
from .topic_evidence_audit import evidence_view
from .topic_repair_contract import validate_repaired_rows
from . import topic_review_protocol as legacy
from . import topic_review_protocol_v4 as v4


METHOD = 'topic-repair-axes-v4-v1'
AXES_VALIDATOR = jsonschema.Draft202012Validator(v4.SCHEMA)


def validate_candidate(candidate: dict) -> dict:
    validate_repaired_rows([candidate])
    if (candidate['schema_version'] != 'topic-reply-candidate-v2' or candidate['prior_hard_risks']
            or candidate['context_message_ids'] != [m['message_id'] for m in candidate['context_messages']]
            or candidate['target_message_ids'] != [m['message_id'] for m in candidate['target_messages']]
            or candidate['messages'][-1] != {'role': 'assistant', 'content': '\n'.join(
                m['content'] for m in candidate['target_messages'])}):
        raise TopicContractError('repair candidate source or eligibility mismatch')
    return evidence_view(candidate, candidate['context_messages'], 'selected')


def validate_axes(candidate: dict, axes: dict) -> dict:
    view = validate_candidate(candidate)
    if not AXES_VALIDATOR.is_valid(axes):
        raise TopicContractError('invalid repair axes response')
    responding, required = axes['responds_to_indices'], axes['required_context_indices']
    for indices in (responding, required):
        if any(type(i) is not int or i >= len(view['source_ids']) for i in indices):
            raise TopicContractError('repair source index invalid')
    if ((axes['risk_status'] == 'flagged') != bool(axes['risk_flags'])
            or not set(responding) <= set(required)
            or any(candidate['context_messages'][i]['role'] != 'self' for i in responding)
            or (axes['relation'] in legacy.LINKED) != bool(responding)):
        raise TopicContractError('repair axes contradict atomic evidence')
    unresolved = {axis for axis, pending in (
        ('link', axes['relation'] == 'undetermined'), ('context', axes['context_status'] != 'sufficient'),
        ('value', axes['communicative_value'] == 'undetermined'), ('risk', axes['risk_status'] == 'undetermined')) if pending}
    witnesses = axes['uncertainties']
    if len(witnesses) != len(unresolved) or {w['axis'] for w in witnesses} != unresolved:
        raise TopicContractError('repair uncertainty witnesses mismatch')
    for witness in witnesses:
        axis, issue = witness['axis'], witness['issue']
        if (issue not in v4.ISSUES[axis] or witness['reply_quote'] not in view['case']['reply']
                or any(type(i) is not int or i not in required for i in witness['source_indices'])):
            raise TopicContractError('repair uncertainty evidence invalid')
        if axis == 'context' and ((axes['context_status'] != 'undetermined' and issue != axes['context_status'])
                or (axes['context_status'] == 'undetermined' and issue not in
                    {'multiple_referents', 'context_conflict', 'other_context_gap'})):
            raise TopicContractError('repair context witness contradicts axis')
    status, reason = v4.derive_status(axes)
    return {'method': METHOD, 'sample_id': candidate['sample_id'], 'candidate_sha256': candidate['candidate_sha256'],
            'split': 'train', 'axes': axes, 'axes_sha256': digest(axes), 'status': status, 'reason': reason,
            'reply_link_correct': axes['relation'] in legacy.LINKED, 'context_complete': axes['context_status'] == 'sufficient',
            'responds_to_ids': [view['source_ids'][i] for i in responding],
            'required_context_ids': [view['source_ids'][i] for i in required],
            'review_kind': 'machine', 'human_review_completed': False, 'formal_training_eligible': False,
            'confidence_role': 'uncalibrated_diagnostic_only'}


def decode_response(response: dict, judge: dict, candidate: dict, exact_model: str) -> dict:
    if response.get('model') != exact_model:
        raise TopicContractError('model_identity_mismatch')
    if judge['transport'] == 'responses':
        if response.get('status') != 'completed':
            raise TopicContractError('incomplete axes response')
        content = response.get('output_text') or ''.join(part.get('text', '') for item in response.get('output', [])
            for part in item.get('content', []) if part.get('type') == 'output_text')
    else:
        choices = response.get('choices', [])
        if len(choices) != 1 or choices[0].get('finish_reason') not in {'stop', 'end_turn'}:
            raise TopicContractError('incomplete axes response')
        content = choices[0].get('message', {}).get('content')
    if isinstance(content, str):
        fenced = re.fullmatch(r'\s*```(?:json)?\s*\n(.*?)\n```\s*', content, flags=re.DOTALL)
        if fenced:
            content = fenced.group(1)
    try:
        result = strict_json(content)
    except (TypeError, ValueError) as exc:
        raise TopicContractError('invalid axes JSON') from exc
    if not isinstance(result, dict) or set(result) != {'results'} or not isinstance(result['results'], list):
        raise TopicContractError('invalid axes envelope')
    if len(result['results']) != 1:
        raise TopicContractError('missing_or_duplicate_case')
    label = result['results'][0]
    if not isinstance(label, dict) or type(label.get('index')) is not int or label['index'] != 0:
        raise TopicContractError('unknown axes case index')
    try:
        decision = validate_axes(candidate, {k: v for k, v in label.items() if k != 'index'})
    except TopicContractError as exc:
        raise TopicContractError('invalid_case_axes') from exc
    return {**decision, 'model_requested': judge['model'], 'model_returned': exact_model,
            'requested_family': judge['family']}
