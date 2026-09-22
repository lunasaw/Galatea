#!/usr/bin/env python3
"""Compile a private draft with evidence links; does not grant training eligibility."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from wechat_persona.topic_evidence_export import compile_evidence_draft


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('pilot', 'review', 'audit', 'output-root', 'controlled-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    result = compile_evidence_draft(**vars(parser.parse_args()))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
