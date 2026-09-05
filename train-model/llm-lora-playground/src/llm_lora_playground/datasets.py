"""Synthetic, privacy-safe SFT data and immutable dataset manifests."""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping


class DataContractError(ValueError):
    pass


@dataclass(frozen=True)
class TrainingSample:
    sample_id: str
    scenario_id: str
    messages: list[dict[str, str]]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class DatasetManifest:
    dataset_id: str
    generator_version: str
    preprocessing_version: str
    seed: int
    sample_count: int
    dataset_sha256: str
    path: Path

    @property
    def manifest_sha256(self) -> str:
        manifest_path = self.path.parent / "dataset_manifest.json"
        return _sha256(manifest_path) if manifest_path.is_file() else self.dataset_sha256


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_sample(sample: Mapping[str, Any]) -> None:
    required = {"sample_id", "scenario_id", "messages", "metadata"}
    if set(sample) != required:
        raise DataContractError(f"sample keys must be {sorted(required)}")
    if not isinstance(sample["sample_id"], str) or not sample["sample_id"]:
        raise DataContractError("sample_id must be non-empty")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", sample["sample_id"]):
        raise DataContractError("sample_id contains unsupported characters")
    messages = sample["messages"]
    if not isinstance(messages, list) or len(messages) < 2:
        raise DataContractError("messages must contain at least two entries")
    roles = {"system", "user", "assistant"}
    for message in messages:
        if set(message) - {"role", "content", "text_original_ref"}:
            raise DataContractError("unknown message fields")
        if message.get("role") not in roles:
            raise DataContractError("unknown message role")
        if not isinstance(message.get("content"), str) or not message["content"].strip():
            raise DataContractError("message content must be non-empty")
        if "text_original_ref" in message:
            raise DataContractError("restricted original text reference")
    if messages[-1]["role"] != "assistant":
        raise DataContractError("last message must be assistant")
    metadata = sample["metadata"]
    for key in ("style_label", "generator_version", "seed"):
        if key not in metadata:
            raise DataContractError(f"metadata.{key} is required")


def _make_sample(index: int, seed: int, version: str) -> dict[str, Any]:
    rng = random.Random(seed * 100_003 + index)
    scenario = index % 20
    options = ["燕麦拿铁", "热可可", "低因美式", "蜂蜜柚子茶"]
    question = ["今天想喝点不苦的。", "请推荐一杯温和的饮品。", "我想要简短的建议。", "有什么清爽的选择吗？"][index % 4]
    answer = f"可以试试{options[rng.randrange(len(options))]}，口感温和。"
    return {
        "sample_id": f"toy-s{index:06d}",
        "scenario_id": f"coffee_order_{scenario:02d}",
        "messages": [
            {"role": "system", "content": "你是温柔但简短的咖啡店店员。"},
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ],
        "metadata": {
            "style_label": "warm_brief",
            "generator_version": version,
            "preprocessing_version": "toy-sft-v1",
            "seed": seed,
            "source": "synthetic",
        },
    }


def compute_dataset_digest(path: Path) -> str:
    return _sha256(path)


def compute_manifest_digest(path: Path) -> str:
    """Return the digest of a manifest JSON file, not the server-side data path."""
    return _sha256(path)


def generate_dataset(output_dir: Path, count: int, seed: int, version: str) -> DatasetManifest:
    if count <= 0:
        raise ValueError("count must be positive")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = output_dir / "dataset.jsonl"
    rows = [_make_sample(i, seed, version) for i in range(count)]
    for row in rows:
        validate_sample(row)
    dataset_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    digest = compute_dataset_digest(dataset_path)
    manifest = {
        "schema_version": "toy-lora-sample-v1",
        "dataset_id": f"toy-lora-synthetic-{version}",
        "source": "synthetic",
        "generator_version": version,
        "preprocessing_version": "toy-sft-v1",
        "seed": seed,
        "sample_count": count,
        "dataset_sha256": digest,
        "scenario_count": len({row["scenario_id"] for row in rows}),
        "data_file": dataset_path.name,
    }
    (output_dir / "dataset_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return DatasetManifest(manifest["dataset_id"], version, "toy-sft-v1", seed, count, digest, dataset_path)


def load_samples(path: Path) -> Iterator[TrainingSample]:
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
                validate_sample(row)
            except (json.JSONDecodeError, DataContractError) as exc:
                raise DataContractError(f"invalid sample at line {line_number}: {exc}") from exc
            if row["sample_id"] in seen:
                raise DataContractError(f"duplicate sample_id: {row['sample_id']}")
            seen.add(row["sample_id"])
            yield TrainingSample(row["sample_id"], row["scenario_id"], row["messages"], row["metadata"])
