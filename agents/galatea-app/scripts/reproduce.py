"""Read-only, API-backed reproduction of an existing governed Cats/Dogs execution.

This imports execution evidence into a new inspection task; it never claims the
original training was executed by this task or starts training itself.
"""

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from galatea_app.service import TaskService, Context
from galatea_app.projections import Workbench
from galatea_app.integrations.project_governance import ProjectGovernance
from galatea_app.common import utc


def stage(id, title, dependencies):
    return {
        "id": id,
        "kind": {
            "data": "data.inspect",
            "code": "code.prepare",
            "evidence": "result.review",
        }[id],
        "title": title,
        "depends_on": dependencies,
        "required": True,
        "input_refs": [],
        "output_slots": [
            {"name": "report", "role": "report", "media_type": "application/json"}
        ],
        "acceptance": [
            {
                "id": id + "_report",
                "type": "artifact_exists",
                "parameters": {"artifact_role": "report"},
            }
        ],
        "execution": {
            "mode": "skill",
            "capability_ref": "data-local/v1",
            "skill_refs": [],
            "retry_policy": {"max_attempts": 1, "requires_reconciliation": True},
        },
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True)
    p.add_argument("--submission-receipt", required=True)
    p.add_argument("--plan-receipt", required=True)
    p.add_argument("--state-dir", required=True)
    p.add_argument("--python", required=True)
    args = p.parse_args()
    project = Path(args.project).resolve()
    root = Path(args.state_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root / "viewer.json").exists():
        raise SystemExit(
            "Use a new state directory; existing acceptance records are immutable"
        )
    submission = Path(args.submission_receipt).resolve()
    plan_path = Path(args.plan_receipt).resolve()
    receipt = json.loads(submission.read_text())
    target = receipt["target"]
    adapter = ProjectGovernance(
        project_id="ray-cats-and-dogs",
        profile=target["clusterProfile"],
        ray_api=target["rayApiServer"],
        tracking_uri=target["mlflowTrackingUri"],
        receipts_root=submission.parent,
    )
    operation = adapter.register_receipt(submission.name, plan_relative=plan_path.name)
    print("Querying governed Ray/MLflow execution via APIs", flush=True)
    fact = adapter.observe(operation)
    if fact["status"] != "succeeded":
        raise SystemExit(
            "Ray/MLflow/round-trip evidence is not successful; refusing a completed demo"
        )
    plan = operation["plan"]
    data = plan["plan"]["dataset"]
    print(
        "Observed",
        fact.get("ray_status"),
        fact.get("mlflow_status"),
        fact.get("run_id"),
        flush=True,
    )
    service = TaskService(root / "tasks.sqlite3", workspaces={"evidence": root})
    user = Context(
        "evidence-reviewer",
        "local-evidence-viewer",
        "evidence-session",
        "evidence-thread",
        "evidence",
        role="app",
        confirmed=True,
        interaction_id="explicit-cli-evidence-review",
    )
    agent = Context(
        "evidence-reviewer",
        "local-evidence-viewer",
        "evidence-session",
        "evidence-thread",
        "evidence",
        turn_id="evidence-import",
    )
    task_id = service.create(
        user,
        command_id="create",
        goal="复查 ray-cats-and-dogs 受治理训练：核验数据、固定入口、Job / Run 与产物，展示可追溯复现证据。",
        constraints={
            "scope": "analysis",
            "allow_training": False,
            "max_auto_continuations": 20,
        },
        start=True,
    )["task_id"]
    counter = 0

    def command(name, payload, **pre):
        nonlocal counter
        counter += 1
        return service.command(
            agent, task_id, name, payload, command_id=f"acceptance-{counter}", **pre
        )

    command(
        "replace_plan",
        {
            "reason": "对指定历史执行做只读证据复查，不重新训练。",
            "stages": [
                stage("data", "核验数据与冻结切分", []),
                stage("code", "检查项目与固定入口", ["data"]),
                stage("evidence", "回读训练与产物证据", ["code"]),
            ],
        },
        expected_plan_revision=0,
    )
    workbench = Workbench(service)

    def finish(id, report):
        a = command("begin_stage", {"stage_id": id}, expected_plan_revision=1)["result"]
        file = root / (id + "-report.json")
        file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        command(
            "report_result",
            {
                "stage_id": id,
                "attempt_id": a["attempt_id"],
                "execution_token": a["execution_token"],
                "result": report,
                "artifact_candidates": [
                    {
                        "path": file.name,
                        "role": "report",
                        "slot": "report",
                        "media_type": "application/json",
                    }
                ],
            },
        )
        service.verify_pending()
        return a

    finish(
        "data",
        {
            "dataset_digest": data["content_sha256"],
            "split_digest": data["split_sha256"],
            "split_counts": data["split_counts"],
            "source": "governed readiness + MLflow API",
            "final_test": "仅读取身份和统计；未读取测试样本",
        },
    )
    a = command("begin_stage", {"stage_id": "code"}, expected_plan_revision=1)["result"]
    check = subprocess.run(
        [
            args.python,
            str(project / "scripts/train.py"),
            "--config",
            str(project / "configs/smoke.yaml"),
            "--check-config",
        ],
        cwd=project,
        text=True,
        capture_output=True,
        timeout=120,
    )
    if check.returncode:
        raise RuntimeError(
            "Read-only project config check failed: " + check.stderr[-1000:]
        )
    files = [
        "configs/smoke.yaml",
        "scripts/train.py",
        "src/ray_cats_dogs/data.py",
        "tests/test_galatea_contract.py",
    ]
    fingerprints = {}
    for relative in files:
        content = (project / relative).read_text()
        fingerprints[relative] = hashlib.sha256(content.encode()).hexdigest()
        # Explicitly show an imported source snapshot against an empty import baseline.
        workbench.capture_change(
            agent,
            task_id,
            stage_id="code",
            attempt_id=a["attempt_id"],
            relative_path=relative,
            before="",
            after=content,
            checks=[
                {
                    "id": "config",
                    "status": "passed",
                    "source": "scripts/train.py --check-config",
                }
            ],
        )
    report = {
        "config_check": "passed",
        "source_snapshot_baseline": "empty evidence-import baseline, not an Agent-authored edit",
        "files": fingerprints,
        "release_id": plan["releaseId"],
    }
    (root / "code-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    command(
        "report_result",
        {
            "stage_id": "code",
            "attempt_id": a["attempt_id"],
            "execution_token": a["execution_token"],
            "result": report,
            "artifact_candidates": [
                {
                    "path": "code-report.json",
                    "role": "report",
                    "slot": "report",
                    "media_type": "application/json",
                }
            ],
        },
    )
    service.verify_pending()
    reports = {}
    for path in [
        "reports/model-selection.json",
        "reports/preprocessing-parity.json",
        "reports/migration-contamination.json",
    ]:
        data_bytes = adapter.read_artifact(
            {
                "route": "project_governance",
                "operation_id": operation["id"],
                "run_id": fact["run_id"],
                "path": path,
            },
            8 * 1024 * 1024,
        )
        reports[path] = {
            "sha256": hashlib.sha256(data_bytes).hexdigest(),
            "size_bytes": len(data_bytes),
            "content": json.loads(data_bytes),
        }
    from mlflow import MlflowClient

    client = MlflowClient(tracking_uri=target["mlflowTrackingUri"])
    entries = client.list_artifacts(fact["run_id"], "checkpoints/best")
    checkpoint = next(
        (e for e in entries if e.path.endswith(".pt") and not e.is_dir), None
    )
    if checkpoint is None:
        raise RuntimeError("No checkpoint in the registered MLflow artifact namespace")
    if checkpoint.file_size > 256 * 1024 * 1024:
        raise RuntimeError("Checkpoint exceeds bounded verification budget")
    checkpoint_file = Path(
        client.download_artifacts(fact["run_id"], checkpoint.path, str(root))
    )
    checked = subprocess.run(
        [
            args.python,
            str(project / "scripts/verify_checkpoint.py"),
            str(checkpoint_file),
        ],
        cwd=project,
        text=True,
        capture_output=True,
        timeout=120,
    )
    if checked.returncode:
        raise RuntimeError(
            "Fresh process checkpoint verification failed: " + checked.stderr[-1000:]
        )
    checkpoint_evidence = {
        "path": checkpoint.path,
        "sha256": hashlib.sha256(checkpoint_file.read_bytes()).hexdigest(),
        "size_bytes": checkpoint_file.stat().st_size,
        "fresh_process_forward": "passed",
        "output": checked.stdout.strip(),
        "source": "MLflow Artifact API",
        "final_test_access": False,
    }
    final = {
        "scope": "历史受治理训练的真实 API 回读，非本次新增训练",
        "observed_at": utc(),
        "job": operation["id"],
        "run_id": fact["run_id"],
        "ray_status": fact["ray_status"],
        "mlflow_status": fact.get("mlflow_status"),
        "governance_receipt_sha256": operation["receipt_sha256"],
        "release_id": fact.get("release_id"),
        "lineage": fact.get("lineage"),
        "reports": reports,
        "metrics": fact["metrics"],
        "checkpoint_roundtrip_recorded": fact.get("artifact_roundtrip"),
        "checkpoint_verification": checkpoint_evidence,
        "full_model_quality_accepted": False,
    }
    a = finish("evidence", final)
    # Persist actual operation observations as evidence-view records, without claiming ownership/execution.
    with service.storage.transaction() as db:
        aggregate = service._load(db, agent, task_id)
        aggregate["snapshot"]["external_operations"].append(
            {
                "id": operation["id"],
                "provider": "project_governance",
                "governance_route": "project_governance",
                "stage_id": "evidence",
                "attempt_id": a["attempt_id"],
                "status": fact["status"],
                "job_id": fact.get("ray_job_id") or operation["id"],
                "run_id": fact["run_id"],
                "last_observed_at": utc(),
            }
        )
        aggregate["observations"] = [{**fact, "attempt_id": a["attempt_id"]}]
        aggregate["project"] = {
            "project_id": "ray-cats-and-dogs",
            "name": "猫狗训练 · 复现证据",
            "slug": "ray-cats-and-dogs",
            "workspace_display_path": str(project),
            "workspace_ref": "evidence",
            "initialization_state": "ready",
        }
        service._save(db, aggregate)
    snap = service.get_task(agent, task_id)
    command(
        "complete_task",
        {
            "summary": "历史训练证据回读完成。数据、代码快照、Ray Job、MLflow Run 和报告已关联；smoke 只证明训练链路，完整模型效果未在本次验收。",
            "artifact_refs": [v["id"] for v in snap["artifacts"]],
        },
        expected_plan_revision=1,
    )
    (root / "viewer.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "context": asdict(user),
                "workspaces": {"evidence": str(root)},
                "fixture": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    (root / "acceptance.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2)
    )
    print(
        json.dumps(
            {
                "task_id": task_id,
                "status": service.get_task(agent, task_id)["task"]["status"],
                "state_dir": str(root),
                "run_id": fact["run_id"],
                "job_id": operation["id"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    service.close()


if __name__ == "__main__":
    main()
