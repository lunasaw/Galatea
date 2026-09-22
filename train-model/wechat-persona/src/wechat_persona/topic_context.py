"""Versioned prefix-only topic state and complete-turn context selection."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Sequence

from ._common import digest


POLICY_VERSION = "prefix-topic-rules-v1"
EXCHANGE_POLICY_VERSION = "prefix-topic-exchanges-v2"
OPENER_POLICY_VERSION = "prefix-topic-openers-v3"
POLICY_VERSIONS = {POLICY_VERSION, EXCHANGE_POLICY_VERSION, OPENER_POLICY_VERSION}
CATEGORIES = {
    "work": ("工作", "上班", "下班", "公司", "开会", "汇报", "项目", "加班", "老板"),
    "study": ("学习", "考试", "作业", "上课", "论文", "复习"),
    "food": ("吃饭", "吃什么", "早饭", "午饭", "晚饭", "早餐", "午餐", "晚餐", "饿", "面条", "火锅"),
    "health": ("生病", "头疼", "头痛", "发烧", "感冒", "医院", "吃药", "肚子疼"),
    "plans": ("几点", "明天", "安排", "出发", "到家", "周末", "起床", "睡觉"),
    "support": ("难过", "紧张", "担心", "委屈", "焦虑", "害怕", "开心", "心情"),
    "relationship": ("想你", "喜欢你", "爱你", "吵架", "分手", "道歉", "对不起"),
}


class TopicContractError(ValueError):
    pass


@dataclass(frozen=True)
class AtomicMessage:
    message_id: str
    session_id: str
    owner_scope: str
    split: str
    timestamp: str
    order: int
    day: str
    role: str
    content: str
    kind: str = "text"
    reply_to: str | None = None


@dataclass(frozen=True)
class Turn:
    messages: tuple[AtomicMessage, ...]

    @property
    def role(self) -> str:
        return self.messages[0].role

    @property
    def content(self) -> str:
        return "\n".join(row.content for row in self.messages)

    @property
    def ids(self) -> list[str]:
        return [row.message_id for row in self.messages]


def categories(text: str) -> set[str]:
    return {category for category, words in CATEGORIES.items() if any(word in text for word in words)}


def merge_turns(messages: Sequence[AtomicMessage], gap_seconds: int = 120) -> list[Turn]:
    turns: list[Turn] = []
    for row in messages:
        if turns:
            previous = turns[-1].messages[-1]
            if (row.session_id, row.owner_scope, row.split) != (
                previous.session_id, previous.owner_scope, previous.split
            ):
                raise TopicContractError("mixed session/owner/split in message stream")
            if (row.timestamp, row.order) <= (previous.timestamp, previous.order):
                raise TopicContractError("message order must be strictly increasing")
            gap = (datetime.fromisoformat(row.timestamp) - datetime.fromisoformat(previous.timestamp)).total_seconds()
            previous_categories = categories(turns[-1].content)
            current_categories = categories(row.content)
            topic_agrees = not previous_categories or not current_categories or bool(previous_categories & current_categories)
            if (row.role == previous.role and row.day == previous.day and 0 <= gap <= gap_seconds
                    and row.kind == previous.kind == "text" and topic_agrees):
                turns[-1] = Turn((*turns[-1].messages, row))
                continue
        turns.append(Turn((row,)))
    return turns


def _terms(text: str) -> set[str]:
    text = re.sub(r"[^\w\u4e00-\u9fff]", "", text)
    return {text[index:index + 2] for index in range(len(text) - 1)}


def forward_topics(prefix: Sequence[Turn]) -> list[dict[str, Any]]:
    """Assign each turn from its own prefix; future turns cannot revise it."""
    states: list[dict[str, Any]] = []
    for index, turn in enumerate(prefix):
        found = categories(turn.content)
        category = sorted(found)[0] if len(found) == 1 else "unresolved" if found else "other"
        references = {row.reply_to for row in turn.messages if row.reply_to}
        linked = next((state for state in reversed(states)
                       if references.intersection(state["message_ids"])), None)
        if linked is None and states:
            previous = states[-1]
            if not found:
                linked = previous
            else:
                # A category alone cannot join independent conversations.
                terms = _terms(turn.content)
                linked = next((state for state in reversed(states)
                               if state["topic_category"] == category
                               and len(terms & _terms(prefix[state["turn_index"]].content)) >= 2), None)
        topic_id = linked["topic_id"] if linked else "topic_" + digest({
            "owner": turn.messages[0].owner_scope, "session": turn.messages[0].session_id,
            "first_message": turn.ids[0], "policy": POLICY_VERSION,
        })[:24]
        if linked and not found:
            category = linked["topic_category"]
        states.append({
            "schema_version": "topic-segment-v1", "turn_index": index,
            "topic_id": topic_id, "topic_category": category,
            "message_ids": turn.ids, "observed_through": turn.ids[-1],
            "policy_version": POLICY_VERSION,
        })
    return states


def serialize_history(turns: Sequence[Turn], *, with_gaps: bool = True) -> str:
    chunks: list[str] = []
    previous: AtomicMessage | None = None
    for turn in turns:
        if with_gaps and previous and turn.messages[0].order > previous.order + 1:
            chunks.append("[省略部分历史消息]")
        chunks.append(f"{turn.role}: {turn.content}")
        previous = turn.messages[-1]
    return "\n".join(chunks)


def prompt_ids(tokenizer: Any, system: str, turns: Sequence[Turn]) -> list[int]:
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": serialize_history(turns)}]
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    return list(tokenizer(text, truncation=False)["input_ids"])


def exchange_dependencies(prefix: Sequence[Turn], seeds: set[int], *, allow_opening_target: bool = False) -> set[int]:
    """Close visible references and historical replies over their incoming turn.

    Only prefix messages participate. A historical answer is useful context only
    with its preceding incoming turn; references are followed transitively.
    """
    positions = {row.message_id: (index, offset)
                 for index, turn in enumerate(prefix)
                 for offset, row in enumerate(turn.messages)}
    selected: set[int] = set()
    pending = list(seeds)
    while pending:
        index = pending.pop()
        if index in selected:
            continue
        selected.add(index)
        turn = prefix[index]
        if turn.role == "target":
            incoming = index - 1
            while incoming >= 0 and prefix[incoming].role == "target":
                incoming -= 1
            if incoming < 0 and allow_opening_target:
                # The complete session/day prefix can legitimately begin with
                # target initiating a conversation. It is history, not a label
                # that must answer an invented preceding self question.
                pending.extend(range(index))
            elif incoming < 0 or prefix[incoming].role != "self":
                raise TopicContractError("historical_reply_missing_incoming_turn")
            else:
                pending.extend(range(incoming, index))
        for offset, row in enumerate(turn.messages):
            if row.reply_to:
                position = positions.get(row.reply_to)
                if position is None or position >= (index, offset):
                    raise TopicContractError("prefix_reference_unavailable_or_nonpast")
                pending.append(position[0])
    return selected


def _select_exchanges(prefix: Sequence[Turn], states: list[dict], tokenizer: Any,
                      system: str, budget: int, max_turns: int, *, allow_opening_target: bool = False) -> set[int]:
    selected = exchange_dependencies(prefix, {len(prefix) - 1}, allow_opening_target=allow_opening_target)

    def fits(indices: set[int]) -> bool:
        return (len(indices) <= max_turns
                and len(prompt_ids(tokenizer, system, [prefix[i] for i in sorted(indices)])) <= budget)

    if not fits(selected):
        raise TopicContractError("required_context_over_budget")
    # First keep recent exchanges, then older active-topic evidence, then fill
    # unused space from the causal prefix. Every addition is an atomic closure.
    recent = list(range(len(prefix) - 1, max(-1, len(prefix) - 9), -1))
    active = [i for i in reversed(range(len(prefix)))
              if states[i]["topic_id"] == states[-1]["topic_id"]]
    for index in dict.fromkeys([*recent, *active, *reversed(range(len(prefix)))]):
        if index in selected:
            continue
        try:
            trial = exchange_dependencies(prefix, selected | {index}, allow_opening_target=allow_opening_target)
        except TopicContractError:
            # Optional incomplete history can be omitted, never half-included.
            continue
        if fits(trial):
            selected = trial
    return selected


def select_context(
    prefix: Sequence[Turn], tokenizer: Any, *, system: str,
    max_length: int, target_reserve: int, max_turns: int = 16,
    arm: str = "topic",
    policy_version: str = POLICY_VERSION,
    prefix_complete_start: bool = False,
) -> tuple[list[Turn], dict[str, Any]]:
    """The selector takes neither target text nor target-side reply_to."""
    if not prefix or prefix[-1].role != "self":
        raise TopicContractError("no_incoming_self_turn")
    if arm not in {"topic", "window8", "causal"}:
        raise TopicContractError("unknown context arm")
    if policy_version not in POLICY_VERSIONS:
        raise TopicContractError("unknown context policy")
    states = forward_topics(prefix)
    latest = states[-1]
    required = {len(prefix) - 1}
    # Prefix-side explicit references are visible at inference time.
    references = {row.reply_to for row in prefix[-1].messages if row.reply_to}
    for index, turn in enumerate(prefix):
        if references.intersection(turn.ids):
            required.add(index)
    if arm == "window8":
        eligible = list(range(max(0, len(prefix) - 8), len(prefix)))
    elif arm == "causal":
        eligible = list(range(len(prefix)))
    else:
        eligible = [index for index, state in enumerate(states)
                    if state["topic_id"] == latest["topic_id"]]
        eligible += list(range(max(0, len(prefix) - 2), len(prefix)))
    selected = set(required)
    budget = max_length - target_reserve
    if len(prompt_ids(tokenizer, system, [prefix[i] for i in sorted(selected)])) > budget:
        raise TopicContractError("required_context_over_budget")
    for index in reversed(sorted(set(eligible) - selected)):
        if len(selected) >= max_turns:
            break
        trial = selected | {index}
        if len(prompt_ids(tokenizer, system, [prefix[i] for i in sorted(trial)])) <= budget:
            selected = trial
    if arm == "topic" and policy_version in {EXCHANGE_POLICY_VERSION, OPENER_POLICY_VERSION}:
        selected = _select_exchanges(prefix, states, tokenizer, system, budget, max_turns,
                                    allow_opening_target=policy_version == OPENER_POLICY_VERSION and prefix_complete_start)
    chosen = [prefix[i] for i in sorted(selected)]
    return chosen, {
        **latest, "selector_arm": arm,
        **({"selector_policy_version": policy_version} if policy_version != POLICY_VERSION else {}),
        **({"prefix_complete_start": prefix_complete_start} if policy_version == OPENER_POLICY_VERSION else {}),
        "prefix_sha256": digest([{
            "ids": turn.ids, "role": turn.role, "content": turn.content,
            "references": [row.reply_to for row in turn.messages],
        } for turn in prefix]),
        "prompt_tokens": len(prompt_ids(tokenizer, system, chosen)),
        "context_ids": [mid for turn in chosen for mid in turn.ids],
    }
