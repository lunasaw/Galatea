# 05 Console

更新：2026-10-15。网页聊天与追踪台已实施；目标为用户指定的 Codex 0.154.0。
测试证据、边界和未完成的现场验收见 [Console 验收记录](13-console-acceptance.md)。

Console 与现有 Python ASGI Host 同进程，静态 HTML/CSS/JavaScript 随 wheel 发布。
无 Node 构建依赖、Redis、隧道或第二个 Agent Loop。所有访问者共享配置的 Host principal。
页面无需 Token；部署必须以网络 ACL/防火墙限制可信来源，同源校验不能替代网络访问控制。

## HTTP 与 SSE

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health/live`、`/health/ready` | 进程存活、执行就绪；后者不受观察存储故障冒充 |
| GET | `/api/capabilities`、`/api/console/status` | 能力边界、epoch、Runtime 实例、采集/提交/积压与观察健康 |
| GET/POST | `/api/sessions` | 持久化会话列表、创建 |
| GET | `/api/sessions/{id}` | 执行状态与恢复信息 |
| POST | `/api/sessions/{id}/messages`、`interrupt`、`close`、`resume` | 沿用现有控制及持久化幂等 |
| GET | `/api/events/history`、`/api/events/stream` | 全局历史、SSE；`unbound=true` 查询运行实例未绑定消息 |
| GET | `/api/events/{event_id}` | 完整脱敏事件详情 |
| GET | `/api/sessions/{id}/history`、`events` | 会话历史、SSE |
| GET | `/api/sessions/{id}/snapshot`、`messages`、`loop`、`loop/{turn_id}` | 对话、步骤和 Turn 投影；支持分页 |

写请求带 `Idempotency-Key`，消息可以提供同值 `request_id`；复用键但正文不同仍报冲突。
键和回执继续落盘，Console 重启不清空执行幂等。POST 拒绝跨源 Origin/Fetch Metadata，关闭 CORS；
无 Origin 的非浏览器客户端允许使用。没有 cookie 或应用层访问者身份验证。

事件查询支持 `after`（不含）、`until`（含）、`limit=1..1000`，以及 method、kind、direction、
source、session_id、thread_id、turn_id、item_id、call_id、unbound 精确过滤。
`seq` 使用十进制字符串，游标为 `epoch:seq`，跨 epoch 报 `EPOCH_MISMATCH`。
无匹配事件时 next_cursor 仍推进；下一页复用原 watermark 作为 until，避免混入新事件。

快照默认返回各视图最近 200 项，`view=messages|steps|turns|all` 可选视图。
`next_page` 绑定 session/turn/view/limit/水位，后续请求必须保持上下文一致。
前端按需渲染和翻页，不截断服务端正文、列表或历史。浏览器“更早”在固定水位读取前页；
返回最新视图后恢复流式更新。

SSE 使用 fetch 流。`observation` 承载事件，`cursor` 推进扫描游标，`status` 报告观察状态；
晚绑定引发 `snapshot_required`，会话订阅重新获取快照。控制帧不受 method 过滤抑制。
支持一致的 Last-Event-ID 或 after，冲突报错。UTF-8 decoder 和 SSE parser 处理跨块、多行与半帧。
每连接发送超时 10 秒，默认最多 32 个连接；慢读或断连只影响本订阅，观察写入继续。

## 采集与数据归属

`JsonRpcPeer` 在入站 JSON/envelope 解析后、业务处理前捕获副本；出站在 write 成功后、drain 前捕获。
initialize、双向 request/response/error、未知 method 和未绑定消息均记录；不建立第二个 reader。
出站记录只能证明写入本地缓冲，drain 失败诊断引用出站 event_id，不能当作执行成功。

`ConsoleObserver → ConsoleObservationService → ConsoleObservationStore` 以单写者有序提交。
内存适配器无 TTL、队列上限、数量淘汰或采样。原始事件和增量投影同批原子提交；稳定 batch_id
支持提交成功但确认丢失后的幂等重试。关联、脱敏、投影或存储故障明确显示 incomplete/unavailable，
观察故障不修改 Host 的执行结果，不触发工具重执行。

原文在入库前按键、类型及嵌套 JSON 脱敏，记录 JSON pointer 和原因；不包含凭据、私有 reasoning、
系统/开发指令、测试样本/标签、原始日志和完整 Artifact。公开 assistant 文本、公开摘要、普通长正文和
累计 totalTokens 保留。HTML 仅以 textContent 呈现。

message_accepted 按持久化 request 身份只出现一次；delta 累积而 completed 替换文本。
工具按 thread/turn/call 合并，展示实际回执中的 operation/job 和最近观察时间，不直接轮询后端。
Turn 保留实际 completed/failed/interrupted 状态；累计 Token 以最新快照替换。
审批、权限和提问请求及实际回执可查，本期没有页面回复入口。

Host 会话、HTTP 请求、工具 journal、进程记录和单写锁继续持久化。Console 事件、投影和订阅仅在当前
进程内；重启换 epoch 并显示历史已清空，不从旧 JSONL 或 Codex 私有存储补造历史。
后续 Redis 只替换异步存储端口；它不负责 Host 接管、socket、Future 或 Runtime 控制。
