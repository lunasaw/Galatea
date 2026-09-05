#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    print({"run_id": args.run_id, "roundtrip_status": "blocked", "reason": "MLflow API configuration is required"})
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
