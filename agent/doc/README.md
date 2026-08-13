# Galatea Agent 文档索引

> 状态：当前代码对齐索引。本文只列 active 文档；历史背景和外部对照材料已移到
> [`archive/`](archive/)。除非文档明确标记为“当前已有”，设计草案都不代表已经部署的
> 常驻服务或已实现能力。

Galatea 的 Agent 层不是替代 Ray、MLflow、MinIO 或训练项目代码，而是在这些确定性组件
之上增加一个可控的决策、巡检、编排和治理层。当前仓库已有 Python SDK runtime、hooks、
permission、budget、inspection tools、stage schemas 和 workflow skeleton；尚未实现的
Patrol/Data/Training/Inference 能力必须在文档中明确标记为计划或契约。

## 1. Active 文档分层

| 层级 | 关注点 | 主文档 |
| --- | --- | --- |
| 当前架构 | Agent 为什么存在、当前已有基础、分层和非目标 | [`current-agent-architecture.md`](current-agent-architecture.md) |
| 当前实现 | 当前 `agent/` 包如何承载 SDK runtime、tools、schemas、policies | [`python-agent-architecture.md`](python-agent-architecture.md) |
| SDK 规范 | Claude Agent SDK options、hooks、permissions、sessions、安全禁令 | [`claude-sdk-development-guidelines.md`](claude-sdk-development-guidelines.md) |
| 阶段契约 | Data/Training/Inference 的输入输出、工具和审批边界 | [`stage-contracts-and-tools.md`](stage-contracts-and-tools.md) |
| 巡推契约 | Patrol/Push Agent 的职责、记忆、推荐治理和测试计划 | [`patrol-push-agent-contract.md`](patrol-push-agent-contract.md) |
| 路线图 | 从当前只读 runtime 到 Patrol P0、阶段 Agent 和治理闭环 | [`implementation-roadmap.md`](implementation-roadmap.md) |
| 运维使用 | API 配置、日志边界和排障 | [`configuration.md`](configuration.md)、[`logging.md`](logging.md) |

## 2. 推荐阅读路径

| 任务 | 阅读顺序 |
| --- | --- |
| 理解当前 Agent 边界 | 本文 -> `current-agent-architecture.md` -> `python-agent-architecture.md` |
| 修改当前 SDK runtime/tools | 本文 -> `python-agent-architecture.md` -> `claude-sdk-development-guidelines.md` |
| 增加 Data/Training/Inference 工具 | `stage-contracts-and-tools.md` -> `python-agent-architecture.md` -> `implementation-roadmap.md` |
| 实现 Patrol/Push P0 | `patrol-push-agent-contract.md` -> `patrol-memory-and-compaction.md` -> `patrol-recommendation-governance.md` -> `patrol-agent-test-plan.md` |
| 配置本地运行 | `configuration.md` -> `logging.md` |
| 查历史对照材料 | `archive/README.md` |

## 3. Active 文档目录

| 文档 | 类型 | 与当前代码关系 |
| --- | --- | --- |
| [`current-agent-architecture.md`](current-agent-architecture.md) | 架构说明 | 描述当前已有基础和后续目标边界。 |
| [`python-agent-architecture.md`](python-agent-architecture.md) | 实现蓝图 | 对齐当前 `agent/` 包结构；新增能力以计划形式标出。 |
| [`claude-sdk-development-guidelines.md`](claude-sdk-development-guidelines.md) | SDK 规范 | 对齐当前 `GalateaSDKRuntime`、hooks、permissions 和 tool policy。 |
| [`stage-contracts-and-tools.md`](stage-contracts-and-tools.md) | 阶段契约 | 规划 Data/Training/Inference 的稳定输入输出和工具清单。 |
| [`patrol-push-agent-contract.md`](patrol-push-agent-contract.md) | 巡推契约 | 规划 Patrol/Push Agent 的输入输出、状态机和权限等级。 |
| [`patrol-memory-and-compaction.md`](patrol-memory-and-compaction.md) | 巡推记忆 | 规划 PatrolMemory、EvidenceIndex、压缩和保真校验。 |
| [`patrol-recommendation-governance.md`](patrol-recommendation-governance.md) | 巡推治理 | 规划 Finding、Recommendation、Approval、cooldown 和 rollback。 |
| [`patrol-agent-test-plan.md`](patrol-agent-test-plan.md) | 测试契约 | 规划 fake clients、离线回放和 P0 Go/No-Go。 |
| [`implementation-roadmap.md`](implementation-roadmap.md) | 路线图 | 将当前能力和未来里程碑拆开。 |
| [`configuration.md`](configuration.md) | 配置说明 | 对齐当前 `agent.config` 和 `GalateaRuntime(auto_load_config=...)`。 |
| [`logging.md`](logging.md) | 日志说明 | 对齐当前 `agent.runtime` model logging，并补充生产/巡推安全边界。 |

## 4. Archive 文档

历史背景材料位于 [`archive/`](archive/)，不再作为当前实现契约：

- `archive/claude-code-architecture.md`
- `archive/sdk-architecture-comparison.md`
- `archive/corecoder-vs-galatea-gap-plan.md`

如果 active 文档与 archive 文档冲突，以 active 文档和当前代码为准。

## 5. 统一命名和当前实现边界

- 当前 Python 包直接位于 `agent/`，不是早期草案中的 `agent/src/galatea_agent/`。
- 当前内置 MCP server alias 使用 `galatea-platform`，完整工具名形如
  `mcp__galatea-platform__inspect_ray_status`。
- 当前只读 tools 位于 `agent/tools/inspection.py` 和 `agent/tools/server.py`。
- 当前测试位于 `agent/test/`；根 `tests/` 目录当前不存在。
- `train-model/<project-name>/` 是 workload 边界；`train-model/ray-cats-and-dogs/` 只是当前 Ray POC 示例。
- `platform-data/` 是运行状态，不是源码，不作为客户端直接集成接口。
- Patrol/Push 与 Data/Training/Inference 阶段 Agent 是互补主线：前者负责长期巡检、证据链和推荐治理；后者负责明确输入下的阶段执行。

## 6. 当前代码结构映射

```text
agent/
├── core/                 # Claude SDK runtime wrapper and SDK result collection
├── runtime.py            # backwards-compatible high-level runtime facade
├── client.py             # convenience client for platform-aware operations
├── tools/                # in-process SDK MCP tools and deterministic tool executor
├── schemas/              # shared StageResult, ArtifactRef, inspection schemas
├── state/                # session and experiment state persistence helpers
├── workflows/            # stage workflow state machine and orchestrator
├── policies/             # permission, budget, quality gate policies
├── hooks/                # local hook registry and built-in SDK hook adapters
├── agents/               # AgentDefinition wrappers and predefined SDK agents
├── commands/             # command routing for runtime prompts
├── config/               # Anthropic settings/env loading
├── skills/               # skill runtime resolution helpers
├── demo/                 # local demos
├── test/                 # current unit/integration tests
└── doc/                  # this document set
```

Planned Patrol additions should extend the current package without migrating existing runtime:

```text
agent/
├── schemas/patrol.py
├── state/patrol.py
├── workflows/patrol.py
├── policies/patrol.py
├── tools/patrol_output.py
└── patrol/
    ├── runner.py
    ├── compaction.py
    └── clients.py
```

## 7. Current Versus Planned

| Capability | Current code | Planned docs |
| --- | --- | --- |
| Claude SDK runtime | `agent/core/sdk.py`, `agent/runtime.py` | Harden runtime prompts and structured patrol output. |
| Hooks | `agent/hooks/*`, built-in compaction/output/failure hooks | Patrol-specific audit and fidelity checks. |
| Permissions | `agent/policies/permission.py` | Action-level Patrol policy L0-L3. |
| Budget | `agent/policies/budget.py` | Per-round/session/tool/resource budgets. |
| Inspection tools | `agent/tools/inspection.py`, `agent/tools/server.py` | Tool envelope with `summary_for_model/evidence/raw_ref`. |
| Stage schemas | `agent/schemas/common.py`, `agent/schemas/inspection.py` | Data/Training/Inference and Patrol schemas. |
| Workflow state | `agent/workflows/state_machine.py` | `PatrolRunStateMachine` loop. |
| Session state | `agent/state/store.py`, `agent/state/experiment.py` | Durable `PatrolSessionStore`. |

## 8. 一句话决策

- Claude Agent SDK 负责“决策、工具调用、会话、hooks 和结构化输出”。
- Ray 负责“大数据处理、分布式训练、批量推理和可恢复执行”。
- MLflow 负责“实验、指标、Artifact、模型注册和治理证据”。
- MinIO 负责“不可变数据、runtime release、checkpoint、模型和报告持久化”。
- Patrol/Push Agent 负责“长期状态、证据索引、风险发现、推荐和审批请求”。
- Agent 不直接读写 `mlflow.db`，不直接拿 MinIO 长期密钥，不用裸 Bash 做平台动作。

## 9. 当前仓库约束

设计和实现必须遵守仓库根 `AGENTS.md`、平台 README 和训练项目 contract：

- `train-model/<project-name>/` 承载项目代码、配置、环境和项目测试。
- Notebook 只用于探索、展示和短 smoke，不承载正式长任务。
- 正式训练通过 Ray Job、Ray Train 或参数化脚本执行。
- 数据身份、split、预处理、代码、环境、资源和超参数必须可追踪。
- MLflow 访问必须通过 Tracking、Artifact 和 Registry API。
- Registry alias 变更需要显式 review 或 promotion action。
- `platform-data/` 是运行状态，不是源码或客户端集成接口。

## 10. 首版落地顺序

1. 巩固只读 SDK runtime 和 inspection tools。
2. 补齐 Patrol P0：`PatrolMemory`、`EvidenceIndex`、状态机、dedupe/cooldown、tool envelope。
3. Data 阶段小预算 Ray Data job。
4. Training 阶段 `--check-config`、`--plan` 和 1 epoch smoke。
5. Inference 阶段 artifact recovery、smoke inference、serve plan。
6. Governance 阶段 approval store、request/apply 分离和审计报告。
7. CodeMaintenanceAgent 单独处理代码修改，不混入训练/巡推默认权限。
