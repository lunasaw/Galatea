#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from llm_lora_playground.datasets import generate_dataset


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--count", type=int, default=400)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--version", default="toy-v1")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if args.check_only:
        args.count = min(args.count, 8)
    manifest = generate_dataset(args.output_dir, args.count, args.seed, args.version)
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
