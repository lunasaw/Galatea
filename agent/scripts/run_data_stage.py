#!/usr/bin/env python3
"""
Data stage CLI.

Runs DataAgent to prepare dataset.
"""

import sys
import asyncio
from pathlib import Path


def main():
    """
    Run data preparation stage.

    Usage:
        python agent/scripts/run_data_stage.py --source SOURCE_URI --project PROJECT_NAME
    """
    print("Data Stage CLI")
    print("=" * 50)
    print()
    print("Future: Stage 2+ - DataAgent implementation")
    print()
    print("This will:")
    print("  1. Inspect data source")
    print("  2. Compute manifest")
    print("  3. Submit Ray Data job")
    print("  4. Validate output")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
