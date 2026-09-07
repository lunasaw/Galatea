#!/usr/bin/env python3
"""Review redacted candidates and append ID/hash-only audit events."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wechat_persona.review import append_review_event, append_reviewed_row, apply_review


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--event-log", type=Path)
    parser.add_argument("--status", choices=("keep", "redact_keep", "reject", "uncertain"))
    parser.add_argument("--reviewer-id")
    parser.add_argument("--reason")
    parser.add_argument("--edited-candidate", type=Path)
    parser.add_argument("--reviewed-row-log", type=Path, help="separate controlled content log for reviewed rows")
    args = parser.parse_args()
    if args.candidate is None:
        print(json.dumps({"status": "planned", "content": "redacted_only", "append_only_events": True, "formal_training_eligible": False}, sort_keys=True))
        return 0
    if not all((args.event_log, args.status, args.reviewer_id)):
        parser.error("candidate review requires --event-log, --status and --reviewer-id")
    if args.status == "redact_keep" and not args.reviewed_row_log:
        parser.error("redact_keep requires --reviewed-row-log")
    row = json.loads(args.candidate.read_text(encoding="utf-8"))
    edited = json.loads(args.edited_candidate.read_text(encoding="utf-8"))["messages"] if args.edited_candidate else None
    reviewed = apply_review(row, args.status, reviewer_id=args.reviewer_id, reason=args.reason, edited_messages=edited)
    event = append_review_event(args.event_log, reviewed)
    if args.reviewed_row_log and args.status == "redact_keep":
        append_reviewed_row(args.reviewed_row_log, reviewed)
    print(json.dumps(event, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
