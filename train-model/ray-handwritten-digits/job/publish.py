#!/usr/bin/env python3
"""Build and upload the Ray Job runtime release to MinIO."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ray_handwritten_digits.job_release import publish_main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(publish_main())
