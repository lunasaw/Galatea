import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wechat_persona.binding import BindingError, verify_execution_binding
from wechat_persona.driver import execute
from wechat_persona.runtime import load_project_config
from wechat_persona.training import (
    _base_model_loader,
    build_training_plan,
    run_training,
    validate_binding_identity,
)


class Context:
    def get_job_id(self):
        return "ray-job-1"


def packet(role="baseline"):
    views = (
        {"test": {"bucket": "b", "key": "test", "version_id": "v", "sha256": "1" * 64, "size_bytes": 1}}
        if role == "evaluate"
        else {
            "train": {"bucket": "b", "key": "train", "version_id": "v", "sha256": "2" * 64, "size_bytes": 1},
            "validation": {"bucket": "b", "key": "validation", "version_id": "v", "sha256": "3" * 64, "size_bytes": 1},
        }
    )
    return {
        "schema_version": "galatea.execution/v1",
        "project_id": "wechat-persona",
        "campaign_id": "campaign",
        "operation_id": "op",
        "submission_id": "submission",
        "step_id": role,
        "attempt": 1,
        "role": role,
        "config_id": "config",
        "release_id": "release",
        "readiness_digest": "a" * 64,
        "deadline_at": 4102444800,
        "candidate_id": "candidate" if role == "evaluate" else None,
        "champion_run_id": "champion-run" if role == "evaluate" else None,
        "champion_model_sha256": "4" * 64 if role == "evaluate" else None,
        "metadata": {"galatea.operation": "op"},
        "config_path": "configs/config.json",
        "config_digest": "5" * 64,
        "release_digest": "6" * 64,
        "code_revision": "deadbeef",
        "environment_digest": "7" * 64,
        "dataset_digest": "8" * 64,
        "split_digest": "9" * 64,
        "preprocessing": "v1",
        "metric_definition": "wechat-persona-quality-v1",
        "evaluation_protocol": "wechat-persona-style-v1",
        "objective": {"metric": "val_loss", "direction": "min"},
        "seed": 42,
        "resources": {"cpus": 4, "gpus": 1, "memory_bytes": 1024, "workers": 1, "seconds": 60, "cleanup_seconds": 5},
        "experiment_id": "1",
        "tracking_uri": "https://mlflow.invalid",
        "views": views,
        "clean_start": role != "evaluate",
        "ray_address": "https://ray.invalid",
        "model_artifact_path": "model/adapter_model.safetensors",
    }


class TrainingBoundaryTests(unittest.TestCase):
    def raw(self, value):
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return raw

    def test_formal_role_plans_are_ready_and_read_only(self):
        for name in ("baseline", "trial", "champion", "champion-trial", "evaluate"):
            config = load_project_config(ROOT / "configs" / f"formal-sft-v2-{name}.yaml")
            plan = build_training_plan(config)
            self.assertEqual("planned", plan["status"], plan["errors"])
            self.assertEqual(
                "qwen3_5_conditional_generation",
                plan["model_architecture"],
            )
            self.assertFalse(plan["will_create_mlflow_run"])

    def test_qwen35_uses_conditional_generation_loader(self):
        config = load_project_config(ROOT / "configs/formal-sft-v2-baseline.yaml")
        causal_loader = object()
        conditional_loader = object()
        self.assertIs(
            conditional_loader,
            _base_model_loader(
                config["model"],
                causal_loader=causal_loader,
                conditional_loader=conditional_loader,
            ),
        )

    def test_gpt_prelabel_baseline_is_non_promotable_and_ready(self):
        config = load_project_config(ROOT / "configs/gpt-prelabel-v1-baseline.yaml")
        plan = build_training_plan(config)
        self.assertEqual("planned", plan["status"], plan["errors"])
        self.assertFalse(config["dataset"]["formal_dataset_ready"])
        self.assertFalse(config["governance"]["human_review_completed"])
        self.assertFalse(config["governance"]["formal_training_eligible"])
        self.assertFalse(config["run"]["promotable"])
        self.assertEqual("untouched", plan["test_access"])

    def test_gpt_prelabel_cannot_claim_promotion_or_human_review(self):
        config = load_project_config(ROOT / "configs/gpt-prelabel-v1-baseline.yaml")
        for replacement, expected in (
            (
                {"run": {**config["run"], "promotable": True}},
                "experimental prelabel training must be non-promotable",
            ),
            (
                {
                    "governance": {
                        **config["governance"],
                        "human_review_completed": True,
                    }
                },
                "experimental prelabel human_review_completed must be false",
            ),
            (
                {
                    "evaluation": {
                        **config["evaluation"],
                        "test_access": "evaluator-only",
                    }
                },
                "experimental prelabel training must leave test untouched",
            ),
        ):
            changed = {**config, **replacement}
            self.assertIn(expected, build_training_plan(changed)["errors"])

    def test_unsigned_binding_enforces_role_views_and_deadline(self):
        raw = self.raw(packet())
        verified = verify_execution_binding(raw, now=1)
        self.assertEqual({"train", "validation"}, set(verified["views"]))
        invalid = packet() | {"views": {"test": packet("evaluate")["views"]["test"]}}
        raw = self.raw(invalid)
        with self.assertRaises(BindingError):
            verify_execution_binding(raw, now=1)

    def test_driver_admission_matches_mcp_packet_and_ray_job(self):
        value = packet()
        raw = self.raw(value)

        class Client:
            def get_job_info(self, submission):
                return SimpleNamespace(job_id="ray-job-1", metadata=value["metadata"])

        result = execute(
            Context(),
            fit=lambda binding, admission: {"status": "succeeded", "role": binding["role"]},
            raw_binding=raw,
            client_factory=lambda _: Client(),
        )
        self.assertEqual("succeeded", result["status"])

    def test_actual_mcp_signer_packet_is_accepted_by_driver(self):
        service_src = ROOT.parents[1] / "services" / "galatea-mcp" / "src"
        sys.path.insert(0, str(service_src))
        try:
            from galatea_mcp.backends.binding import BindingSigner

            value = packet()
            operation = {
                key: value[key]
                for key in (
                    "campaign_id", "operation_id", "submission_id", "step_id", "attempt",
                    "role", "config_id", "release_id", "readiness_digest", "deadline_at",
                    "candidate_id", "champion_run_id", "champion_model_sha256", "metadata",
                )
            }
            project = SimpleNamespace(
                project_id="wechat-persona",
                dataset=SimpleNamespace(
                    manifest_digest=value["dataset_digest"],
                    split_digest=value["split_digest"],
                    preprocessing=value["preprocessing"],
                    views={name: SimpleNamespace(model_dump=lambda ref=ref: ref) for name, ref in value["views"].items()},
                ),
                metric_definition=value["metric_definition"],
                evaluation_protocol=value["evaluation_protocol"],
                objective=SimpleNamespace(model_dump=lambda: value["objective"]),
                experiment_id=value["experiment_id"],
                model_artifact_path=value["model_artifact_path"],
            )
            config = SimpleNamespace(
                path=value["config_path"], sha256=value["config_digest"], seed=42,
                resources=SimpleNamespace(model_dump=lambda: value["resources"]),
            )
            release = SimpleNamespace(
                sha256=value["release_digest"], code_revision=value["code_revision"],
                environment_digest=value["environment_digest"],
            )
            environment = BindingSigner(
                None,
                tracking_uri=value["tracking_uri"],
                role_env={},
                ray_addresses={"trainer": value["ray_address"], "evaluator": "https://eval.invalid"},
            )(operation, project, config, release)

            class Client:
                def get_job_info(self, submission):
                    return SimpleNamespace(job_id="ray-job-1", metadata=value["metadata"])

            result = execute(
                Context(),
                fit=lambda binding, admission: {"status": "succeeded", "role": binding["role"]},
                raw_binding=environment["GALATEA_EXECUTION_BINDING"],
                client_factory=lambda _: Client(),
            )
            self.assertEqual("succeeded", result["status"])
        finally:
            sys.path.remove(str(service_src))

    def test_training_function_rejects_forged_admission_before_mlflow(self):
        config = load_project_config(ROOT / "configs/formal-sft-v2-baseline.yaml")
        with self.assertRaises(PermissionError):
            run_training(
                config,
                binding=packet(),
                admission=None,
                s3_client=object(),
                mlflow_client=object(),
            )

    def test_binding_identity_cannot_swap_dataset_or_role(self):
        config = load_project_config(ROOT / "configs/formal-sft-v2-baseline.yaml")
        value = packet()
        value.update(
            project_id="wechat-persona",
            dataset_digest=config["dataset"]["manifest_sha256"],
            split_digest=config["dataset"]["split_sha256"],
            preprocessing=config["dataset"]["preprocessing_version"],
            role="baseline",
            seed=config["training"]["seed"],
            objective={"metric": "val_loss", "direction": "min"},
        )
        self.assertEqual([], validate_binding_identity(value, config))
        self.assertIn(
            "binding dataset_digest differs from embedded config",
            validate_binding_identity({**value, "dataset_digest": "0" * 64}, config),
        )

    def test_direct_scripts_fail_closed(self):
        local = subprocess.run(
            [sys.executable, str(ROOT / "scripts/train.py")], capture_output=True, text=True, check=False
        )
        self.assertEqual(2, local.returncode)
        run = subprocess.run(
            [sys.executable, str(ROOT / "scripts/submit_train.py"), "--run"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(0, run.returncode)
        self.assertIn("execution binding required", run.stderr)


if __name__ == "__main__":
    unittest.main()
