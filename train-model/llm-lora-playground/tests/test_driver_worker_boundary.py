import ast
from pathlib import Path
import unittest


class DriverWorkerBoundaryTests(unittest.TestCase):
    def test_submit_entrypoint_has_one_driver_owner_boundary(self):
        path = Path(__file__).resolve().parents[1] / "scripts/submit_train.py"
        tree = ast.parse(path.read_text())
        names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
        self.assertIn("run_driver", names)
        self.assertIn("run_worker", names)


if __name__ == "__main__":
    unittest.main()
