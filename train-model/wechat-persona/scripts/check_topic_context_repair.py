#!/usr/bin/env python3
"""Record structural context repairs without transferring previous quality labels."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona.topic_comparison import report_context_repair


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('before', 'after', 'audit', 'output-root', 'controlled-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    result = report_context_repair(**vars(parser.parse_args()))
    result['report'].pop('cases')
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
