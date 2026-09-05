"""Project-local tests.

The package is intentionally kept in ``src``.  Adding it to ``sys.path`` here keeps
the tests runnable from a source checkout without installing the project first.
"""

from pathlib import Path
import sys


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
