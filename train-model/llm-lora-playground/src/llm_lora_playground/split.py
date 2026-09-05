"""Deterministic group split manifests."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from typing import Iterable

from .datasets import TrainingSample


@dataclass(frozen=True)
class SplitManifest:
    strategy: str
    seed: int
    sample_ids_by_split: dict[str, list[str]]
    group_ids_by_split: dict[str, list[str]]
    digest: str


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_group_split(samples: Iterable[TrainingSample], group_key: str, ratios: tuple[float, float, float], seed: int) -> SplitManifest:
    items = list(samples)
    groups: dict[str, list[TrainingSample]] = {}
    for sample in items:
        group = getattr(sample, group_key)
        groups.setdefault(group, []).append(sample)
    names = sorted(groups)
    random.Random(seed).shuffle(names)
    total = len(items)
    targets = [total * ratios[0], total * ratios[0] + total * ratios[1]]
    assignment = {"train": [], "validation": [], "test": []}
    counts = {key: 0 for key in assignment}
    for group in names:
        destination = "train" if counts["train"] < targets[0] else "validation" if counts["validation"] + counts["train"] < targets[1] else "test"
        assignment[destination].extend(groups[group])
        counts[destination] += len(groups[group])
    sample_ids = {key: sorted(sample.sample_id for sample in rows) for key, rows in assignment.items()}
    group_ids = {key: sorted({sample.scenario_id for sample in rows}) for key, rows in assignment.items()}
    payload = {"strategy": "scenario_group", "seed": seed, "sample_ids_by_split": sample_ids, "group_ids_by_split": group_ids}
    return SplitManifest(payload["strategy"], seed, sample_ids, group_ids, _digest(payload))


def validate_split_manifest(samples: Iterable[TrainingSample], manifest: SplitManifest) -> None:
    known = {sample.sample_id: sample.scenario_id for sample in samples}
    seen: set[str] = set()
    groups: dict[str, str] = {}
    for split, ids in manifest.sample_ids_by_split.items():
        for sample_id in ids:
            if sample_id not in known:
                raise ValueError(f"unknown sample in split: {sample_id}")
            if sample_id in seen:
                raise ValueError(f"sample appears in multiple splits: {sample_id}")
            seen.add(sample_id)
            group = known[sample_id]
            if group in groups and groups[group] != split:
                raise ValueError(f"group crosses splits: {group}")
            groups[group] = split
