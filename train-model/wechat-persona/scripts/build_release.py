#!/usr/bin/env python3
"""Build a local, code-only wechat-persona Ray Release and registration draft."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wechat_persona.job_release import (  # noqa: E402
    build_release,
    write_registration_materials,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--registration-output", type=Path)
    parser.add_argument("--snapshot-manifest", type=Path)
    parser.add_argument("--campaign-id", default="wechat-persona-formal-sft-v2")
    parser.add_argument("--experiment-id", default="ADMIN_MUST_BIND_MLFLOW_EXPERIMENT_ID")
    parser.add_argument("--approved-by", default="PENDING_ADMIN_APPROVAL")
    parser.add_argument("--expires-at", type=int)
    args = parser.parse_args()

    release = build_release(
        args.project_root,
        args.output_dir,
        allow_dirty=args.allow_dirty,
    )
    registration: dict[str, str] = {}
    if args.registration_output:
        registration = {
            key: str(path)
            for key, path in write_registration_materials(
                release,
                args.registration_output,
                snapshot_manifest=args.snapshot_manifest,
                campaign_id=args.campaign_id,
                experiment_id=args.experiment_id,
                approved_by=args.approved_by,
                expires_at=args.expires_at,
            ).items()
        }
    print(json.dumps({
        "status": "built",
        "release_id": release.manifest["release_id"],
        "archive_path": str(release.archive_path),
        "manifest_path": str(release.manifest_path),
        "registration_materials": registration,
        "uploaded": False,
        "registered": False,
        "training_started": False,
        "mlflow_run_created": False,
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
