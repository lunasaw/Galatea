# 03 事件协议与展示

本期事件能力服务于“网页聊天与追踪台”：让用户在浏览器读到同一 Codex Runtime 的回复，
并检查现有训练工具的调用和结果。事件只是观察记录，不是执行授权或训练/推理新入口；
从历史记录重放或翻页不得触发模型调用、工具重执行或 server request 的再次回复。

## 1. 完整性的定义

采集边界是当前 Host 与所托管 `codex app-server` 的 stdio JSON-RPC 连接。
每一条有效消息均保留一条协议观察记录，包含两个方向的 request、response、notification 和 error。
不依赖 `AgentHost.handle_notification()` 的业务白名单，也不只采集模型最终回复。

| 来源 | 覆盖内容 |
| --- | --- |
| Host → Runtime | initialize/initialized、thread/turn 操作、配置查询以及所有其他实际发送的消息 |
| Runtime → Host | thread/turn/item 通知、文本增量、用量、计划、warning/error、所有 server request 与 RPC 回执 |
| Host → Runtime 的回复 | 动态工具成功/失败结果、未知方法的错误回执，以及其他实际响应 |
| Host 内部观察 | HTTP 消息受理、会话状态、中断/关闭/恢复结果、Runtime 退出、观察状态、关联建立 |
| 未识别的方法/item | 保留方法名、方向、身份、完整可展示 payload，使用通用 JSON 卡片 |
| 无 session 的消息 | 放入“运行实例事件”，例如 initialize、启动配置查询和无法可靠归属的通知 |

协议错误或无法解析的输入以 `source=host, kind=protocol_error` 记录可用的错误元数据，
不伪造为成功解析的 native 消息。当前 stderr 被定向到 DEVNULL；本方案不把进程 stderr、Runtime
内部数据库或平台日志当作 Codex 协议事件。“所有”不承诺获得协议外未发送的数据。
同样，Runtime 没有通过该连接发送的 Skill 状态、模型内部过程或平台后台日志不能由 Console
补造；本期不启用 Skill，也不以事件页面证明 Skill 已参与任务。

事件数量完整、内容完整和业务成功是三个维度：

- 事件采集不按类型采样，服务存活期间不淘汰已存记录。
- 凭据/私有内容仍按明确规则脱敏，每次内容省略都有标记；普通正文、delta、参数和结果不因长度截断。
- `turn/completed` 中的实际 status、工具回执 success 和 Host 恢复状态分别展示，不能统一写成成功。

这里的“完整”是协议消息的类型与数量覆盖，以及脱敏后可公开内容的完整保留；它不意味着
泄露私有指令或推理、绕过 Galatea 数据权限，也不代表模型结果正确或训练作业完成。

## 2. 事件信封

以下为拟新增 HTTP 契约，字段与 Runtime 原始 JSON 分开。示例中的标识仅作说明：

```json
{
  "schema_version": "galatea.console.event/v1",
  "event_id": "epoch-demo:42",
  "epoch": "epoch-demo",
  "seq": "42",
  "observed_at": "2026-10-14T12:00:00.000Z",
  "source": "codex",
  "runtime_instance_id": "runtime-demo",
  "direction": "runtime_to_host",
  "kind": "notification",
  "method": "item/agentMessage/delta",
  "rpc_id": null,
  "session_id": "session-demo",
  "thread_id": "thread-demo",
  "turn_id": "turn-demo",
  "item_id": "item-demo",
  "call_id": null,
  "host_request_id": "request-demo",
  "correlation": "bound",
  "payload": {
    "method": "item/agentMessage/delta",
    "params": {
      "threadId": "thread-demo",
      "turnId": "turn-demo",
      "itemId": "item-demo",
      "delta": "已完成参数检查。"
    }
  },
  "redactions": []
}
```

约定：

- `seq` 是该观察 epoch 内全局递增的十进制整数字符串，避免 JavaScript Number 精度问题；
  `event_id=<epoch>:<seq>` 同时作为 SSE 游标。
- `epoch` 标识一次 Host 服务进程的观察生命周期；`runtime_instance_id` 标识一次 Runtime 连接。
- 时间由 Host 记录，输出 UTC ISO 8601，浏览器本地化展示；排序使用序号，不使用墙钟。
- `kind=request/response/notification` 表示协议类别；response 是否失败看 `payload.error`。
  Host 事件使用单独类别，例如 `message_accepted`、`session_state`、`binding_changed`。
- response 本身通常没有 method；信封中的 method 从配对请求补齐，payload 不额外伪造 method。
- `rpc_id` 保持原始数字或字符串类型，不能把 `0` 当空，也不能把数字 `1` 与字符串 `"1"` 合并。
- `host_request_id` 是 HTTP/Host 的消息请求 ID，不能与 JSON-RPC id 或工具 callId 混用。
- ID 无可靠来源时填 null、correlation 标为 `unbound`，不生成貌似真实的 thread/turn/item ID。
- 已存的协议 payload 不变；后续补齐关联以追加绑定事实和查询投影表达，不重写历史协议消息。

## 3. 收发观察顺序

### 入站

reader 取得消息并完成 JSON/envelope 解析 → observer 捕获副本 → 原有 response 匹配、
server request 分发或 notification 状态机。observer 必须位于业务过滤之前。
即使业务层随后拒绝错误 turn 或未知 request，入站消息和实际错误回执仍可查看。

### 出站

在写锁内、`writer.write()` 成功返回之后、`await writer.drain()` 之前捕获实际序列化的消息。
此时只能说明已写入本地 pipe 缓冲，不能表示 Runtime 已收到或执行。
`write()` 失败时生成发送失败诊断并保留可展示的尝试消息；`drain()` 失败追加故障事实并引用
出站 event_id。RPC 响应或 Host 对账才提供执行结果。

在发请求之前先登记待关联 RPC context，防止 Runtime 很快回复时缺少请求关联。
observer 不创建第二个 stdin/stdout reader，不改变 JSON-RPC envelope、ID、调用顺序和超时语义。
入站原字典与观察副本不得共享可变嵌套对象；脱敏不能修改业务处理的数据。

## 4. 关联规则与乱序

配对键为 `(runtime_instance_id, 请求发起方向, rpc_id 类型, rpc_id 值)`。
Host 发出的 request 与 Runtime 发出的 request 拥有独立 ID 空间；response 查对向请求。
请求完成、连接终止时释放活跃配对表；完整历史继续保留在观察存储。

会话关联优先使用显式 Host context，其次使用协议中的 thread ID 和已经确认的绑定。
同时支持 `params.threadId`、`params.thread.id`、`result.thread.id` 等已经核实的协议结构。
turn/item/call 采用同样的结构化提取；未知结构仍保存 payload，但不猜测关联。

`thread/start` 请求的 Host context 预先包含 session_id；成功响应建立 session/thread 映射。
`turn/start` 的本地 context 包含 session_id 和 host_request_id。`turn/started` 可能先于响应到达，
不能等 response 到来才开始采集。旧 turn 的迟到回执按其原有 turn/request 映射归属，
不能挂到 session 此刻的 active_request。

无可靠上下文的早到 thread 事件先作为运行实例事件存储。后续得到绑定时：

1. 将 `binding_changed` 作为新序号的 Host 观察事件提交。
2. 同一提交更新 session/thread 索引及派生视图，原事件 ID 和 payload 不变。
3. 全局订阅收到绑定事件后按当前映射更新展示；session 订阅收到 `snapshot_required` 控制帧，
   重新读取快照及水位，避免旧序号的新增关联被游标跳过。

这也是新建会话期间通知先于响应、启动后恢复旧 thread 时的统一处理方式。
不为“没有匹配 session”而丢弃事件，也不把广播通知强行分配给最近一个 session。

## 5. 字段展示规则

参考项目的脱敏思路可复用，其固定 64000 字符截断及深度截断不能直接沿用到本需求。

| 内容 | 处理 |
| --- | --- |
| 用户消息、assistant 文本与增量、执行计划、公开推理摘要 | 可展示并完整保留可公开文本 |
| 动态工具参数与结果、公开命令输出 | 按字段规则脱敏后展示，长内容按需展开 |
| Token/API key/password/cookie、后端凭据、签名 URL | 替换为明确标记，并列出 JSON Pointer 及原因 |
| system/developer 指令、私有 reasoning/加密推理字段 | 保留事件外壳，正文省略并标明原因；公开摘要单独保留 |
| 受保护测试样本/标签、后端原始日志、Artifact 原始内容 | 沿用平台既有展示边界，保留事件与省略原因 |
| Skill 包内容或配置 | 本期不安装/启用/注入；若 wire 消息偶然携带相关字段，仍按配置和私有内容规则处理，不将其标为已接入能力 |
| 未知 method/item | 通用脱敏后展示，不以“前端不认识”为由删除 |

脱敏发生在存储可查询记录之前；队列中短暂处理的内部副本不能写日志或返回浏览器。
工具结果中以字符串嵌套的 JSON 也经过相同投影。用量中的 `totalTokens` 等统计不能被粗糙的
`token` 子串匹配误删。规则需要覆盖既有合同及代表性夹具，不能声称可以识别任意自由文本中的秘密。
投影失败保留事件身份和 `content_unavailable` 原因，同时报告完整性异常，不把异常当作空成功事件。

## 6. 派生视图

原生事件日志是观察事实源，对话和 Loop 只是可重建的显示投影。

- 消息以 `(session, turn, item)` 聚合；assistant delta 顺序追加，`item/completed` 的完整内容
  用作最终快照替换，不能在 delta 后再次追加完整文本。
- 用户输入仅在 Host 已受理后进入正式对话；HTTP 重试返回原回执不再次追加消息。
  未受理输入只作为当前页面草稿/错误保留。
- Host 受理消息与原生 userMessage 用 request/turn/item 绑定去重；不能用文本相同判断同一条消息。
- 工具按 thread/turn/call 聚合请求及响应；原生 item 与 Host 工具摘要关联后作为一个工具步骤，
  原始双向消息依然分别存在。
- turn 终态按实际 completed/failed/interrupted 展示；缺少终态时显示运行中或未知，不能因 EOF 推断成功。
- `thread/tokenUsage/updated` 原始快照完整可查；页面显示最新累计值，不能把累计快照逐条求和。
- 计划、公开摘要和 warning/error 有专门卡片；不支持操作的审批/提问仅显示实际请求和实际 Host 回执。
- 完整消息、工具结果及事件详情保存在服务内存；浏览器的折叠和分页不改变服务端记录。

对话区只显示经 Runtime 实际返回的 assistant 文本；工具区显示既有 ToolRegistry 的实际调用、
回执及可公开作业状态。二者不能把未发生的训练、评估或推理步骤补成“成功”。

重启后的持久化 session 可以存在，但当前 epoch 无旧消息。页面显示“本次启动暂无历史事件”，
不能拿旧 JSONL、Host 的 payload digest 或重新调用模型补成历史对话。
