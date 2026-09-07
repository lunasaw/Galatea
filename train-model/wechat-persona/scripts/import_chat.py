#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wechat_persona.runtime import load_project_config, validate_project_config
from wechat_persona.pipeline import run_import_pipeline
from wechat_persona.importers import import_messages
from wechat_persona.consent import ConsentError

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
    parser.add_argument("--speaker-map-file", type=Path, help="controlled JSON file with self_speaker and target_speaker")
    parser.add_argument("--derive-wechat-speakers", action="store_true", help="derive sender labels from native WeChat isSent/senderUsername fields without printing them")
    args = parser.parse_args()
    config = load_project_config(args.config)
    errors = validate_project_config(config)
    if errors:
        print(json.dumps({"status": "blocked", "errors": errors, "will_write_formal_data": False, "will_create_mlflow_run": False}, ensure_ascii=False, sort_keys=True))
        return 2
    if args.execute:
        self_speaker = args.self_speaker
        target_speaker = args.target_speaker
        if args.speaker_map_file:
            try:
                mapping = json.loads(args.speaker_map_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                mapping = {}
            if isinstance(mapping, dict):
                self_speaker = self_speaker or mapping.get("self_speaker")
                target_speaker = target_speaker or mapping.get("target_speaker")
        if args.derive_wechat_speakers and args.source and (not self_speaker or not target_speaker):
            try:
                source_rows, _ = import_messages(args.source, timezone_name="Asia/Shanghai", allowed_root=args.source.parent)
                self_values = {row.get("speaker") for row in source_rows if row.get("is_sent") is True and row.get("speaker")}
                target_values = {row.get("speaker") for row in source_rows if row.get("is_sent") is not True and row.get("speaker")}
            except Exception:
                self_values, target_values = set(), set()
            if len(self_values) == 1 and len(target_values) == 1:
                self_speaker = next(iter(self_values))
                target_speaker = next(iter(target_values))
        required = (args.source, args.consent, args.output_root, self_speaker, target_speaker)
        if not all(required):
            print(json.dumps({"status": "blocked", "errors": ["--execute requires source, consent, output-root, self-speaker and target-speaker"], "will_write_formal_data": False, "will_create_mlflow_run": False}, ensure_ascii=False, sort_keys=True))
            return 2
        root_env = str(config["dataset"].get("source_root_env", "WECHAT_PERSONA_RAW_ROOT"))
        allowed_root = Path(os.environ[root_env]) if os.environ.get(root_env) else args.source.parent
        try:
            result = run_import_pipeline(source=args.source, consent=args.consent, output_root=args.output_root, allowed_root=allowed_root, speaker_map={self_speaker: "self", target_speaker: "target"}, execute=True)
        except (ConsentError, OSError, ValueError) as exc:
            print(json.dumps({"status": "blocked", "errors": [str(exc)], "will_write_formal_data": False, "will_create_mlflow_run": False}, ensure_ascii=False, sort_keys=True))
            return 2
        result["dataset_root"] = str(result["dataset_root"])
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    print(json.dumps({"status": "ok", "errors": [], "will_write_formal_data": False, "will_create_mlflow_run": False}, ensure_ascii=False, sort_keys=True))
    return 0

if __name__ == "__main__": raise SystemExit(main())
