"""Cluster selection errors must fail before release loading or service access."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
TARGET_ENV = {
    "GALATEA_RAY_PROFILE": "dev",
    "RAY_ADDRESS": "10.26.0.140:18104",
    "RAY_API_SERVER_ADDRESS": "http://10.26.0.140:8265",
    "MLFLOW_TRACKING_URI": "http://10.26.0.140:18103",
}


@unittest.skipUnless(NODE, "Node is required by the governed submission wrapper")
class GovernedTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        version = subprocess.check_output([NODE, "--version"], text=True).strip().lstrip("v")
        parts = tuple(int(part) for part in version.split("."))
        if parts < (22, 19, 0):
            raise unittest.SkipTest("Node 22.19+ is required for native TypeScript imports")

    def _assert_rejected(self, overrides: dict[str, str], expected: str) -> None:
        environment = {
            key: value for key, value in os.environ.items()
            if key not in {*TARGET_ENV, "RAY_DASHBOARD_URL"}
        }
        result = subprocess.run(
            [NODE, "job/governed.mjs", "plan", "configs/smoke.yaml",
             "/nonexistent/galatea-release/release.json", "-", "invalid-target-test"],
            cwd=PROJECT_ROOT,
            env={**environment, **overrides},
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(expected, output)
        self.assertNotIn("ENOENT", output)
        self.assertNotIn("ECONNREFUSED", output)
        self.assertNotIn("fixture-secret", output)

    def test_missing_profile_does_not_fall_back_to_development(self) -> None:
        self._assert_rejected({}, "Set GALATEA_RAY_PROFILE")

    def test_auto_discovery_is_rejected(self) -> None:
        self._assert_rejected({**TARGET_ENV, "RAY_ADDRESS": "auto"}, "Set RAY_ADDRESS")

    def test_gcs_address_cannot_be_used_as_jobs_api_url(self) -> None:
        self._assert_rejected(
            {**TARGET_ENV, "RAY_API_SERVER_ADDRESS": "10.26.0.140:18104"},
            "Set RAY_API_SERVER_ADDRESS",
        )

    def test_url_credentials_are_rejected_without_printing_them(self) -> None:
        self._assert_rejected(
            {**TARGET_ENV, "MLFLOW_TRACKING_URI": "http://user:fixture-secret@localhost:18103"},
            "Set MLFLOW_TRACKING_URI",
        )

    def test_valid_target_loads_governance_without_the_retired_plugin(self) -> None:
        environment = {
            key: value for key, value in os.environ.items()
            if key not in {*TARGET_ENV, "RAY_DASHBOARD_URL"}
        }
        result = subprocess.run(
            [NODE, "job/governed.mjs", "plan", "configs/smoke.yaml",
             "/nonexistent/galatea-release/release.json", "-", "migration-test"],
            cwd=PROJECT_ROOT,
            env={**environment, **TARGET_ENV},
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("ERR_MODULE_NOT_FOUND", output)
        self.assertNotIn("ECONNREFUSED", output)
        self.assertIn('"category":"precondition-failed"', output)
        self.assertIn("path resolves outside project root", output)
