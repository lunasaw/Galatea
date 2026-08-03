#!/usr/bin/env python3
"""Submit an already published release through the Ray Jobs API."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_cats_dogs.job_release import cd_main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(cd_main())
