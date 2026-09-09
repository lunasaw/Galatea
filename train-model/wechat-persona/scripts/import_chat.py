#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from wechat_persona.runtime import load_project_config, validate_project_config
from wechat_persona.pipeline import PipelineError, run_import_pipeline
from wechat_persona.importers import ImportErrorSafe, import_messages
from wechat_persona.consent import ConsentError


def _blocked(errors: list[str]) -> int:
    """Emit a deliberately small, machine-readable blocked response."""
    print(json.dumps({
        "status": "blocked",
        "errors": [str(error) for error in errors],
        "will_write_formal_data": False,
        "will_create_mlflow_run": False,
    }, ensure_ascii=False, sort_keys=True))
    return 2


def _load_speaker_map(path: Path | None) -> tuple[str | None, str | None, list[str]]:
    if path is None:
        return None, None, []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, None, ["speaker-map-file must be readable UTF-8 JSON"]
    if not isinstance(value, dict):
        return None, None, ["speaker-map-file must contain an object"]
    if set(value) != {"self_speaker", "target_speaker"}:
        return None, None, [
            "speaker-map-file must contain only self_speaker and target_speaker"
        ]
    self_speaker = value.get("self_speaker")
    target_speaker = value.get("target_speaker")
    if self_speaker is not None and not isinstance(self_speaker, str):
        return None, None, ["speaker-map-file self_speaker must be a string"]
    if target_speaker is not None and not isinstance(target_speaker, str):
        return None, None, ["speaker-map-file target_speaker must be a string"]
    self_speaker = self_speaker.strip() if isinstance(self_speaker, str) else ""
    target_speaker = target_speaker.strip() if isinstance(target_speaker, str) else ""
    if not self_speaker or not target_speaker:
        return None, None, ["speaker-map-file speaker values must be non-empty"]
    if self_speaker == target_speaker:
        return None, None, ["speaker-map-file self and target speakers must differ"]
    return self_speaker, target_speaker, []


def _allowed_root(config: dict) -> tuple[Path | None, list[str]]:
    """Resolve the explicitly configured source root; never widen it implicitly."""
    dataset = config.get("dataset") or {}
    root_env = str(dataset.get("source_root_env", "WECHAT_PERSONA_RAW_ROOT"))
    configured = os.environ.get(root_env)
    if not configured:
        return None, [f"{root_env} must identify the controlled source root"]
    return Path(configured), []


def _derive_speakers(source: Path, allowed_root: Path) -> tuple[str | None, str | None]:
    """Derive exactly one self and target label from native WeChat fields."""
    rows, _ = import_messages(source, timezone_name="Asia/Shanghai", allowed_root=allowed_root)
    self_values = {
        str(row.get("speaker")) for row in rows
        if row.get("is_sent") is True and row.get("speaker")
    }
    target_values = {
        str(row.get("speaker")) for row in rows
        if row.get("is_sent") is not True and row.get("speaker")
    }
    if len(self_values) == 1 and len(target_values) == 1:
        return next(iter(self_values)), next(iter(target_values))
    return None, None

def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only importer contract check")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="run a full read-only source preflight")
    parser.add_argument("--check-source", action="store_true", help="alias for --check")
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
        return _blocked(errors)
    # Both flags intentionally select the same strict preflight.  Accepting
    # both is harmless and keeps wrappers that append their preferred alias
    # from accidentally bypassing the check.
    if args.execute:
        if args.check or args.check_source:
            return _blocked(["--execute cannot be combined with --check"])
        if not args.source or not args.consent or not args.output_root:
            return _blocked(["--execute requires source, consent and output-root"])
        loaded_self, loaded_target, map_errors = _load_speaker_map(args.speaker_map_file)
        if map_errors:
            return _blocked(map_errors)
        self_speaker = args.self_speaker
        target_speaker = args.target_speaker
        self_speaker = self_speaker or loaded_self
        target_speaker = target_speaker or loaded_target
        if self_speaker == target_speaker:
            return _blocked(["self-speaker and target-speaker must differ"])
        allowed_root, root_errors = _allowed_root(config)
        if root_errors or allowed_root is None:
            return _blocked(root_errors)
        if args.derive_wechat_speakers and args.source and (not self_speaker or not target_speaker):
            try:
                self_speaker, target_speaker = _derive_speakers(args.source, allowed_root)
            except (ImportErrorSafe, OSError, ValueError):
                self_speaker, target_speaker = None, None
        if not self_speaker or not target_speaker:
            return _blocked(["--execute requires self-speaker and target-speaker, or --derive-wechat-speakers"])
        try:
            result = run_import_pipeline(
                source=args.source,
                consent=args.consent,
                output_root=args.output_root,
                allowed_root=allowed_root,
                speaker_map={self_speaker: "self", target_speaker: "target"},
                execute=True,
            )
        except (ConsentError, ImportErrorSafe, PipelineError, OSError, ValueError) as exc:
            return _blocked([str(exc)])
        result["dataset_root"] = str(result["dataset_root"])
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0

    # ``--check`` and ``--check-source`` are strict read-only preflights.  A
    # bare invocation is treated the same way so it cannot silently report an
    # unverified source as healthy.
    if not args.source or not args.consent:
        return _blocked(["--check requires source and consent"])
    loaded_self, loaded_target, map_errors = _load_speaker_map(args.speaker_map_file)
    if map_errors:
        return _blocked(map_errors)
    self_speaker = args.self_speaker or loaded_self
    target_speaker = args.target_speaker or loaded_target
    if self_speaker == target_speaker:
        return _blocked(["self-speaker and target-speaker must differ"])
    allowed_root, root_errors = _allowed_root(config)
    if root_errors or allowed_root is None:
        return _blocked(root_errors)
    if args.derive_wechat_speakers and (not self_speaker or not target_speaker):
        try:
            self_speaker, target_speaker = _derive_speakers(args.source, allowed_root)
        except (ImportErrorSafe, OSError, ValueError):
            self_speaker, target_speaker = None, None
    if not self_speaker or not target_speaker:
        return _blocked(["--check requires self-speaker and target-speaker, or --derive-wechat-speakers"])
    try:
        result = run_import_pipeline(
            source=args.source,
            consent=args.consent,
            output_root=args.output_root or args.source.parent / ".preflight-unused",
            allowed_root=allowed_root,
            speaker_map={self_speaker: "self", target_speaker: "target"},
            execute=False,
        )
    except (ConsentError, ImportErrorSafe, PipelineError, OSError, ValueError) as exc:
        return _blocked([str(exc)])
    # The plan contains only IDs, digests, versions, counts and aggregate
    # scanner fields.  It intentionally omits source text and consent fields.
    result["will_write"] = False
    result["will_write_formal_data"] = False
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0

if __name__ == "__main__": raise SystemExit(main())
