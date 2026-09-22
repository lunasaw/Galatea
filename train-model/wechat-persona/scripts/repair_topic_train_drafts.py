#!/usr/bin/env python3
"""Replay existing train evidence and publish an immutable offline repair packet."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona.topic_train_repair import run_repair


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('pilot', 'review', 'raw', 'source', 'consent', 'tokenizer-path', 'output-root', 'controlled-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--config-path', type=Path, default=ROOT / 'configs/topic-train-repair-v1.yaml')
    parser.add_argument('--review-config', type=Path, default=ROOT / 'configs/topic-axes-review-v4.yaml')
    parser.add_argument('--base-url', required=True, help='Original review gateway identity; no requests are sent')
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--plan', action='store_true')
    modes.add_argument('--execute', action='store_true')
    args = vars(parser.parse_args())
    args.pop('plan')
    print(json.dumps(run_repair(**args), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
