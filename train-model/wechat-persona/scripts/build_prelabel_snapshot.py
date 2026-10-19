#!/usr/bin/env python3
"""Build an immutable experimental SFT view from the current GPT prelabels."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wechat_persona.curation import CONTROLLED_ROOT
from wechat_persona.prelabel_snapshot import (
    PrelabelSnapshotError,
    compile_prelabel_snapshot,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curated-dataset", type=Path, required=True)
    parser.add_argument("--review-state", type=Path, required=True)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=CONTROLLED_ROOT / "experimental-snapshots",
    )
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        result = compile_prelabel_snapshot(
            curated_dataset=args.curated_dataset,
            review_state_path=args.review_state,
            output_root=args.output_root,
            authorization_id=args.authorization_id,
            execute=args.execute,
        )
    except (PrelabelSnapshotError, FileExistsError, OSError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
