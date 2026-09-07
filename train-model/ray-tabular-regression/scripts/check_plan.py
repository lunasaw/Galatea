#!/usr/bin/env python3
"""Read-only configuration validation and resource estimate; cannot execute training."""
import argparse
import json
from pathlib import Path
import yaml

parser = argparse.ArgumentParser()
parser.add_argument("--config", type=Path, required=True)
args = parser.parse_args()
config = yaml.safe_load(args.config.read_text())
resources = config["resources"]
if resources["workers"] != 1:
    raise SystemExit("first reference workload requires workers=1")
print(json.dumps({"mode": "read-only-plan", "training": False,
                  "reserved_cpus": resources["cpus"] * resources["workers"],
                  "reserved_gpus": resources["gpus"] * resources["workers"],
                  "deadline_seconds": resources["seconds"], "config": config}, sort_keys=True))

