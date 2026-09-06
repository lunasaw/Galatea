#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wechat_persona.runtime import load_project_config, validate_project_config
from wechat_persona.datasets import write_sft_snapshot

def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only dataset plan/check boundary")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--check-approved", action="store_true")
    parser.add_argument("--execute", action="store_true", help="create a new formal snapshot after all gates")
    parser.add_argument("--reviewed-jsonl", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = load_project_config(args.config); errors = validate_project_config(config)
    if args.execute:
        if not args.check_approved or not args.reviewed_jsonl or not args.output:
            errors.append("--execute requires --check-approved, --reviewed-jsonl and --output")
        if not errors:
            with args.reviewed_jsonl.open(encoding="utf-8") as handle:
                rows = [json.loads(line) for line in handle if line.strip()]
            manifest = write_sft_snapshot(args.output, rows)
            print(json.dumps({"status": "completed", "manifest": manifest, "will_write_formal_data": True, "will_create_mlflow_run": False}, ensure_ascii=False, sort_keys=True))
            return 0
    elif args.check_approved:
        errors.append("formal dataset export requires explicit --execute after verified consent and completed human review")
    print(json.dumps({"status": "planned" if not errors else "blocked", "errors": errors, "will_write_formal_data": False, "will_create_mlflow_run": False}, ensure_ascii=False, sort_keys=True))
    return 0 if not errors else 2

if __name__ == "__main__": raise SystemExit(main())
