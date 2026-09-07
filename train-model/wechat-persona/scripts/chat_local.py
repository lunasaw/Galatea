#!/usr/bin/env python3
"""Local-only text chat prototype with explicit AI identity and no default logs."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wechat_persona.runtime import load_eligible_runtime, load_project_config, safe_chat_response


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--owner-scope", required=True)
    parser.add_argument("--message", required=True)
    parser.add_argument("--memory", action="store_true")
    args = parser.parse_args()
    config = load_project_config(args.config)
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    load_eligible_runtime(config, candidate)
    response = safe_chat_response(args.message, owner_scope=args.owner_scope, memory_enabled=args.memory)
    print(response.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
