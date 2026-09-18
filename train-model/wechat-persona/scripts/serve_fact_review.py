#!/usr/bin/env python3
"""Serve one immutable daily-memory snapshot for loopback-only fact review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wechat_persona.fact_review_server import (  # noqa: E402
    FactReviewApplication,
    FactReviewServerError,
    FactReviewStore,
    create_server,
)


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--review-dir", type=Path, required=True)
    parser.add_argument(
        "--page",
        type=Path,
        default=project_root / "docs" / "fact-review.html",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=51645)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        store = FactReviewStore(
            snapshot_dir=args.snapshot_dir,
            review_dir=args.review_dir,
            create_workspace=not args.check,
        )
        if args.check:
            print(
                json.dumps(
                    {
                        "status": "ready",
                        **store.bootstrap(),
                        "host": args.host,
                        "port": args.port,
                        "training_started": False,
                        "confirmed_cards_built": False,
                        "rag_index_built": False,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        application = FactReviewApplication(store=store, page_path=args.page)
        server = create_server(
            application=application,
            host=args.host,
            port=args.port,
        )
    except (OSError, FactReviewServerError) as exc:
        print(
            json.dumps(
                {"status": "blocked", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "status": "serving",
                "host": args.host,
                "port": args.port,
                "snapshot_id": store.snapshot_id,
                "training_started": False,
                "confirmed_cards_built": False,
                "rag_index_built": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
