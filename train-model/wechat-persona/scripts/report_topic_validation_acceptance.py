#!/usr/bin/env python3
"""Join immutable validation evidence into a count-only P3 acceptance decision."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona.topic_validation_acceptance import run_acceptance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('packet', 'machine', 'blind', 'duplicates', 'output-root', 'controlled-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--plan', action='store_true')
    modes.add_argument('--execute', action='store_true')
    args = vars(parser.parse_args())
    args.pop('plan')
    try:
        print(json.dumps(run_acceptance(**args), ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'blocked', 'error_type': type(exc).__name__}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
