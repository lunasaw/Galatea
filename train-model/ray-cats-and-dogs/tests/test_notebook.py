from __future__ import annotations

import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK_PATH = PROJECT_ROOT / "notebooks" / "smoke-run-guide.ipynb"


class SmokeNotebookTest(unittest.TestCase):
    def test_notebook_is_clean_and_delegates_formal_training(self) -> None:
        notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
        self.assertEqual(4, notebook["nbformat"])
        code = "\n".join(
            "".join(cell["source"])
            for cell in notebook["cells"]
            if cell["cell_type"] == "code"
        )

        self.assertIn("RUN_SMOKE = False", code)
        self.assertIn("read_only_plan", code)
        self.assertIn("submit_job", code)
        self.assertIn("build_runtime_env(PROJECT_ROOT)", code)
        self.assertNotIn("model.fit", code)
        self.assertNotIn("mlflow.db", code)
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                self.assertIsNone(cell["execution_count"])
                self.assertEqual([], cell["outputs"])


if __name__ == "__main__":
    unittest.main()
