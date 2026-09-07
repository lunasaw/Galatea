import tempfile
import unittest
from pathlib import Path

from wechat_persona.deletion import plan_deletion, execute_deletion
from wechat_persona.deletion import DeletionError


class DeletionTests(unittest.TestCase):
    def test_plan_and_execute_records_only_ids(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root / "raw").mkdir(); (root / "raw" / "m1.txt").write_text("secret", encoding="utf-8")
            lineage = [{"object_id": "m1", "source_message_ids": ["m1"], "path": str(root / "raw" / "m1.txt")}, {"object_id": "s1", "source_session_ids": ["s1"], "path": str(root / "raw" / "m1.txt")}]
            plan = plan_deletion(lineage, source_message_ids={"m1"})
            self.assertEqual({x["object_id"] for x in plan["objects"]}, {"m1", "s1"})
            receipt = execute_deletion(plan, ledger_path=root / "ledger.json")
            self.assertFalse((root / "raw" / "m1.txt").exists())
            self.assertNotIn("secret", (root / "ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["status"], "completed")

    def test_execute_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); safe = root / "safe"; safe.mkdir(); outside = root / "outside.txt"; outside.write_text("keep", encoding="utf-8")
            link = safe / "escape.txt"; link.symlink_to(outside)
            plan = {"objects": [{"object_id": "bad", "path": str(link)}]}
            receipt = execute_deletion(plan, ledger_path=safe / "ledger.json")
            self.assertEqual(receipt["status"], "partial")
            self.assertTrue(outside.exists())

    def test_external_artifacts_require_invalidation_callback(self):
        plan = {"objects": [{"object_id": "adapter-1", "artifact_uri": "mlflow-artifacts:/adapters/1"}]}
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(DeletionError):
                execute_deletion(plan, ledger_path=Path(td) / "ledger.json")
            receipt = execute_deletion(plan, ledger_path=Path(td) / "ledger.json", invalidate_artifact=lambda uri: uri.endswith("/1"))
            self.assertEqual(receipt["status"], "completed")


if __name__ == "__main__":
    unittest.main()
