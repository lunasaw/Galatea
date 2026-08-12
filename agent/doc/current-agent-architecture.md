# 当前 Agent 架构设计

> 状态：设计草案；目标平台：Galatea；主 SDK：Claude Agent SDK for Python；执行层：Ray；治理层：MLflow + MinIO。

## 1. 设计目标

新的 Agent 架构不是替代 Ray、MLflow 或训练项目代码，而是在这些确定性组件之上增加一个可控的决策层。每个阶段 Agent 接收明确输入，调用受控工具，最终产出结构化结果。

```text
用户目标 / 项目配置
        |
        v
PlatformCoordinator
        |
        +--> DataAgent      -> DataStageResult
        +--> TrainingAgent  -> TrainingStageResult
        +--> InferenceAgent -> InferenceStageResult
        |
        v
审计日志 / MLflow Run / Ray Job / Artifact Manifest / 人工审批
```

核心目标：

- 将“数据、训练、推理”拆成三个可独立运行、可恢复、可审计的阶段。
- 每个阶段可以自主选择下一步工具，但不能越过阶段权限边界。
- 大计算交给 Ray，状态治理交给 MLflow/MinIO，Agent 只做计划、编排、诊断和受控重试。
- 所有阶段输出结构化 JSON，便于上层服务、Notebook 或 CLI 消费。
- 对昂贵、破坏性或生产影响动作引入 human approval。

## 2. 当前已有基础

仓库当前已有：

| 能力 | 当前位置 | Agent 架构中的作用 |
| --- | --- | --- |
| 平台文档和约束 | `README.md`, `AGENTS.md`, `doc/` | 定义训练治理、Ray/MLflow/MinIO 边界。 |
| Ray 参考项目 | `train-model/ray-cats-and-dogs/` | 首个 Agent POC 的执行对象。 |
| Ray Job CI/CD | `train-model/ray-cats-and-dogs/job/` | 后续 `submit_ray_job` 工具的实现参考。 |
| MLflow 分析 Skill | `.codex/skills/mlflow-optimize-models/` | 历史 Run 分析、可比性和调优策略参考。 |
| Ray Skill | `.codex/skills/ray/` | Ray Data/Train/Job/Serve 设计参考。 |
| Claude SDK 本地源码 | `/data/ai/chenzhangyue/code/claude-agent-sdk-python` | Agent runtime 和工具封装依据。 |

当前没有已经部署的常驻 Agent 服务；本文档描述的是下一步要实现的设计。

## 3. 分层架构

```text
┌─────────────────────────────────────────────────────────────┐
│ User / Notebook / CLI / Future API                           │
└───────────────────────┬─────────────────────────────────────┘
                        v
┌─────────────────────────────────────────────────────────────┐
│ Agent Runtime                                                │
│ - ClaudeSDKClient lifecycle                                  │
│ - session_id / session_store                                 │
│ - output_format schema                                       │
│ - hooks / permissions / budgets                              │
└───────────────────────┬─────────────────────────────────────┘
                        v
┌─────────────────────────────────────────────────────────────┐
│ Agent Layer                                                  │
│ - PlatformCoordinator                                        │
│ - DataAgent                                                  │
│ - TrainingAgent                                              │
│ - InferenceAgent                                             │
└───────────────────────┬─────────────────────────────────────┘
                        v
┌─────────────────────────────────────────────────────────────┐
│ Tool Layer: in-process SDK MCP tools                         │
│ - RayDataTools / RayJobTools / RayTrainTools                 │
│ - MLflowTools / ArtifactTools / RegistryTools                │
│ - DatasetValidationTools / ConfigTools / ApprovalTools       │
└───────────────────────┬─────────────────────────────────────┘
                        v
┌─────────────────────────────────────────────────────────────┐
│ Execution and Governance                                     │
│ - Ray Data / Ray Jobs / Ray Train / Ray Serve                │
│ - MLflow Tracking / Artifact / Model Registry API            │
│ - MinIO via MLflow or least-privilege service credentials     │
└─────────────────────────────────────────────────────────────┘
```

### 3.1 Agent Runtime

Agent Runtime 是 `ClaudeSDKClient` 的薄封装，负责：

- 创建 `ClaudeAgentOptions`。
- 注入 in-process SDK MCP server。
- 配置 `strict_mcp_config`、`setting_sources`、`allowed_tools`、`disallowed_tools`。
- 设置 `output_format`，从 `ResultMessage.structured_output` 读取阶段结果。
- 收集 `ResultMessage` 中的 cost、usage、session_id、stop_reason、permission_denials。
- 管理 session resume/fork/store。
- 注册 hooks 做确定性权限和输出治理。

### 3.2 Agent Layer

| Agent | 职责 | 不负责 |
| --- | --- | --- |
| `PlatformCoordinator` | 接收用户目标，拆解阶段，合并结果，决定下一阶段是否可运行。 | 直接操作数据、训练循环或 Registry alias。 |
| `DataAgent` | 检查数据源、选择数据处理计划、提交 Ray Data job、验证输出 manifest。 | 读取大量原始样本到上下文或随意 reshuffle 评估集。 |
| `TrainingAgent` | 检查数据和配置、分析 MLflow Runs、提交 Ray Train/Job、判断是否 retry。 | 重复使用 test set 调参或隐式发布模型。 |
| `InferenceAgent` | 验证模型 artifact、执行 smoke/batch inference、生成 serving/promotion plan。 | 未审批切换 production/champion alias。 |

### 3.3 Tool Layer

工具必须是平台 API，不是通用 Shell。推荐工具类型：

| 类型 | 示例 | 设计要求 |
| --- | --- | --- |
| inspect | `inspect_dataset`, `inspect_mlflow_runs` | 只读、可缓存、返回摘要和 URI。 |
| propose | `propose_ray_data_plan`, `propose_training_config` | 不产生外部副作用。 |
| submit | `submit_ray_data_job`, `submit_ray_training_job` | 幂等、有预算、有 submission_id。 |
| status | `get_ray_job_status`, `get_mlflow_run_status` | 返回状态、最近错误摘要、日志 URI。 |
| validate | `validate_dataset_output`, `verify_checkpoint`, `run_smoke_inference` | 返回 pass/fail 和证据。 |
| log | `log_dataset_manifest`, `log_stage_report` | 通过 MLflow/Artifact API 写证据。 |
| approval | `request_training_approval`, `request_promotion_approval` | 只生成审批请求，不自行批准。 |

## 4. 三阶段自治边界

每个阶段都允许 Agent 在边界内自主循环：

```text
plan -> call tool -> observe -> validate -> retry or finish
```

但自治边界不同：

| 阶段 | 可自主执行 | 必须审批 |
| --- | --- | --- |
| 数据 | schema/profile、manifest、受限 Ray Data job、质量校验。 | 覆盖既有数据版本、删除数据、访问未授权外部源。 |
| 训练 | `--check-config`、`--plan`、小预算 smoke、读取 MLflow 历史。 | 长训练、大规模调参、使用 `--force`、消耗 GPU 大预算。 |
| 推理 | artifact 回读、load test、batch smoke、生成 serving plan。 | 修改 Registry alias、替换生产服务、暴露公网 endpoint。 |

## 5. 推荐执行流

### 5.1 数据阶段

```text
DataStageInput
  -> inspect_dataset
  -> compute_source_manifest
  -> propose_ray_data_plan
  -> submit_ray_data_job
  -> get_ray_job_status loop
  -> validate_dataset_output
  -> log_dataset_manifest
  -> DataStageResult
```

输出必须至少包含：数据 URI、manifest URI、manifest digest、split id、preprocessing version、row counts、feature schema、Ray job id、MLflow run id 或 stage report id。

### 5.2 训练阶段

```text
TrainingStageInput
  -> validate_training_config
  -> inspect_compatible_mlflow_runs
  -> propose_training_config
  -> submit_ray_training_job 或 check_config/plan
  -> get_ray_job_status loop
  -> verify_checkpoint
  -> summarize_training_result
  -> TrainingStageResult
```

训练阶段必须区分 train/validation/final test。搜索和 early stopping 只能用 train/validation evidence。最终 test 只允许在 champion/final evaluation 阶段读取一次。

### 5.3 推理阶段

```text
InferenceStageInput
  -> load_model_artifact_metadata
  -> verify_artifact_recovery
  -> run_smoke_inference
  -> run_batch_inference_or_serve_plan
  -> evaluate_quality_gates
  -> request_promotion_approval
  -> InferenceStageResult
```

推理阶段默认只生成候选发布计划。任何生产 alias 或服务流量切换必须是独立审批动作。

## 6. 推荐实现布局

```text
agent/src/galatea_agent/
├── __init__.py
├── runtime/
│   ├── client.py                 # ClaudeSDKClient wrapper
│   ├── messages.py               # message/result collection
│   └── sessions.py               # session_id/session_store helpers
├── agents/
│   ├── coordinator.py
│   ├── data_agent.py
│   ├── training_agent.py
│   └── inference_agent.py
├── schemas/
│   ├── common.py                 # StageStatus, ApprovalState, ArtifactRef
│   ├── data.py                   # DataStageInput/Result
│   ├── training.py               # TrainingStageInput/Result
│   └── inference.py              # InferenceStageInput/Result
├── tools/
│   ├── server.py                 # create_sdk_mcp_server("galatea", tools=[...])
│   ├── ray_jobs.py
│   ├── ray_data.py
│   ├── mlflow_tracking.py
│   ├── artifacts.py
│   ├── registry.py
│   └── validation.py
├── policies/
│   ├── permissions.py
│   ├── budgets.py
│   ├── approvals.py
│   └── quality_gates.py
└── hooks.py
```

## 7. Claude subagents 的使用边界

Claude SDK 的 `AgentDefinition` 适合定义专职提示词和工具集合，例如 `data-agent`、`training-agent`、`inference-agent`。但不要把 Claude subagent 当成真正的分布式执行单元：

- subagent 用于分工推理、代码检查、计划生成。
- Ray Job/Ray Data/Ray Train 才是分布式执行和恢复边界。
- 每个 subagent 的工具集必须比主 Agent 更窄。
- 允许并行 subagent 时，hooks 必须通过 `agent_id` / `agent_type` 做归因。

## 8. 状态和审计

每次阶段运行建议生成一个 `stage_run_id`：

```text
<project>-<stage>-<yyyyMMddTHHmmssZ>-<short-random>
```

需要持久化：

- Claude session id 和 transcript store key。
- 所有 Ray job id / submission id。
- 所有 MLflow run id / experiment name。
- 输入配置 digest 和输出 manifest digest。
- 工具调用摘要、权限拒绝、approval request id。
- 最终 `StageResult` JSON。

Agent transcript 可以保存在独立 session store；训练证据仍以 MLflow 和 Artifact Manifest 为权威。

## 9. 非目标

首版不做：

- 常驻无人值守 AutoML 服务。
- 未审批的 Registry alias 切换。
- 让 LLM 自由执行任意 Bash/Ray CLI。
- 直接读写 MLflow SQLite backend store。
- 把 MinIO 服务端数据目录当客户端 API。
- 自动大规模调参或长期 GPU 训练。
