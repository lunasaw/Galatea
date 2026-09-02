"""仓库级平台边界契约测试。"""

from __future__ import annotations

import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class RepositoryContractTest(unittest.TestCase):
    """验证跨项目的 Runtime 和目录边界没有回退。"""

    def test_deepseek_harness_is_the_only_runtime_entrypoint(self) -> None:
        self.assertFalse((REPOSITORY_ROOT / "agent").exists())
        plugin = REPOSITORY_ROOT / "plugins" / "dsh-galatea"
        self.assertTrue((plugin / "package.json").is_file())
        self.assertTrue((plugin / "cordis.patch.yml").is_file())

    def test_training_projects_own_their_tests(self) -> None:
        projects = (
            REPOSITORY_ROOT / "train-model" / "ray-cats-and-dogs",
            REPOSITORY_ROOT / "train-model" / "ray-handwritten-digits",
        )
        for project in projects:
            self.assertTrue((project / "tests").is_dir(), project)
            self.assertTrue(any((project / "tests").glob("test_*.py")), project)
            self.assertTrue((project / "configs").is_dir(), project)
            self.assertTrue(any((project / "configs").glob("*.yaml")), project)
            self.assertTrue((project / "galatea.project.yaml").is_file(), project)

    def test_runtime_artifacts_stay_ignored(self) -> None:
        ignore = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("/platform-data/", ignore)
        self.assertIn("/mlflow.db", ignore)

    def test_platform_sources_do_not_use_unsafe_shell_or_server_filesystem(self) -> None:
        source_roots = (
            REPOSITORY_ROOT / "plugins" / "dsh-galatea" / "src",
            REPOSITORY_ROOT / "train-model" / "ray-cats-and-dogs" / "src",
            REPOSITORY_ROOT / "train-model" / "ray-handwritten-digits" / "src",
        )
        forbidden = ("shell: true", "execSync", "claude_agent_sdk")
        for root in source_roots:
            for path in root.rglob("*.ts" if root.name == "src" and "plugins" in root.parts else "*.py"):
                text = path.read_text(encoding="utf-8")
                for marker in forbidden:
                    self.assertNotIn(marker, text, path)


if __name__ == "__main__":
    unittest.main()
