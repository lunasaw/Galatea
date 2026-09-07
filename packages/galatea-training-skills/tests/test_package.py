import hashlib
import importlib.util
import json
import tarfile
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


class PackageTests(unittest.TestCase):
    def test_two_builds_are_byte_identical_and_archive_validates_in_isolation(self):
        package = load_script("package")
        validate = load_script("validate")
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.tar.gz"
            second = Path(directory) / "second.tar.gz"
            package.build(ROOT, first)
            package.build(ROOT, second)
            self.assertEqual(hashlib.sha256(first.read_bytes()).digest(), hashlib.sha256(second.read_bytes()).digest())
            extracted = Path(directory) / "extracted"
            with tarfile.open(first, "r:gz") as archive:
                self.assertTrue(all(not member.issym() and not member.islnk() for member in archive.getmembers()))
                archive.extractall(extracted, filter="data")
            install = extracted / "galatea-training-skills"
            report = validate.validate_package(install, verify_manifest=True)
            self.assertEqual(report["skills"], 6)
            manifest = json.loads((install / "MANIFEST.json").read_text())
            self.assertTrue(all(len(item["sha256"]) == 64 for item in manifest["files"]))
            self.assertIn("codex_version", manifest["lock"])


if __name__ == "__main__":
    unittest.main()
