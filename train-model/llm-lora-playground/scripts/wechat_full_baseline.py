#!/usr/bin/env python3
"""Deprecated local baseline entrypoint; governed training owns Base/LoRA evaluation."""

from __future__ import annotations

import json


def main() -> int:
    print(
        json.dumps(
            {
                "status": "blocked",
                "reason": (
                    "local full-dataset baseline execution is disabled; use the project config, "
                    "immutable release, galatea_plan_run, and galatea_submit_job. The governed "
                    "Driver evaluates Base and LoRA on the validation split in the same Run."
                ),
                "will_create_mlflow_run": False,
                "test_access": "untouched",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
