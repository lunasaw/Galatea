# 05 网页聊天、HTTP、SSE 与追踪台

> **历史 Console 设计（2026-10-16 标注）**：下文“当前/本期/首版”指重构前版本，
> 不作为现行功能或部署依据。当前为通用 Host，无内置 Galatea Adapter；
> Skill/MCP、前端构建和 SQLite 观察存储以现行组件文档为准。
> 当前边界见 [总体架构](../../agent-galatea.md)、[Host 文档](../../../agents/codex-app-server/docs/README.md)。

本期页面是现有训练 Agent 的浏览器入口：使用既有 Host 会话 API 聊天，并读取观察 API
检查 Codex 协议事件和 Galatea 训练工具轨迹。它不直接连接 Runtime，也不提供新训练、
在线推理、Skill 管理或模型能力调优界面。

## 1. 服务入口

在现有 `ConsoleApp` 扩展路由，静态页面与 API 同源，仍使用 HTTP + SSE。
页面直接打开，无登录表单、Token 输入、Bearer 请求头或 token 文件前置条件。
监听地址/端口可配置，拟用默认 `0.0.0.0:18880`，从浏览器访问 `http://<服务器IP>:18880/`。

移除现有只允许回环地址的 CLI 检查；Console API 不再用 Authorization 或 Origin 白名单
作为访问凭据。前端正常使用相对路径访问同源接口，首版不需要额外跨域接入或网关。
Host 仍使用配置 principal 访问平台；已有 Idempotency-Key 是防重复提交机制，继续保留。
非回环端口开放前，防火墙/网络 ACL 必须只允许可信内网来源；无鉴权的写接口对所有可达客户端
共享同一 Host principal，不能面向公网部署。写接口保留同源来源检查（Origin/Fetch Metadata），
不开启跨域 CORS，以减少浏览器跨站误调用；这不是身份认证，不能替代网络隔离。

协议路由沿用 `/api`，不复制参考项目的 task/shop 字段或 `/console` 路径前缀。
新增 API 结构版本通过 `/api/capabilities` 的 `console_schema_version` 声明。
能力响应和页面文案应标明本期角色为“训练 Agent 网页聊天与追踪台”，只展示实际可用的
Host 工具/交互；不得把仓库中的 Skill 文件、Console 追踪能力或 `evaluate` 作业自动宣传为
已接入 Skill、通用模型推理服务或优于原生 Codex 的模型效果。

## 2. API 清单

### 保留现有会话控制

| 方法与路径 | 语义 | 本次处理 |
| --- | --- | --- |
| `GET /health/live` | 服务进程存活 | 保留 |
| `GET /health/ready` | Host/Runtime/Registry 可用 | 保留原就绪语义；观察状态单列 |
| `GET /api/capabilities` | Host/工具能力 | 增加 Console 契约、观察 backend、交互能力声明 |
| `GET /api/sessions` | Host 持久化会话列表 | 保持 Host 为事实源；事件为空不隐藏会话 |
| `POST /api/sessions` | 新建/幂等返回会话 | 保留；生成观察关联 context |
| `GET /api/sessions/{id}` | 会话权威状态 | 保留 |
| `POST /api/sessions/{id}/messages` | 发消息、返回 request/turn 身份 | 保留文本输入及 request_id 规则 |
| `POST /api/sessions/{id}/interrupt` | 请求中断当前 turn | 保留；不把 HTTP 回执当作中断完成 |
| `POST /api/sessions/{id}/close` | 关闭会话 | 保留；保留本 epoch 已捕获的历史 |
| `POST /api/sessions/{id}/resume` | 核对后恢复会话 | 保留 Host 原有恢复核验 |

所有写入保持原有持久化幂等：相同 key、路径、身份和 body 重试返回既有结果，冲突返回 409。
去掉登录后身份为配置中的 Host principal，不由客户端提交新的 principal。
浏览器按钮重试沿用同一 key，不能为一次请求自动生成多个不同 key。

### 新增/改造观察接口

| 方法与路径 | 返回与用途 |
| --- | --- |
| `GET /api/console/status` | epoch、runtime_instance_id、存储模式、保留策略、观察健康、捕获/提交水位、积压和内存计数 |
| `GET /api/events/history` | 当前 epoch 的全局事件分页，覆盖无 session 事件，可按方向/method/kind/session/turn 筛选 |
| `GET /api/events/stream` | 全局 SSE，提供事件和提交水位 |
| `GET /api/events/{event_id}` | 单事件完整可展示详情及脱敏说明；ID 必须 URL 编码 |
| `GET /api/sessions/{id}/snapshot` | 当前 session 对话/Loop 首屏、历史覆盖范围、绑定版本和一致水位 |
| `GET /api/sessions/{id}/history` | 该 session 的事件分页，采用统一事件信封 |
| `GET /api/sessions/{id}/events` | 改造原有 SSE，采用同一事件信封与 epoch 游标 |
| `GET /api/sessions/{id}/messages` | 对话历史分页；同路径 POST 保持发消息语义 |
| `GET /api/sessions/{id}/loop` | session 的 turn/步骤分页，替换旧字段裁剪结果 |
| `GET /api/sessions/{id}/loop/{turn_id}` | 指定 turn 的 Loop 步骤分页与水位 |

首屏 snapshot 返回有限条最近消息/步骤及分页令牌，不复制整个进程历史到浏览器。
历史分页可取早先消息；原生事件提供 seq 升序读取。分页令牌必须绑定 epoch、scope、filter 和
查询水位，翻页中不因新事件造成遗漏或重复。`snapshot` 和派生分页实现同一水位的查询合同。

默认页大小可以采用 200 条、最大 1000 条，属于实施参数而非历史保留限制；单条有效长事件
仍完整返回。筛选精确匹配结构化 method/type/direction，前端标签不改变原始方法名。

旧版 `/events` 整数 seq 与新 epoch 游标不兼容，同步更新服务内置前端、测试和现行文档。
明确拒绝旧游标，不添加猜测转换。现有消息操作路径及请求字段无需更名。

## 3. 状态与错误合同

建议 status 结构：

```json
{
  "console_schema_version": "galatea.console/v1",
  "console_role": "training_agent_chat_trace",
  "epoch": "epoch-demo",
  "runtime_instance_id": "runtime-demo",
  "storage": {"backend": "memory", "retention": "process_lifetime"},
  "skill_integration": false,
  "inference_endpoint": false,
  "native_codex_comparison": "not_claimed",
  "principal_scope": "host_configured",
  "network_access": "trusted_network_required",
  "host_ready": true,
  "observation": {
    "status": "ready",
    "captured_seq": "42",
    "committed_seq": "42",
    "pending_events": 0,
    "event_count": 42,
    "encoded_bytes": 58021,
    "error": null
  },
  "interactive_requests": false
}
```

观察状态至少区分 `ready`、`lagging`、`incomplete`、`unavailable`。
返回明确错误码和当前 epoch，不把服务错误转为“没有事件”。

| HTTP 状态/错误码 | 条件 | 前端动作 |
| --- | --- | --- |
| 400 `INVALID_CURSOR` | 格式错误、负数、超过字段合同 | 停止该次请求并显示错误 |
| 409 `CURSOR_AHEAD` | 当前 epoch 中游标超出已提交水位 | 重新取 status/snapshot，不静默夹到水位 |
| 409 `EPOCH_MISMATCH` | 服务重启，客户端仍持有旧 epoch | 明示历史重置，清除旧视图，取新快照 |
| 409 `PAGE_CONTEXT_MISMATCH` | 翻页令牌与 scope/filter/水位不一致 | 重新开始该查询，不混入另一组历史 |
| 404 `UNKNOWN_SESSION` | Host 中不存在会话 | 刷新会话列表 |
| 404 `UNKNOWN_EVENT` | 当前 epoch 内不存在该事件 | 显示无法找到，不请求 Runtime 重放 |
| 429 `STREAM_LIMIT` | 超过当前 HTTP 订阅限制 | 退避重连；不影响采集和其他订阅 |
| 503 `OBSERVATION_UNAVAILABLE` | 无法提供可靠观察结果 | 显示观察故障，可继续查看 Host 权威状态 |
| 409/503 既有控制错误 | 幂等冲突、Host 未就绪或需要核对 | 按原有控制接口语义显示 |

`ready` 不代表 Runtime 当前必有输出；`host_ready=false` 时仍应能查看故障前已提交历史。

## 4. SSE 与断线续读

继续采用 `fetch` + `ReadableStream`，支持可取消请求、状态码处理及明确游标。
不需要 Authorization；`Last-Event-ID` 和 `after` 都可携带公共 cursor，二者同时存在时必须一致。
采用下面的帧类型：

```text
id: epoch-demo:42
event: observation
data: {"event_id":"epoch-demo:42", "...":"完整事件信封"}

id: epoch-demo:45
event: cursor
data: {"cursor":"epoch-demo:45"}

event: status
data: {"observation_status":"lagging","pending_events":3}

event: snapshot_required
data: {"reason":"binding_changed","binding_version":4}

: keepalive

```

`cursor` 帧表示此次已扫描且已交付完匹配事件的水位，解决过滤后长时间没有记录时无法前进的问题。
它不是一个新的原生事件。status/keepalive 不推进客户端业务游标。
binding/status 控制帧不受原生方法筛选影响；SSE 在返回 observation 前处理需要重载的绑定版本变化。

基本流程：

1. 读取 status 和会话列表，选中 session 后取得 snapshot 及水位 H。
2. 渲染 snapshot，以 `after=H` 订阅。全局事件页首次加载历史到水位 H 后同样订阅。
3. 服务端先按游标回放，再等待水位变化。分页发送现有历史，SSE 慢不会阻塞采集 worker。
4. 前端按 `event_id` 幂等归并；delta 重复重连不重复追加。处理完一个事件/游标帧后才更新游标。
5. 连接中断，按最近处理完成的 cursor 退避重连，不重新发消息或重新运行 turn。
6. 新 epoch 返回 `EPOCH_MISMATCH`，显示“服务已重启，上一进程的事件历史已清空”，重新加载
   Host 会话及新快照；不会把旧 turn 展示为新进程已经完成的消息。
7. 收到 `snapshot_required` 时暂停该 session 流，重新读取一致快照后从新水位继续。

SSE 已发送响应头后的故障使用控制帧（可写时）并关闭连接，不能再发第二个 HTTP 响应。
慢连接发送超时只断开该连接，历史仍在内存；重连可以补齐。
断线清理对应等待器与 task，不能取消全局观察 writer。

UTF-8 和 SSE 解析需支持跨 chunk 多字节字符、半帧、多帧、注释、空行与多行 data；
不能沿用只取第一个 `data:` 行就认为完整的解析器。

## 5. 前端信息组织

基于当前静态页面改造，复用参考项目的“对话 + 执行流程 + 原生事件”思路。

| 区域 | 内容与操作 |
| --- | --- |
| 会话列表 | Host 会话、状态、当前 turn；新建与选择，关闭会话仍可查看历史 |
| 对话 | 用户输入、完整 assistant 流式回复、错误与状态；发送/中断/关闭/恢复 |
| 执行流程 | 按 turn 展示计划、公开摘要、工具调用/结果、用量和终态；字段可展开 |
| 训练工具轨迹 | 关联现有 Galatea 工具名、调用/回执、operation/job 身份及回执中最近已观察到的可公开状态；未触发工具时明确显示没有调用 |
| 原生事件 | 所有方法，方向、类型、时间、ID、关联、JSON 与脱敏项；支持结构化筛选 |
| 运行实例事件 | 未归属 session 的初始化/配置/异常等事件；可跳转到关联后的会话 |
| 状态信息 | 当前观察 epoch、存储模式、完整性、事件数量及积压；重启、错误、能力边界和共享 principal 提示 |

普通页面只显示任务与对话所需内容；RPC ID、JSON 和存储计数放在事件/诊断区域。
原生未知事件使用通用卡片，不阻断对话和已知事件渲染。
审批/权限/提问事件显示“当前版本不提供页面回复”，同时展示 Host 的实际处理结果，不出现假按钮。

前端按需加载长列表和长详情，可释放屏幕外 DOM；服务端仍保留所有事件，用户可以再次翻页获取。
消息/工具内容以纯文本或受控渲染展示，不将原始 HTML 插入 `innerHTML`。
首版不扩展图片上传、附件管理、店铺上下文、人工交接、账户管理或隧道状态界面。
页面不增加 Skill 安装/开关、训练参数编辑器或独立推理 Playground。对话区的“成功”只表示
当前 turn 按 Runtime 实际状态完成；训练任务是否成功要看对应 Galatea 作业/工具回执。
Console 不在后台直接轮询 Ray/MLflow；若没有新的 `galatea_observe_job` 等工具回执，页面应
标明该状态的观察时间，不能把旧状态当成实时作业状态。
