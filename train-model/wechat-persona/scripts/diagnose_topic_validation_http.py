#!/usr/bin/env python3
"""Capture one HTTP diagnostic separately from validation judgments."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona.topic_validation_http_diagnostic import run_diagnostic


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('failed', 'packet', 'consent', 'output-root', 'controlled-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--config', dest='config_path', type=Path, default=ROOT / 'configs/topic-validation-http-diagnostic-v1.yaml')
    parser.add_argument('--base-url', default=os.environ.get('OPENAI_BASE_URL'))
    parser.add_argument('--auth-file', type=Path, default=Path.home() / '.codex/auth.json')
    parser.add_argument('--authorization-reference', required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--plan', action='store_true')
    modes.add_argument('--execute', action='store_true')
    args = vars(parser.parse_args())
    args.pop('plan')
    try:
        print(json.dumps(run_diagnostic(**args), ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'blocked', 'error_type': type(exc).__name__}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
