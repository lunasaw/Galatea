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
        source = path.read_text()
        driver = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "run_driver")
        worker = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "run_worker")
        driver_text = ast.get_source_segment(source, driver)
        worker_text = ast.get_source_segment(source, worker)
        self.assertIn("start_training_run", driver_text)
        self.assertNotIn("start_training_run", worker_text)


if __name__ == "__main__":
    unittest.main()
