#!/usr/bin/env python3
"""Plan five-variant evaluation and manage candidate/test governance records."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wechat_persona.evaluation import claim_test_once, freeze_candidate


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--freeze-candidate", action="store_true")
    parser.add_argument("--test-once", action="store_true")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.run:
        payload = {"status": "blocked", "reason": "durable evaluation requires the Galatea-authorized fixed Ray Driver", "execution_backend": "ray", "will_create_mlflow_run": False}
        print(json.dumps(payload, sort_keys=True))
        return 2
    if args.freeze_candidate:
        if not args.candidate or not args.protocol:
            parser.error("--freeze-candidate requires --candidate and --protocol")
        payload = freeze_candidate(_read_json(args.candidate), _read_json(args.protocol))
    elif args.test_once:
        if not args.candidate or not args.protocol or not args.ledger:
            parser.error("--test-once requires --candidate, --protocol and --ledger")
        frozen = _read_json(args.candidate)
        payload = claim_test_once(str(frozen.get("candidate_freeze_id", frozen.get("freeze_id", ""))), _read_json(args.protocol), args.ledger)
    else:
        payload = {"status": "planned", "variants": ["base", "prompt-only", "rag", "lora", "rag+lora"], "will_create_mlflow_run": False, "test_access": "untouched"}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
