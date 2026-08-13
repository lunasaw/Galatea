# Galatea Python Agent 实现蓝图

> 状态：统一后的实现蓝图。本文合并并收敛了早期 `python-agent-architecture.md` 草案中的可用内容，删除了与当前平台治理冲突的示例，例如默认开放 `Bash`、`acceptEdits`、自动注册模型和直接用通用 shell 提交 Ray Job。

## 1. 本文定位

本文回答“如何在 Galatea 中用 Python 实现 Agent Runtime”。更高层的阶段设计见 [`current-agent-architecture.md`](current-agent-architecture.md)，SDK 开发规范见 [`claude-sdk-development-guidelines.md`](claude-sdk-development-guidelines.md)，阶段输入输出契约见 [`stage-contracts-and-tools.md`](stage-contracts-and-tools.md)。

目标实现不是复刻 Claude Code 的交互式 REPL，而是构建一个 Python-native、工作流驱动、平台受控的 Agent 层：

```text
ClaudeSDKClient
  -> GalateaAgentRuntime
  -> TrainInferenceIntegratedAgent
  -> SDK MCP tools
  -> Ray / MLflow / MinIO APIs
  -> StageResult JSON
```

## 2. 从 Claude Code 到 Galatea Python 的映射

| Claude Code 概念 | Python SDK 对应 | Galatea 实现 |
| --- | --- | --- |
| Query engine | `ClaudeSDKClient` | 当前由 `agent/core/sdk.py` 的 `GalateaSDKRuntime` 统一创建；`agent/runtime.py` 保留兼容 facade。 |
| Tool system | `@tool` + `create_sdk_mcp_server` | 当前 `agent/tools/server.py` 注册 `galatea-platform` 只读工具；Patrol envelope 在 `agent/tools/patrol_output.py`。 |
| Services | Python API / controlled adapters | 当前 inspection tool 直接封装 systemd/Ray CLI/MLflow Tracking API；后续 Ray Job/Artifact/Registry adapter 再拆。 |
| Sub-agent | `AgentDefinition` | 当前 `agent/agents/definitions.py` 提供 read-only 和 future stage definitions；训推内部专业分工仍靠工具策略和 Ray 隔离。 |
| State/session | SDK `SessionStore`, `resume`, `fork_session` | 当前 `agent/state/store.py` 管 SDK/session 状态，`agent/state/patrol.py` 管 Patrol 权威状态。 |
| Permission loop | `permission_mode`, `allowed_tools`, `disallowed_tools`, hooks | 当前 `agent/policies/permission.py` + hooks 默认 deny；`agent/policies/patrol.py` 补 action-level policy。 |
| Workflow | Python code + Ray | 当前 `agent/workflows/state_machine.py` 和 `orchestrator.py` 是 stage skeleton；`workflows/patrol.py` 是训推 P0 巡检治理状态机。 |

## 3. 当前实现布局和新增位置

当前 Python 包直接位于 `agent/`，不是早期草案中的 `agent/src/galatea_agent/`。
首版应在现有包上小步扩展，不迁移 runtime。

```text
agent/
├── core/
│   └── sdk.py                   # GalateaSDKRuntime, AgentSDKConfig, message/result collection
├── runtime.py                   # backwards-compatible high-level runtime facade
├── client.py                    # convenience platform-aware client
├── tools/
│   ├── server.py                # create_sdk_mcp_server("galatea-platform", ...)
│   ├── inspection.py            # current read-only platform/project/Ray/MLflow tools
│   ├── patrol_output.py         # summary/evidence/raw_ref envelope helpers
│   └── executor.py              # deterministic tool executor for tests and non-LLM flows
├── schemas/
│   ├── common.py                # StageStatus, ArtifactRef, StageEvidence, ApprovalRequest
│   ├── inspection.py            # current inspection result schemas
│   └── patrol.py                # EvidenceRecord, Finding, Recommendation, PatrolMemory
├── state/
│   ├── store.py                 # SessionStore, MemorySessionStore, SessionManager
│   ├── experiment.py            # ExperimentState and file-backed manager
│   ├── patrol.py                # FilePatrolSessionStore, PatrolSession
│   └── persistence.py           # JSON persistence helpers
├── workflows/
│   ├── state_machine.py         # current linear workflow state machine
│   ├── orchestrator.py          # workflow orchestration skeleton
│   └── patrol.py                # PatrolRunStateMachine
├── policies/
│   ├── permission.py
│   ├── budget.py
│   ├── quality.py
│   └── patrol.py               # PatrolLifecyclePolicy, PatrolActionPolicy
├── hooks/
│   ├── builtin.py
│   ├── registry.py
│   └── types.py
├── patrol/
│   ├── runner.py                # deterministic read-only PatrolRunner.run_once()
│   ├── compaction.py            # PatrolMemory compaction and fidelity checks
│   ├── audit.py                 # JSONL audit writer
│   ├── channels.py              # CLI/Markdown report rendering
│   └── sdk.py                   # Patrol SDK config and LLM output validation
└── agents/
    ├── definition.py
    ├── definitions.py
    └── registry.py
```

数据清洗、模型训练、推理加速和文档更新专用执行能力后续再按契约新增：

```text
agent/
├── schemas/data.py
├── schemas/training.py
├── schemas/inference.py
├── tools/ray_jobs.py
├── tools/ray_data.py
├── tools/mlflow_tracking.py
├── tools/artifacts.py
├── tools/registry.py
├── tools/documentation.py
└── workflows/stage_handlers.py
```

说明：

- `tools/` 可以直接包含 service adapter，首版不必额外拆 `services/`，避免抽象过早。
- `schemas/` 是 Agent 与平台代码的稳定契约，prompt 不应成为唯一接口。
- `policies/` 放不可交给 LLM 自由判断的规则，例如预算、promotion 审批和 destructive action 禁令。
- 训推一体化长期状态当前由 `state/patrol.py` 和 `patrol/` core 承载，不要退回到 Claude transcript。

## 4. Runtime 基线

当前只有 `agent/core/sdk.py` 的 `GalateaSDKRuntime` 直接创建 `ClaudeSDKClient`。
`agent/runtime.py` 的 `GalateaRuntime` 是兼容 facade，不应在阶段 Agent 中复制 SDK client
生命周期逻辑。

当前运行入口模式：

```python
from pathlib import Path

from agent.core import AgentSDKConfig, GalateaSDKRuntime


config = AgentSDKConfig(
    project_root=Path("/data/ai/chenzhangyue/code/galatea"),
    agent_type="train-inference-integrated",
    allowed_tools=[
        "mcp__galatea-platform__list_training_projects",
        "mcp__galatea-platform__inspect_project_structure",
        "mcp__galatea-platform__check_service_health",
        "mcp__galatea-platform__inspect_mlflow_experiment",
        "mcp__galatea-platform__inspect_ray_status",
    ],
    disallowed_tools=["Bash", "Write", "Edit", "MultiEdit"],
    permission_mode="dontAsk",
    output_schema=None,
)

async with GalateaSDKRuntime(config) as runtime:
    result = await runtime.query("Inspect the platform and return a concise report.")
```

当前实现点：

- `DEFAULT_MCP_SERVER_ALIAS = "galatea-platform"`，完整工具名形如
  `mcp__galatea-platform__inspect_ray_status`。
- `AgentSDKConfig.output_schema` 会被 `GalateaSDKRuntime.build_options()` 转换成 SDK
  `output_format={"type": "json_schema", ...}`。
- `validate_result()` 会检查 SDK error、terminal reason、budget、permission denials 和
  structured output schema。
- 默认 hooks 来自 `agent/hooks/builtin.py`，负责日志、权限、审计、tool output 裁剪和
  compaction guidance。
- Patrol SDK 集成使用 `agent/patrol/sdk.py` 的 `make_patrol_sdk_config()` 和
  `validate_llm_patrol_result()`，让 LLM 输出先过 Pydantic 和 action policy。

## 5. SDK MCP 工具服务器

当前使用 in-process SDK MCP server，减少独立进程和 IPC 复杂度。实际代码在
`agent/tools/server.py`，当前只注册只读 inspection tools：

```python
from claude_agent_sdk import create_sdk_mcp_server


INSPECTION_TOOLS = [
    tool_list_training_projects,
    tool_inspect_project_structure,
    tool_check_service_health,
    tool_inspect_mlflow_experiment,
    tool_inspect_ray_status,
]


def create_galatea_mcp_server():
    return create_sdk_mcp_server(
        name="galatea-platform",
        tools=INSPECTION_TOOLS,
    )
```

当前完整工具名：

- `mcp__galatea-platform__list_training_projects`
- `mcp__galatea-platform__inspect_project_structure`
- `mcp__galatea-platform__check_service_health`
- `mcp__galatea-platform__inspect_mlflow_experiment`
- `mcp__galatea-platform__inspect_ray_status`

训推一体化后续工具（数据清洗、模型训练、推理加速、文档更新）必须在新增文件后
再加入 `INSPECTION_TOOLS` 或新的 tool group；不要在文档里把未注册工具当成当前可用工具。

工具命名建议：

- 读操作：`inspect_*`, `get_*`, `list_*`。
- 计划操作：`propose_*`, `validate_*`。
- 执行操作：`submit_*`, `log_*`, `request_*_approval`。
- 高风险应用操作：`apply_*`，默认不加入 Agent `allowed_tools`。

## 6. Ray 工具实现模式

当前 Ray 工具只有 `inspect_ray_status()`，实现位置是 `agent/tools/inspection.py`。
它是只读状态检查，返回 `is_available` 和裁剪后的 `raw_output`；不提交 Ray Job。

后续 `submit_*` 类 Ray 工具优先使用 Ray Jobs Python API 或项目已有
`train-model/ray-cats-and-dogs/job/` 稳定逻辑；不要把任意 shell 字符串交给 Agent。
规划中的 submit tool 必须由工具内部从白名单模式生成 entrypoint，例如只允许
`scripts/train.py --config <known-config> --check-config|--plan|--smoke`。

规则：

- `entrypoint` 由工具内部从白名单模式生成，不从 Agent prompt 直接拼接任意命令。
- 当前训推一体化 P0 不提交 Ray Job；默认只读取 `inspect_ray_status`。
- 后续训练阶段默认先 `--check-config`，再 `--plan`，再 `smoke`。
- `submission_id` 必须包含阶段、配置、attempt token 或明确幂等 key。
- Runtime Env release 走不可变 URI，不把 S3 密钥写进 `runtime_env`。
- Job logs 通过摘要和 artifact URI 返回，不把完整日志塞给模型。

## 7. MLflow 工具实现模式

当前 MLflow 工具只有 `inspect_mlflow_experiment()`，实现位置是
`agent/tools/inspection.py`。它通过 MLflow Tracking API 获取 experiment metadata 和
run count；不打开 `mlflow.db`，不读取服务端 MinIO 目录，也不执行 Registry 写操作。

后续 `inspect_compatible_mlflow_runs`、artifact verification、Registry proposal/apply
工具应使用 `MlflowClient`、Tracking API、Artifact API 和 Model Registry API。

规则：

- 当前工具只返回 experiment 摘要；后续 run-level 工具只返回可比较性所需摘要，完整分析报告写 Artifact。
- Run 比较必须确认 dataset/split/preprocess/metric/eval protocol 兼容。
- 训练阶段不读取 final test 指标做搜索。
- Registry 写操作拆成 `request_model_promotion_approval` 和人工审批后的 `apply_model_promotion`。

## 8. AgentDefinition 当前边界

Subagent 是提示词和工具集合，不是执行隔离边界。执行隔离由 Ray、工具策略和
`PermissionPolicy` / `PatrolActionPolicy` 承担。

当前主要定义在 `agent/agents/definitions.py`：

- `PLATFORM_INSPECTOR`：只使用 5 个 `mcp__galatea-platform__*` inspection tools。
- `EXPERIMENT_ANALYZER` 和 `DOCUMENTATION_GENERATOR`：仍只使用当前只读工具。
- `DATA_PREPARER`、`TRAINING_ORCHESTRATOR`、`MODEL_EVALUATOR`：prompt 中描述 future
  stage tools，但当前 `tools` 列表仍限制为 Stage 1 只读工具。

注意：`agent/agents/definition.py` 还保留一组早期 dataclass preset（如 `DATA_AGENT`、
`TRAINING_AGENT`、`INFERENCE_AGENT`），其中包含 `acceptEdits` 和未实现工具名。它们只能
视为 legacy placeholder，不能作为安全 runtime 默认配置；新增实现应优先使用
`AgentSDKConfig` 和 `agent/agents/definitions.py` 中的精确 MCP 工具名。

规划中的 stage Agent 在对应工具真正实现并注册前，只能写成契约，不应放入当前
`allowed_tools`：

```python
from claude_agent_sdk import AgentDefinition


training_agent = AgentDefinition(
    description="Run safe training checks and propose controlled Ray Train jobs.",
    prompt=(
        "Use MLflow validation evidence and configured objective metrics. "
        "Do not use final test metrics for tuning. Long jobs require approval."
    ),
    tools=[
        "mcp__galatea-platform__validate_training_config",
        "mcp__galatea-platform__inspect_compatible_mlflow_runs",
        "mcp__galatea-platform__submit_ray_check_config_job",
        "mcp__galatea-platform__verify_checkpoint_artifact",
    ],
    disallowedTools=["Bash", "Write", "Edit", "MultiEdit"],
    model="sonnet",
    maxTurns=12,
    permissionMode="dontAsk",
)
```

## 9. Workflow 编排模式

平台编排应由 Python 代码持有最终控制权，而不是让一个 prompt 一次性“完成全部流程”。

```python
async def run_safe_training_workflow(runtime_factory, data_input, training_input):
    data_result = await runtime_factory("data").run(build_data_prompt(data_input))
    if data_result["status"] != "success":
        return {"status": "blocked", "stage": "data", "data": data_result}

    training_input["dataset_manifest_uri"] = data_result["manifest_uri"]
    training_input["dataset_manifest_digest"] = data_result["manifest_digest"]
    training_result = await runtime_factory("training").run(build_training_prompt(training_input))
    if training_result.get("requires_approval"):
        return {"status": "needs_approval", "stage": "training", "training": training_result}

    return {"status": "completed", "data": data_result, "training": training_result}
```

规则：

- 上层代码检查 `StageResult.status` 再进入下一阶段。
- Agent 不自行跨阶段跳转到 promotion。
- 失败和审批都是正常状态，不是异常路径。
- `auto_register` 或 `auto_promote` 不作为首版功能。

## 10. Session 管理

Claude SDK 的 `SessionStore` 是 transcript mirror/resume 接口。首版可以先记录本地 transcript path；生产前再实现或复制 reference adapter，并通过 conformance 测试。

建议记录：

```json
{
  "stage_run_id": "...",
  "claude_session_id": "...",
  "project_name": "...",
  "stage": "data|training|inference",
  "ray_job_ids": [],
  "mlflow_run_ids": [],
  "stage_result_artifact": "mlflow-artifacts:/.../stage_result.json"
}
```

生产注意：

- transcript 可能含敏感内容，需要 retention 和脱敏策略。
- `platform-data/agent-sessions/` 是运行状态，不提交 Git。
- 分支探索使用 `fork_session=True`，不要污染主阶段链路。

## 11. 与早期草案相比的收敛点

早期草案中的以下写法已废弃：

- `permission_mode="acceptEdits"` 作为训练平台默认值。
- `allowed_tools=["Read", "Write", "Edit", "Bash", "mcp__*__*"]` 作为阶段默认权限。
- 用 `subprocess.run(["ray", "job", ...])` 暴露通用 CLI 拼接给 Agent。
- `auto_register` 或自动 production promotion。
- 自定义 `SessionStoreInterface`；应使用 SDK 的 `SessionStore` 协议和 conformance harness。
- 工具返回完整 MLflow dataframe、完整 Ray logs 或大量样本。

## 12. 相关文档

- 总体架构：[`current-agent-architecture.md`](current-agent-architecture.md)。
- SDK 使用规范：[`claude-sdk-development-guidelines.md`](claude-sdk-development-guidelines.md)。
- 阶段契约：[`stage-contracts-and-tools.md`](stage-contracts-and-tools.md)。
- 训推一体化 P0 实现契约：[`patrol-push-agent-contract.md`](patrol-push-agent-contract.md)。
