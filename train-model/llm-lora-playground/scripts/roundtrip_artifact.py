#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from llm_lora_playground.tracking import ArtifactIntegrityError, download_and_verify_artifact, reproduce_evaluation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        print({"run_id": args.run_id, "roundtrip_status": "blocked", "reason": "MLFLOW_TRACKING_URI is required"})
        return 2
    try:
        result = reproduce_evaluation(args.run_id, args.output_dir, tracking_uri)
    except Exception as exc:
        print({"run_id": args.run_id, "roundtrip_status": "failed", "reason": f"{type(exc).__name__}: {exc}"})
        return 2
    (args.output_dir / "roundtrip.json").write_text(json.dumps({**result, "roundtrip_status": "passed"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print({"run_id": args.run_id, "roundtrip_status": "passed", "output_dir": str(args.output_dir)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
