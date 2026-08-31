# Galatea Agent SDK / Claude Code 设计对齐审核报告

审核日期：2026-08-14  
审核路径：`/data/ai/chenzhangyue/code/galatea/agent`  
报告文件：`agent/summary/current-agent-sdk-claude-alignment-audit.md`  
整改状态：已按推荐路线完成代码、测试与文档收敛

## 1. 总体结论

结论：`agent/` 工程已经完成本报告建议的 SDK-first 收敛。正式运行链路以
`GalateaSDKRuntime` / `GalateaRuntime` 为唯一入口；Skills discovery/authorization、permission
modes、approval handler、hook schema、MCP lifecycle、session transcript 等均由 SDK/Claude Code
抽象承担。Galatea 只保留平台特定的 MCP adapters、allow/deny 规则、审计证据、质量门控和状态模型。

原审核发现保留在第 5 节作为整改前基线；逐项闭环证据和验证结果见第 9 节。

判定矩阵：

| 审核项 | 结论 | 说明 |
| --- | --- | --- |
| SDK 基座依赖 | 通过 | `agent/requirements.txt` 已固定 `claude-agent-sdk==0.2.136`，核心代码直接使用 SDK 类型和 client。 |
| Claude Code 设计对齐 | 通过 | 生命周期、工具、权限、hooks、subagent、skills、MCP 均直接映射 SDK/Claude Code 抽象。 |
| 少做自定义功能 | 通过 | Skill registry 仅展示/预检；hooks 使用 SDK typed dict；direct executor 与 workflow 均已限定职责。 |
| 安全默认值 | 通过 | 默认只启用只读 MCP；bypass 需显式提权；full helper 使用 `unsafe_` 命名。 |
| 当前是否需要重写 | 不建议重写 | 建议做减法和边界加固，而不是推倒重来。 |

## 2. 审核基准与方法

本次只做静态与本地单元测试审核，未调用真实 Claude API，未进行网络访问。

对照基准：

- 已安装运行时：`claude-agent-sdk==0.2.136`、`mcp==1.26.0`、`pydantic==2.12.3`、`PyYAML==6.0.3`。
- 已安装 SDK 源码：`/data/conda/envs/attend-ray-py312/lib/python3.12/site-packages/claude_agent_sdk/`。
- bundled Claude Code CLI：`2.1.228 (Claude Code)`。
- 本机 Claude Agent SDK clone：`/data/ai/chenzhangyue/code/claude-agent-sdk-python`，Git `e3320df`，`pyproject.toml` 标注 `0.2.135`。
- 本机 Claude Code source clone：`/data/ai/chenzhangyue/code/claude-code-source-code`，Git `2ca5dda`，`package.json` 为 `@anthropic-ai/claude-code-source@2.1.88`。

注意：本机 Claude Code source clone `2.1.88` 明显落后于 bundled CLI `2.1.228`，只能作为设计参考；具体 runtime 行为应以已安装 SDK 源码、SDK 类型和 bundled CLI 实测为准。

验证命令：

```bash
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover -s agent/test -p 'test_*.py'
```

整改前基线结果：`Ran 32 tests ... OK`；整改后结果见第 9 节。

## 3. 整改前已经对齐 SDK 的基线证据

本节行号保留原审核时快照；整改后实现位置以第 9 节为准。

| SDK 能力 | 当前实现证据 | 评价 |
| --- | --- | --- |
| 依赖声明 | `agent/requirements.txt:3` 固定 `claude-agent-sdk==0.2.136` | 已可复现安装 agent 基座依赖。 |
| SDK client 生命周期 | `agent/core/sdk.py:237` 创建 `ClaudeSDKClient` 并在 async context 中管理 | 对齐 SDK-native client lifecycle。 |
| SDK options 汇总 | `agent/core/sdk.py:247` 到 `agent/core/sdk.py:306` 构造 `ClaudeAgentOptions` | 关键开关集中，正式入口一致。 |
| in-process MCP | `agent/tools/server.py:10` 使用 `create_sdk_mcp_server` 和 `tool`；`agent/tools/server.py:116` 创建 server | 对齐 SDK MCP，不需要外部 MCP 进程。 |
| SDK AgentDefinition | `agent/agents/definitions.py:20` 直接使用 `claude_agent_sdk.AgentDefinition` | 未再定义同名自研 AgentDefinition。 |
| SDK hook adapter | `agent/hooks/registry.py:7` 使用 SDK `HookMatcher`；`agent/core/sdk.py:291` 注入 `hooks` | 对齐 SDK hooks，但本地适配层可进一步变薄。 |
| SDK permission result | `agent/policies/permission.py:10` 使用 `PermissionResultAllow` / `PermissionResultDeny` | 与 SDK `can_use_tool` 返回结构兼容。 |
| `can_use_tool` shadow 处理 | `agent/core/sdk.py:279` 在 `dontAsk` 下不传 `can_use_tool` | 避免 allowed tools shadow callback 的 SDK warning，方向正确。 |
| structured output | `agent/core/sdk.py:249` 到 `agent/core/sdk.py:251` 转为 SDK `output_format`；`agent/core/sdk.py:351` 读取 `structured_output` | 使用 SDK 结构化输出能力。 |
| session store 边界 | `agent/core/sdk.py:187` 校验 SDK `append/load` 协议；`agent/state/store.py:1` 明确应用状态不是 SDK transcript store | 修复了“业务 state store 冒充 SDK SessionStore”的风险。 |
| context / MCP 控制 | `agent/core/sdk.py:379`、`agent/core/sdk.py:413` 暴露 SDK `get_context_usage()` / `get_mcp_status()` | 对齐 SDK runtime control。 |
| 正式 CLI 入口 | `agent/scripts/inspect_platform.py:49` 通过 `GalateaRuntime` 调用 | 已不再绕过统一 runtime。 |

## 4. 整改前与 Claude Code 源码设计的对应关系

Claude Code 源码中可观察到的核心设计点包括：

- `ToolUseContext` 汇总工具、MCP、agentDefinitions、权限上下文、agent_id/agent_type 等运行上下文。
- `Tool` 抽象包含 `validateInput`、`checkPermissions`、`isReadOnly`、`isDestructive`、`mcpInfo`、hook matcher 等能力。
- 权限模式包括 `acceptEdits`、`bypassPermissions`、`default`、`dontAsk`、`plan`，并通过 allow/deny/ask 规则、hooks 和 canUseTool 串联。
- hooks 使用 `PreToolUse`、`PostToolUse`、`PostToolUseFailure`、`SessionStart`、`SubagentStart`、`SubagentStop` 等事件，并把 subagent attribution 放进事件上下文。
- Agent/Skill 都是运行时能力：AgentDefinition 由 SDK/Claude Code 加载，Skill 通过 Skill tool、plugins、settings sources 发现。

当前 `agent/` 的对应情况：

| Claude Code 设计点 | Galatea 对应 | 评价 |
| --- | --- | --- |
| 工具通过统一 tool/MCP 层暴露 | `agent/tools/server.py` 注册 5 个只读 MCP tools | 对齐；平台工具应继续走 MCP。 |
| 权限使用 rule + mode + hook | `PermissionPolicy` + `PreToolUse` permission hook + SDK `disallowed_tools` | 基本对齐；本地 mode 语义需要收窄。 |
| hooks 是一等运行时能力 | `HookManager.to_sdk_hooks()` 转 SDK `HookMatcher` | 对齐；本地类型可更薄。 |
| subagent 用 AgentDefinition | `agent/agents/definitions.py` 和 `agent/agents/registry.py` | 对齐；`.claude/agents` 已无 legacy prompt 文件。 |
| Skill/plugin 由 SDK 启用 | `AgentSDKConfig.skills`、`skill_plugins`、`plugins` 注入 SDK options | 方向正确；本地 Skill discovery 重复 SDK 逻辑。 |
| session transcript 与业务状态分离 | SDK `SessionStore` 与 `AgentStateStore` 分开 | 对齐，且有测试保护。 |
| 不在 prompt 里实现权限/能力 | 权限、工具、hooks 都有代码路径 | 对齐。 |

## 5. 整改前主要问题与建议

本节记录 2026-08-14 整改前的静态基线，便于追溯问题来源；其中 P1/P2/P3 均已按第 9 节
所列证据闭环，不再代表当前实现状态。

### P1：Skills 发现与授权仍重复 SDK，且 legacy `.codex/skills` 可能与 SDK 实际发现不一致

证据：

- `.claude/skills` 当前不存在；`.claude-plugin/plugin.json` 指向 `./.codex/skills`。
- `agent/skills/registry.py:98` 到 `agent/skills/registry.py:172` 自行发现 `.claude/skills`、`.codex/skills` 和 plugin skills，并生成 `Skill(...)` allowed rules。
- `agent/skills/registry.py:245` 到 `agent/skills/registry.py:254` 生成 `Skill` / `Skill(name)` 规则。
- `agent/core/sdk.py:261` 到 `agent/core/sdk.py:264` 把这些 skill rules 再加入 `allowed_tools`。
- `agent/core/sdk.py:471` 到 `agent/core/sdk.py:478` 在启用 skills 时自行设置 `setting_sources=["project"]`。
- SDK 类型说明中，`ClaudeAgentOptions.skills` 是启用 Skills 的单一入口；SDK/transport 会补充 Skill allowed rules 和 settings defaults。

影响：

- 本地 Skill parser 必须追随 Claude Code frontmatter、plugin、skill name 规则变化，维护成本高。
- `.codex/skills` 被本地 registry 视为可用，但真实 CLI 是否通过 plugin 成功发现，要靠 SDK/CLI 实测；本地 discovery 成功不等于 SDK runtime 可调用成功。
- `SkillRuntimeConfig.allowed_tools` 与 SDK 自己的 Skill rule 注入重复，未来 SDK 规则变化时容易漂移。

建议：

- 把 `SkillRegistry` 降级为“展示/预校验 helper”，不要作为运行时授权事实来源。
- 运行时只传 `ClaudeAgentOptions.skills` 和 `plugins`，让 SDK/CLI 负责 Skill tool rule 与 discovery。
- 优先把仓库 Skills 迁移为 Claude Code 原生 `.claude/skills` 或一个明确的本地 plugin；避免把 legacy `.codex/skills` 同时作为第一等运行来源。
- 增加一个可选集成检查：启动 SDK 后用 `get_mcp_status()` 或一次受控 Skill 调用确认指定 Skill 真能被 CLI 发现。

### P1：`PermissionPolicy` 重写权限模式，`ask/default/bypass` 语义与 SDK/Claude Code 不完全一致

证据：

- `agent/policies/permission.py:126` 在 `bypassPermissions` 下直接返回 `allow`，早于本地 deny rules。
- `agent/policies/permission.py:143` 在 `dontAsk` 下无匹配规则返回 `deny`，这符合只读默认策略。
- `agent/policies/permission.py:191` 到 `agent/policies/permission.py:202` 的 SDK `can_use_tool` adapter 对 `ask` 也返回 `PermissionResultDeny`，没有真正的人机审批路径。
- `agent/core/sdk.py:279` 仅 `dontAsk` 时不传 `can_use_tool`；其他 mode 会走本地 callback。
- SDK 源码说明 `allowed_tools` 的整工具 auto-allow 会 shadow `can_use_tool`，如需 gate 每次调用应使用 `PreToolUse` hook。

影响：

- 调用者如果把 `permission_mode="default"` 当作 Claude Code 默认交互审批，当前本地 callback 可能把 `ask` 直接变成 deny。
- 调用者如果设置 `bypassPermissions`，本地 `PermissionPolicy` 会先 allow，再依赖 SDK `disallowed_tools` 或另一个 deny hook 兜底；语义不够清晰。
- 这类权限重写属于 SDK/Claude Code 已有能力，继续扩大会成为本地第二套 permission framework。

建议：

- 把安全主边界固定为 SDK `disallowed_tools` + `PreToolUse` hook；本地 policy 只表达 Galatea 特有的 deny/allow 规则，不再完整复刻 Claude permission modes。
- 对正式 runtime 禁止 `bypassPermissions`，或要求显式 `allow_bypass=True`，并保留 `disallowed_tools` 强制 deny。
- 如果需要人工审批，优先接 SDK 的 `permission_prompt_tool_name` / `PermissionRequest` hook / console permission flow，而不是让 `ask` 在 `can_use_tool` 里静默 deny。
- 对 `default` / `acceptEdits` 模式，评估是否不传 `can_use_tool`，让 Claude Code 自身权限流处理；只在非交互服务模式下使用 fail-closed callback。

### P2：本地 Hook 类型与 adapter 仍偏厚，可进一步改成 SDK typed hook callback

证据：

- `agent/hooks/types.py:12` 到 `agent/hooks/types.py:26` 自定义 `HookEvent`。
- `agent/hooks/types.py:43` 到 `agent/hooks/types.py:104` 自定义 `HookInput`、`HookOutput`、`HookMatcher`。
- `agent/hooks/registry.py:89` 到 `agent/hooks/registry.py:173` 将本地 hook 输入/输出转换回 SDK JSON。
- SDK 已提供 `HookInput`、`HookJSONOutput`、`HookMatcher`，并规定 Python 字段 `continue_` / `async_` 会转为 CLI 字段。

影响：

- 新 SDK hook 字段、hook-specific output 或 event 增加时，本地 adapter 必须同步更新。
- 本地 `HookOutput.to_dict()` 使用 `continue`，SDK callback 输出使用 `continue_`，虽然当前 adapter 处理了 runtime 输出，但存在测试/日志语义混淆。
- 对“尽可能使用 SDK 能力”而言，这层可以明显变薄。

建议：

- 业务 hook 直接接收 SDK `HookInput` typed dict，并直接返回 SDK `HookJSONOutput` dict。
- 本地只保留少量 helper factory，例如 `make_permission_hook(policy)`、`compact_context_hook()`。
- 保留 conformance test：本地支持的 HookEvent 集合必须等于当前 SDK 事件集合。

### P2：`ToolExecutor` 是第二套工具执行器，应严格限定为测试/确定性流程

证据：

- `agent/tools/executor.py:18` 定义本地 `ToolSpec`，`agent/tools/executor.py:39` 定义 `ToolRegistry`，`agent/tools/executor.py:63` 定义 `ToolExecutor`。
- `ToolExecutor` 自行调用 hooks 和 permission policy，而不是通过 SDK MCP transport。
- `agent/tools/server.py` 已经有 SDK-native MCP 工具注册路径。

影响：

- 如果后续业务阶段直接接 `ToolExecutor`，会绕过 SDK MCP lifecycle、MCP status、Claude Code tool naming、SDK hook event stream。
- 工具权限和输出裁剪可能与 SDK 真实调用路径不一致。

建议：

- 明确 `ToolExecutor` 只服务于单元测试、无 LLM smoke test、CI deterministic checks。
- 生产/正式 agent 工具统一通过 SDK MCP server 暴露。
- 若要保留 direct executor，建议从 `INSPECTION_TOOLS` / MCP tool metadata 生成测试 registry，避免维护两套工具清单。

### P2：full Claude Code helper 会显式允许 Bash/Write，应避免成为默认或文档推荐入口

证据：

- `agent/runtime.py:255` 到 `agent/runtime.py:264` 的 `claude_code_allowed_tools()` 会返回基础 Claude Code 工具，包括 `Bash`、`Write`、`Edit`、`MultiEdit`。
- `agent/test/test_sdk_core.py:346` 到 `agent/test/test_sdk_core.py:374` 验证该 helper 可以允许 `Bash` 和 `Write`。
- 默认 runtime `agent/runtime.py:89` 使用 `_default_allowed_tools()`，只包含 Galatea MCP inspection tools，默认是安全的。

影响：

- helper 本身不是错误，但如果被 CLI、Notebook 或 README 当成常规入口，就会偏离平台“最小权限默认”的要求。
- 与“尽可能使用 SDK 能力”一致的做法是保留 SDK preset，但把高风险工具通过 SDK permission layer 或 sandbox 明确管住。

建议：

- 将 `claude_code_allowed_tools()` 标注为 maintainer-only / explicit elevated mode。
- 常规交互继续使用 `claude_code_read_only_allowed_tools()`。
- 如果未来要开放 Bash/Write，使用 SDK permission flow、sandbox、scoped `Bash(git status:*)` 等规则，不要整工具 allow。

### P2：demo 文档有绕过统一 runtime 和不完整 SDK permission 配置的误导风险

证据：

- `agent/demo/demo_sdk_direct.py:5` 描述“直接使用 Claude SDK，不经过 GalateaRuntime 封装”是最灵活/最佳实践。
- `agent/demo/demo_sdk_direct.py:60` 到 `agent/demo/demo_sdk_direct.py:65` 创建 `ClaudeAgentOptions` 时设置 `permission_mode="dontAsk"`，但没有设置 `allowed_tools`。
- `agent/demo/demo_sdk_direct.py:96` 的 subagent tool names 使用未加前缀的 `list_training_projects` 等，与当前 `agent/agents/definitions.py:22` 的 `mcp__galatea-platform__` 前缀规则不一致。

影响：

- demo 可能无法按预期调用 MCP 工具，也会让维护者绕过 `GalateaSDKRuntime` 的 hooks、budget、disallowed tools、skill/plugin、result validation。
- 对外部读者来说，“最佳实践”措辞会削弱“统一 runtime 是正式入口”的约束。

建议：

- 把 direct SDK demo 改为“低层示例，仅用于理解 SDK”。
- 给 demo 补完整 `allowed_tools`、`strict_mcp_config=True`、`disallowed_tools`，或直接改为调用 `GalateaRuntime`。
- 所有正式 CLI/Notebook 文档统一指向 `GalateaRuntime` / `GalateaAgentClient`。

### P3：workflow/state/quality policy 是平台业务层，当前应保持薄封装，不要扩成 agent 框架

证据：

- `agent/workflows/state_machine.py:107` 定义本地 `WorkflowStateMachine`。
- `agent/workflows/orchestrator.py:13` 定义 `WorkflowOrchestrator`，没有 stage handler 时返回 skipped。
- `agent/policies/quality.py:55` 定义本地 `QualityGatePolicy`。
- `agent/state/experiment.py:20` 定义本地 `ExperimentState`。

影响：

- 这些模块适合作为 Galatea 平台确定性状态/质量门控，不应承担 LLM agent loop、tool dispatch、retry orchestration。
- 如果后续把 Ray Job、MLflow Run、promotion 等都塞进 workflow orchestrator，而不是通过 SDK MCP tools 和明确 approval flow，会偏离 SDK-first 设计。

建议：

- 保留它们作为“平台状态和门控模型”，不要把它们做成第二套 agent runtime。
- 业务执行动作统一落到 MCP tools；workflow 只记录状态、检查前置条件和保存证据。
- 对 stage CLI 继续返回 unsupported，直到对应 SDK MCP tools 和审批边界设计完成。

## 6. 建议保留的边界

建议继续保留：

- `agent/core/sdk.py`：作为唯一正式 SDK runtime option 汇总层，但继续压缩非 SDK 逻辑。
- `agent/tools/server.py`：作为平台 MCP tools 注册入口，后续 Data/Training/Inference 工具也应走这里或同级 MCP module。
- `agent/agents/definitions.py`：使用 SDK `AgentDefinition` 定义窄权限 subagents。
- `agent/state/store.py`：保留 Galatea 应用状态，但继续与 SDK `SessionStore` transcript protocol 明确分离。
- `agent/schemas/`、`agent/policies/quality.py`：保留训练平台特有的结构化契约和质量门控。
- `agent/config/loader.py`：仅用于把 `~/.claude/settings.json` 中的 API env 应用到进程，不要扩成自研 settings layer。

建议收敛或避免扩大：

- `agent/skills/registry.py`：避免作为 runtime discovery 权威；优先使用 SDK `skills`/`plugins`。
- `agent/hooks/types.py`、`agent/hooks/registry.py`：逐步减少自定义类型，靠 SDK hook typed dict。
- `agent/policies/permission.py`：避免完整重写 Claude Code permission modes，只保留 Galatea 特定 allow/deny 策略。
- `agent/tools/executor.py`：限定为测试/确定性 direct checks。
- `agent/workflows/orchestrator.py`：限定为状态编排，不承载 LLM loop 和 tool dispatch。
- direct SDK demos：避免被文档描述成正式最佳实践入口。

## 7. 整改路线（已执行）

### 近期（已完成）

1. 简化 Skill runtime：正式运行时只依赖 SDK `skills` + `plugins`；本地 `SkillRegistry` 仅用于 CLI 展示和启动前校验。
2. 加固 permission modes：禁止默认 runtime 使用 `bypassPermissions`；`ask/default` 不要静默 deny，应接 SDK permission flow 或明确服务端 fail-closed 模式。
3. 清理 demo 文案和配置：把 `demo_sdk_direct.py` 改成低层示例，补 `allowed_tools` 或指向 `GalateaRuntime`。
4. 给 `claude_code_allowed_tools()` 增加警示命名或文档，例如 `unsafe_full_claude_code_allowed_tools()`。
5. 给 direct executor 增加模块注释和测试约束，声明非正式 agent runtime。

### 中期（基础能力已完成，业务工具按阶段实施）

1. 将 hook callback 改为 SDK-native typed callback，删除或缩小本地 `HookInput` / `HookOutput` adapter。
2. 增加 SDK Skill 可用性集成测试：不调用真实模型也可检查 SDK options、plugin manifest、Skill name rule 与 CLI discover 前置条件。
3. 将权限审批接入 SDK-native `PermissionRequest` hook 或 `permission_prompt_tool_name`，并记录 approval id、scope、reason、persistence。
4. 将未来业务工具统一封装为 MCP tool：`inspect_*`、`validate_*`、`submit_*`、`status_*`、`promote_*` 分级注册。
5. 对每个高风险 tool 建立 `read_only`、`destructive`、`requires_approval` 元数据与 quality gate。

其中第 4 项是未来业务能力的注册规范，不代表本次新增尚未设计的 submit/promote tools；当前只读
inspection tools 已统一走 MCP。第 5 项已为当前工具补齐标准 MCP annotations，未来高风险工具仍须
在实现时同时提供 approval 和 quality-gate 契约。

## 8. 最终判断

当前 `agent/` 已满足“基座依赖 SDK，并参照 Claude 源码设计；尽可能使用 SDK 能力，不额外
做第二套 agent framework”的要求。后续新增业务能力仍须遵守当前边界：先定义 schema 和 MCP
tool，再通过 SDK permission/approval flow 暴露；workflow 只记录状态和证据，不执行 LLM loop、
tool dispatch、Ray retry 或 Registry promotion。

## 9. 整改闭环证据

| 原问题 | 状态 | 当前证据 |
| --- | --- | --- |
| P1 Skills 重复 discovery/authorization | 已闭环 | `agent/core/sdk.py:366`、`agent/core/sdk.py:374` 直接传 `setting_sources`、`skills`、`plugins`；不再生成 `Skill(name)`、注入 `Skill` tool 或调用本地 registry。`agent/skills/registry.py:63` 明确仅用于展示/预检。 |
| P1 permission modes 被本地重写 | 已闭环 | `agent/policies/permission.py:55` 只返回 Galatea `allow` / `deny` / `defer`；`agent/core/sdk.py:209` 禁止隐式 bypass 和冲突审批配置；`default` / `acceptEdits` / `plan` / `dontAsk` 交由 SDK。 |
| SDK-native approval flow 与证据 | 已闭环 | `agent/core/sdk.py:131` 直接接收 `can_use_tool` / `permission_prompt_tool_name`；`agent/core/sdk.py:246` 记录 request ID、scope、reason、persistence suggestions 和最终 decision；`agent/hooks/builtin.py:102` 的 `PermissionRequest` hook 记录 prompt-tool 请求。 |
| P2 本地 Hook schema/adapter 偏厚 | 已闭环 | `agent/hooks/types.py:18` 从 SDK `HookEvent` 推导事件集合；业务 callback 直接使用 SDK `HookInput` / `HookJSONOutput`；`agent/hooks/registry.py:9` 只收集 SDK `HookMatcher`，无 JSON 转换。 |
| P2 direct executor 可能成为第二 runtime | 已闭环 | `agent/tools/executor.py:54` 改为非公开的离线测试 harness，只接受生产 MCP catalog 的 SDK `SdkMcpTool`，不再模拟 hooks/MCP lifecycle。 |
| P2 full Claude Code helper 风险 | 已闭环 | `agent/runtime.py:273` 重命名为 `unsafe_full_claude_code_allowed_tools()`，文档标明 maintainer-only；默认入口不使用。 |
| P2 direct SDK demo 误导 | 已闭环 | `agent/demo/demo_sdk_direct.py:33` 标为低层示例，并集中补齐 exact allowlist、disallowlist、`strict_mcp_config` 和隔离 settings；正式文档指向 `GalateaRuntime`。 |
| P3 workflow 承载执行风险 | 已闭环 | `agent/workflows/orchestrator.py:13` 仅管理状态/证据；`agent/workflows/orchestrator.py:68` 只接收外部 MCP 结果，不再执行 stage handler 或生成虚假 skipped 结果。 |
| 高风险 tool 元数据缺失 | 当前工具已闭环 | `agent/tools/server.py:37` 起的 5 个 MCP tools 均声明标准 `ToolAnnotations`；当前均为 read-only、non-destructive、idempotent。未来 mutation tools 仍须先设计审批和 quality gate。 |

### 验证结果

```text
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s agent/test -p 'test_*.py'
Ran 44 tests ... OK

/data/conda/envs/attend-ray-py312/bin/python -m compileall -q agent
OK

git diff --check
OK
```

仓库级 `tests/` 目录当前不存在，因此仓库指南中的 repository-level discovery 命令返回
`Ran 0 tests / NO TESTS RAN`（退出码 5）；这不是 agent 测试失败。本次未调用真实 Claude API，
也未执行昂贵训练、外部写操作或 Registry alias 变更。真实模型侧的 Skill invocation 和
permission UI 仍属于部署环境集成验证，不影响本次静态/离线代码闭环结论。
