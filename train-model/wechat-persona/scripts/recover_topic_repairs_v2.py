#!/usr/bin/env python3
"""Recover the remaining repair judgments after repeated stable route probes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona.topic_repair_recovery_v2 import run_recovery


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('parent', 'queue', 'repair', 'consent', 'output-root', 'controlled-root'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--preflight', action='append', required=True, type=Path)
    parser.add_argument('--config-path', type=Path, default=ROOT / 'configs/topic-repair-recovery-v2.yaml')
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--auth-file', type=Path, default=Path.home() / '.codex/auth.json')
    parser.add_argument('--authorization-reference', required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--plan', action='store_true')
    modes.add_argument('--execute', action='store_true')
    args = vars(parser.parse_args())
    args.pop('plan')
    args['preflights'] = args.pop('preflight')
    result = run_recovery(**args)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result['status'] in {'planned', 'complete'} else 2


if __name__ == '__main__':
    raise SystemExit(main())
