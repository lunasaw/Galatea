import json
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(REPOSITORY / "services" / "galatea-mcp" / "src"))

from galatea_mcp.contracts import CampaignSpec
from galatea_mcp.projects import Registry
from wechat_persona.job_release import build_release, write_registration_materials


class Objects:
    verify = verify_metadata = lambda self, ref: True


class JobReleaseTests(unittest.TestCase):
    def test_build_is_deterministic_and_excludes_runtime_state(self):
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            output = temp / "releases"
            first = build_release(ROOT, output, allow_dirty=True)
            second = build_release(ROOT, output, allow_dirty=True)
            self.assertEqual(first.manifest["release_id"], second.manifest["release_id"])
            self.assertEqual(first.archive_path.read_bytes(), second.archive_path.read_bytes())
            with zipfile.ZipFile(first.archive_path) as archive:
                names = set(archive.namelist())
            self.assertIn("scripts/submit_train.py", names)
            self.assertNotIn("release/execution-public.pem", names)
            self.assertIn("configs/formal-sft-v2-baseline.json", names)
            self.assertNotIn("tests/test_job_release.py", names)
            self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in names))
            self.assertEqual(["python", "scripts/submit_train.py", "--run"], first.manifest["entrypoint"])

    def test_registration_materials_match_real_mcp_schemas_and_zip_contract(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            release = build_release(ROOT, root / "releases", allow_dirty=True)
            snapshot = root / "snapshot.json"
            snapshot.write_text(json.dumps({
                "dataset_id": "wechat-snapshot",
                "manifest_sha256": "a" * 64,
                "split_sha256": "b" * 64,
                "preprocessing_version": "wechat-v1",
                "split_file_sha256": {"test": "c" * 64},
            }), encoding="utf-8")
            files = write_registration_materials(
                release, root / "registration", snapshot_manifest=snapshot
            )
            raw = json.loads(files["projects"].read_text(encoding="utf-8"))
            campaign = CampaignSpec.model_validate(
                json.loads(files["campaign"].read_text(encoding="utf-8"))
            )
            registry = Registry(raw, Objects())
            project = registry.get("wechat-persona")
            release_id = next(iter(project.releases))
            registry.verify(
                "wechat-persona", "formal-sft-v2-baseline", release_id, "baseline"
            )
            self.assertTrue(project.releases[release_id].deadline_enforced)
            self.assertTrue(project.releases[release_id].path.endswith(".zip"))
            self.assertEqual(58560, campaign.budget.cpu_seconds)
            self.assertEqual(14640, campaign.budget.gpu_seconds)
            champion = next(slot for slot in campaign.slots if slot.role == "champion")
            self.assertEqual(
                {"formal-sft-v2-champion", "formal-sft-v2-champion-trial"},
                set(champion.config_ids),
            )
            for path in files.values():
                self.assertEqual(stat.S_IRUSR | stat.S_IWUSR, stat.S_IMODE(path.stat().st_mode))

    def test_clean_build_rejects_dirty_repository(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError, "clean Git commit"):
                root = Path(td)
                build_release(ROOT, root / "release")

    def test_release_rejects_symlink_and_output_inside_project(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            (root / "src").mkdir(parents=True)
            (root / "scripts").mkdir()
            (root / "configs").mkdir()
            for name in ("galatea.project.yaml", "conda.yaml"):
                (root / name).write_text("x\n", encoding="utf-8")
            (root / "scripts/submit_train.py").write_text("x\n", encoding="utf-8")
            target = root / "outside.txt"
            target.write_text("x\n", encoding="utf-8")
            (root / "src/link").symlink_to(target)
            with self.assertRaises(ValueError):
                build_release(root, Path(td) / "out", allow_dirty=True)
            with self.assertRaises(ValueError):
                build_release(root, root / "releases", allow_dirty=True)


if __name__ == "__main__":
    unittest.main()
