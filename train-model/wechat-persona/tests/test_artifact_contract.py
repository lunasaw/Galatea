import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona.artifacts import ArtifactContractError, verify_artifact_roundtrip


class FakeClient:
    def __init__(self, payload: bytes): self.payload = payload
    def download_artifacts(self, run_id, path, dst_path):
        target = Path(dst_path) / Path(path).name; target.write_bytes(self.payload); return str(target)


class ArtifactContractTests(unittest.TestCase):
    def test_roundtrip_uses_client_api_and_hashes_download(self):
        payload = b"adapter"
        with tempfile.TemporaryDirectory() as td:
            receipt = verify_artifact_roundtrip(FakeClient(payload), "run", "model/adapter.safetensors", hashlib.sha256(payload).hexdigest(), Path(td))
        self.assertTrue(receipt["roundtrip_verified"])

    def test_digest_mismatch_blocks(self):
        with tempfile.TemporaryDirectory() as td, self.assertRaises(ArtifactContractError):
            verify_artifact_roundtrip(FakeClient(b"wrong"), "run", "model/adapter.safetensors", "a" * 64, Path(td))


if __name__ == "__main__": unittest.main()
