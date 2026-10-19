"""Offline reply-link verification without feeding target text to the selector."""
from __future__ import annotations

from typing import Mapping, Sequence

from .topic_context import AtomicMessage, TopicContractError, Turn


def validate_references(messages: Sequence[AtomicMessage], universe: Mapping[str, tuple[str, str]]) -> None:
    previous: set[str] = set()
    for message in messages:
        reference = message.reply_to
        if reference:
            identity = universe.get(reference)
            if identity is None:
                raise TopicContractError("missing_reference")
            if identity[1] != message.split:
                raise TopicContractError("cross_split_reference")
            if identity[0] != message.session_id:
                raise TopicContractError("cross_session_reference_disabled")
            if reference not in previous:
                raise TopicContractError("nonpast_or_unavailable_reference")
        previous.add(message.message_id)


def build_reply_link(prefix: Sequence[Turn], target: Turn, context: Sequence[Turn]) -> dict:
    previous_ids = {mid for turn in prefix for mid in turn.ids}
    context_ids = {mid for turn in context for mid in turn.ids}
    target_refs = {row.reply_to for row in target.messages if row.reply_to}
    if not target_refs.issubset(previous_ids):
        raise TopicContractError("target_reference_not_in_prefix")
    if not target_refs.issubset(context_ids):
        raise TopicContractError("target_reference_not_reconstructible_online")
    return {
        "schema_version": "reply-link-v1",
        "target_message_ids": target.ids,
        "responds_to_ids": sorted(target_refs) if target_refs else prefix[-1].ids,
        "basis": "target_reference_offline_verification" if target_refs else "prefix_adjacency_hypothesis",
        "used_target_text_for_selection": False,
        "target_reference_used_as_input": False,
    }
