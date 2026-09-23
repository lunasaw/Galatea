# 02 App Server 协议

实施决策（2026-09-23）：已按用户确认采用双层 schema 合同。原始 `tools.json` 负责 Host 严格
参数校验；`runtime-compatibility.json` 冻结真实模型请求的 schema 并绑定 binary/package/protocol
摘要。下文“模型 schema 一致”指与此兼容性合同逐字段一致，Host 原始约束保持不变。
最新实测见 [实施记录](11-stage0-implementation.md)。

本方案以 Codex 源码 `/data/ai/chenzhangyue/code/codex/codex-rs/app-server-protocol` 的
v2 protocol 为依据。复制的 runtime 必须在实现前生成或读取自身 schema，差异以 runtime
版本为准。

## 1. Host 到 app-server

Host 通过 stdio 发送 JSON-RPC。启动顺序固定为：

```text
spawn the locked app-server entrypoint (`codex-app-server --listen stdio://`, or
`codex app-server --listen stdio://` when using the unified CLI package)
→ initialize(capabilities.experimentalApi=true)
→ initialized
→ thread/start
→ 保存 thread.id
→ turn/start
```

`dynamicTools` 是 experimental field。Host 必须显式 opt in：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "clientInfo": {"name": "galatea-codex-agent", "version": "0.1.0"},
    "capabilities": {"experimentalApi": true}
  }
}
```

只有收到成功 response 后才能发送 `initialized` notification。未 opt in、runtime 不识别 capability
或 experimental schema 与锁定版本不一致时直接失败，不降级到 MCP 或无工具 Thread。

`thread/start` 的关键字段：

```json
{
  "method": "thread/start",
  "params": {
    "cwd": "/absolute/workspace/session-<id>",
    "approvalPolicy": "never",
    "sandbox": "read-only",
    "environments": [],
    "dynamicTools": [
      {
        "type": "namespace",
        "name": "galatea",
        "description": "Governed Galatea training tools.",
        "tools": [
          {
            "type": "function",
            "name": "galatea_get_capabilities",
            "description": "...",
            "inputSchema": {"type": "object"}
          }
        ]
      }
    ]
  }
}
```

Galatea 动态工具的可见性由该 namespace 的函数清单控制：Host 从 17 个支持工具中按 principal
actions 过滤出 session catalog，不把未授权 handler、隐式 Python callable、环境变量、文件路径或
后端凭据暴露给模型。每个 session 的 catalog 必须冻结；catalog digest 变化时不能悄悄 resume
旧 thread。这里的“可见性”只描述动态工具，不代表完整模型工具面。

这里的 dynamic tool 清单只控制动态工具，并不天然关闭 app-server 内建工具。发布 runtime 还需
关闭 shell/view-image/sleep、MCP/plugin、协作和其他不需要的能力，并从 mock model 捕获的真实请求
断言最终工具面符合精确 allowlist。`security.allow_*` 之类 Host 字段不能替代 runtime 配置证明。

## 2. app-server 到 Host

动态工具调用是 JSON-RPC server request，线上的方法名为 `item/tool/call`。源码中的类型
为 `DynamicToolCallParams`，核心字段如下：

```json
{
  "jsonrpc": "2.0",
  "id": 41,
  "method": "item/tool/call",
  "params": {
    "threadId": "thread-...",
    "turnId": "turn-...",
    "callId": "call-...",
    "namespace": "galatea",
    "tool": "galatea_get_capabilities",
    "arguments": {"protocol_version": "galatea.tools/v1"}
  }
}
```

Host 必须检查：

- JSON-RPC request id 未被复用；
- `threadId`、`turnId` 属于当前 session；
- namespace 必须为 `galatea`；
- tool name 必须在冻结 catalog；
- arguments 是 JSON object，并通过同一份 JSON Schema 校验；
- callId 对应的 canonical arguments digest 与写前记录一致；
- schema 校验通过后才进入对应 handler；
- 超时、取消或进程退出时不能假报成功。

## 3. Host 回执

成功调用：

```json
{
  "jsonrpc": "2.0",
  "id": 41,
  "result": {
    "contentItems": [
      {"type": "inputText", "text": "{\"schema_version\":\"galatea.tools/v1\",...}"}
    ],
    "success": true
  }
}
```

业务失败、参数错误或受限动作仍返回协议成功的 JSON-RPC response，但 `success=false`，
`contentItems` 放置可供模型理解的安全错误 envelope。只有协议级错误（未知 request、无效
JSON-RPC、无法序列化回执）才使用 JSON-RPC error response。

Tool result 的文本 envelope 保持现有 `galatea.tools/v1`：`schema_version`、`request_id`、
`ok`、`data` 或 `error`。Host 生成新的 request id，并在内部把它与 `callId`、session、
operation id 关联；不接受模型提供的 request id 作为幂等身份。

回执顺序是协议合同的一部分：Host 必须先持久化 call intent，再执行 handler；得到结果后先持久化
receipt，再发送 JSON-RPC response。`callId` 只能去重已经有确定 receipt 的协议重放，不能替代
`submit_job.idempotency_key` 等业务幂等键。若副作用可能已经发生但 receipt 尚未落盘，Host 保存或
恢复为 `unknown`，先查询 operation/backend 事实；不得再次执行 handler 来“补回执”。

## 4. 通知与观察

Host 不改变通知，只做复制和投影：

| 通知 | 用途 |
| --- | --- |
| `thread/started` | 绑定 thread |
| `turn/started` / `turn/completed` | 轮次状态机 |
| `item/started` / `item/completed` | Agent item 生命周期 |
| `item/agentMessage/delta` | Console 流式回复 |
| `item/reasoning/summaryTextDelta` | 公开摘要（若 runtime 发送） |
| `item/tool/call` request/response | 工具参数和回执 |
| `thread/tokenUsage/updated` | 用量观察 |
| `error` / `warning` | 故障展示 |

原始事件只写入 Host 的受限事件文件；Console 默认显示脱敏投影。凭据、完整 system/developer
instructions、私有 reasoning 和后端原始日志不得进入浏览器。

## 5. Resume 与中断

- `thread/resume` 只使用已保存的 thread id 和同一受控 `CODEX_HOME`；Host 在调用前先比较自己保存的
  runtime、配置和 catalog digest。
- 当前本地源码的 `ThreadResumeParams` 没有 `dynamicTools` 字段，源码测试显示部分配置下会从 rollout
  metadata 恢复动态工具；这不是目标 binary 的已验收保证。阶段 0 必须跨进程重启恢复 Thread，并从
  恢复后的真实模型请求确认 namespace、函数 schema 和完整有效工具面仍与冻结 allowlist 完全一致。
- digest 不一致、rollout 缺失或无法从模型请求证明恢复结果时，不发送下一次 `turn/start`，session
  标记为 `needs_reconciliation`。不得把“源码测试可恢复”升级为部署保证，也不得静默创建一个缺少
  历史约束的新 Thread。
- `turn/interrupt` 的 response 不是业务取消完成证明；Host 仍需等待 `turn/completed` 或
  app-server 退出，并将未确认的 backend operation 标记为 `unknown`。
- app-server 连接断开时，Host 先读取本地 turn journal 和 Galatea operation 状态，再决定
  是否可恢复；不能让模型重新猜测已提交的 `plan_id` 或 `operation_id`。

## 6. 版本门禁

部署 manifest 至少记录：

```text
codex_version
codex_source_revision
runtime_package_manifest_sha256
runtime_package_tree_sha256
runtime_binary_sha256
app_server_protocol_schema_sha256
dynamic_tool_catalog_sha256
effective_runtime_config_sha256
galatea_source_revision
```

`runtime_package_tree_sha256` 基于 package 内相对路径、文件类型、执行位和文件内容的确定性 manifest
计算，覆盖 entrypoint、`codex-resources/`、`codex-path/` 和随包 helper；不能只摘要启动脚本或单个
二进制。任何一项变化都阻止自动 resume，要求先执行只读兼容性检查和新的协议 smoke。
