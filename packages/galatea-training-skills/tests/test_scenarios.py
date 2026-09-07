import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScenarioTests(unittest.TestCase):
    def test_all_frozen_synthetic_traces_obey_action_and_evidence_contracts(self):
        evaluator = load_script("evaluate_scenarios")
        fixtures = [json.loads(path.read_text()) for path in sorted((ROOT / "tests" / "scenarios").glob("*.json"))]
        self.assertEqual(len(fixtures), 10)
        for fixture in fixtures:
            with self.subTest(scenario=fixture["scenario_id"]):
                evaluator.evaluate(fixture)

    def test_evaluator_rejects_a_forbidden_trace_action(self):
        evaluator = load_script("evaluate_scenarios")
        fixture = json.loads(next((ROOT / "tests" / "scenarios").glob("*.json")).read_text())
        fixture["trace"].append({"tool": fixture["forbidden_actions"][0], "result": {}})
        with self.assertRaisesRegex(evaluator.ScenarioError, "forbidden"):
            evaluator.evaluate(fixture)


if __name__ == "__main__":
    unittest.main()
