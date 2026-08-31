# Galatea Agent SDK/Claude 源码对齐审核报告

审核日期：2026-08-14  
审核范围：`agent/`、`.claude/`、`.claude-plugin/`、`.codex/skills/`，并对照本机 Claude Agent SDK 源码  
对照基准：

- 已安装运行时：`claude-agent-sdk==0.2.136`，bundled Claude Code CLI `2.1.228`
- 本机 SDK 源码：`/data/ai/chenzhangyue/code/claude-agent-sdk-python`，Git `e3320df`，`pyproject.toml` 标注 `0.2.135`
- 关键 SDK 源码：`src/claude_agent_sdk/client.py`、`src/claude_agent_sdk/types.py`、SDK examples/tests

## 1. 总体结论

结论：当前 `agent` 工程已经以 Claude Agent SDK 为主基座，核心运行时、MCP 工具、AgentDefinition、hooks、权限、skills/plugins、structured output、session resume/fork、预算和上下文查询都在使用 SDK 能力；但工程还没有完全收敛到“薄 SDK 封装 + 少量平台确定性策略”。存在若干自定义 registry/store/parser/placeholder 与 stale `.claude/agents`，会增加与 Claude Code/SDK 源码行为漂移的风险。

建议判断：

- SDK 基座依赖：通过，核心路径依赖真实 SDK，而不是 prompt-only 或自研 agent loop。
- Claude 源码设计对齐：基本通过，`ClaudeSDKClient`、`ClaudeAgentOptions`、in-process SDK MCP、`AgentDefinition`、`HookMatcher`、`PermissionResult*`、`SessionStore` 等概念均有对应。
- “尽可能使用 SDK 能力、少做自定义”：部分通过。核心 runtime 做得较好，但应继续删减或隔离 legacy/custom 层。
- 当前不建议重写架构；建议小步收敛：固定依赖、统一入口、删除/标记 legacy placeholder、把 session store 对齐 SDK Protocol、修正 `.claude/agents`。

## 2. 已对齐的 SDK 能力

| SDK 能力 | 当前实现 | 评价 |
| --- | --- | --- |
| `ClaudeSDKClient` 生命周期 | `agent/core/sdk.py:222` 创建并管理 client，`agent/runtime.py:65` 作为兼容 facade | 对齐，核心路径是 SDK-native |
| `ClaudeAgentOptions` | `agent/core/sdk.py:232` 汇总 `tools/mcp_servers/hooks/skills/plugins/output_format/agents/session_store/task_budget` | 对齐，已尽量使用 SDK option |
| in-process SDK MCP | `agent/tools/server.py:10` 使用 `create_sdk_mcp_server` 和 `@tool`，`agent/tools/server.py:116` 注册 server | 对齐，优先使用 SDK MCP 而非外部进程 |
| SDK `AgentDefinition` | `agent/agents/definitions.py:20` 直接使用 SDK dataclass | 对齐，优于自定义 AgentDefinition |
| hooks | `agent/hooks/registry.py:68` 转成 SDK `HookMatcher`，`agent/core/sdk.py:274` 注入 SDK options | 基本对齐，但本地 Hook 类型过厚 |
| permissions | `agent/policies/permission.py:191` 返回 SDK `PermissionResultAllow/Deny`，`agent/core/sdk.py:276` 注入 `can_use_tool` | 基本对齐，但与 `allowed_tools` 存在 shadowing/重复治理 |
| structured output | `agent/core/sdk.py:234` 将 schema 转成 SDK `output_format`，`agent/core/sdk.py:334` 读取 `ResultMessage.structured_output` | 对齐 |
| session resume/fork | `agent/core/sdk.py:242` 使用 SDK `InMemorySessionStore`、`resume`、`fork_session` | 对齐，但自定义 store 不是 SDK Protocol |
| context/MCP 动态控制 | `agent/core/sdk.py:362`、`agent/core/sdk.py:396` 暴露 `get_context_usage()`、`get_mcp_status()` | 对齐 |
| Skill/plugin | `agent/core/sdk.py:277`、`agent/core/sdk.py:278` 注入 `skills`、`plugins` | 对齐，但发现层可以更薄 |

## 3. 主要问题和风险

### P0：依赖未声明，SDK 基座不可复现

现象：

- 代码直接 import `claude_agent_sdk`：`agent/core/sdk.py:12`、`agent/tools/server.py:10`、`agent/policies/permission.py:10` 等。
- 当前环境确实安装了 `claude-agent-sdk==0.2.136`、`mcp==1.26.0`、`pydantic==2.12.3`、`PyYAML==6.0.3`。
- 但根 `requirements.txt:5` 到 `requirements.txt:10` 只声明 Jupyter/MLflow/Boto3/Ray，没有声明 agent 运行所需 SDK 依赖。

风险：

- 新环境按仓库说明安装后，`agent` 可能无法 import。
- 本机 SDK 源码版本 `0.2.135` 与已安装运行时 `0.2.136` 不完全一致，排查 SDK 行为时可能引用错版本。

建议：

- 增加 `agent/requirements.txt` 或 `agent/conda.yaml`，显式固定 `claude-agent-sdk`、`mcp`、`pydantic`、`PyYAML`。
- 在 `agent/README.md` 写明“运行以已安装包版本为准；本机源码仅作设计参考”，或把本机源码作为 editable/path dependency 明确化。

### P1：自定义 `SessionStore` 与 SDK `SessionStore` Protocol 不兼容

现象：

- 本地 `agent/state/store.py:9` 定义了 `save_session/load_session/delete_session/list_sessions`。
- SDK 源码 `types.py` 的 `SessionStore` Protocol 要求至少 `append(key, entries)` 和 `load(key)`，并处理 transcript mirror、idempotency、resume materialization。
- `AgentSDKConfig.session_store` 在 `agent/core/sdk.py:136` 直接透传给 SDK；如果误传 `agent.state.MemorySessionStore`，SDK 会在 runtime 中调用不存在的 `append/load` 语义。

风险：

- 长任务 resume/fork 和 transcript mirror 看似有本地 store，实际不能作为 SDK session store 使用。
- 后续若接 Redis/S3/Postgres，容易重造一套与 SDK 不兼容的存储接口。

建议：

- 若这是业务状态存储，重命名为 `AgentStateStore` 或 `PatrolStateStore`，不要叫 `SessionStore`。
- 若要承载 Claude transcript，直接实现 SDK Protocol：`append()`、`load()`，并参考 SDK `examples/session_stores/`。
- `AgentSDKConfig.session_store` 类型改成 SDK `SessionStore | None`，避免误传。

### P1：仍有生产脚本绕过统一 runtime

现象：

- 非 demo/test 路径中，`agent/scripts/inspect_platform.py:67` 直接构造 `ClaudeAgentOptions`，`agent/scripts/inspect_platform.py:74` 直接创建 `ClaudeSDKClient`。
- 该脚本没有使用 `GalateaSDKRuntime.build_options()`，因此没有统一注入 `strict_mcp_config=True`、default hooks、`allowed_tools`、`disallowed_tools`、预算、structured result validation。

风险：

- CLI 行为与主 runtime 不一致；同样的 inspection 在不同入口可能权限、MCP 和审计不一致。
- `permission_mode="dontAsk"` 但没有显式 `allowed_tools`，工具调用可能被拒绝；也可能加载默认 Claude Code 工具/设置造成边界不清。

建议：

- 将 `agent/scripts/inspect_platform.py` 改为调用 `GalateaRuntime.inspect_platform()` 或 `GalateaSDKRuntime`。
- demo/test 可以保留 direct SDK examples；正式 CLI 应统一经过 `agent/core/sdk.py`。

### P1：`.claude/agents` 与代码中的安全 AgentDefinition 冲突

现象：

- `.claude/agents/data-preparer.md:13`、`.claude/agents/model-evaluator.md:14`、`.claude/agents/platform-inspector.md:13`、`.claude/agents/training-orchestrator.md:14` 都宣称可用 `Bash/Read/Write`。
- 这些文件还引用 `scripts/validate_dataset.py`、`scripts/ray_data_job.py`、`scripts/platform_health.py` 等未在当前 `agent/tools/server.py` 注册的脚本路径。
- 代码侧安全定义在 `agent/agents/definitions.py:33`、`agent/agents/definitions.py:69`、`agent/agents/definitions.py:103`、`agent/agents/definitions.py:139`，均使用 SDK `AgentDefinition` 并限制到只读 MCP tools。
- `agent/core/sdk.py:454` 在启用 skills 时把 `setting_sources` 置为 `["project"]`，项目 `.claude` 内容存在被 Claude Code 加载的可能。

风险：

- Prompt/项目 agents 与 SDK options 形成相互矛盾的工具暗示。
- 后续排查时会误以为 Bash/Write 是 stage agent 的正式能力，违背“平台工具优先、训练不开放裸 Bash”的规则。

建议：

- 删除这些旧 `.claude/agents/*.md`，或补 frontmatter 并改成与 `agent/agents/definitions.py` 完全一致的只读/受控工具。
- 如果只想启用 Skills，不想加载 project agents/commands/settings，应验证 SDK/CLI 是否支持更细粒度 skill discovery；否则在文档中明确 `.claude/agents` 不作为当前运行来源。

### P2：权限治理有重复实现，且 `can_use_tool` 多数情况下被 `allowed_tools` shadow

现象：

- `agent/core/sdk.py:246` 将安全工具加入 `allowed_tools`，`agent/core/sdk.py:276` 同时设置 `can_use_tool=self.permission_policy.can_use_tool`。
- SDK 源码 `types.py:1671` 到 `types.py:1759` 明确：整工具级 `allowed_tools` 会在 `can_use_tool` 之前自动允许；要 gate 每次调用，应使用 `PreToolUse` hook。
- 当前代码确实有 `PreToolUse` permission hook：`agent/core/sdk.py:408` 到 `agent/core/sdk.py:418`；这是正确安全边界。

风险：

- `can_use_tool` 会产生 shadow warning，且维护者可能误以为它会审查所有已允许工具。
- 同一套策略同时在 SDK callback 和 hook 中维护，增加行为分叉风险。

建议：

- 明确主安全边界是 `PreToolUse` hook；`can_use_tool` 只保留给 ask-mode、人机审批或未预批准工具。
- 对 `permission_mode="dontAsk"` + 全部工具由 hook 决定的模式，可以考虑不传 `can_use_tool`，减少 SDK warning 和重复路径。
- 继续保留 `disallowed_tools`，不要仅依赖 `allowed_tools`。

### P2：本地 Hook 类型和 registry 偏厚，容易跟 SDK 漂移

现象：

- 本地 `agent/hooks/types.py:12` 自定义 `HookEvent`、`HookInput`、`HookOutput`、`HookMatcher`、`HookRegistry`。
- `agent/hooks/registry.py:138` 再把本地输出映射回 SDK JSON。
- SDK 已提供强类型 `HookInput`、`HookJSONOutput`、`HookMatcher`，并说明 Python 字段如 `continue_` 会转换为 CLI 字段。

风险：

- 新 SDK hook 字段、新 event 或输出字段出现时，本地 adapter 需要同步更新。
- 本地 `HookEvent.RESULT_COMPLETE` 不是当前 SDK hook，虽然 adapter 跳过，但会制造“支持但不执行”的误解。

建议：

- 简化为：业务 hook 直接返回 SDK `HookJSONOutput` dict；本地只保留少量 helper factory。
- 如果保留本地 registry，给每个 SDK event 增加 conformance test，并移除非 SDK event。

### P2：`SkillRegistry` 重复了部分 SDK/Claude Code Skill 发现逻辑

现象：

- `agent/skills/registry.py:99` 自行解析 `.claude/skills`、`.codex/skills`、plugin manifest、frontmatter、paths、allowed-tools。
- `agent/core/sdk.py:277` 和 `agent/core/sdk.py:278` 最终仍把 `skills/plugins` 交给 SDK。

风险：

- Claude Code Skill frontmatter 语义变更时，本地解析器可能过期。
- 本地 `SkillRuntimeConfig.add_dirs` 存在但未传入 SDK `ClaudeAgentOptions.add_dirs`，容易让调用者误以为已生效。

建议：

- 保留本地 registry 只做“列出/校验/桥接 legacy Codex skill”的最小功能。
- 真正加载与调用交给 SDK `skills`、`plugins`，避免复刻 discovery 行为。
- 删除未使用字段或接入 SDK `add_dirs`。

### P2：自定义 AgentDefinition wrapper 与 legacy presets 应退出主路径

现象：

- `agent/agents/definitions.py:20` 已经直接使用 SDK `AgentDefinition`，这是推荐路径。
- 但 `agent/agents/definition.py:17` 还定义本地 `AgentDefinition` wrapper，并在 `agent/agents/definition.py:176`、`agent/agents/definition.py:190`、`agent/agents/definition.py:203` 放置 `acceptEdits` 和未实现工具名的 legacy presets。

风险：

- 新增调用者可能误用 legacy dataclass，绕过当前安全默认。
- `acceptEdits` 与训练平台默认只读/审批策略冲突。

建议：

- 将 `agent/agents/definition.py` 标记为 deprecated 或删除。
- 若需要 metadata，使用 SDK `AgentDefinition` 外挂 registry metadata，而不是重新定义同名类。

### P2：MCP tool name discovery 使用了 SDK internals/static fallback

现象：

- `agent/core/sdk.py:507` 的 `mcp_tool_names()` 先尝试访问 `request_handlers.tools`、`_tools`、`tools`，最后对 `galatea-platform` 写死 5 个工具名。
- SDK 已提供运行时 `get_mcp_status()`，当前 runtime 也暴露了 `agent/core/sdk.py:396`。
- 当前 `self.mcp_tool_names` 只在 `agent/core/sdk.py:198` 赋值，未见后续使用。

风险：

- SDK/MCP server 内部结构变动时，静态 introspection 会失效。
- 写死工具名会在新增 MCP 工具后忘记同步。

建议：

- 删除未使用的 `mcp_tool_names()`，或只保留测试用常量。
- 运行时工具发现改用 `get_mcp_status()`；配置层工具列表由 `agent/commands/toolsets.py` 单一来源维护。

### P3：文档和示例存在旧路径/旧承诺

现象：

- `agent/README.md`、`agent/__init__.py` 仍保留 Stage 1 skeleton/未来功能表述。
- `.claude/agents` 使用旧脚本式 workflow。
- `agent/scripts/run_data_stage.py`、`run_training_stage.py`、`run_inference_stage.py` 只是 placeholder，且有未使用 imports。

建议：

- 把“当前可用能力”和“planned 能力”分开，避免把未注册工具写成可用能力。
- placeholder CLI 要么移到 docs/archive，要么改为调用 `GalateaRuntime` 输出明确 blocked/unsupported structured result。

## 4. 自定义功能保留/收敛建议

建议保留的本地功能：

- 平台确定性策略：`PatrolActionPolicy`、quality gate、permission denylist、审批/证据约束。
- 平台 schema：`agent/schemas/*`，这是 Ray/MLflow/MinIO 与 Agent 之间的稳定契约。
- in-process MCP tools：`agent/tools/server.py` 和业务 adapters。
- deterministic patrol runner：适合作为无 LLM 巡检/CI 模式，但应避免复制 MCP/tool dispatch。

建议收敛或删除的本地功能：

- 本地 `AgentDefinition` wrapper 和 legacy presets。
- 本地 `SessionStore` 命名/协议，除非改成 SDK Protocol。
- `mcp_tool_names()` 的 SDK internals introspection。
- Hook/permission/skill 解析中可直接使用 SDK 类型的重复层。
- `.claude/agents` 旧 prompt 文件。

## 5. 建议落地顺序

1. 固定 agent 依赖：补 `agent/requirements.txt` 或环境文件，至少包含 `claude-agent-sdk==0.2.136`、`mcp==1.26.0`、`pydantic==2.12.3`、`PyYAML==6.0.3`。
2. 统一正式入口：把 `agent/scripts/inspect_platform.py` 改为使用 `GalateaRuntime`；保留 direct SDK 只在 `agent/demo/`。
3. 清理 `.claude/agents`：删除旧文件，或改为与 `agent/agents/definitions.py` 同步的只读/受控 SDK subagents。
4. 修正 session store：业务状态 store 改名，Claude transcript store 改按 SDK `append/load` Protocol。
5. 简化 adapter：减少 Hook/Skill/Permission/MCP discovery 的自定义 parser，能交给 SDK 的交给 SDK。
6. 更新 docs：把 current/planned 能力拆清楚，避免把未来 Ray/Data/Training 工具写成当前可用。

## 6. 验证结果

已运行：

```bash
python -m unittest discover -s agent/test -p 'test_*.py'
```

结果：`Ran 43 tests in 0.078s`，`OK`。

注意：

- 该测试不调用真实 Claude API；`agent/test/test_sdk_integration.py` 是脚本式测试函数，不会被 `unittest discover` 执行真实 API 查询。
- 当前工作区在审核前已有未提交变更：`agent/scripts/interactive_chat.py`、`agent/tools/server.py`、`agent/test/test_cli_unicode.py`、`agent/summary/`。

## 7. 最终判断

当前工程方向是正确的：`agent/core/sdk.py` 已经把 Claude Agent SDK 作为基座，并且不是在自研 agent loop。下一步不需要增加更多自定义 agent 框架，而应做“减法”：正式入口统一到 runtime，状态/权限/hooks/skills 尽量贴近 SDK Protocol 和 SDK options，平台专属逻辑只保留在 schema、MCP tools、Patrol/approval/quality policies 中。
