# Galatea Python Agent 实现蓝图

> 状态：统一后的实现蓝图。本文合并并收敛了早期 `python-agent-architecture.md` 草案中的可用内容，删除了与当前平台治理冲突的示例，例如默认开放 `Bash`、`acceptEdits`、自动注册模型和直接用通用 shell 提交 Ray Job。

## 1. 本文定位

本文回答“如何在 Galatea 中用 Python 实现 Agent Runtime”。更高层的阶段设计见 [`current-agent-architecture.md`](current-agent-architecture.md)，SDK 开发规范见 [`claude-sdk-development-guidelines.md`](claude-sdk-development-guidelines.md)，阶段输入输出契约见 [`stage-contracts-and-tools.md`](stage-contracts-and-tools.md)。

目标实现不是复刻 Claude Code 的交互式 REPL，而是构建一个 Python-native、工作流驱动、平台受控的 Agent 层：

```text
ClaudeSDKClient
  -> GalateaAgentRuntime
  -> DataAgent / TrainingAgent / InferenceAgent
  -> SDK MCP tools
  -> Ray / MLflow / MinIO APIs
  -> StageResult JSON
```

## 2. 从 Claude Code 到 Galatea Python 的映射

| Claude Code 概念 | Python SDK 对应 | Galatea 实现 |
| --- | --- | --- |
| Query engine | `ClaudeSDKClient` | `runtime/client.py` 封装生命周期、消息收集和结果校验。 |
| Tool system | `@tool` + `create_sdk_mcp_server` | `tools/` 中的受控平台工具。 |
| Services | Python service adapters | `services` 或 `tools` 内部封装 MLflow/Ray/Artifact API。 |
| Sub-agent | `AgentDefinition` | Data/Training/Inference 专职提示词和窄工具集。 |
| State/session | SDK `SessionStore`, `resume`, `fork_session` | 阶段 session、transcript、Ray job id、MLflow run id 交叉记录。 |
| Permission loop | `permission_mode`, `allowed_tools`, `disallowed_tools`, hooks | 默认 deny，精确 allow，hooks 做确定性治理。 |
| Workflow | Python code + Ray | 由 `PlatformCoordinator` 串联阶段，Ray 负责真实执行。 |

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
│   └── executor.py              # deterministic tool executor for tests and non-LLM flows
├── schemas/
│   ├── common.py                # StageStatus, ArtifactRef, StageEvidence, ApprovalRequest
│   └── inspection.py            # current inspection result schemas
├── state/
│   ├── store.py                 # SessionStore, MemorySessionStore, SessionManager
│   ├── experiment.py            # ExperimentState and file-backed manager
│   └── persistence.py           # JSON persistence helpers
├── workflows/
│   ├── state_machine.py         # current linear workflow state machine
│   └── orchestrator.py          # workflow orchestration skeleton
├── policies/
│   ├── permission.py
│   ├── budget.py
│   └── quality.py
├── hooks/
│   ├── builtin.py
│   ├── registry.py
│   └── types.py
└── agents/
    ├── definition.py
    ├── definitions.py
    └── registry.py
```

建议新增 Patrol/Push 能力时使用：

```text
agent/
├── schemas/patrol.py            # EvidenceRecord, Finding, Recommendation, PatrolMemory
├── state/patrol.py              # PatrolSessionStore and file-backed implementation
├── workflows/patrol.py          # PatrolRunStateMachine
├── policies/patrol.py           # dedupe, cooldown, escalation, action permission
├── tools/patrol_output.py       # summary/evidence/raw_ref envelope helpers
└── patrol/
    ├── runner.py                # deterministic run_once()
    ├── compaction.py            # PatrolMemory compaction and fidelity checks
    └── clients.py               # Ray/MLflow/Artifact protocols and fake clients
```

说明：

- `tools/` 可以直接包含 service adapter，首版不必额外拆 `services/`，避免抽象过早。
- `schemas/` 是 Agent 与平台代码的稳定契约，prompt 不应成为唯一接口。
- `policies/` 放不可交给 LLM 自由判断的规则，例如预算、promotion 审批和 destructive action 禁令。
- Patrol/Push 的长期状态应放在 `state/patrol.py` 和 `patrol/` core 中，不要放在 Claude transcript。

## 4. Runtime 基线

`GalateaAgentRuntime` 是唯一直接创建 `ClaudeSDKClient` 的地方。阶段 Agent 不应分散创建 SDK client。

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage


@dataclass(frozen=True)
class AgentRunConfig:
    project_root: Path
    stage_name: str
    stage_run_id: str
    allowed_tools: list[str]
    output_format: dict[str, Any]
    max_turns: int = 12
    max_budget_usd: float = 0.20


class GalateaAgentRuntime:
    def __init__(self, config: AgentRunConfig, mcp_server: dict[str, Any], hooks: dict[str, Any]):
        self.config = config
        self.options = ClaudeAgentOptions(
            cwd=str(config.project_root),
            mcp_servers={"galatea-platform": mcp_server},
            strict_mcp_config=True,
            setting_sources=[],
            permission_mode="dontAsk",
            allowed_tools=config.allowed_tools,
            disallowed_tools=["Bash", "Write", "Edit", "MultiEdit"],
            hooks=hooks,
            include_hook_events=True,
            max_turns=config.max_turns,
            max_budget_usd=config.max_budget_usd,
            output_format=config.output_format,
        )

    async def run(self, prompt: str) -> dict[str, Any]:
        async with ClaudeSDKClient(options=self.options) as client:
            await client.query(prompt, session_id=self.config.stage_run_id)
            result: ResultMessage | None = None
            async for message in client.receive_response():
                if isinstance(message, ResultMessage):
                    result = message

        if result is None:
            raise RuntimeError("Claude run ended without ResultMessage")
        if result.is_error:
            raise RuntimeError(f"Claude run failed: {result.errors or result.subtype}")
        if result.structured_output is None:
            raise RuntimeError("Claude run did not return structured output")
        return result.structured_output
```

实现注意：

- 上面是结构示例，不是最终完整实现。
- `allowed_tools` 必须由阶段配置传入，不能在 runtime 中放宽成全局通配。
- `ResultMessage` 的 `total_cost_usd`、`permission_denials`、`terminal_reason` 应记录到阶段审计日志。
- 如果需要 `can_use_tool`，必须使用 streaming-mode prompt；首版优先 hooks + `dontAsk`。

## 5. SDK MCP 工具服务器

首版使用 in-process SDK MCP server，减少独立进程和 IPC 复杂度。

```python
from claude_agent_sdk import create_sdk_mcp_server

from .project import inspect_project_structure
from .ray_jobs import get_ray_job_status, submit_ray_check_config_job
from .validation import validate_training_config


def create_galatea_mcp_server():
    return create_sdk_mcp_server(
        name="galatea-platform",
        version="0.1.0",
        tools=[
            inspect_project_structure,
            validate_training_config,
            submit_ray_check_config_job,
            get_ray_job_status,
        ],
    )
```

工具命名建议：

- 读操作：`inspect_*`, `get_*`, `list_*`。
- 计划操作：`propose_*`, `validate_*`。
- 执行操作：`submit_*`, `log_*`, `request_*_approval`。
- 高风险应用操作：`apply_*`，默认不加入 Agent `allowed_tools`。

## 6. Ray 工具实现模式

优先使用 Ray Jobs Python API 或项目已有 `job/cd.py` 的稳定逻辑；不要把任意 shell 字符串交给 Agent。

```python
import json
from typing_extensions import TypedDict

from claude_agent_sdk import tool
from ray.job_submission import JobSubmissionClient


class SubmitRayCheckConfigInput(TypedDict):
    dashboard_url: str
    submission_id: str
    runtime_env: dict
    config_path: str


@tool(
    "submit_ray_check_config_job",
    "Submit a Ray Job that only runs project training config validation.",
    SubmitRayCheckConfigInput,
)
async def submit_ray_check_config_job(args: SubmitRayCheckConfigInput):
    client = JobSubmissionClient(args["dashboard_url"])
    entrypoint = f"python scripts/train.py --config {args['config_path']} --check-config"
    job_id = client.submit_job(
        entrypoint=entrypoint,
        runtime_env=args["runtime_env"],
        submission_id=args["submission_id"],
    )
    payload = {
        "job_id": job_id,
        "submission_id": args["submission_id"],
        "entrypoint": entrypoint,
        "status": str(client.get_job_status(job_id)),
    }
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}
```

规则：

- `entrypoint` 由工具内部从白名单模式生成，不从 Agent prompt 直接拼接任意命令。
- 默认先 `--check-config`，再 `--plan`，再 `smoke`。
- `submission_id` 必须包含阶段、配置、attempt token 或明确幂等 key。
- Runtime Env release 走不可变 URI，不把 S3 密钥写进 `runtime_env`。
- Job logs 通过摘要和 artifact URI 返回，不把完整日志塞给模型。

## 7. MLflow 工具实现模式

MLflow 工具使用 `MlflowClient` 和 Artifact API。不要打开 `mlflow.db`，不要读取服务端 MinIO 目录。

```python
import json
from typing_extensions import TypedDict

from claude_agent_sdk import tool
from mlflow.tracking import MlflowClient


class InspectRunsInput(TypedDict):
    tracking_uri: str
    experiment_name: str
    objective_metric: str
    max_results: int


@tool(
    "inspect_compatible_mlflow_runs",
    "Inspect MLflow runs and return a small compatibility-aware summary.",
    InspectRunsInput,
)
async def inspect_compatible_mlflow_runs(args: InspectRunsInput):
    client = MlflowClient(tracking_uri=args["tracking_uri"])
    experiment = client.get_experiment_by_name(args["experiment_name"])
    if experiment is None:
        return {"content": [{"type": "text", "text": json.dumps({"runs": [], "warnings": ["experiment not found"]})}]}

    runs = client.search_runs(
        experiment_ids=[experiment.experiment_id],
        max_results=args["max_results"],
        order_by=["attributes.start_time DESC"],
    )
    summary = []
    for run in runs:
        summary.append(
            {
                "run_id": run.info.run_id,
                "status": run.info.status,
                "metrics": {args["objective_metric"]: run.data.metrics.get(args["objective_metric"])},
                "dataset_digest": run.data.tags.get("dataset.digest") or run.data.tags.get("dataset.content_sha256"),
                "split_digest": run.data.tags.get("dataset.split_sha256") or run.data.tags.get("data.split_sha256"),
            }
        )
    return {"content": [{"type": "text", "text": json.dumps({"runs": summary})}]}
```

规则：

- 工具只返回可比较性所需摘要，完整分析报告写 Artifact。
- Run 比较必须确认 dataset/split/preprocess/metric/eval protocol 兼容。
- 训练阶段不读取 final test 指标做搜索。
- Registry 写操作拆成 `request_model_promotion_approval` 和人工审批后的 `apply_model_promotion`。

## 8. AgentDefinition 示例

Subagent 是提示词和工具集合，不是执行隔离边界。执行隔离由 Ray 和工具策略承担。

```python
from claude_agent_sdk import AgentDefinition


def create_stage_agents() -> dict[str, AgentDefinition]:
    return {
        "data-agent": AgentDefinition(
            description="Prepare and validate datasets through controlled Ray Data tools.",
            prompt=(
                "You are the data stage agent. Produce a DataStageResult. "
                "Use only approved Galatea MCP tools. Do not run Bash. "
                "Never silently reshuffle an existing evaluation population."
            ),
            tools=[
                "mcp__galatea-platform__inspect_dataset_source",
                "mcp__galatea-platform__compute_source_manifest",
                "mcp__galatea-platform__submit_ray_data_job",
                "mcp__galatea-platform__get_ray_job_status",
                "mcp__galatea-platform__validate_dataset_output",
            ],
            disallowedTools=["Bash", "Write", "Edit", "MultiEdit"],
            model="sonnet",
            maxTurns=10,
            permissionMode="dontAsk",
        ),
        "training-agent": AgentDefinition(
            description="Run safe training checks and propose controlled Ray Train jobs.",
            prompt=(
                "Use MLflow validation evidence and configured objective metrics. "
                "Do not use final test metrics for tuning. Long jobs require approval."
            ),
            tools=[
                "mcp__galatea-platform__validate_training_config",
                "mcp__galatea-platform__inspect_compatible_mlflow_runs",
                "mcp__galatea-platform__submit_ray_check_config_job",
                "mcp__galatea-platform__submit_ray_training_job",
                "mcp__galatea-platform__verify_checkpoint_artifact",
            ],
            disallowedTools=["Bash", "Write", "Edit", "MultiEdit"],
            model="sonnet",
            maxTurns=12,
            permissionMode="dontAsk",
        ),
    }
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
- 巡推 P0 实现契约：[`patrol-push-agent-contract.md`](patrol-push-agent-contract.md)。
