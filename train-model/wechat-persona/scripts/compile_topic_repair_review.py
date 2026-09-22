#!/usr/bin/env python3
"""Compile an immutable train draft from fully replayed repair-review evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona.topic_repair_export import compile_repaired_draft


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('machine', 'queue', 'repair', 'consent', 'output-root', 'controlled-root'):
        parser.add_argument('--' + name, required=True, type=Path)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--plan', action='store_true')
    modes.add_argument('--execute', action='store_true')
    args = vars(parser.parse_args())
    args.pop('plan')
    print(json.dumps(compile_repaired_draft(**args), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
