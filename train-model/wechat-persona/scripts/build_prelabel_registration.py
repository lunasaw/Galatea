#!/usr/bin/env python3
"""Create Galatea registry material for a non-promotable GPT-prelabel baseline."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wechat_persona.job_release import (
    BuiltRelease,
    write_prelabel_registration_materials,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--snapshot-manifest", type=Path, required=True)
    parser.add_argument("--base-registry", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--train-version-id", required=True)
    parser.add_argument("--validation-version-id", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--approved-by", required=True)
    args = parser.parse_args()
    release_manifest = json.loads(args.release_manifest.read_text(encoding="utf-8"))
    release = BuiltRelease(
        directory=args.release_manifest.parent.resolve(),
        archive_path=(args.release_manifest.parent / "wechat-persona-release.zip").resolve(),
        manifest=release_manifest,
        manifest_path=args.release_manifest.resolve(),
    )
    files = write_prelabel_registration_materials(
        release,
        args.output_dir,
        snapshot_manifest=args.snapshot_manifest,
        base_registry_path=args.base_registry,
        train_version_id=args.train_version_id,
        validation_version_id=args.validation_version_id,
        campaign_id=args.campaign_id,
        approved_by=args.approved_by,
    )
    print(
        json.dumps(
            {key: str(path) for key, path in files.items()},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
