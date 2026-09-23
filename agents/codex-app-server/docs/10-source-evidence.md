# 10 源码依据与决策记录

## 1. 本地源码依据

| 结论 | 依据 |
| --- | --- |
| app-server 使用 stdio JSON-RPC | `/data/ai/chenzhangyue/code/codex/sdk/python/src/openai_codex/client.py` 的 `codex app-server --listen stdio://` 启动逻辑；`codex-rs/app-server` 测试 harness |
| Thread 可以注册动态工具 | `/data/ai/chenzhangyue/code/codex/codex-rs/app-server-protocol/src/protocol/v2/thread.rs` 的 `ThreadStartParams.dynamic_tools` |
| 动态工具描述格式 | `/data/ai/chenzhangyue/code/codex/codex-rs/protocol/src/dynamic_tools.rs` 的 `DynamicToolSpec`、`DynamicToolFunctionSpec`、`DynamicToolNamespaceSpec` |
| 工具调用是 app-server server request | `/data/ai/chenzhangyue/code/codex/codex-rs/app-server-protocol/src/protocol/common.rs` 的 `item/tool/call`；v2 `DynamicToolCallParams/Response` |
| 工具回执支持文本/图片/音频 content item 和 success | `/data/ai/chenzhangyue/code/codex/codex-rs/app-server-protocol/src/protocol/v2/item.rs` |
| 动态工具事件可进入 Thread history 和 item notifications | `/data/ai/chenzhangyue/code/codex/codex-rs/app-server/src/bespoke_event_handling.rs`、`app-server-protocol/src/protocol/thread_history.rs` |
| dynamicTools 需要显式 experimental opt-in | `ThreadStartParams.dynamic_tools` 的 experimental 标记、`InitializeCapabilities.experimental_api`；本机 `codex-cli 0.153.4` 负向/正向 smoke |
| dynamicTools 与内建工具合并 | `/data/ai/chenzhangyue/code/codex/codex-rs/core/src/tools/spec_plan.rs` 先注册 core/MCP/extension sources，再 `append_dynamic_tool_runtimes` |
| Codex package 布局 | `/data/ai/chenzhangyue/code/codex/scripts/codex_package/layout.py` 与 `scripts/codex_package/README.md`：`bin/`、`codex-resources/`、`codex-path/`、`codex-package.json`；具体 variant/target 由发布 package 决定 |
| 配置层与项目配置边界 | `/data/ai/chenzhangyue/code/codex/codex-rs/config/src/loader/mod.rs`：package、admin/system/cloud、`CODEX_HOME`、cwd、父目录 `.codex`、repo 与 runtime 层；不应仅凭 `CODEX_HOME` 推断没有其他配置 |
| `CODEX_HOME` 解析 | `/data/ai/chenzhangyue/code/codex/codex-rs/utils/home-dir/src/lib.rs`：显式环境变量必须指向已存在目录，否则默认回退用户 home 下 `.codex` |

实现时应以实际复制 runtime 的生成 schema 和 binary 行为为最终依据。源码路径只用于设计和
构建来源，不代表允许新应用直接 import Codex Rust 内部 crate。

2026-09-22 的临时 `CODEX_HOME` smoke 观察到：未设置 `experimentalApi` 时
`thread/start.dynamicTools` 返回 JSON-RPC `-32600`；设置为 `true` 后同一 namespace/function
payload 成功创建 ephemeral Thread。该 smoke 没有启动 turn、调用模型或执行动态工具，因此只证明
握手和 Thread 注册可用，不是完整 round-trip 验收；也没有证明恢复后的 dynamic tool 持久化、完整
allowlist 或生产配置隔离。

## 2. Galatea 工具依据

| 结论 | 依据 |
| --- | --- |
| 工具数量为 17 | `services/galatea-mcp/contracts/tools.json` 的 `tools` 对象 |
| schema、分页、枚举和字段约束 | 同一 `tools.json`；不得在 Host 另写一份不一致 schema |
| envelope 是 `galatea.tools/v1` | `services/galatea-mcp/src/galatea_mcp/server.py` 与 `contracts.py` |
| mutation allowlist | `services/galatea-mcp/src/galatea_mcp/server.py` 的 `MUTATIONS` |
| Campaign、operation、evidence、Ray、MLflow 和 Artifact 语义 | `services/galatea-mcp/src/galatea_mcp/service.py`、`evidence.py`、`backends/`、`state.py` |

新 Host 复用这些 transport-neutral 业务规则；MCP server 仅作为现有兼容入口，不是新 Agent 的
运行依赖。

## 3. 参考项目只借鉴的内容

`/data/ai/chenzhangyue/code/service-manager-agent` 只用于理解以下通用交互模式：

- 一个 Host 管理一个 app-server 子进程；
- 双向 JSON-RPC 旁路观察；
- Thread/Turn/call 关联和幂等；
- Console 用 SSE 展示 agent loop；
- 进程组清理和未知副作用 fail-closed。

不复制其商家业务工具、task-manager、Redis、CubeFS、Tunnel、MemoryLedger 或
`agents/training-agent-runtime`。

## 4. 外部组件确认边界

本方案没有提出新的外部组件。它只接入仓库已经存在的：

- Codex runtime（由指定 `/data/ai/chenzhangyue/code/codex` 源码构建/复制）；
- Galatea 既有 Ray、MLflow Tracking/Artifact 和对象存储客户端；
- 当前训练项目和 registry/config 资产。

不在方案中引入 MCP SDK、OpenAI Agents SDK、Redis、PostgreSQL、SQLite、消息队列、第三方
Agent framework 或远程 tunnel。本文记录最初来源核对；2026-09-23 已实现 uvicorn ASGI、Bearer、
Origin 防护及 reconciler，并通过真实 loopback HTTP/SSE 测试。当前实现和现场验收边界见
[实施记录](11-stage0-implementation.md)。

## 5. 文档状态

OpenAI 官方文档站点在当前环境请求返回 403，因此本方案没有把无法访问的网页行为当作证据；
协议结论来自本地 Codex 源码、生成类型和测试。正式发布前仍应在目标 runtime 上完成
`thread/start.dynamicTools` 与 `item/tool/call` 的真实 round-trip 验收，并把输出写入部署
evidence manifest。
