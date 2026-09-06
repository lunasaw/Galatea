#!/usr/bin/env python3
"""Generate a text-only screenplay from approved, redacted event cards."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wechat_persona.runtime import load_project_config, validate_project_config
from wechat_persona.screenplay import generate_screenplay


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--event-cards", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = load_project_config(args.config)
    validate_project_config(config, raise_on_error=True)
    cards = json.loads(args.event_cards.read_text(encoding="utf-8"))
    if not isinstance(cards, list):
        raise ValueError("event cards must be a JSON array")
    options = dict(config["screenplay"])
    options.pop("rewrite_policy", None)
    screenplay = generate_screenplay(cards, **options)
    rendered = json.dumps(screenplay, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
