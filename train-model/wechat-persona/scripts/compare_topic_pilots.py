#!/usr/bin/env python3
"""Write a private aggregate comparison on a frozen target population."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona.topic_comparison import compare_pilots


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('before', 'after', 'before-review', 'after-review', 'output-root', 'controlled-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = compare_pilots(**vars(args))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
