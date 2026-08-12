# Claude Agent SDK 开发规范

> 状态：设计规范；依据：`/data/ai/chenzhangyue/code/claude-agent-sdk-python` 本地 README 和源码。

## 1. SDK 能力映射

| SDK 能力 | 本地源码/文档依据 | Galatea 用法 |
| --- | --- | --- |
| `ClaudeSDKClient` | `src/claude_agent_sdk/client.py` | 长阶段、可交互、可中断、可检查 MCP 状态的默认 runtime。 |
| `query()` | `src/claude_agent_sdk/query.py` | 只用于一次性只读分析、CI 小任务或批处理提示。 |
| in-process SDK MCP server | `README.md`, `src/claude_agent_sdk/__init__.py` | 把 Ray/MLflow/MinIO 平台函数暴露给 Agent。 |
| `AgentDefinition` | `src/claude_agent_sdk/types.py` | 定义 Data/Training/Inference 专职 subagent。 |
| hooks | `src/claude_agent_sdk/types.py`, `README.md` | 确定性权限、日志裁剪、质量门禁和审计。 |
| `output_format` | `src/claude_agent_sdk/types.py` | 强制阶段输出 JSON schema。 |
| `session_store` / resume / fork | `src/claude_agent_sdk/types.py`, `examples/session_stores/` | 长任务恢复、审计和分支探索。 |
| `ResultMessage` | `src/claude_agent_sdk/types.py` | 读取 `structured_output`、cost、usage、permission_denials。 |
| sandbox settings | `src/claude_agent_sdk/types.py` | 仅作为 Bash 隔离辅助手段，不替代权限规则。 |

## 2. 选择 `ClaudeSDKClient` 还是 `query()`

默认使用 `ClaudeSDKClient`：

- 需要 custom tools 或 hooks。
- 需要在 Ray job 运行中轮询并继续对话。
- 需要 `interrupt()`、`stop_task()`、`get_mcp_status()` 或 `get_context_usage()`。
- 需要 session resume、fork 或 transcript mirror。

只在以下场景用 `query()`：

- 无状态的一次性总结。
- 已知所有输入、无后续交互。
- CI 中短时间只读检查。
- 不需要自定义权限回调和动态控制。

## 3. Options 基线

生产型阶段 Agent 的默认配置：

```python
from claude_agent_sdk import ClaudeAgentOptions

BASE_OPTIONS = ClaudeAgentOptions(
    cwd="/data/ai/chenzhangyue/code/galatea",
    strict_mcp_config=True,
    setting_sources=[],
    permission_mode="dontAsk",
    disallowed_tools=["Bash", "Write", "Edit", "MultiEdit"],
    max_turns=12,
    max_budget_usd=0.20,
    include_hook_events=True,
)
```

按场景放开：

| 场景 | 可调整项 | 注意 |
| --- | --- | --- |
| 只读仓库分析 | 允许 `Read`, `Grep`, `Glob` | 不允许 `Bash`。 |
| 代码维护 | 允许 `Read`, `Grep`, `Glob`, `Edit` | 必须启用 file checkpointing，必要时 human approval。 |
| 数据阶段执行 | 只允许 `mcp__galatea__data_*` 和 `mcp__galatea__ray_job_*` | 不允许直接 Bash 或 MinIO 底层写。 |
| 训练阶段 smoke | 允许小预算 `submit_ray_training_job` | 长训练需要 approval。 |
| production promotion | 默认不允许 | 只生成 approval request。 |

## 4. allowed_tools 不是安全边界

Claude SDK README 明确说明：`allowed_tools` 是自动批准列表，不会从 Claude 工具集中移除其他工具。要阻止工具必须使用 `disallowed_tools`，或者用 hooks/can_use_tool 做动态拒绝。

因此 Galatea 规范：

- 永远不要只设置 `allowed_tools` 而不设置 `disallowed_tools`。
- 数据、训练、推理阶段默认禁止 `Bash`, `Write`, `Edit`, `MultiEdit`。
- 对 MCP 工具使用精确名称，例如 `mcp__galatea__submit_ray_data_job`，不要用过宽通配。
- 不使用 `permission_mode="bypassPermissions"` 运行平台任务。
- 服务端自动化优先 `permission_mode="dontAsk"`，让未预批准动作快速失败。

## 5. can_use_tool 与 hooks 的边界

`can_use_tool` 只在权限规则进入 ask 时触发；已经被 `allowed_tools`、permission mode 或设置文件允许的工具不会进入该回调。若要观察或治理每次工具调用，使用 `PreToolUse` hook。

推荐策略：

| 机制 | 用途 | 不适合 |
| --- | --- | --- |
| `allowed_tools` | 预批准明确安全的工具。 | 隐藏或禁用工具。 |
| `disallowed_tools` | 强制禁用通用危险工具。 | 细粒度参数校验。 |
| `can_use_tool` | ask 场景下动态允许/拒绝/改写输入。 | 全量审计。 |
| `PreToolUse` hook | 每次调用前做确定性策略、预算、路径、资源校验。 | 复杂耗时计算。 |
| `PostToolUse` hook | 裁剪输出、追加上下文、标准化错误。 | 改变外部副作用。 |
| `Stop` hook | 检查阶段是否完整、缺少证据时阻止结束。 | 代替业务工具。 |

## 6. Hooks 规范

推荐 hooks：

```python
from claude_agent_sdk import HookMatcher

HOOKS = {
    "PreToolUse": [
        HookMatcher(matcher="Bash|Write|Edit|MultiEdit", hooks=[deny_builtin_mutation]),
        HookMatcher(matcher="mcp__galatea__*", hooks=[validate_platform_tool_call]),
    ],
    "PostToolUse": [
        HookMatcher(matcher="mcp__galatea__*", hooks=[summarize_large_tool_output]),
    ],
    "PostToolUseFailure": [
        HookMatcher(matcher="mcp__galatea__*", hooks=[classify_tool_failure]),
    ],
    "Stop": [
        HookMatcher(hooks=[validate_stage_completion]),
    ],
    "SubagentStart": [
        HookMatcher(hooks=[record_subagent_start]),
    ],
    "SubagentStop": [
        HookMatcher(hooks=[record_subagent_stop]),
    ],
}
```

实现要求：

- hook 必须短小、确定性、无重副作用。
- 同一事件的多个 hook 可能并发执行，不要依赖顺序。
- `PreToolUse` 可以返回 `permissionDecision: "deny"` 阻止危险动作。
- `PostToolUse` 可以用 `updatedToolOutput`/`updatedMCPToolOutput` 裁剪大输出。
- `Stop` hook 用于检查是否缺少 `stage_result`、`ray_job_id`、`manifest_digest` 等关键证据。
- 对 subagent 产生的工具调用，依赖 hook input 中的 `agent_id` / `agent_type` 做归因。

## 7. 工具开发规范

Claude SDK 的 `@tool` 要求 handler 是 async，并返回 MCP content 结构。Galatea 约定所有工具：

- 输入 schema 必须是 JSON schema 或 TypedDict/Pydantic 转换后的 schema。
- 输出必须小而结构化；大日志和报告通过 URI 引用。
- 成功返回 `content` 中的 JSON 文本或摘要文本。
- 业务失败返回 `is_error: True`，并包含可恢复建议。
- 不在工具里吞掉异常；将错误分类后返回给 Agent。
- 工具必须幂等；重复调用不能覆盖已有成功结果。

示例：

```python
import json
from typing_extensions import TypedDict
from claude_agent_sdk import tool

class SubmitRayDataJobInput(TypedDict):
    project_name: str
    plan_uri: str
    submission_id: str
    max_runtime_seconds: int

@tool(
    "submit_ray_data_job",
    "Submit a bounded Ray Data job from an approved immutable plan URI.",
    SubmitRayDataJobInput,
)
async def submit_ray_data_job(args: SubmitRayDataJobInput):
    result = await submit_bounded_ray_job(args)
    return {
        "content": [
            {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
        ]
    }
```

## 8. in-process MCP server 规范

首版优先使用 `create_sdk_mcp_server`：

```python
from claude_agent_sdk import create_sdk_mcp_server

server = create_sdk_mcp_server(
    name="galatea",
    version="0.1.0",
    tools=[
        inspect_dataset,
        submit_ray_data_job,
        get_ray_job_status,
        validate_dataset_output,
    ],
)
```

适合 in-process 的工具：

- MLflow Tracking API 只读查询。
- Ray Jobs API 提交/查询。
- Manifest 读写和摘要计算。
- 项目配置校验。
- Artifact 回读验证。

考虑外部 MCP server 的场景：

- 工具有独立部署和权限边界。
- 需要跨语言服务。
- 需要与 Web 服务或长连接隔离。
- 工具依赖很重，不应拖入 Agent runtime 进程。

## 9. 结构化输出规范

每个阶段都必须设置 `output_format`，并从 `ResultMessage.structured_output` 读取结果。不要从自然语言里解析关键字段。

基础字段：

```json
{
  "stage": "data|training|inference",
  "status": "success|failed|needs_approval|skipped",
  "stage_run_id": "...",
  "evidence": [],
  "warnings": [],
  "errors": [],
  "requires_approval": false,
  "next_action": "..."
}
```

Runtime 收到 `ResultMessage` 后必须校验：

- `is_error` 是否为 false。
- `structured_output` 是否存在且符合 schema。
- `terminal_reason` 是否为 completed。
- `permission_denials` 是否为空或已解释。
- `total_cost_usd` 是否在预算内。

## 10. Session 和恢复规范

长阶段必须持久化 session：

- 每次阶段运行生成固定 `session_id`。
- transcript mirror 到 `session_store`，或记录本地 transcript path。
- Ray job id、MLflow run id、stage result 与 session id 交叉记录。
- 探索性分支使用 `fork_session=True`，不污染主链路。
- `session_store` adapter 上生产前需要做 conformance、load test 和 retention 设计。

参考实现位于 Claude SDK 的 `examples/session_stores/`，但 README 明确说明这些是 reference adapters，不是可直接生产使用的维护代码。

## 11. 文件修改规范

普通数据/训练/推理阶段不应该改源码。如果需要代码维护型 Agent：

- 单独使用 `CodeMaintenanceAgent`，不要复用 `DataAgent` 权限。
- 开启 `enable_file_checkpointing=True`。
- 设置 `extra_args={"replay-user-messages": None}` 以便必要时 rewind。
- 只允许在 workspace 内修改。
- 修改前后运行窄测试。
- 不改动用户未授权的 dirty worktree 文件。

## 12. Cost、上下文和中断

- 设置 `max_budget_usd` 和 `max_turns`。
- 对长流程使用 `task_budget` 或阶段级预算。
- 用 `get_context_usage()` 监控 MCP tools、memory files、agents 的上下文占用。
- Ray job 运行中不要让 Agent 长时间阻塞；工具返回 job handle，Agent 轮询状态。
- 对挂起或误提交的任务，Runtime 需要暴露 `interrupt()` 和 `stop_task()`。

## 13. 安全禁令

- 禁止 `permission_mode="bypassPermissions"` 跑生产平台动作。
- 禁止给训练阶段开放裸 `Bash`。
- 禁止工具返回完整大日志、数据样本或敏感标签。
- 禁止 Agent 读取 `platform-data/mlflow/mlflow.db`。
- 禁止在 prompt、工具输出、Artifact 或日志里暴露 MinIO 长期密钥。
- 禁止未审批修改 MLflow Registry production/champion alias。
