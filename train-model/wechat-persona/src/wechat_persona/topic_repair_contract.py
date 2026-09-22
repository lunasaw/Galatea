"""Versioned v2 shapes retain all v1 atomic-message and encoding constraints."""
from __future__ import annotations

from pathlib import Path

import jsonschema

from ._common import digest
from .topic_candidates import read_json
from .topic_context import TopicContractError


SCHEMA_PATH = Path(__file__).resolve().parents[2] / 'schemas/topic-reply-candidate.schema.json'


def repair_schema() -> dict:
    schema = read_json(SCHEMA_PATH)
    schema['$id'] = 'galatea://wechat-persona/topic-repair-candidate-v2'
    properties = schema['properties']
    properties.update({
        'schema_version': {'enum': ['topic-reply-candidate-v2', 'topic-reviewed-candidate-v2']},
        'parent_sample_id': {'type': 'string', 'minLength': 1},
        'parent_candidate_sha256': {'$ref': '#/$defs/sha'},
        'reply_link_hypothesis': properties['reply_link'],
        'review_status': {'enum': ['uncertain', 'keep']},
        'review_kind': {'const': 'machine'}, 'promotable': {'const': False}, 'training_run': {'const': False},
        'fresh_review_required': {'const': True}, 'old_quality_decisions_transferred': {'const': False},
        'prior_hard_risks': {'type': 'array', 'uniqueItems': True, 'items': {'type': 'string'}},
        'reply_link': {'type': 'object', 'required': ['schema_version', 'basis', 'responds_to_ids',
                                                    'target_message_ids', 'source_reply_to_claimed'],
                       'properties': {'target_message_ids': {'$ref': '#/$defs/ids'},
                                      'source_reply_to_claimed': {'const': False}}},
    })
    schema['required'] += ['parent_sample_id', 'parent_candidate_sha256', 'reply_link_hypothesis']
    schema['oneOf'] = [
        {'properties': {'schema_version': {'const': 'topic-reply-candidate-v2'},
                        'review_status': {'const': 'uncertain'},
                        'reply_link': {'properties': {
                            'schema_version': {'const': 'reply-link-unresolved-v2'},
                            'basis': {'const': 'pending_semantic_verification'},
                            'responds_to_ids': {'const': []}}}},
         'required': ['fresh_review_required', 'old_quality_decisions_transferred', 'prior_hard_risks']},
        {'properties': {'schema_version': {'const': 'topic-reviewed-candidate-v2'}, 'review_status': {'const': 'keep'},
                        'reply_link': {'required': ['required_context_ids', 'evidence_sha256', 'decision_sha256',
                                                    'review_manifest_sha256', 'parent_candidate_sha256',
                                                    'used_target_text_for_selection', 'review_kind',
                                                    'independent_quality_validation_completed'],
                                       'properties': {'schema_version': {'const': 'reply-link-machine-v2'},
                                           'basis': {'const': 'dual_machine_atomic_consensus'},
                                           'responds_to_ids': {'$ref': '#/$defs/ids'},
                                           'required_context_ids': {'$ref': '#/$defs/ids'},
                                           'evidence_sha256': {'$ref': '#/$defs/sha'},
                                           'review_manifest_sha256': {'$ref': '#/$defs/sha'},
                                           'used_target_text_for_selection': {'const': False},
                                           'review_kind': {'const': 'machine'},
                                           'independent_quality_validation_completed': {'const': False}}}},
         'required': ['review_kind', 'promotable', 'training_run']},
    ]
    return schema


def validate_repaired_rows(rows: list[dict]) -> None:
    validator = jsonschema.Draft202012Validator(repair_schema())
    for row in rows:
        if (row['candidate_sha256'] != digest({k: v for k, v in row.items() if k != 'candidate_sha256'})
                or not validator.is_valid(row)):
            raise TopicContractError('invalid v2 repair candidate contract')
