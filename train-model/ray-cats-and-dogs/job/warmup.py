#!/usr/bin/env python3
"""Verify the node-local project environment before formal Ray Jobs."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_cats_dogs.job_release import warmup_main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(warmup_main())
