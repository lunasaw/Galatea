#!/usr/bin/env python3
"""Freeze validation candidates and a blind audit packet; no external requests."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona.topic_validation import prepare_validation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', dest='config_path', type=Path, default=ROOT / 'configs/topic-validation-v1.yaml')
    for name in ('source', 'memory', 'consent', 'development', 'completed-review', 'tokenizer-path',
                 'output-root', 'controlled-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--base-url', required=True, help='Bind the previous route; never contacted by this command')
    parser.add_argument('--authorization-reference', required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--plan', action='store_true')
    modes.add_argument('--execute', action='store_true')
    args = vars(parser.parse_args())
    args.pop('plan')
    try:
        print(json.dumps(prepare_validation(**args), ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'blocked', 'error_type': type(exc).__name__}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
