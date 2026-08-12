# SDK 架构对比与映射

> 状态：统一后的对比参考。本文保留早期 SDK comparison 草案中的概念映射，但去掉了与 Galatea 治理冲突或未经验证的性能数字、过宽权限和自动上线示例。实现细节以 [`python-agent-architecture.md`](python-agent-architecture.md) 和 [`claude-sdk-development-guidelines.md`](claude-sdk-development-guidelines.md) 为准。

## 1. 总览

```text
Claude Code / CLI
  Entry -> REPL -> Query Engine -> Built-in Tools -> Services -> Session State

Galatea Python Agent
  CLI/API/Notebook -> Workflow -> ClaudeSDKClient -> SDK MCP Tools -> Ray/MLflow -> Stage State
```

两者共享“模型 + 工具 + 会话 + 权限”的核心模式，但优化目标不同：

| 维度 | Claude Code | Galatea Agent |
| --- | --- | --- |
| 主要场景 | 交互式软件开发。 | 受控 ML 工作流编排。 |
| 用户界面 | Terminal/IDE/Claude Code surfaces。 | Python CLI、Notebook、未来 API。 |
| 工具集合 | 通用代码工具和 MCP。 | Ray、MLflow、Artifact、Validation 等平台工具。 |
| 执行边界 | CLI agent loop。 | Python workflow + Ray jobs。 |
| 状态权威 | Claude session/transcript。 | StageResult + Ray job id + MLflow run/artifact。 |
| 风险治理 | 用户交互权限和 hooks。 | 默认 deny、阶段工具白名单、approval gates。 |

## 2. 组件映射

| Claude Code/TypeScript 概念 | Python SDK 概念 | Galatea 设计 |
| --- | --- | --- |
| `main.tsx` / REPL loop | Python workflow function | `PlatformCoordinator` 串联 data/training/inference。 |
| Query engine | `ClaudeSDKClient`, `query()` | `GalateaAgentRuntime`，默认用 `ClaudeSDKClient`。 |
| Built-in tools | Built-ins + MCP tools | 阶段默认禁用 `Bash/Write/Edit/MultiEdit`，只放精确 MCP 工具。 |
| Tool classes | `@tool` decorator | `agent/src/galatea_agent/tools/*.py`。 |
| Agent tool/subagent | `AgentDefinition` | 专职 DataAgent/TrainingAgent/InferenceAgent。 |
| Permission dialog | `permission_mode`, `can_use_tool`, hooks | `dontAsk` + `disallowed_tools` + `PreToolUse`。 |
| Session files | `SessionStore`, `resume`, `fork_session` | 阶段 transcript、resume、审计关联。 |
| Telemetry/logging | SDK messages/hooks | MLflow stage reports + tool audit + transcript store。 |
| Workflow tool | Python code/Ray | Ray Data/Ray Train/Ray Jobs 是执行层。 |

## 3. 工具系统对比

### TypeScript 风格

```typescript
class FileReadTool extends Tool {
  name = "Read"
  schema = { /* JSON Schema */ }
  async execute(params, context) {
    return { content: [...] }
  }
}
```

### Python SDK 风格

```python
from claude_agent_sdk import tool

@tool(
    "inspect_dataset_source",
    "Inspect dataset metadata without loading large samples.",
    {
        "type": "object",
        "properties": {"source_uri": {"type": "string"}},
        "required": ["source_uri"],
    },
)
async def inspect_dataset_source(args):
    return {"content": [{"type": "text", "text": "{...small json summary...}"}]}
```

差异：

- TypeScript 常见是类式工具；Python SDK 使用 decorator 和 in-process MCP server。
- Python 工具可以直接调用本进程内的 MLflow/Ray client，但必须保持小输出和幂等。
- Galatea 工具不暴露任意 shell；Ray 执行由白名单工具内部构造。

## 4. Agent 定义对比

### Python `AgentDefinition`

```python
from claude_agent_sdk import AgentDefinition

training_agent = AgentDefinition(
    description="Run safe training checks and propose controlled jobs.",
    prompt="Use validation evidence only. Long training requires approval.",
    tools=[
        "mcp__galatea__validate_training_config",
        "mcp__galatea__submit_ray_check_config_job",
        "mcp__galatea__get_ray_job_status",
    ],
    disallowedTools=["Bash", "Write", "Edit", "MultiEdit"],
    model="sonnet",
    permissionMode="dontAsk",
)
```

注意：

- Subagent 只是上下文、提示词和工具集合，不是资源隔离边界。
- 并行或长任务由 Ray 负责，不由 Claude subagent 长时间占用上下文。
- 每个 subagent 的工具集应比 coordinator 更窄。

## 5. 查询执行模式

| 模式 | 适合 | Galatea 默认 |
| --- | --- | --- |
| `query()` | 一次性、无状态、所有输入已知的短任务。 | 只读分析或 CI 短检查。 |
| `ClaudeSDKClient` | 多轮、工具调用、hooks、MCP 状态、session/resume。 | 阶段 Agent 默认。 |
| Python workflow | 跨阶段控制、审批、失败处理。 | PlatformCoordinator 默认。 |

推荐模式：

```python
async with ClaudeSDKClient(options=options) as client:
    await client.query(prompt, session_id=stage_run_id)
    async for message in client.receive_response():
        collect_and_validate(message)
```

## 6. 权限系统对比

早期草案把 `acceptEdits`、`Bash` 和通配 MCP 工具作为默认值，这不适合 Galatea。统一规范改为：

```python
ClaudeAgentOptions(
    permission_mode="dontAsk",
    allowed_tools=[
        "mcp__galatea__inspect_dataset_source",
        "mcp__galatea__submit_ray_data_job",
        "mcp__galatea__get_ray_job_status",
    ],
    disallowed_tools=["Bash", "Write", "Edit", "MultiEdit"],
)
```

关键差异：

- `allowed_tools` 是自动批准列表，不是隐藏列表。
- `disallowed_tools` 才用于禁用工具。
- `can_use_tool` 只在 ask 权限路径触发，不能代替全量审计。
- `PreToolUse` hook 用于每次工具调用前的确定性策略。

## 7. 状态管理对比

| 状态 | Claude session | Galatea stage state |
| --- | --- | --- |
| 对话历史 | transcript/session store | 记录到 agent session store。 |
| 工具执行 | message stream / hook events | tool audit + stage evidence。 |
| 分布式任务 | 不直接管理 | Ray job id / submission id。 |
| 训练证据 | 不作为权威 | MLflow run id / artifacts / metrics。 |
| 产物身份 | 不作为权威 | manifest digest / artifact digest。 |

统一原则：Claude transcript 解释“Agent 为什么这么做”；MLflow/Ray/Artifact 证明“平台实际做了什么”。

## 8. 错误处理与重试

Claude Code 和 Python SDK 都能承载多轮修正，但 Galatea 的重试必须由平台策略约束：

- Ray job 失败后，Agent 先调用 `get_ray_job_status` 和日志摘要工具。
- 可恢复失败可以提交新的 attempt id。
- 不使用 `--force` 作为普通重试。
- Artifact 或 lineage 验证失败时，停止后续训练/promotion。
- 预算耗尽、权限拒绝和 `needs_approval` 都是正常状态，不应该被自动绕过。

## 9. 使用场景适配

| 使用场景 | 推荐实现 |
| --- | --- |
| 快速代码修改 | Claude Code 或单独 `CodeMaintenanceAgent`。 |
| 数据处理阶段 | `DataAgent` + Ray Data tools。 |
| 训练配置检查 | `TrainingAgent` + `--check-config` Ray Job。 |
| 历史实验分析 | MLflow tools + `mlflow-optimize-models` 策略。 |
| 批量推理 | `InferenceAgent` + Ray Data batch inference。 |
| 模型上线 | Approval workflow + 人工 apply，不默认交给 Agent。 |

## 10. 与其他文档的关系

- [`claude-code-architecture.md`](claude-code-architecture.md)：底层 Claude Code 架构背景。
- [`python-agent-architecture.md`](python-agent-architecture.md)：Galatea Python 实现蓝图。
- [`claude-sdk-development-guidelines.md`](claude-sdk-development-guidelines.md)：SDK 使用规范和安全边界。
- [`stage-contracts-and-tools.md`](stage-contracts-and-tools.md)：阶段输入输出和工具契约。
- [`implementation-roadmap.md`](implementation-roadmap.md)：落地路线。
