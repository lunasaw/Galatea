"""Real binary acceptance, opt-in because the package is deliberately not vendored."""
from pathlib import Path
import os
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from runtime_probe import probe_runtime


@unittest.skipUnless(os.environ.get("CODEX_TEST_LEGACY_RUNTIME_PACKAGE"),
                     "Set CODEX_TEST_LEGACY_RUNTIME_PACKAGE to the frozen 0.154.0 package")
class RuntimeProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.report = probe_runtime(Path(os.environ["CODEX_TEST_LEGACY_RUNTIME_PACKAGE"]),
                                   Path(cls.directory.name), ROOT / "tests/fixtures/dynamic-tools",
                                   probe_tool="galatea_get_capabilities", probe_arguments={"protocol_version": "galatea.tools/v1"})

    def test_real_binary_success_and_error_round_trip(self):
        self.assertEqual(set(self.report["cases"]), {"full", "subset"})
        for name, case in self.report["cases"].items():
            with self.subTest(principal=name):
                self.assertGreaterEqual(case["model_requests"], 3)
                self.assertTrue(case["tool_call_received"], case.get("error"))
                self.assertTrue(case["receipt_seen_by_model"], case.get("error"))

    def test_cross_process_resume_retains_receipt_history(self):
        for case in self.report["cases"].values():
            self.assertTrue(case["resumed_in_new_process"], case.get("error"))

    def test_experimental_opt_in_is_required(self):
        self.assertTrue(self.report["checks"]["experimental_negative"])

    def test_tool_names_exactly_match_full_and_restricted_principals(self):
        for case in self.report["cases"].values():
            self.assertEqual(case["inventory_errors"], [])

    def test_model_schemas_match_approved_compatibility_contract(self):
        self.assertEqual(self.report["protocol_status"], "passed", self.report["checks"])
        for name, case in self.report["cases"].items():
            with self.subTest(principal=name):
                first = case["surface_errors"][:1]
                detail = first[0] if first else case.get("error")
                self.assertFalse(bool(case["surface_errors"]), detail)


if __name__ == "__main__":
    unittest.main()
