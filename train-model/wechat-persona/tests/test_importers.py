import csv
import json
import tempfile
import unittest
from pathlib import Path

from wechat_persona.importers import detect_importer, import_messages, ImportErrorSafe
from wechat_persona.importers.csv import CsvImporter
from wechat_persona.importers.html import HtmlImporter
from wechat_persona.importers.json import JsonImporter
from wechat_persona.importers.text import TextImporter


class ImporterTests(unittest.TestCase):
    def test_format_importers_are_first_class_modules(self):
        self.assertEqual((".txt",), TextImporter.extensions)
        self.assertIn(".csv", CsvImporter.extensions)
        self.assertIn(".json", JsonImporter.extensions)
        self.assertIn(".html", HtmlImporter.extensions)

    def test_all_formats_and_source_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "chat.txt").write_text("2026-09-01 10:00\tMe: hello\n2026-09-01 10:01\tTarget: hi\n", encoding="utf-8")
            with (root / "chat.csv").open("w", newline="", encoding="utf-8") as h:
                writer = csv.DictWriter(h, fieldnames=["timestamp", "speaker", "text"]); writer.writeheader(); writer.writerow({"timestamp": "2026-09-01T10:00:00+08:00", "speaker": "Me", "text": "hello"})
            (root / "chat.json").write_text(json.dumps({"messages": [{"timestamp": "2026-09-01T10:00:00+08:00", "speaker": "Target", "text": "hi"}]}), encoding="utf-8")
            (root / "chat.html").write_text("<div data-timestamp='2026-09-01T10:00:00+08:00' data-speaker='Me'>hello</div>", encoding="utf-8")
            for path in root.iterdir():
                records, manifest = import_messages(path, timezone_name="Asia/Shanghai", allowed_root=root)
                self.assertEqual(len(records), 1 if path.suffix in {".csv", ".json", ".html"} else 2)
                self.assertEqual(len(manifest["source_sha256"]), 64)

    def test_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root, outside = Path(td) / "root", Path(td) / "outside.txt"; root.mkdir(); outside.write_text("secret", encoding="utf-8")
            link = root / "chat.txt"; link.symlink_to(outside)
            with self.assertRaises(ImportErrorSafe):
                detect_importer(link, allowed_root=root)


if __name__ == "__main__":
    unittest.main()
