from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from codex_agent.release import source_digest
from codex_agent.stage0 import GateError


class SoftwareMeasurementTests(unittest.TestCase):
    def test_source_digest_covers_code_and_web_assets_but_not_interpreter_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'app.py').write_text('x=1\n')
            before=source_digest(root)
            (root/'__pycache__').mkdir();(root/'__pycache__/app.pyc').write_bytes(b'cache')
            self.assertEqual(source_digest(root),before)
            (root/'app.js').write_text('changed')
            self.assertNotEqual(source_digest(root),before)
            (root/'escape').symlink_to('/etc/passwd')
            with self.assertRaises(GateError):
                source_digest(root)

    def test_modified_runtime_is_rejected_before_any_binary_execution(self):
        import json
        from types import SimpleNamespace
        from unittest.mock import patch
        from codex_agent.release import measure
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'bin').mkdir()
            binary=root/'bin/codex';binary.write_text('#!/bin/sh\nexit 0\n');binary.chmod(0o755)
            (root/'codex-package.json').write_text(json.dumps({'variant':'codex','entrypoint':'bin/codex','version':'0.153.4'}))
            config=SimpleNamespace(contracts=ROOT/'config/contracts/tools.json',
                catalog_metadata=ROOT/'config/contracts/catalog-metadata.json',actions=frozenset({'*'}),
                runtime_dir=root,binary=binary,runtime_version='codex-cli 0.153.4')
            with patch('codex_agent.release.subprocess.check_output') as execute:
                with self.assertRaises(GateError):
                    measure(config)
                execute.assert_not_called()
