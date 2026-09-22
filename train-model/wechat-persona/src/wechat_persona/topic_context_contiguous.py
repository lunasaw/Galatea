"""Train-only context rework using a causal, contiguous suffix of whole exchanges."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Sequence

from ._common import digest
from .topic_candidates import build_candidate
from .topic_context import (TopicContractError, Turn, exchange_dependencies, forward_topics,
                            prompt_ids)


POLICY_VERSION = 'prefix-contiguous-exchanges-v4'


def select_contiguous(prefix: Sequence[Turn], tokenizer: Any, *, system: str,
                      max_length: int, target_reserve: int, max_turns: int,
                      prefix_complete_start: bool = False) -> tuple[list[Turn], dict]:
    """Stop at an unfit exchange instead of skipping it to fill from older turns.

    Closure over prefix references may extend the suffix. All intervening turns
    must fit too. Target text, target references and reviewer labels are absent.
    """
    if not prefix or prefix[-1].role != 'self':
        raise TopicContractError('no_incoming_self_turn')
    if max_turns < 1 or not 0 < target_reserve < max_length:
        raise TopicContractError('invalid contiguous context budget')
    flat = [m for turn in prefix for m in turn.messages]
    if (len({m.message_id for m in flat}) != len(flat)
            or any(a.order >= b.order or a.timestamp > b.timestamp for a, b in zip(flat, flat[1:]))
            or len({(m.session_id, m.day, m.split, m.owner_scope) for m in flat}) != 1):
        raise TopicContractError('invalid contiguous prefix scope or order')

    def closure(start: int) -> int:
        while True:
            indices = exchange_dependencies(prefix, set(range(start, len(prefix))),
                                              allow_opening_target=prefix_complete_start)
            earlier = min(indices)
            if earlier == start:
                return start
            start = earlier

    def fits(start: int) -> bool:
        return (len(prefix) - start <= max_turns
                and len(prompt_ids(tokenizer, system, prefix[start:])) <= max_length - target_reserve)

    start = closure(len(prefix) - 1)
    if not fits(start):
        raise TopicContractError('required_contiguous_context_over_budget')
    stop_reason = 'complete_prefix'
    while start > 0:
        try:
            proposed = closure(start - 1)
        except TopicContractError:
            stop_reason = 'older_exchange_unavailable'
            break
        if not fits(proposed):
            stop_reason = 'older_exchange_over_budget'
            break
        start = proposed
    chosen = list(prefix[start:])
    return chosen, {**forward_topics(prefix)[-1], 'selector_arm': 'contiguous',
                    'selector_policy_version': POLICY_VERSION,
                    'prefix_complete_start': prefix_complete_start,
                    'selected_complete_start': start == 0 and prefix_complete_start,
                    'prefix_sha256': digest([{'ids': t.ids, 'role': t.role, 'content': t.content,
                                             'references': [m.reply_to for m in t.messages]} for t in prefix]),
                    'context_ids': [mid for turn in chosen for mid in turn.ids],
                    'prompt_tokens': len(prompt_ids(tokenizer, system, chosen)),
                    'omitted_prefix_turns': start, 'internal_omitted_turns': 0,
                    'stop_reason': stop_reason}


def build_contiguous_candidate(prefix: list[Turn], target: Turn, tokenizer: Any,
                               config: dict, *, parent: dict, prefix_complete_start: bool) -> dict:
    if (parent['split'] != 'train' or parent['target_message_ids'] != target.ids
            or parent['candidate_sha256'] != digest({k: v for k, v in parent.items() if k != 'candidate_sha256'})):
        raise TopicContractError('contiguous rework requires frozen train target')
    encoding = config['encoding']
    chosen, topic = select_contiguous(prefix, tokenizer, system=encoding['system_prompt'],
        max_length=encoding['max_length'], target_reserve=encoding['target_token_reserve'],
        max_turns=config['context']['max_context_turns'], prefix_complete_start=prefix_complete_start)
    # Reuse all existing scope/privacy/encoding checks. The causal arm must keep
    # the already bounded suffix in full; an assertion prevents a second filter.
    checker_config = deepcopy(config)
    checker_config['context']['policy_version'] = 'prefix-topic-openers-v3'
    row = build_candidate(chosen, target, tokenizer, checker_config, arm='causal',
                          prefix_complete_start=topic['selected_complete_start'])
    if (row['context_message_ids'] != topic['context_ids']
            or row['target_messages'] != parent['target_messages']
            or row['messages'][-1] != parent['messages'][-1]
            or row['messages'][0] != parent['messages'][0]):
        raise TopicContractError('contiguous rework changed source target or selected context')
    row.update(schema_version='topic-reply-candidate-v2', topic=topic, policy_sha256=digest(config),
               parent_sample_id=parent['sample_id'], parent_candidate_sha256=parent['candidate_sha256'],
               sample_id='topicreply_' + digest({'parent': parent['candidate_sha256'], 'policy': config})[:24],
               fresh_review_required=True, old_quality_decisions_transferred=False)
    # A candidate hypothesis is explicitly unresolved until semantic review.
    row['reply_link_hypothesis'] = row.pop('reply_link')
    row['reply_link'] = {'schema_version': 'reply-link-unresolved-v2',
                         'basis': 'pending_semantic_verification', 'responds_to_ids': [],
                         'target_message_ids': target.ids, 'source_reply_to_claimed': False}
    row['candidate_sha256'] = digest({k: v for k, v in row.items() if k != 'candidate_sha256'})
    return row
