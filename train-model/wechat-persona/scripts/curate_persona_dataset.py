#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wechat_persona.curation import CONTROLLED_ROOT, CurationError, curate_snapshot


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plan or publish a private girlfriend-assistant SFT curation draft."
    )
    parser.add_argument("--parent-snapshot", type=Path, required=True)
    parser.add_argument("--review-summary", type=Path, required=True)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=CONTROLLED_ROOT / "curated-drafts",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Atomically write a new non-overwriting private draft; default is read-only.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = curate_snapshot(
            parent_snapshot=args.parent_snapshot,
            review_summary_path=args.review_summary,
            output_root=args.output_root,
            execute=args.execute,
        )
    except (CurationError, FileExistsError, OSError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 2
    report = result.pop("quality_report")
    result["quality_report_summary"] = {
        split: {
            key: report["splits"][split][key]
            for key in ("parent_count", "selected_count", "excluded_count", "selection_rate")
        }
        for split in ("train", "validation")
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
