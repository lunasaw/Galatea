"""Validation-scope adapter for frozen v4 axes; never relabel data as train."""
from __future__ import annotations

import re

import jsonschema

from ._common import digest
from .topic_axes_support_v4 import strict_json
from .topic_context import TopicContractError
from . import topic_review_protocol as legacy
from . import topic_review_protocol_v4 as v4
from .topic_validation import CANDIDATE_VERSION, validation_schema, validation_view


METHOD = 'topic-validation-axes-v4'
VALIDATOR = jsonschema.Draft202012Validator(validation_schema())
AXES_VALIDATOR = jsonschema.Draft202012Validator(v4.SCHEMA)


def validate_candidate(candidate: dict) -> dict:
    if (candidate.get('schema_version') != CANDIDATE_VERSION or candidate.get('candidate_sha256') !=
            digest({k: v for k, v in candidate.items() if k != 'candidate_sha256'})):
        raise TopicContractError('validation candidate binding invalid')
    try:
        VALIDATOR.validate(candidate)
    except jsonschema.ValidationError as exc:
        raise TopicContractError('invalid validation candidate schema') from exc
    if candidate['messages'][-1] != {'role': 'assistant', 'content': '\n'.join(
            r['content'] for r in candidate['target_messages'])}:
        raise TopicContractError('validation target text changed')
    return validation_view(candidate, candidate['context_messages'], 'selected')


def validate_axes(candidate: dict, axes: dict) -> dict:
    view = validate_candidate(candidate)
    try:
        AXES_VALIDATOR.validate(axes)
    except jsonschema.ValidationError as exc:
        raise TopicContractError('invalid validation axes response') from exc
    responding, required = axes['responds_to_indices'], axes['required_context_indices']
    for indices in (responding, required):
        if any(type(i) is not int or i >= len(view['source_ids']) for i in indices):
            raise TopicContractError('validation source index invalid')
    if ((axes['risk_status'] == 'flagged') != bool(axes['risk_flags'])
            or not set(responding) <= set(required)
            or any(candidate['context_messages'][i]['role'] != 'self' for i in responding)
            or (axes['relation'] in legacy.LINKED) != bool(responding)):
        raise TopicContractError('validation axes contradict atomic evidence')
    unresolved = {axis for axis, pending in (
        ('link', axes['relation'] == 'undetermined'), ('context', axes['context_status'] != 'sufficient'),
        ('value', axes['communicative_value'] == 'undetermined'), ('risk', axes['risk_status'] == 'undetermined')) if pending}
    witnesses = axes['uncertainties']
    if len(witnesses) != len(unresolved) or {w['axis'] for w in witnesses} != unresolved:
        raise TopicContractError('validation uncertainty witnesses mismatch')
    for witness in witnesses:
        axis, issue = witness['axis'], witness['issue']
        if (issue not in v4.ISSUES[axis] or witness['reply_quote'] not in view['case']['reply']
                or any(type(i) is not int or i not in required for i in witness['source_indices'])):
            raise TopicContractError('validation uncertainty evidence invalid')
        if axis == 'context' and ((axes['context_status'] != 'undetermined' and issue != axes['context_status'])
                or (axes['context_status'] == 'undetermined' and issue not in
                    {'multiple_referents', 'context_conflict', 'other_context_gap'})):
            raise TopicContractError('validation context witness contradicts axis')
    status, reason = v4.derive_status(axes)
    shadow, shadow_reason = legacy.derive_status(axes)
    return {'method': METHOD, 'sample_id': candidate['sample_id'], 'candidate_sha256': candidate['candidate_sha256'],
            'split': 'validation', 'axes': axes, 'axes_sha256': digest(axes), 'status': status, 'reason': reason,
            'reply_link_correct': axes['relation'] in legacy.LINKED, 'context_complete': axes['context_status'] == 'sufficient',
            'responds_to_ids': [view['source_ids'][i] for i in responding],
            'required_context_ids': [view['source_ids'][i] for i in required],
            'review_kind': 'machine', 'protocol_calibration_passed': False,
            'human_review_completed': False, 'formal_training_eligible': False,
            'shadow_v2_status': shadow, 'shadow_v2_reason': shadow_reason,
            'confidence_role': 'uncalibrated_diagnostic_only'}


def decode_response(response: dict, judge: dict, candidate: dict, exact_model: str) -> dict:
    if not isinstance(response, dict):
        raise TopicContractError('invalid_provider_JSON')
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
