# 自定义 Tool Provider

Host 自定义工具是可选项。普通 Shell、文件和 MCP 工具由原生 Codex 执行，不经过此 Registry。
本仓库不附带 Galatea Service 的 Provider 实现；可配置扩展机制不表示已安装 Galatea 集成。

## 配置与目录

`tools.catalog_dir` 包含 `tools.json` 和 `catalog-metadata.json`，格式见 [echo 示例](../examples/echo)。namespace 和工具名由扩展定义，不保留业务专用名称。`tools.actions` 是允许注册/执行的工具名列表，`["*"]` 表示全部配置工具，`[]` 表示不暴露。

`tools.providers` 列表每项含 Python 文件 `path`、可选工厂名 `factory`（默认 `create_provider`）、对象 `options`。工厂返回 `codex_agent.providers.ToolProvider`，其中 `handlers` 是工具名到同步 callable 的映射。所有 Provider 合起来必须恰好覆盖整个 catalog；缺失或重复均拒绝启动。

Provider 文件以独立模块名加载；共享实现应打包为安装依赖。将扩展源码放在独立、不可变目录；发布测量覆盖入口所在目录树和 Python 依赖。不要把状态或输出写回扩展源码目录。

## 调用和恢复

Handler 签名为 `handler(context, arguments)`。`context` 含 session/thread/turn/call 身份、agent 身份、操作 allowlist、配置与 catalog digest、deadline 和 cancel。返回 JSON 可序列化数据，或 `ToolResult` 表达明确业务失败。密钥字段在记录和返回模型前脱敏；单次结果最大 256 KiB。

执行前按原始 JSON Schema 校验参数，写入调用身份与 intent；同一 call_id 的成功回执重复返回，身份不同则拒绝。未知结果不会重执行，未决写操作阻止新的写调用，读取仍可用于核对。

写工具应把 `context.call_id` 作为外部幂等键。工厂可提供以下同步钩子：

- `reconcile()`：核对扩展后端状态，不发起新业务操作。
- `observe_receipt(identity)`：根据 `identity.call_id` 等绑定只读查询已发生操作；未确认返回 `None`，确认后返回成功数据。
- `close()`：释放客户端等资源，逆序调用。

catalog 或 Host 配置绑定不同的未知调用不会交给当前 Provider 判定完成。没有恢复钩子时保持 unknown，需使用原扩展核对；禁止把超时等同于失败并重发训练。

MCP 外部调用由原生 Codex 管理，Host 不为外部 MCP 自动提供此操作 journal。MCP 服务需要自身幂等和状态查询，Skill 指导 Agent 先查询再决定下一步。
