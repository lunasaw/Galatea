# Agent 架构落地路线

> 状态：设计草案。目标是在不引入不可控自动化风险的情况下，逐步把 Claude Agent SDK 接入 Galatea。

## 1. 阶段 0：文档和边界确认

输出：

- `agent/doc/` 文档集。
- 三阶段 StageResult schema 草案。
- 工具权限矩阵。
- POC 范围确认：优先 `train-model/ray-cats-and-dogs/`。

不做：

- 不启动长训练。
- 不修改 Registry。
- 不实现常驻服务。

## 2. 阶段 1：只读 Agent Runtime POC

目标：验证 Claude SDK runtime、结构化输出和只读工具。

建议实现：

```text
agent/src/galatea_agent/
├── runtime/client.py
├── tools/server.py
├── tools/mlflow_tracking.py
├── tools/project_inspection.py
├── schemas/common.py
└── agents/data_agent.py
```

工具：

- `inspect_project_structure`
- `inspect_training_configs`
- `inspect_mlflow_experiment`
- `inspect_ray_status`

验收：

- Agent 能输出符合 schema 的只读报告。
- 未开放 Bash/Edit/Write。
- `permission_mode="dontAsk"` 下未知工具会失败。
- `ResultMessage.structured_output` 被 runtime 校验。

## 3. 阶段 2：DataAgent 小预算 Ray Data POC

目标：完成数据阶段闭环。

工具：

- `inspect_dataset_source`
- `compute_source_manifest`
- `propose_ray_data_plan`
- `submit_ray_data_job`
- `get_ray_job_status`
- `validate_dataset_output`
- `log_dataset_manifest`

验收：

- 相同输入重复运行不覆盖不同 digest 的输出。
- Ray job 有 `submission_id`、runtime_env、resource budget。
- 输出 `DataStageResult` 包含 manifest、split、preprocessing、row counts、Ray job id。
- 数据质量失败时返回 `failed` 或 `needs_approval`，不进入训练。

## 4. 阶段 3：TrainingAgent check-config/plan/smoke

目标：让训练阶段从安全模式开始。

执行顺序：

1. `--check-config`
2. `--plan`
3. `smoke.yaml` 1 epoch
4. 读取 MLflow run summary
5. Artifact/checkpoint 回读验证

验收：

- check-config 和 plan 不创建训练 Run 或长任务。
- smoke 使用小预算。
- `force_new_attempt` 默认拒绝。
- 长训练返回 approval request。
- 不读取 final test set。

## 5. 阶段 4：InferenceAgent smoke 和 serve plan

目标：验证候选模型 artifact 和生成部署计划。

工具：

- `load_model_artifact_metadata`
- `verify_model_artifact_recovery`
- `run_smoke_inference`
- `generate_ray_serve_plan`
- `evaluate_quality_gates`
- `request_model_promotion_approval`

验收：

- 能通过 MLflow Artifact API 回读模型。
- smoke inference 输出 schema 校验报告。
- Ray Serve plan 是 artifact，不直接部署。
- promotion 只生成审批请求。

## 6. 阶段 5：审批和治理闭环

目标：把高风险动作拆成 proposal + approval + apply。

实现：

- Approval store。
- `request_*_approval` 工具。
- `apply_*` 工具默认不在 Agent allowed_tools 中。
- 人工审批后由 CLI/API 显式执行 apply。

验收：

- 未审批不能执行 production alias 变更。
- approval request 包含证据、风险、回滚方案。
- 所有 apply 动作写 MLflow tag/comment/artifact。

## 7. 阶段 6：CodeMaintenanceAgent

目标：单独处理代码生成、脚本修复和测试补充。

约束：

- 与数据/训练/推理 Agent 分离。
- 开启 `enable_file_checkpointing=True`。
- 允许 `Read/Grep/Glob/Edit`，Bash 只允许窄命令或人工审批。
- 修改后运行窄测试。
- 不触碰用户未授权 dirty worktree。

## 8. 推荐验证命令

只读/配置类：

```bash
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s tests -p 'test_*.py'
```

Ray 项目配置检查：

```bash
cd /data/ai/chenzhangyue/code/galatea/train-model/ray-cats-and-dogs
/data/conda/envs/attend-ray-py312/bin/python scripts/train.py \
  --config configs/smoke.yaml --check-config
```

Ray Job CI/CD dry run：

```bash
cd /data/ai/chenzhangyue/code/galatea/train-model/ray-cats-and-dogs
/data/conda/envs/attend-ray-py312/bin/python job/ci.py --dry-run
```

平台服务健康检查：

```bash
systemctl is-active minio.service mlflow.service jupyterlab.service
curl -fsS -H 'Host: localhost' http://127.0.0.1:5000/health
curl -fsS http://127.0.0.1:9000/minio/health/live
ray status
```

## 9. 里程碑退出条件

| 里程碑 | 退出条件 |
| --- | --- |
| M1 Runtime | 能运行只读 prompt，返回 schema 校验通过的 result。 |
| M2 Data | 能产出 DataStageResult，并通过 manifest/split 校验。 |
| M3 Training | 能自动跑 check-config/plan/smoke，并记录 MLflow/Ray 证据。 |
| M4 Inference | 能回读模型、跑 smoke inference、生成 serve plan。 |
| M5 Governance | 高风险动作全部需要 approval，且 apply 可审计。 |

## 10. 主要风险

- Agent 工具过宽导致越权执行。
- Ray job 失败日志过大，污染上下文。
- 数据 split 被重复生成且身份不一致。
- MLflow Run 可比性证据不足却被用于调参结论。
- Artifact 没有回读验证就进入 promotion。
- session transcript 含敏感样本或凭据。

缓解方式：

- 工具最小权限、精确 allowlist、强 disallowlist。
- PostToolUse 裁剪大输出，只传 URI 和摘要。
- 所有数据输出 create-only + digest 校验。
- MLflow 比较必须有兼容 cohort。
- promotion 前强制 artifact recovery check。
- transcript retention、脱敏和最小样本策略。
