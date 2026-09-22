#!/usr/bin/env python3
"""Freeze an already audited topic pilot as a train-only draft and review page."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona.topic_review_export import export_review


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("pilot", "review", "output-root", "controlled-root"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        result = export_review(args.pilot, args.review, args.output_root, args.controlled_root)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"status": "blocked", "error_type": type(exc).__name__, "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
