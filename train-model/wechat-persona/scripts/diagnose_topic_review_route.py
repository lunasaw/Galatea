#!/usr/bin/env python3
"""Capture one failed synthetic probe's HTTP error without disclosing gateway text."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from wechat_persona.topic_route_diagnostic import main

if __name__ == '__main__':
    raise SystemExit(main())
