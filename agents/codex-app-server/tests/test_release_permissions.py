from pathlib import Path
import os
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from codex_agent import release
from codex_agent.release import require_protected_path
from codex_agent.stage0 import GateError


class ReleasePermissionTests(unittest.TestCase):
    def test_release_file_and_parents_must_be_owned_and_not_writable_by_others(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'manifest.json';path.write_text('{}')
            require_protected_path(path,owner_uid=os.getuid(),boundary=root)
            path.chmod(0o666)
            with self.assertRaises(GateError):
                require_protected_path(path,owner_uid=os.getuid(),boundary=root)
            path.chmod(0o600);root.chmod(0o777)
            with self.assertRaises(GateError):
                require_protected_path(path,owner_uid=os.getuid(),boundary=root)

    def test_wrong_owner_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'manifest.json';path.write_text('{}')
            with self.assertRaises(GateError):
                require_protected_path(path,owner_uid=os.getuid()+1,boundary=root)
            (root/'link').symlink_to(path)
            with self.assertRaises(GateError):
                require_protected_path(root/'link',owner_uid=os.getuid(),boundary=root)

    def test_runtime_tree_rejects_writable_resources_and_special_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); resource=root/'resource';resource.write_text('immutable')
            release.require_protected_tree(root,owner_uid=os.getuid(),boundary=root)
            resource.chmod(0o666)
            with self.assertRaises(GateError):
                release.require_protected_tree(root,owner_uid=os.getuid(),boundary=root)
            resource.chmod(0o600)
            os.mkfifo(root/'pipe')
            with self.assertRaises(GateError):
                release.require_protected_tree(root,owner_uid=os.getuid(),boundary=root)
