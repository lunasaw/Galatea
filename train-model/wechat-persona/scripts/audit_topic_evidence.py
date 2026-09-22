#!/usr/bin/env python3
"""Blind evidence review and source-context diagnosis; never train or rewrite targets."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona.topic_evidence_audit import run_audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'configs/topic-evidence-audit-v1.yaml')
    for name in ('pilot', 'review', 'consent', 'output-root', 'controlled-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--base-url', default=os.environ.get('OPENAI_BASE_URL'))
    parser.add_argument('--auth-file', type=Path, default=Path.home() / '.codex/auth.json')
    parser.add_argument('--authorization-reference', required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--plan', action='store_true')
    modes.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.base_url:
        parser.error('explicit base URL or OPENAI_BASE_URL required')
    try:
        result = run_audit(pilot=args.pilot, review=args.review, config_path=args.config,
                           consent=args.consent, output_root=args.output_root, controlled_root=args.controlled_root,
                           base_url=args.base_url, auth_file=args.auth_file,
                           authorization_reference=args.authorization_reference, execute=args.execute)
        # Per-sample IDs and diagnosis stay in the private report.
        public = {key: value for key, value in result.items() if key not in {'consensus_keep_ids', 'diagnosis_cases'}}
        print(json.dumps(public, ensure_ascii=False, sort_keys=True))
        return 0 if result['status'] in {'planned', 'complete'} else 2
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({'status': 'blocked', 'error_type': type(exc).__name__, 'error': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
