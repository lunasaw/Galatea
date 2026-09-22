#!/usr/bin/env python3
"""Audit frozen synthetic confidence evidence without external requests or fitting."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona.topic_confidence_audit import run_audit

parser = argparse.ArgumentParser(description=__doc__)
for name in ('v2', 'v3', 'output-root', 'controlled-root'):
    parser.add_argument('--' + name, required=True, type=Path)
mode = parser.add_mutually_exclusive_group(required=True)
mode.add_argument('--plan', action='store_true')
mode.add_argument('--execute', action='store_true')
if __name__ == '__main__':
    args = parser.parse_args()
    try:
        result = run_audit(sources={'v2': (args.v2, ROOT / 'configs/topic-axes-review-v2.yaml'),
                                    'v3': (args.v3, ROOT / 'configs/topic-axes-review-v3-sol.yaml')},
                           output_root=args.output_root, controlled_root=args.controlled_root, execute=args.execute)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({'status': 'blocked', 'error_type': type(exc).__name__}))
        raise SystemExit(2)
