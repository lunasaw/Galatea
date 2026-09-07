#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ray_tabular_regression.release import build_release, write_registration_examples

parser = argparse.ArgumentParser(description="Build an immutable code-only workload release")
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--execution-public-key", type=Path, required=True)
parser.add_argument("--registration-output", type=Path)
parser.add_argument("--environment-identity", default="conda-lock:conda.yaml")
args = parser.parse_args()
result = build_release(ROOT, args.output, args.execution_public_key.read_bytes())
if args.registration_output:
    project, campaign = write_registration_examples(ROOT, args.registration_output, result,
                                                     args.environment_identity)
    result.update(project_json=str(project), campaign_json=str(campaign))
print(json.dumps(result, sort_keys=True))
