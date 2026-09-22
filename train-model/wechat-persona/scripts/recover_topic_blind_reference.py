#!/usr/bin/env python3
"""Check explicit speaker indices, then recover only invalid blind references."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona.topic_blind_reference_recovery import run_reference


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', choices=['preflight', 'private'], required=True)
    parser.add_argument('--config', dest='config_path', type=Path, default=ROOT / 'configs/topic-blind-reference-index-recovery-v1.yaml')
    for name in ('output-root', 'controlled-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('packet', 'consent', 'preflight', 'parent'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--base-url', default=os.environ.get('OPENAI_BASE_URL'))
    parser.add_argument('--auth-file', type=Path, default=Path.home() / '.codex/auth.json')
    parser.add_argument('--authorization-reference', required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--plan', action='store_true')
    modes.add_argument('--execute', action='store_true')
    args = vars(parser.parse_args())
    args.pop('plan')
    try:
        report = run_reference(**args)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report['status'] == 'planned' or (report['status'] == 'complete'
            and (args['scope'] != 'preflight' or report['route_gate_passed'])) else 2
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'blocked', 'error_type': type(exc).__name__}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
