#!/usr/bin/env python3
"""验证并提交 House Prices 已发布 Ray release。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_kaggle_house_prices.job_release import cd_main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(cd_main())
