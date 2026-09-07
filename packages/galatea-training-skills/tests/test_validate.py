import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ValidateTests(unittest.TestCase):
    def setUp(self):
        self.validate = load_script("validate")

    def copy_package(self):
        temporary = tempfile.TemporaryDirectory()
        target = Path(temporary.name) / "package"
        shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.addCleanup(temporary.cleanup)
        return target

    def test_repository_package_is_valid_and_has_six_unique_skills(self):
        report = self.validate.validate_package(ROOT)
        self.assertEqual(report["skills"], 6)
        self.assertEqual(report["scenarios"], 10)
        self.assertEqual(report["tools"], 17)

    def test_rejects_broken_relative_link(self):
        package = self.copy_package()
        skill = package / "skills" / "training-campaign" / "SKILL.md"
        skill.write_text(skill.read_text() + "\n[missing](references/nope.md)\n")
        with self.assertRaisesRegex(self.validate.ValidationError, "broken relative link"):
            self.validate.validate_package(package)

    def test_rejects_symlinks_even_when_they_resolve_inside_package(self):
        package = self.copy_package()
        (package / "skills" / "training-campaign" / "references" / "alias.md").symlink_to("workflow.md")
        with self.assertRaisesRegex(self.validate.ValidationError, "symlink"):
            self.validate.validate_package(package)

    def test_rejects_path_traversal_links(self):
        package = self.copy_package()
        skill = package / "skills" / "training-campaign" / "SKILL.md"
        skill.write_text(skill.read_text() + "\n[escape](../../../../README.md)\n")
        with self.assertRaisesRegex(self.validate.ValidationError, "escapes package"):
            self.validate.validate_package(package)

    def test_rejects_tool_contract_drift(self):
        package = self.copy_package()
        tools = json.loads((package / "contracts" / "tools.json").read_text())
        tools["tools"].pop()
        (package / "contracts" / "tools.json").write_text(json.dumps(tools))
        with self.assertRaisesRegex(self.validate.ValidationError, "tool contract digest"):
            self.validate.validate_package(package)

    def test_rejects_duplicate_frontmatter_name(self):
        package = self.copy_package()
        skill = package / "skills" / "dataset-readiness" / "SKILL.md"
        skill.write_text(skill.read_text().replace("name: dataset-readiness", "name: training-campaign", 1))
        with self.assertRaisesRegex(self.validate.ValidationError, "duplicate skill name"):
            self.validate.validate_package(package)

    def test_rejects_secrets_and_developer_absolute_paths(self):
        for bad_text, message in [
            ("token = ghp_abcdefghijklmnopqrstuvwxyz123456", "possible secret"),
            ("/Users/example/project/private", "developer absolute path"),
        ]:
            with self.subTest(bad_text=bad_text):
                package = self.copy_package()
                readme = package / "README.md"
                readme.write_text(readme.read_text() + "\n" + bad_text)
                with self.assertRaisesRegex(self.validate.ValidationError, message):
                    self.validate.validate_package(package)


if __name__ == "__main__":
    unittest.main()
