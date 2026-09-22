#!/usr/bin/env python3
"""Plan or publish an explicitly accepted machine-confirmed daily-fact batch."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from wechat_persona.fact_resolution import prepare_facts, publish_facts  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--review-dir", type=Path, required=True)
    parser.add_argument("--consent", type=Path, required=True)
    parser.add_argument("--controlled-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--smoke-query-count", type=int, default=32)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--plan", action="store_true")
    modes.add_argument("--execute", action="store_true")
    parser.add_argument("--selection-sha256")
    parser.add_argument("--acceptance-reference")
    args = parser.parse_args()
    if args.execute and not all((args.output_root, args.selection_sha256, args.acceptance_reference)):
        parser.error("execution requires output root, selection digest and acceptance reference")
    try:
        prepared = prepare_facts(
            snapshot_dir=args.snapshot_dir, review_dir=args.review_dir,
            consent_path=args.consent, controlled_root=args.controlled_root,
            smoke_query_count=args.smoke_query_count,
        )
        result = prepared.summary()
        if args.execute:
            result = publish_facts(
                prepared, output_root=args.output_root,
                selection_sha256=args.selection_sha256,
                acceptance_reference=args.acceptance_reference,
            )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, StopIteration) as exc:
        # Contract errors contain codes and identities, never candidate text.
        print(json.dumps({"status": "blocked", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
