import sys
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona.training import TrainingBoundaryError, build_training_plan, run_training


class TrainingBoundaryTests(unittest.TestCase):
    def test_plan_is_read_only_and_training_requires_complete_galatea_binding(self):
        config = {"project": "wechat-persona", "task": "causal-language-model-sft-lora", "run": {"role": "smoke", "promotable": False}, "execution": {"backend": "ray"}}
        plan = build_training_plan(config)
        self.assertFalse(plan["will_create_mlflow_run"])
        with self.assertRaises(TrainingBoundaryError):
            run_training(config, runtime={})

    def test_binding_is_strict_and_retry_does_not_overwrite(self):
        config = {"project": "wechat-persona", "task": "causal-language-model-sft-lora", "run": {"role": "smoke", "promotable": False}, "execution": {"backend": "ray"}}
        runtime = {"execution_mode": "governed-ray-job", "release_id": "r", "readiness_digest": "sha256:" + "a" * 64, "execution_identity": "sha256:" + "b" * 64, "attempt_id": "a1", "ray_submission_id": "sub", "ray_job_id": "job", "galatea_project": "wechat-persona", "role": "smoke", "promotable": False}
        result = run_training(config, runtime=runtime)
        self.assertEqual("blocked", result["status"])
        self.assertFalse(result["will_create_mlflow_run"])

    def test_direct_scripts_fail_closed(self):
        local = subprocess.run([sys.executable, str(ROOT / "scripts/train.py")], capture_output=True, text=True, check=False)
        self.assertEqual(2, local.returncode)
        self.assertIn("blocked", local.stdout)
        evaluate = subprocess.run([sys.executable, str(ROOT / "scripts/evaluate.py"), "--config", str(ROOT / "configs/persona-lora-smoke.yaml"), "--run"], capture_output=True, text=True, check=False)
        self.assertEqual(2, evaluate.returncode)
        self.assertIn("Galatea-authorized", evaluate.stdout)


if __name__ == "__main__":
    unittest.main()
