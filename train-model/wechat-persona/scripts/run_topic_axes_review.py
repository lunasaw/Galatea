#!/usr/bin/env python3
"""Plan/run synthetic v2 calibration, or a calibration-gated private review."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona.topic_axes_review import run_review


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scope', choices=['calibration', 'private_review'], required=True)
    parser.add_argument('--config', dest='config_path', type=Path, default=ROOT / 'configs/topic-axes-review-v2.yaml')
    for name in ('output-root', 'controlled-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--calibration', type=Path)
    for name in ('before', 'after', 'review', 'audit', 'cross', 'consent'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--cross-policy', type=Path, default=ROOT / 'configs/topic-cross-review-v1.yaml')
    parser.add_argument('--base-url', default=os.environ.get('OPENAI_BASE_URL'))
    parser.add_argument('--auth-file', type=Path, default=Path.home() / '.codex/auth.json')
    parser.add_argument('--authorization-reference', required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--plan', action='store_true')
    modes.add_argument('--execute', action='store_true')
    args = vars(parser.parse_args())
    args.pop('plan')
    private_inputs = {name: args.pop(name) for name in ('before', 'after', 'review', 'audit', 'cross', 'consent', 'cross_policy')}
    if args['scope'] == 'private_review' and any(v is None for v in private_inputs.values()):
        parser.error('private_review requires all source paths')
    try:
        result = run_review(**args, private_inputs=private_inputs)
        # IDs and aggregate counts only, never model text or private replies.
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result['status'] in {'planned', 'complete'} and result.get('protocol_gate_passed', True) else 2
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'blocked', 'error_type': type(exc).__name__}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
