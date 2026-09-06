#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wechat_persona.runtime import load_project_config, validate_project_config
from wechat_persona.pipeline import run_import_pipeline

def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only importer contract check")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--execute", action="store_true", help="write a new redacted review-only dataset version")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--consent", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--self-speaker")
    parser.add_argument("--target-speaker")
    args = parser.parse_args()
    config = load_project_config(args.config)
    errors = validate_project_config(config)
    if errors:
        print(json.dumps({"status": "blocked", "errors": errors, "will_write_formal_data": False, "will_create_mlflow_run": False}, ensure_ascii=False, sort_keys=True))
        return 2
    if args.execute:
        required = (args.source, args.consent, args.output_root, args.self_speaker, args.target_speaker)
        if not all(required):
            print(json.dumps({"status": "blocked", "errors": ["--execute requires source, consent, output-root, self-speaker and target-speaker"], "will_write_formal_data": False, "will_create_mlflow_run": False}, ensure_ascii=False, sort_keys=True))
            return 2
        root_env = str(config["dataset"].get("source_root_env", "WECHAT_PERSONA_RAW_ROOT"))
        allowed_root = Path(os.environ[root_env]) if os.environ.get(root_env) else args.source.parent
        result = run_import_pipeline(source=args.source, consent=args.consent, output_root=args.output_root, allowed_root=allowed_root, speaker_map={args.self_speaker: "self", args.target_speaker: "target"}, execute=True)
        result["dataset_root"] = str(result["dataset_root"])
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    print(json.dumps({"status": "ok", "errors": [], "will_write_formal_data": False, "will_create_mlflow_run": False}, ensure_ascii=False, sort_keys=True))
    return 0

if __name__ == "__main__": raise SystemExit(main())
