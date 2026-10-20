"""Clearly labeled, read-only six-scene fixtures. Never submit external work."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import argparse
from dataclasses import asdict
from galatea_app.service import TaskService, Context
from galatea_app.projections import Workbench
from galatea_app.common import utc, canonical


def spec(id, title, depends, kind="data.inspect"):
    return {
        "id": id,
        "title": title,
        "kind": kind,
        "depends_on": depends,
        "required": True,
        "input_refs": [],
        "output_slots": [
            {"name": "report", "role": "report", "media_type": "application/json"}
        ],
        "acceptance": [
            {
                "id": title,
                "type": "artifact_exists",
                "parameters": {"artifact_role": "report"},
            }
        ],
        "execution": {
            "mode": "skill",
            "capability_ref": (
                "governed-training/v1" if kind == "training.run" else "data-local/v1"
            ),
            "skill_refs": [],
            "retry_policy": {"max_attempts": 1, "requires_reconciliation": True},
        },
    }


class FixtureAuthority:
    def authorized(self, p):
        return True

    def owns(self, i, o):
        return o["intent_key"] == i["idempotency_key"]


def generate(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    service = TaskService(
        root / "tasks.sqlite3",
        workspaces={"work": root},
        governance={"project_governance": FixtureAuthority()},
    )
    workbench = Workbench(service)
    task_ids = []
    for scene in [
        "data",
        "code",
        "proposal",
        "training",
        "result",
        "two-stage",
        "replan",
    ]:
        user = Context(
            "fixture-owner",
            "fixture-host",
            scene,
            scene,
            "work",
            role="app",
            confirmed=True,
            interaction_id="fixture-only",
        )
        agent = Context(
            "fixture-owner",
            "fixture-host",
            scene,
            scene,
            "work",
            turn_id="fixture-turn",
        )
        training = scene not in {"two-stage", "replan"}
        task = service.create(
            user,
            command_id=scene + "-create",
            goal="模拟数据 · 猫狗训练复现" if training else "模拟数据 · CSV 检查与清洗",
            constraints={
                "scope": "governed_training" if training else "data_processing",
                "allow_training": training,
                "max_auto_continuations": 0,
            },
            start=True,
        )["task_id"]
        task_ids.append(task)
        n = 0

        def cmd(name, p, ctx=agent, **pre):
            nonlocal n
            n += 1
            return service.command(
                ctx, task, name, p, command_id=scene + "-" + str(n), **pre
            )

        stages = (
            [
                spec("data", "Skill 数据处理", []),
                spec("code", "编写代码", ["data"], "code.prepare"),
                spec("train", "开始训练", ["code"], "training.run"),
            ]
            if training
            else [
                spec("data", "检查数据", []),
                spec("clean", "清洗并交付", ["data"], "data.transform"),
            ]
        )
        cmd(
            "replace_plan",
            {"reason": "模拟场景 fixture，不能用于真实执行", "stages": stages},
            expected_plan_revision=0,
        )

        def begin(id):
            return cmd(
                "begin_stage",
                {"stage_id": id},
                expected_plan_revision=service.get_task(agent, task)["task"][
                    "plan_revision"
                ],
            )["result"]

        def finish(id, result, a=None):
            a = a or begin(id)
            file = scene + "-" + id + ".json"
            (root / file).write_text(json.dumps(result, ensure_ascii=False))
            cmd(
                "report_result",
                {
                    "stage_id": id,
                    "attempt_id": a["attempt_id"],
                    "execution_token": a["execution_token"],
                    "result": result,
                    "artifact_candidates": [
                        {
                            "path": file,
                            "role": "report",
                            "slot": "report",
                            "media_type": "application/json",
                        }
                    ],
                },
            )
            service.verify_pending()
            return a

        if scene == "data":
            begin("data")
        else:
            finish(
                "data",
                {
                    "有效图像": 24997,
                    "异常隔离": 3,
                    "训练 / 验证 / 测试": "22497 / 1250 / 1250",
                    "fixture": True,
                },
            )
            # A zero continuation budget intentionally blocks automation; explicit fixture driver continues.
            with service.storage.transaction() as db:
                a = service._load(db, agent, task)
                a["snapshot"]["task"]["status"] = "running"
                service._save(db, a)
            if training:
                a = begin("code")
                workbench.capture_change(
                    agent,
                    task,
                    stage_id="code",
                    attempt_id=a["attempt_id"],
                    relative_path="configs/smoke.yaml",
                    before="training:\n  epochs: 36\n",
                    after="training:\n  epochs: 1\n",
                    checks=[{"id": "config", "status": "passed"}],
                )
                if scene != "code":
                    finish(
                        "code",
                        {
                            "固定 Ray 入口": "检查通过",
                            "配置检查": "通过",
                            "fixture": True,
                        },
                        a,
                    )
                    with service.storage.transaction() as db:
                        agg = service._load(db, agent, task)
                        agg["snapshot"]["task"]["status"] = "running"
                        service._save(db, agg)
                    proposal = {
                        "objective": "复现训练链路 · 1 Epoch，测试集保持封存",
                        "immutable_inputs": {
                            "route": "project_governance",
                            "project_id": "fixture-cats-dogs",
                            "release_digest": "fixture-release",
                            "config_digest": "fixture-config",
                            "data_digest": "fixture-data",
                            "split_digest": "fixture-split",
                            "role": "smoke",
                            "profile": "fixture-only",
                            "final_test": False,
                        },
                        "resources": {
                            "gpu": 1,
                            "worker_cpu": 2,
                            "decode_cpu": 16,
                            "memory": {
                                "worker": "4 GiB",
                                "object_store": "单独预算",
                                "unaccounted": "未纳入部分明确列示",
                            },
                        },
                        "hard_limits": {
                            "attempts": 1,
                            "retries": 0,
                            "wall_seconds": 900,
                            "memory_bytes": 8589934592,
                        },
                        "estimate": {
                            "duration": "约 5 分钟（模拟）",
                            "cost": None,
                            "source": "fixture",
                        },
                        "readiness": [
                            {
                                "id": "release",
                                "label": "固定入口与不可变 Release",
                                "status": "passed",
                                "reason": "模拟已就绪",
                            },
                            {
                                "id": "authorization",
                                "label": "治理预授权",
                                "status": "passed",
                                "reason": "仅模拟",
                            },
                        ],
                        "expires_at": "2099-01-01T00:00:00Z",
                    }
                    r = cmd(
                        "propose_training",
                        {"stage_id": "train", "proposal": proposal},
                        expected_plan_revision=1,
                    )["result"]
                    if scene in {"training", "result"}:
                        cmd(
                            "submit_input",
                            {
                                "request_id": r["request"]["request_id"],
                                "values": {
                                    "proposal_digest": r["proposal"]["digest"],
                                    "decision": "confirm",
                                },
                            },
                            ctx=user,
                            expected_version=service.get_task(agent, task)["task"][
                                "version"
                            ],
                        )
                        with service.storage.transaction() as db:
                            agg = service._load(db, agent, task)
                            agg["snapshot"]["task"]["status"] = "running"
                            service._save(db, agg)
                        a = begin("train")
                        intent = cmd(
                            "prepare_external",
                            {
                                "attempt_id": a["attempt_id"],
                                "execution_token": a["execution_token"],
                                "route": "project_governance",
                                "immutable_inputs": proposal["immutable_inputs"],
                            },
                        )["result"]
                        cmd(
                            "bind_external",
                            {
                                "intent_id": intent["intent_id"],
                                "operation_ref": {
                                    "id": "fixture-job-" + scene,
                                    "job_id": "fixture-job-" + scene,
                                    "run_id": "fixture-run-" + scene,
                                    "intent_key": intent["idempotency_key"],
                                },
                            },
                        )
                        service.observe(
                            task,
                            "fixture-job-" + scene,
                            {
                                "status": (
                                    "running" if scene == "training" else "succeeded"
                                ),
                                "run_id": "fixture-run-" + scene,
                                "metrics": [
                                    {
                                        "name": "train_loss",
                                        "value": 0.53,
                                        "split": "train",
                                        "step": 1,
                                    },
                                    {
                                        "name": "val_accuracy",
                                        "value": 0.74,
                                        "split": "validation",
                                        "step": 1,
                                    },
                                ],
                            },
                            source="project_governance",
                            message_id="fixture-observation-" + scene,
                        )
                        if scene == "result":
                            finish(
                                "train",
                                {
                                    "checkpoint": "模拟回读通过",
                                    "范围": "仅 smoke；完整效果尚未验收",
                                    "fixture": True,
                                },
                                a,
                            )
                            cmd(
                                "complete_task",
                                {
                                    "summary": "模拟数据 · 训练链路 smoke 验收完成，完整训练效果尚未验收。",
                                    "artifact_refs": [
                                        a["id"]
                                        for a in service.get_task(agent, task)[
                                            "artifacts"
                                        ]
                                    ],
                                },
                                expected_plan_revision=1,
                            )
            elif scene == "replan":
                cmd(
                    "replace_plan",
                    {
                        "reason": "模拟：发现编码混杂，插入统一编码步骤",
                        "stages": [
                            stages[0],
                            spec("repair", "统一编码", ["data"], "data.transform"),
                            spec("clean", "清洗并交付", ["repair"], "data.transform"),
                        ],
                    },
                    expected_plan_revision=1,
                )
                begin("repair")
            else:
                begin("clean")
        workbench.ingest(
            task,
            {
                "host_id": "fixture-host",
                "session_id": scene,
                "thread_id": scene,
                "epoch": "fixture-" + scene,
                "seq": 1,
                "type": "public_message",
                "message_id": scene + "-message",
                "role": "assistant",
                "content_revision": 1,
                "safe_content": {
                    "data": "正在读取数据结构，检查异常并生成清单。",
                    "code": "数据清单已冻结，正在准备训练配置与固定入口。",
                    "proposal": "数据和代码已准备，下面是本次训练方案，请确认、调整或延后。",
                    "training": "已记录外部执行身份，正在观察训练指标。",
                    "result": "训练链路验收完成，证据保留在右侧详情。",
                    "two-stage": "这个任务只需要检查与清洗两个阶段。",
                    "replan": "发现编码不一致，保留已完成检查并插入修复阶段。",
                }[scene]
                + "（模拟内容）",
            },
        )
        with service.storage.transaction() as db:
            agg = service._load(db, agent, task)
            agg["project"] = {
                "project_id": scene,
                "name": {
                    "data": "Skill 数据处理",
                    "code": "编写代码",
                    "proposal": "确认训练",
                    "training": "训练过程",
                    "result": "验收结果",
                    "two-stage": "两阶段数据任务",
                    "replan": "动态重规划",
                }[scene],
                "slug": "fixture-" + scene,
                "workspace_display_path": "/fixture/projects/" + scene,
                "workspace_ref": "work",
                "initialization_state": "ready",
            }
            service._save(db, agg, increment=False)
    viewer = {
        "task_id": task_ids[4],
        "context": asdict(user),
        "workspaces": {"work": str(root)},
        "fixture": True,
        "tasks": task_ids,
    }
    (root / "viewer.json").write_text(json.dumps(viewer, ensure_ascii=False, indent=2))
    service.close()
    return viewer


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--state-dir", required=True)
    a = p.parse_args()
    print(json.dumps(generate(a.state_dir), ensure_ascii=False))
