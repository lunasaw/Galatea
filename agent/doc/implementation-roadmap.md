# Agent 架构落地路线

> 状态：当前进度 + 后续路线。当前代码已有只读 SDK runtime、inspection tools 和
> 训推一体化 P0 deterministic patrol/governance core；数据清洗、模型训练、推理加速和文档更新专用执行工具仍是 planned。

## 0. 当前进度快照

| 范围 | 当前状态 | 代码位置 |
| --- | --- | --- |
| SDK runtime | 已实现基础 runtime、hooks、permission、budget、structured output 校验 | `agent/core/sdk.py`, `agent/runtime.py` |
| Inspection tools | 已实现 5 个只读 MCP tools | `agent/tools/inspection.py`, `agent/tools/server.py` |
| Stage workflow skeleton | 已实现线性 state machine 和 handler 编排骨架 | `agent/workflows/state_machine.py`, `agent/workflows/orchestrator.py` |
| 训推一体化 P0 patrol core | 已实现 schema、session store、state machine、runner、compaction、policy、report、SDK helper | `agent/schemas/patrol.py`, `agent/state/patrol.py`, `agent/workflows/patrol.py`, `agent/patrol/` |
| 数据清洗/模型训练/推理加速/文档更新 tools | 未实现专用 MCP tools | 见 [`stage-contracts-and-tools.md`](stage-contracts-and-tools.md) |
| Approval/apply flow | 未实现 production approval store 和 apply tools | 见本文阶段 7 |

## 1. 阶段 0：文档和边界确认（已完成基础整理）

输出：

- `agent/doc/` 文档集和阅读索引。
- 三阶段 `StageResult` schema 草案。
- 训推一体化 P0 `PatrolRunResult`、`EvidenceRecord`、`Finding`、`Recommendation` 草案。
- 工具权限矩阵。
- POC 范围确认：首选只读平台巡检；训练项目可从示例 workload 开始。

不做：

- 不启动长训练。
- 不修改 Registry。
- 不实现常驻无人值守服务。
- 不把 Patrol 状态保存在自然语言 transcript 中。

## 2. 阶段 1：只读 Agent Runtime POC（已实现基础）

目标：验证当前 `agent/` 包中的 Claude SDK runtime、结构化输出和只读工具。

当前可复用：

```text
agent/
├── core/sdk.py
├── runtime.py
├── tools/server.py
├── tools/inspection.py
├── schemas/common.py
├── schemas/inspection.py
└── hooks/builtin.py
```

工具：

- `inspect_project_structure`
- `check_service_health`
- `inspect_mlflow_experiment`
- `inspect_ray_status`
- `list_training_projects`

验收：

- Agent 能输出符合 schema 的只读报告。
- 未开放 Bash/Edit/Write/MultiEdit。
- `permission_mode="dontAsk"` 下未知工具会失败。
- `ResultMessage.structured_output` 被 runtime 校验。
- 大工具输出被 hook 裁剪或以 URI 引用。

## 3. 阶段 2：训推一体化 P0 巡检治理底座

目标：让训推一体化 Agent 不依赖真实 LLM 也能离线完成一轮只读全局检查、证据索引、finding、recommendation 和报告生成。

当前代码：

```text
agent/
├── schemas/patrol.py
├── state/patrol.py
├── workflows/patrol.py
├── policies/patrol.py
├── tools/patrol_output.py
└── patrol/
    ├── audit.py
    ├── channels.py
    ├── runner.py
    ├── compaction.py
    └── sdk.py
```

已实现：

- `PatrolMemory`。
- `EvidenceRecord` / `EvidenceIndex`。
- `Finding` lifecycle。
- `Recommendation` schema + dedupe/cooldown。
- `PatrolRunStateMachine`。
- `summary_for_model + evidence + raw_ref` 工具输出 envelope。
- file-backed `FilePatrolSessionStore`。
- `PatrolRunner.run_once()`，通过 `tool_overrides` 支持离线回放。
- CLI/Markdown report 和 Patrol SDK config helper。

待补齐：

- 更细的 budget/tool-call 计数。
- 真实 approval store。
- 更完整的 Ray job、MLflow run、Artifact 级巡检工具。

验收：

- 不接真实服务也能通过 `tool_overrides` 跑完整 `PatrolRunner.run_once()`。
- 每条 recommendation 都能追溯到 evidence。
- 压缩后 open finding / approval / evidence 不丢。
- 同一风险不会重复刷屏。
- Ray 不可用只生成 warning finding，不启动训练。
- promotion、alias、删除、覆盖、长训练无 approval 时不能 apply。

## 4. 阶段 3：训推一体化 SDK 集成

目标：把 deterministic patrol core 接入 Claude SDK，让 LLM 只负责摘要、解释、候选推荐和文档草案。

交付：

- 训推一体化专用 `AgentSDKConfig` helper。
- `PatrolRunResult` JSON schema output format。
- compacted `PatrolMemory` prompt view。
- recommendation candidate 后置 policy 校验。
- CLI/Notebook/Markdown report channel 和文档更新建议。

验收：

- LLM 结构化输出缺字段时失败。
- LLM 试图建议高风险 apply 时被改写为 approval request 或 blocked finding。
- token/cost 超预算时降级 deterministic summary。
- report 不含密钥、样本或长日志。

## 5. 阶段 4：数据清洗小预算 Ray Data POC

目标：完成数据清洗和数据处理阶段闭环。

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

## 6. 阶段 5：模型训练 check-config/plan/smoke

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
- 不读取 final test set 做搜索。

## 7. 阶段 6：推理加速 smoke 和 serve plan

目标：验证候选模型 artifact，生成推理优化、批量推理和部署计划。

工具：

- `load_model_artifact_metadata`
- `verify_model_artifact_recovery`
- `run_smoke_inference`
- `generate_inference_optimization_plan`
- `generate_ray_serve_plan`
- `evaluate_quality_gates`
- `request_model_promotion_approval`

验收：

- 能通过 MLflow Artifact API 回读模型。
- smoke inference 输出 schema 校验报告。
- 推理加速计划包含目标延迟/吞吐、候选后端、资源预算、回滚和验证方式。
- Ray Serve plan 是 artifact，不直接部署。
- promotion 只生成审批请求。

## 8. 阶段 7：审批、治理和文档更新闭环

目标：把高风险动作和源码文档更新拆成 proposal + approval + apply。

实现：

- Approval store。
- `request_*_approval` 工具。
- `apply_*` 和源码文档 patch 工具默认不在 Agent allowed_tools 中。
- 人工审批后由 CLI/API 显式执行 apply。

验收：

- 未审批不能执行 production alias 变更。
- approval request 包含证据、风险、回滚方案和必要文档更新说明。
- 所有 apply 动作写 MLflow tag/comment/artifact。

## 9. 阶段 8：CodeMaintenanceAgent

目标：单独处理代码生成、脚本修复和测试补充。

约束：

- 与训推一体化 Agent 的默认权限分离，尤其不混入训练、推理和 Registry apply 权限。
- 开启 file checkpointing。
- 允许 `Read/Grep/Glob/Edit`，Bash 只允许窄命令或人工审批。
- 修改后运行窄测试。
- 不触碰用户未授权 dirty worktree。

## 10. 推荐验证命令

当前 agent 测试：

```bash
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s agent/test -p 'test_*.py'
```

训推一体化 P0 / Patrol core 测试：

```bash
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s agent/test -p 'test_patrol_*.py'
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

## 11. 里程碑退出条件

| 里程碑 | 退出条件 |
| --- | --- |
| M1 Runtime | 能运行只读 prompt，返回 schema 校验通过的 result。 |
| M2 训推 P0 | `tool_overrides` 离线 `run_once()` 通过，证据、finding、recommendation、session、report 可追溯。 |
| M3 训推 SDK | LLM 候选输出经 policy 校验，超预算可降级 deterministic summary。 |
| M4 数据清洗 | 能产出 DataStageResult，并通过 manifest/split/schema/quality 校验。 |
| M5 模型训练 | 能自动跑 check-config/plan/smoke，并记录 MLflow/Ray 证据。 |
| M6 推理加速 | 能回读模型、跑 smoke inference、生成 optimization/serve plan。 |
| M7 Governance | 高风险动作全部需要 approval，且 apply 可审计。 |

## 12. 主要风险

- Agent 工具过宽导致越权执行。
- Ray job 失败日志过大，污染上下文。
- 训推/Patrol 状态只存在自然语言 transcript 中。
- 数据 split 被重复生成且身份不一致。
- MLflow Run 可比性证据不足却被用于调参结论。
- Artifact 没有回读验证就进入 promotion。
- session transcript 含敏感样本或凭据。

缓解方式：

- 工具最小权限、精确 allowlist、强 disallowlist。
- PostToolUse 裁剪大输出，只传 URI 和摘要。
- 训推 session / Patrol session / EvidenceIndex 作为权威状态。
- 所有数据输出 create-only + digest 校验。
- MLflow 比较必须有兼容 cohort。
- promotion 前强制 artifact recovery check。
- transcript retention、脱敏和最小样本策略。
