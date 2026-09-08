import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona.canary import scan_snapshot, write_evidence


class CanaryScanTests(unittest.TestCase):
    def snapshot(self, root: Path, text: str = "safe") -> Path:
        root.mkdir()
        for split in ("train", "validation", "test"):
            (root / f"{split}.jsonl").write_text(
                json.dumps({"sample_id": split, "messages": [{"role": "assistant", "content": text}]}) + "\n",
                encoding="utf-8",
            )
        return root

    def test_zero_match_scan_emits_digestible_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            report = write_evidence(self.snapshot(root / "snapshot"), root / "evidence.json")
            self.assertEqual("pass", report["status"])
            self.assertEqual(3, report["scanned_sample_count"])
            self.assertEqual(64, len(report["report_sha256"]))

    def test_canary_match_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            report = scan_snapshot(self.snapshot(Path(td) / "snapshot", "canary-personal-marker"))
            self.assertEqual("blocked", report["status"])
            self.assertEqual(3, report["match_count"])


if __name__ == "__main__":
    unittest.main()
