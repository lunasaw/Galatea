#!/usr/bin/env python3
"""Build an offline, immutable adjudication workbench without new model calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona.topic_adjudication import build_adjudication


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('before', 'after', 'review', 'audit', 'cross', 'consent', 'output-root', 'controlled-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--policy', dest='policy_path', type=Path, default=ROOT / 'configs/topic-adjudication-v1.yaml')
    parser.add_argument('--cross-policy', dest='cross_policy_path', type=Path, default=ROOT / 'configs/topic-cross-review-v1.yaml')
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--plan', action='store_true')
    modes.add_argument('--execute', action='store_true')
    params = vars(parser.parse_args())
    params.pop('plan')
    try:
        print(json.dumps(build_adjudication(**params), ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({'status': 'blocked', 'error_type': type(exc).__name__, 'error': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
