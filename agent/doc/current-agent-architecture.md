# 当前 Agent 架构设计

> 状态：当前架构说明 + 后续设计目标。当前代码已有只读 SDK runtime 和训推一体化 P0
> deterministic patrol/governance core；数据清洗、模型训练、推理加速和文档更新的专用执行工具仍是 planned。

## 1. 设计目标

新的 Agent 架构不是替代 Ray、MLflow 或训练项目代码，而是在这些确定性组件之上增加一个可控的训推一体化决策层。对外主 Agent 负责数据清洗、模型训练、推理加速、全局检查和文档更新；内部阶段 handler 接收明确输入，调用受控工具，最终产出结构化结果。

```text
用户目标 / 项目配置
        |
        v
PlatformCoordinator
        |
        +--> TrainInferenceIntegratedAgent
              +--> DataCleaningStage          -> DataStageResult
              +--> ModelTrainingStage         -> TrainingStageResult
              +--> InferenceAccelerationStage -> InferenceStageResult
              +--> GlobalInspection / Docs    -> PatrolRunResult / report
        |
        v
审计日志 / MLflow Run / Ray Job / Artifact Manifest / 文档报告 / 人工审批
```

核心目标：

- 将“数据清洗、模型训练、推理加速、全局检查、文档更新”纳入一个训推一体化 Agent 主线。
- 每个内部阶段可以自主选择下一步工具，但不能越过训推权限边界。
- 大计算交给 Ray，状态治理交给 MLflow/MinIO，Agent 只做计划、编排、诊断和受控重试。
- 所有阶段输出结构化 JSON，便于上层服务、Notebook 或 CLI 消费。
- 对昂贵、破坏性或生产影响动作引入 human approval。

## 2. 当前已有基础

仓库当前已有：

| 能力 | 当前位置 | Agent 架构中的作用 |
| --- | --- | --- |
| 平台文档和约束 | `README.md`, `AGENTS.md`, `doc/` | 定义训练治理、Ray/MLflow/MinIO 边界。 |
| Claude SDK runtime | `agent/core/sdk.py`, `agent/runtime.py` | 创建 `ClaudeSDKClient`、MCP server、hooks、permissions、structured output 校验。 |
| 只读 inspection tools | `agent/tools/inspection.py`, `agent/tools/server.py` | 当前可用的项目、服务、MLflow experiment、Ray status 巡检工具。 |
| Agent definitions | `agent/agents/definitions.py`, `agent/agents/definition.py` | `definitions.py` 是当前 SDK `AgentDefinition` 预置；`definition.py` 是 dataclass wrapper，含 legacy/future preset，不作为安全默认配置。 |
| Stage/common schemas | `agent/schemas/common.py`, `agent/schemas/inspection.py` | 当前通用阶段结果和 inspection 结果对象。 |
| Workflow skeleton | `agent/workflows/state_machine.py`, `agent/workflows/orchestrator.py` | 当前线性 stage workflow 状态机和 handler 编排骨架。 |
| 训推一体化 P0 patrol core | `agent/schemas/patrol.py`, `agent/patrol/`, `agent/state/patrol.py`, `agent/workflows/patrol.py`, `agent/policies/patrol.py` | 当前 deterministic 全局检查、证据、finding/recommendation、session、compaction、report 和 SDK helper；`Patrol*` 为底层兼容命名。 |
| Ray 参考项目 | `train-model/ray-cats-and-dogs/` | 首个 Agent POC 的执行对象。 |
| Ray Job CI/CD | `train-model/ray-cats-and-dogs/job/` | 后续 `submit_ray_job` 工具的实现参考。 |
| MLflow 分析 Skill | `.codex/skills/mlflow-optimize-models/` | 历史 Run 分析、可比性和调优策略参考。 |
| Ray Skill | `.codex/skills/ray/` | Ray Data/Train/Job/Serve 设计参考。 |
| Claude SDK 本地源码 | `/data/ai/chenzhangyue/code/claude-agent-sdk-python` | Agent runtime 和工具封装依据。 |

当前没有已经部署的常驻无人值守 Agent 服务；现有训推一体化 P0 是可离线/按需运行的
deterministic patrol core，不代表已经开放长训练、推理服务部署、源码文档改写或生产变更能力。

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
│ - TrainInferenceIntegratedAgent                              │
│ - Internal: DataCleaning / Training / Inference / Docs        │
└───────────────────────┬─────────────────────────────────────┘
                        v
┌─────────────────────────────────────────────────────────────┐
│ Tool Layer: in-process SDK MCP tools                         │
│ - Current: list/inspect/check read-only platform tools        │
│ - Planned: RayData/Job/Train, Artifact, Registry, Docs, Approval │
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
| `PlatformCoordinator` | 接收用户目标，调用训推一体化 Agent，合并结果，决定是否进入下一阶段或审批。 | 直接操作数据、训练循环或 Registry alias。 |
| `TrainInferenceIntegratedAgent` | 统一负责数据清洗、模型训练、推理加速、全局检查和文档更新的计划、编排、诊断、报告。 | 绕过权限直接执行长训练、生产部署、Registry alias 或任意源码修改。 |
| `DataCleaningStage` | 检查数据源、选择数据清洗/处理计划、提交受控 Ray Data job、验证输出 manifest。 | 读取大量原始样本到上下文或随意 reshuffle 评估集。 |
| `ModelTrainingStage` | 检查数据和配置、分析 MLflow Runs、提交 Ray Train/Job、判断是否 retry。 | 重复使用 test set 调参或隐式发布模型。 |
| `InferenceAccelerationStage` | 验证模型 artifact、执行 smoke/batch inference、生成推理优化和 serving/promotion plan。 | 未审批切换 production/champion alias 或生产流量。 |
| `DocumentationStage` | 生成/更新运行报告、README/guide 更新建议和契约变更记录。 | 未授权修改非任务相关源码或掩盖失败证据。 |

### 3.3 Tool Layer

工具必须是平台 API，不是通用 Shell。当前已实现的是只读 inspect 类工具；下表中的
submit/log/approval 等属于后续阶段工具类型。

| 类型 | 示例 | 设计要求 |
| --- | --- | --- |
| inspect | `inspect_dataset`, `inspect_mlflow_runs` | 只读、可缓存、返回摘要和 URI。 |
| propose | `propose_ray_data_plan`, `propose_training_config` | 不产生外部副作用。 |
| submit | `submit_ray_data_job`, `submit_ray_training_job` | 幂等、有预算、有 submission_id。 |
| status | `get_ray_job_status`, `get_mlflow_run_status` | 返回状态、最近错误摘要、日志 URI。 |
| validate | `validate_dataset_output`, `verify_checkpoint`, `run_smoke_inference` | 返回 pass/fail 和证据。 |
| log | `log_dataset_manifest`, `log_stage_report` | 通过 MLflow/Artifact API 写证据。 |
| docs | `generate_stage_report`, `propose_doc_update`, `apply_doc_update` | 报告可写 artifact；源码文档 patch 需要审批。 |
| approval | `request_training_approval`, `request_promotion_approval`, `request_doc_update_approval` | 只生成审批请求，不自行批准。 |

## 4. 训推一体化自治边界

每个阶段都允许 Agent 在边界内自主循环：

```text
plan -> call tool -> observe -> validate -> retry or finish
```

但自治边界不同：

| 阶段 | 可自主执行 | 必须审批 |
| --- | --- | --- |
| 数据清洗 | schema/profile、manifest、受限 Ray Data job、质量校验。 | 覆盖既有数据版本、删除数据、访问未授权外部源。 |
| 模型训练 | `--check-config`、`--plan`、小预算 smoke、读取 MLflow 历史。 | 长训练、大规模调参、使用 `--force`、消耗 GPU 大预算。 |
| 推理加速 | artifact 回读、load test、batch smoke、生成 optimization/serving plan。 | 修改 Registry alias、替换生产服务、暴露公网 endpoint。 |
| 全局检查 | 服务健康、项目结构、MLflow/Ray/Artifact/资源状态巡检和报告。 | 执行修复、重启服务、提交任务或变更生产状态。 |
| 文档更新 | 生成报告、更新建议和低风险文档草案。 | 修改源码文档、覆盖历史报告或移除审计证据。 |

## 5. 推荐执行流

### 5.1 数据清洗阶段

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

### 5.2 模型训练阶段

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

### 5.3 推理加速阶段

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

推理加速阶段默认只生成候选优化和发布计划。任何生产 alias 或服务流量切换必须是独立审批动作。

## 6. 当前实现布局和规划补齐

当前实现直接位于 `agent/` 包下，已经有 SDK runtime、hooks、policies、state、tools、
schemas、workflow skeleton 和训推一体化 P0 patrol core。不要再按早期草案创建
`agent/src/galatea_agent/`。

```text
agent/
├── core/sdk.py                  # GalateaSDKRuntime, AgentSDKConfig, result collection
├── runtime.py                   # high-level runtime facade
├── client.py                    # platform-aware convenience client
├── tools/
│   ├── server.py                # create_galatea_mcp_server()
│   ├── inspection.py            # current read-only platform tools
│   ├── executor.py              # deterministic executor for tests
│   └── patrol_output.py         # 训推/Patrol summary/evidence/raw_ref envelope
├── schemas/
│   ├── common.py                # StageStatus, ArtifactRef, StageEvidence, ApprovalRequest
│   ├── inspection.py            # inspection result schemas
│   └── patrol.py                # EvidenceRecord, Finding, Recommendation, PatrolMemory
├── state/
│   ├── store.py                 # SessionStore, MemorySessionStore, SessionManager
│   ├── experiment.py            # ExperimentState and persistence manager
│   ├── patrol.py                # FilePatrolSessionStore and PatrolSession
│   └── persistence.py           # JSON save/load helpers
├── workflows/
│   ├── state_machine.py         # current linear stage workflow
│   ├── orchestrator.py          # orchestration skeleton
│   └── patrol.py                # PatrolRunStateMachine
├── policies/
│   ├── permission.py
│   ├── budget.py
│   ├── quality.py
│   └── patrol.py                # lifecycle/action policy, dedupe/cooldown/escalation
├── patrol/
│   ├── runner.py                # deterministic read-only run_once()
│   ├── compaction.py            # PatrolMemory compaction and fidelity checks
│   ├── audit.py                 # JSONL audit writer
│   ├── channels.py              # CLI/Markdown report rendering
│   └── sdk.py                   # 训推/Patrol SDK config and LLM output validation
├── hooks/
│   ├── builtin.py
│   ├── registry.py
│   └── types.py
└── agents/
    ├── definition.py
    ├── definitions.py
    └── registry.py
```

数据清洗、模型训练、推理加速和文档更新补齐时优先在现有包内新增专用 schema、tools
和 stage handlers，而不是迁移现有 runtime：

```text
agent/
├── schemas/data.py
├── schemas/training.py
├── schemas/inference.py
├── tools/ray_jobs.py
├── tools/mlflow_tracking.py
├── tools/artifacts.py
├── tools/registry.py
├── tools/documentation.py
└── workflows/stage_handlers.py
```

训推一体化专用工具和 schema 在实现时再分别新增到 `agent/tools/` 和
`agent/schemas/`，并保持与 [`stage-contracts-and-tools.md`](stage-contracts-and-tools.md) 对齐。

## 7. Claude subagents 的使用边界

Claude SDK 的 `AgentDefinition` 适合定义训推内部专职提示词和工具集合，例如 `data-cleaning-agent`、`model-training-agent`、`inference-acceleration-agent`、`documentation-agent`。但不要把 Claude subagent 当成真正的分布式执行单元：

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

## 10. 相关文档

- 实现布局和 SDK runtime：[`python-agent-architecture.md`](python-agent-architecture.md)。
- 阶段输入输出和工具契约：[`stage-contracts-and-tools.md`](stage-contracts-and-tools.md)。
- 训推一体化 Agent 契约：[`patrol-push-agent-contract.md`](patrol-push-agent-contract.md)。
- 落地顺序：[`implementation-roadmap.md`](implementation-roadmap.md)。
