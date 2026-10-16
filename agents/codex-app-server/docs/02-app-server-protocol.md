# 原生 app-server 协议

目标运行时固定为 `codex-cli 0.154.0`。`src/codex_agent/protocol/0.154.0.json` 来自实际二进制的 `app-server generate-json-schema --experimental`。

Host 完成 `initialize` / `initialized` 后才允许请求，启用原生 experimental API。线程、轮次、文件、进程、Skill、MCP 和配置等方法均按固定版本 JSON Schema 校验，通过原生协议桥转发。

Skill 扩展根通过 `skills/extraRoots/set` 注册，不拼接业务 prompt。MCP 使用原生 `config.toml`；Host 不维护第二套 MCP 客户端。可选 Dynamic Tools 通过 `thread/start.dynamicTools` 注册，回调 `item/tool/call` 进入 Registry，返回原生 success/contentItems。

新线程保留本地执行环境；默认 `workspace-write` / `on-request`。审批与问题通过原生服务端请求进入页面，用户响应后提交给原生运行时。协议可达不等于所有外部服务或账号能力已验收。

历史 native wire fixtures 位于 `tests/fixtures/codex-*`，仅用于验证指定二进制的真实协议行为，不会在默认 Host 启动时加载。
