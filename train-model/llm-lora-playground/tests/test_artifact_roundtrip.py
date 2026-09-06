from pathlib import Path
import tempfile
import unittest

from llm_lora_playground.tracking import ArtifactIntegrityError, download_and_verify_artifact


class FakeClient:
    def __init__(self, source):
        self.source = source

    def download_artifacts(self, run_id, artifact_path, dst_path):
        target = Path(dst_path) / Path(artifact_path).name
        target.write_bytes(self.source.read_bytes())
        return str(target)


class ArtifactRoundtripTests(unittest.TestCase):
    def test_download_and_hash_verification_uses_client_api(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "artifact.json"
            source.write_text("{}")
            import hashlib

            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            output = download_and_verify_artifact(FakeClient(source), "run", "artifact.json", digest, Path(directory) / "out")
            self.assertEqual(output.read_bytes(), b"{}")

    def test_digest_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "artifact.json"
            source.write_text("{}")
            with self.assertRaises(ArtifactIntegrityError):
                download_and_verify_artifact(FakeClient(source), "run", "artifact.json", "0" * 64, Path(directory) / "out")


if __name__ == "__main__":
    unittest.main()
