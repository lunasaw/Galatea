#!/usr/bin/env python3
"""Legacy-looking local training name retained only as an explicit blocker."""
import json
def main() -> int:
    print(json.dumps({"status": "blocked", "reason": "use immutable Release and Galatea-authorized scripts/submit_train.py", "will_create_mlflow_run": False}, sort_keys=True)); return 2
if __name__ == "__main__": raise SystemExit(main())
