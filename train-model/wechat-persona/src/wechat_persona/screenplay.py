"""Lineage-preserving, text-only screenplay prototype helpers."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Iterable, Mapping


class ScreenplayContractError(ValueError):
    pass


APPROVED = {"approved", "edited"}
STAGES = {"acquaintance", "familiar", "ambiguous", "committed", "unknown"}


def build_event_card(event_id: str, relationship_stage: str, scene: str, character_goals: Iterable[str], emotional_turn: str, source_refs: Iterable[str], source_fact_hash: str, *, review_status: str = "approved", rewrite_policy: str = "rewrite", confidence: float = 1.0, **extra: Any) -> dict[str, Any]:
    if not event_id or relationship_stage not in STAGES or not scene.strip() or not emotional_turn.strip() or not list(character_goals) or not list(source_refs):
        raise ScreenplayContractError("event card fields are incomplete")
    if not isinstance(source_fact_hash, str) or len(source_fact_hash) != 64 or any(ch not in "0123456789abcdef" for ch in source_fact_hash):
        raise ScreenplayContractError("source_fact_hash must be SHA-256")
    if review_status not in {"uncertain", "approved", "edited", "rejected", "deleted"}:
        raise ScreenplayContractError("invalid review_status")
    if rewrite_policy not in {"rewrite", "paraphrase_only", "quote_with_explicit_authorization"}:
        raise ScreenplayContractError("invalid rewrite_policy")
    card = {"event_id": event_id, "relationship_stage": relationship_stage, "scene": scene.strip(), "character_goals": [str(x) for x in character_goals], "emotional_turn": emotional_turn.strip(), "source_refs": [str(x) for x in source_refs], "source_fact_hash": source_fact_hash, "confidence": float(confidence), "review_status": review_status, "rewrite_policy": rewrite_policy}
    card.update(extra)
    return card


def generate_screenplay(cards: Iterable[Mapping[str, Any]], *, duration_seconds: int = 240, allow_voice: bool = False, allow_face: bool = False, allow_auto_message: bool = False) -> dict[str, Any]:
    if allow_voice or allow_face or allow_auto_message:
        raise ScreenplayContractError("voice, face and automatic messaging are not supported")
    if duration_seconds <= 0:
        raise ScreenplayContractError("duration_seconds must be positive")
    valid = [dict(card) for card in cards]
    if not valid:
        raise ScreenplayContractError("at least one approved event card is required")
    if any(card.get("review_status") not in APPROVED for card in valid):
        raise ScreenplayContractError("unapproved event card cannot be rendered")
    order = {"acquaintance": 0, "familiar": 1, "ambiguous": 2, "committed": 3}
    known_stages = [card.get("relationship_stage") for card in valid if card.get("relationship_stage") != "unknown"]
    if any(order[known_stages[index]] > order[known_stages[index + 1]] for index in range(len(known_stages) - 1)):
        raise ScreenplayContractError("relationship stage cannot move backwards")
    if any(card.get("relationship_stage") == "unknown" for card in valid):
        raise ScreenplayContractError("unknown relationship stage requires review")
    step = max(duration_seconds // len(valid), 1)
    scenes = []
    for index, card in enumerate(valid):
        start = index * step
        end = duration_seconds if index == len(valid) - 1 else min(duration_seconds, (index + 1) * step)
        scenes.append({
            "scene_index": index + 1,
            "timecode": {"start_seconds": start, "end_seconds": end},
            "source_event_id": card["event_id"],
            "source_fact_hash": card["source_fact_hash"],
            "source_refs": list(card["source_refs"]),
            "relationship_stage": card["relationship_stage"],
            "setting": card["scene"],
            "character_goals": list(card["character_goals"]),
            "emotional_turn": card["emotional_turn"],
            "dialogue": "（根据事件卡改写，待双方确认）",
            "narration": "（文本旁白）",
            "needs_confirmation": card.get("review_status") != "approved" or card.get("relationship_stage") == "unknown",
        })
    lineage = [(item["source_event_id"], item["source_fact_hash"], item["timecode"]) for item in scenes]
    return {"schema_version": "screenplay-v1", "duration_seconds": duration_seconds, "rewrite_policy": "rewrite", "scenes": scenes, "lineage_digest": hashlib.sha256(json.dumps(lineage, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}


def edit_event_card(card: Mapping[str, Any], **changes: Any) -> dict[str, Any]:
    updated = copy.deepcopy(dict(card))
    immutable = {"event_id", "source_refs", "source_fact_hash"}
    if immutable & set(changes):
        raise ScreenplayContractError("event lineage fields are immutable")
    updated.update(changes)
    updated["review_status"] = "edited"
    return updated


def delete_event_card(cards: Iterable[Mapping[str, Any]], event_id: str) -> list[dict[str, Any]]:
    return [dict(card) for card in cards if card.get("event_id") != event_id and card.get("review_status") != "deleted"]
