"""Evidence-producing canary scan over a frozen formal snapshot."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping


CANARY_SCANNER_VERSION = "wechat-canary-scanner-v1"
DEFAULT_PATTERN = re.compile(r"(?i)(?:canary|honeytoken|trap[_ -]?token)")


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contents(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key not in {"sample_id", "session_id", "message_id"}:
                yield from _contents(child)
    elif isinstance(value, list):
        for child in value:
            yield from _contents(child)
    elif isinstance(value, str):
        yield value


def scan_snapshot(
    snapshot_directory: Path,
    *,
    pattern: re.Pattern[str] = DEFAULT_PATTERN,
) -> dict[str, Any]:
    root = snapshot_directory.resolve()
    split_files = {name: root / f"{name}.jsonl" for name in ("train", "validation", "test")}
    if any(not path.is_file() or path.is_symlink() for path in split_files.values()):
        raise ValueError("frozen train/validation/test JSONL files are required")
    counts: dict[str, int] = {}
    matches = 0
    for split, path in split_files.items():
        count = 0
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("snapshot row must contain a JSON object")
                count += 1
                matches += sum(bool(pattern.search(value)) for value in _contents(row))
        counts[split] = count
    return {
        "schema_version": "wechat-canary-evidence-v1",
        "scanner_version": CANARY_SCANNER_VERSION,
        "pattern_sha256": hashlib.sha256(pattern.pattern.encode("utf-8")).hexdigest(),
        "split_file_sha256": {name: _file_digest(path) for name, path in split_files.items()},
        "sample_counts": counts,
        "scanned_sample_count": sum(counts.values()),
        "match_count": matches,
        "status": "pass" if matches == 0 else "blocked",
    }


def write_evidence(snapshot_directory: Path, output: Path) -> dict[str, Any]:
    report = scan_snapshot(snapshot_directory)
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {**report, "report_sha256": _file_digest(output)}
