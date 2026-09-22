#!/usr/bin/env python3
"""Plan or execute label-blind diagnostics without changing frozen candidates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona.topic_validation_duplicates import run_audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', dest='config_path', type=Path, default=ROOT / 'configs/topic-validation-duplicates-v1.yaml')
    for name in ('packet', 'development', 'output-root', 'controlled-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--plan', action='store_true')
    mode.add_argument('--execute', action='store_true')
    args = vars(parser.parse_args())
    args.pop('plan')
    try:
        print(json.dumps(run_audit(**args), ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'blocked', 'error_type': type(exc).__name__}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
