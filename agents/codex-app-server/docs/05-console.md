# 05 Console

实施说明（2026-09-23）：当前 Console 以严格公开字段投影展示生命周期和工具步骤，不公开完整用户
输入、assistant 原文、推理或原始日志。原生 delta 按 item id 聚合为一个状态步骤。
Bearer token 只保存在页面内存，使用 fetch 流读取 SSE；不使用不能附带 Authorization 的 EventSource。
API 增加 `POST /api/sessions/{id}/resume`；全部写请求需要 Idempotency-Key 或同值消息 request_id。
实际部署参数及依赖见 [配置与部署](06-config-deployment.md)。


Console 是同一 Host 的观察和输入界面，不是独立 Agent，也不直接连接 Codex runtime 或
Galatea backend。首版可用原生 HTML/CSS/JavaScript；不需要复制 `service-manager-agent` 的
Redis Console、Tunnel 或任务管理器模拟器。

## 1. HTTP API

| 方法 | 路径 | 语义 |
| --- | --- | --- |
| `GET` | `/health/live` | 进程存活 |
| `GET` | `/health/ready` | runtime、Tool Registry、配置和 app-server 可用 |
| `GET` | `/api/capabilities` | Agent/runtime/catalog digest 和功能开关 |
| `POST` | `/api/sessions` | 创建 session，绑定 thread |
| `GET` | `/api/sessions` | 当前配置目录中可见的 session 列表 |
| `GET` | `/api/sessions/{id}` | session 状态、thread/active turn 与绑定摘要 |
| `POST` | `/api/sessions/{id}/messages` | 发送一条用户消息，返回 request/turn 身份 |
| `POST` | `/api/sessions/{id}/interrupt` | 中断当前 turn，进入核对状态 |
| `POST` | `/api/sessions/{id}/close` | 关闭 session，不删除 Codex history |
| `GET` | `/api/sessions/{id}/events?after=<seq>` | SSE 事件流，支持断线续读 |
| `GET` | `/api/sessions/{id}/loop/{turn_id}` | 聚合后的 Agent Loop 视图 |

所有写请求带 `Idempotency-Key`，消息也支持同值 `request_id`；重复 payload 返回既有 receipt。服务端必须对
`Idempotency-Key` 绑定 HTTP 身份、方法、路径和 canonical body，复用键但 payload 不同返回 conflict。
Console 的 `session_id` 不能代替 Galatea 的 `project_id`/`campaign_id` scope，后者由服务端配置
绑定并在服务端校验，不能由创建请求扩大。

HTTP 写接口必须有明确的本机鉴权，即使只绑定 loopback 也不能省略。实现至少要固定一种受审查的
机制（例如 mode `0600` 的 Unix socket peer credentials，或本机 bearer/capability token）；若浏览器
通过 HTTP 发送 cookie，必须校验 `Origin`/`Referer`、拒绝跨源写入并使用 CSRF token。CORS 默认关闭，
不得以 `Access-Control-Allow-Origin: *` 配合凭据。健康检查可匿名但不能泄露 token、路径、catalog
细节或后端状态。

## 2. SSE 事件

```json
{
  "seq": 18,
  "session_id": "s-...",
  "thread_id": "thread-...",
  "turn_id": "turn-...",
  "kind": "tool_call",
  "method": "item/tool/call",
  "phase": "request",
  "public": {
    "namespace": "galatea",
    "tool": "galatea_query_runs",
    "arguments": {"project_id": "demo", "campaign_id": "c1", "limit": 20}
  }
}
```

当前事件 kind 为 `session`、`turn`、`native`、`tool_call`、`tool_result` 和 `observation_gap`。
公开字段只保留生命周期、已校验的 metadata 参数投影、read-only 标记、operation id、耗时和结果状态。
原生 assistant item/delta 合并为一个回复状态步骤；不发布文本内容、推理或完整输入。
日志写入失败使 Host 失败关闭；SSE 连接失败只终止当前订阅，不能伪造已经交付的 gap。
残缺持久日志尾部恢复时追加带范围的 observation_gap；完整记录损坏则拒绝观察并等待恢复。

## 3. Agent Loop 展示

Console 聚合原生事件，不构造第二个 loop：

```text
Turn
 ├─ 输入 admission 状态
 ├─ Agent message delta（按 item 合并为状态）
 ├─ 原生生命周期摘要
 ├─ galatea_query_runs(arguments) → result
 ├─ Agent message 状态（合并）
 └─ turn/completed
```

工具请求、工具回执按 `callId` 合并；同一 turn 的多个 tool call 保留顺序。页面支持“全部”、
“回复”、“工具”、“摘要”、“错误”筛选，并显示当前筛选数/总数。长字段采用服务端截断和
`truncated=true` 标记，不在浏览器自行猜测是否完整。

## 4. 安全展示

默认不显示：

- `CODEX_HOME` 路径、系统环境、API key、Bearer token、S3 凭据；
- 完整 system/developer instructions、私有 reasoning、原始 Ray 日志；
- 测试样本、测试标签、Artifact 原始内容和任何可重放的签名 URL。

浏览器只接收 `public_detail()` 投影；脱敏必须发生在事件写入 SSE 之前。服务绑定 loopback 只是
网络暴露边界，不是身份认证；远程访问需要用户明确配置受控反向代理和同源/CSRF 策略，首版不内置
tunnel。SSE 订阅也要校验 session 所属身份，不能因为是 GET 就匿名暴露事件。

## 5. 前端交互约束

- 发送期间禁用按钮；列表与事件显示 accepted/running/completed/unknown 等状态，重试复用 request id。
- Tool call 显示只读/有副作用标识、operation id（若已公开）、耗时和回执状态。
- interrupt/close 是确认操作，完成前不从列表删除 session。
- 页面刷新后用最后一个 seq 回放事件，不要求 Codex 重跑 turn。
