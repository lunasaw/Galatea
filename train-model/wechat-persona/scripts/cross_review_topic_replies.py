#!/usr/bin/env python3
"""Plan or execute a bounded review using two requested model families."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona.topic_cross_review import run_cross_review


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'configs/topic-cross-review-v1.yaml')
    for name in ('before', 'after', 'review', 'audit', 'consent', 'output-root', 'controlled-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--base-url', default=os.environ.get('OPENAI_BASE_URL'))
    parser.add_argument('--auth-file', type=Path, default=Path.home() / '.codex/auth.json')
    parser.add_argument('--authorization-reference', required=True)
    parser.add_argument('--resume-from', type=Path, help='Adopt valid responses from a bound incomplete workspace; retry only missing batches')
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--plan', action='store_true')
    modes.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.base_url:
        parser.error('explicit API base URL is required')
    params = vars(args)
    params['config_path'] = params.pop('config')
    params.pop('plan')
    try:
        result = run_cross_review(**params)
        print(json.dumps({key: value for key, value in result.items() if key != 'selected_sample_ids'}, ensure_ascii=False, sort_keys=True))
        return 0 if result['status'] in {'planned', 'complete'} else 2
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({'status': 'blocked', 'error_type': type(exc).__name__, 'error': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
