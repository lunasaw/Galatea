#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from llm_lora_playground.ray_runtime import submit_job


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", default="http://127.0.0.1:8265")
    parser.add_argument("--config", type=Path, default=Path("configs/ray-job-smoke.yaml"))
    parser.add_argument("--data", type=Path)
    parser.add_argument("--submission-id")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    handle = submit_job(args.config, args.address, {"working_dir": str(args.config.resolve().parents[1])}, args.data, args.submission_id, dry_run=not args.run)
    print(handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
