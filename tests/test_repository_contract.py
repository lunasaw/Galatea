"""仓库级平台边界契约测试。"""

from __future__ import annotations

import unittest
import os
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class RepositoryContractTest(unittest.TestCase):
    """验证跨项目的 Runtime 和目录边界没有回退。"""

    def test_codex_and_platform_entrypoints_load_without_plugins(self) -> None:
        sources = (
            REPOSITORY_ROOT / "services" / "galatea-mcp" / "src",
            REPOSITORY_ROOT / "agents" / "codex-app-server" / "src",
        )
        for module, command in (("galatea_mcp.cli", "register"), ("codex_agent.cli", "serve")):
            with self.subTest(module=module):
                result = subprocess.run([sys.executable, "-m", module, "--help"],
                    env={**os.environ, "PYTHONPATH": os.pathsep.join(map(str, sources))},
                    capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(command, result.stdout)
        self.assertFalse((REPOSITORY_ROOT / "plugins").exists())

    def test_runtime_sources_have_no_retired_agent_dependencies(self) -> None:
        import json

        package = REPOSITORY_ROOT / "packages" / "galatea-governance"
        manifest = json.loads((package / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["dependencies"]), {"yaml"})
        roots = (
            package / "src",
            REPOSITORY_ROOT / "services" / "galatea-mcp" / "src",
            REPOSITORY_ROOT / "agents" / "codex-app-server" / "src",
            REPOSITORY_ROOT / "train-model",
        )
        for root in roots:
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix not in {".py", ".ts", ".mjs"}:
                    continue
                content = path.read_text(encoding="utf-8")
                for marker in ("@deepseek-ai/", "deepseek-harness", "plugins/dsh-galatea"):
                    self.assertNotIn(marker, content, path)

    def test_training_projects_own_their_tests(self) -> None:
        projects = (
            REPOSITORY_ROOT / "train-model" / "ray-cats-and-dogs",
            REPOSITORY_ROOT / "train-model" / "ray-handwritten-digits",
            REPOSITORY_ROOT / "train-model" / "ray-kaggle-house-prices",
        )
        for project in projects:
            self.assertTrue((project / "tests").is_dir(), project)
            self.assertTrue(any((project / "tests").glob("test_*.py")), project)
            self.assertTrue((project / "configs").is_dir(), project)
            self.assertTrue(any((project / "configs").glob("*.yaml")), project)
            self.assertTrue((project / "galatea.project.yaml").is_file(), project)

    def test_runtime_artifacts_stay_ignored(self) -> None:
        for path in ("platform-data", "mlflow.db"):
            with self.subTest(path=path):
                result = subprocess.run(
                    ["git", "check-ignore", "--no-index", "--quiet", "--", path],
                    cwd=REPOSITORY_ROOT,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertEqual(result.returncode, 0, result.stderr or path)

    def test_platform_sources_do_not_use_unsafe_shell_or_server_filesystem(self) -> None:
        source_roots = (
            REPOSITORY_ROOT / "packages" / "galatea-governance" / "src",
            REPOSITORY_ROOT / "services" / "galatea-mcp" / "src",
            REPOSITORY_ROOT / "agents" / "codex-app-server" / "src",
            REPOSITORY_ROOT / "train-model" / "ray-cats-and-dogs" / "src",
            REPOSITORY_ROOT / "train-model" / "ray-handwritten-digits" / "src",
            REPOSITORY_ROOT / "train-model" / "ray-kaggle-house-prices" / "src",
        )
        forbidden = ("shell: true", "execSync", "claude_agent_sdk")
        for root in source_roots:
            for path in root.rglob("*.ts" if "galatea-governance" in root.parts else "*.py"):
                text = path.read_text(encoding="utf-8")
                for marker in forbidden:
                    self.assertNotIn(marker, text, path)


if __name__ == "__main__":
    unittest.main()
