#!/usr/bin/env python3
"""Serve one curated draft for loopback-only human usefulness review."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wechat_persona.review_server import (
    ReviewApplication,
    ReviewServerError,
    ReviewStore,
    create_server,
)


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--review-dir", type=Path, required=True)
    parser.add_argument(
        "--page",
        type=Path,
        default=project_root / "docs" / "review.html",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=51644)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        store = ReviewStore(
            dataset_dir=args.dataset_dir,
            review_dir=args.review_dir,
        )
        application = ReviewApplication(store=store, page_path=args.page)
        if args.check:
            print(
                json.dumps(
                    {
                        "status": "ready",
                        **store.bootstrap(),
                        "host": args.host,
                        "port": args.port,
                        "training_started": False,
                        "test_access": "blocked",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        server = create_server(
            application=application,
            host=args.host,
            port=args.port,
        )
    except (OSError, ReviewServerError) as exc:
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
                "curation_id": store.identity.curation_id,
                "test_access": "blocked",
                "training_started": False,
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
