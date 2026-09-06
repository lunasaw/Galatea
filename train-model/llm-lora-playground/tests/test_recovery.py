import unittest

from llm_lora_playground.recovery import (
    RecoveryContractError,
    create_resume_attempt,
    validate_resume_identity,
)


class RecoveryTests(unittest.TestCase):
    def test_matching_identity_can_resume_with_new_attempt(self):
        checkpoint = {"status": "complete", "config_digest": "a", "dataset_manifest_digest": "b", "path": "/tmp/x"}
        validate_resume_identity(checkpoint, {"config_digest": "a", "dataset_manifest_digest": "b"})
        attempt = create_resume_attempt("old-run", checkpoint, {"config_digest": "a"})
        self.assertEqual(attempt.resumed_from, "/tmp/x")
        self.assertEqual(attempt.retry_of, "old-run")

    def test_mismatch_is_blocked(self):
        with self.assertRaises(RecoveryContractError):
            validate_resume_identity({"status": "complete", "config_digest": "a"}, {"config_digest": "b"})


if __name__ == "__main__":
    unittest.main()
