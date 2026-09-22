#!/usr/bin/env python3
"""Plan or run one bounded synthetic rubric check with the existing reviewers."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona.topic_rubric_calibration import run_calibration


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('output-root', 'controlled-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--policy', dest='policy_path', type=Path, default=ROOT / 'configs/topic-adjudication-v1.yaml')
    parser.add_argument('--cross-policy', dest='cross_policy_path', type=Path, default=ROOT / 'configs/topic-cross-review-v1.yaml')
    parser.add_argument('--base-url', default=os.environ.get('OPENAI_BASE_URL'))
    parser.add_argument('--auth-file', type=Path, default=Path.home() / '.codex/auth.json')
    parser.add_argument('--authorization-reference', required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--plan', action='store_true')
    modes.add_argument('--execute', action='store_true')
    params = vars(parser.parse_args())
    params.pop('plan')
    try:
        result = run_calibration(**params)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result['status'] in {'planned', 'complete'} else 2
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({'status': 'blocked', 'error_type': type(exc).__name__}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
