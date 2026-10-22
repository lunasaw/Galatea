# 04 接口与可信调用

本章所有 `galatea_task.*`、App 工具、`/internal/v1/*` 和新增 Host 路由均为待实现接口。
MCP 工具遵循现有 AppBridge；内部 HTTP 只允许经认证的服务调用，不能直接开放给 iframe。

## 1. 信任与角色

| 调用者 | 身份来源 | 允许的操作 |
| --- | --- | --- |
| 用户 / App | Host 已认证主体 + 会话作用域 + 工具调用证明 | 读取所属任务，提交目标、控制与审阅决定 |
| Agent Provider | Host 构造的 ToolContext + Provider 服务凭证 | 在绑定任务内规划、开始阶段、报告结果、申请等待 |
| Observer Worker | 独立服务主体 + operation 范围 | 记录观察，明确授权时执行既有操作的确定性恢复；不能批准新预算 |
| Verifier | 独立服务主体 + 验证器登记 | 核验证据，不能自行创建执行授权 |
| Continuation Worker | 允许的 Host 会话范围 + Outbox 身份 | 提交事实型接续请求，不能改会话权限 |
| 治理管理员 | 治理系统现有可信通道 | 依治理路线创建或修改授权 |

所有读取也校验 owner/tenant。`task_id`、`thread_id`、`_meta.ui.visibility`、
`readOnlyHint` 和用户可见的批准文本均不是身份凭证。

当前 Host 的主体是管理员配置的固定 principal，不能把它描述为已经具备多用户登录认证。
首版按单 owner Host 实例部署，入口由认证代理保护；证明中的 owner 必须绑定该实例配置，
不信任客户端身份头。多个用户使用不同 Host 身份域及独立凭据。
共享 Host 内的多主体会话隔离需要另做认证与授权改造后才能开放。

## 2. MCP UI 资源与工具

MCP 服务名建议 `galatea_app`。入口工具返回结构化任务摘要，并声明：

```json
{
  "name": "open_workbench",
  "description": "打开当前会话的 Galatea 动态任务工作台",
  "inputSchema": {
    "type": "object",
    "properties": {"task_id": {"type": "string"}},
    "additionalProperties": false
  },
  "annotations": {"readOnlyHint": true},
  "_meta": {
    "ui": {"resourceUri": "ui://galatea/workbench/v1"}
  }
}
```

资源 MIME 为 `text/html;profile=mcp-app`。资源不能嵌入令牌、数据路径凭证或全部任务数据。
初始化后通过所属服务的工具读取授权范围内快照。资源本身即使可读取，也不能泄露业务信息。

| App 工具 | 参数要点 | 副作用 |
| --- | --- | --- |
| `open_workbench` | 可选 task_id | 无；取得 UI 与授权任务摘要 |
| `list_projects` | cursor、limit | 无；返回主体范围内项目、任务会话与状态摘要 |
| `get_workbench` | task_id?、timeline_cursor?、limit | 无；返回项目绑定、Task 快照、公开时间线和各源游标 |
| `get_workbench_events` | task_id、after_cursor、limit | 无；时间线新增/更新/移除，游标独立于 TaskEvent.seq |
| `list_tasks` | cursor、limit | 无；仅当前主体可见任务 |
| `get_task` | task_id、known_version? | 无；可返回 not_modified |
| `get_task_events` | task_id、after_seq、limit | 无；增量读取、过期游标明确告知 |
| `get_stage_details` | task_id、stage_id、cursor? | 无；分页操作、证据与受限日志 |
| `get_artifact` | task_id、artifact_id、view | 无；受控预览或资源引用 |
| `get_file_change` | task_id、change_id、cursor? | 无；不可变基线与分页 diff，不能传任意路径 |
| `get_training_proposal` | task_id、proposal_id | 无；方案、预算、有效期、就绪条件与当前待办 |
| `launch_project` | goal、inputs、constraints、command_id | 新建项目/草稿、受限目录和 Host 激活意图；需要用户确认 |
| `activate_project_task` | project_id、task_id、command_id | 请求 Host 激活真实绑定并重新挂载；不重跑任务 |
| `get_activation` | activation_id | 无；查询初始化与会话激活收据 |
| `get_command_receipt` | command_id | 无；仅原主体可查，供未知受理状态对账 |
| `create_task` | goal、inputs、constraints、start、command_id | 解析输入引用，创建并按选择排队开始；需要用户确认 |
| `request_control` | task_id、action、expected_version、reason、command_id | start/pause/interrupt_agent/resume/cancel；需要用户确认 |
| `submit_input` | task_id、request_id、values、expected_version、command_id | 补充当前等待的信息；需要用户确认 |
| `record_review` | task_id、result_digest、decision、command_id | 记录明确结果的用户审阅；不是训练授权 |

App 专用工具可以标记 `visibility: ["app"]`，服务端仍必须实施鉴权。
所有写工具声明 `readOnlyHint: false`，避免现有 Host 按只读跳过确认。
模型直接调用 `open_workbench` 可取得不含业务数据的 UI 入口；缺少可信证明时，
任何任务读取、创建、控制都必须拒绝，不能退回相信传入的 session_id。

## 3. 新增通用 Host 工具调用网关

现有 AppBridge 的工具调用会把 App 提交的 `_meta` 透传，前端确认也不是可持久验证的业务收据。
完整方案需要在 Host 新增通用网关，替换 AppBridge 对业务工具的直接调用路径：

```text
App.callServerTool
  -> Host AppBridge（保留原服务、原会话、原工具白名单）
  -> Host /api/mcp-app/tool-calls（新增）
  -> Host 按真实会话生成 call-proof
  -> 原生 mcpServer/tool/call
  -> 所属 MCP 服务校验证明
```

网关不理解 Task、预算或 Galatea 阶段，只证明调用主体与用户确认的精确内容：

- 认证的 principal、host_id、session_id、thread_id、app_instance_id。
- 已登记 MCP server 身份及工具名；audience 绑定服务身份，不只绑定可重命名的显示名称。
- `call_id`、规范化参数 digest、签发与过期时间、nonce。
- 写调用的 `interaction_id`、`confirmed` 决定、决定时间及相同参数 digest。

建议使用管理员登记的 Ed25519 公钥校验、短时有效的签名证明；密钥留在 Host，
MCP 服务只持公钥。签名算法和 key rotation 在部署实现时固定，拒绝未知算法和过期 key。
证明放入保留的 `_meta["io.codex.host/call-proof"]`，不进入模型工具参数或页面。
Host 必须丢弃 App 自带的同名字段并重新生成，日志仅记录证明 ID 和 digest。

Host 的确认挑战由后端创建，绑定主体、会话、工具和参数；在“请求批准”模式下，用户在 Host 原生界面确认后，
通过有认证和 CSRF 防护的确认端点完成。模型和 iframe 不能直接调用确认端点。
未确认、否决、过期或参数改变均不能执行写调用。重试同一调用复用同一个交互及命令身份。

选择“完全访问权限”或“帮我批准”时，网关按 Host 保存的当前会话及回合权限自动确认
App 写调用，保留参数绑定的签名收据，并标记 `approval_source=permission_profile` 和权限模式。
Galatea Worker 可通过同一网关自动提交当前训练方案的“授权”和“确认执行”决定，不要求打开 App。
“请求批准”仍要求在 App 中手动处理；未知或自定义权限组合也继续要求确认。每次派发前重新检查权限，
切回“请求批准”后未派发的自动确认失效，明确否决不会被权限模式覆盖。
Host 的自动确认不会扩大 Galatea 管理员委派范围；方案 digest、任务和方案有效期、预算、就绪检查、
用户授权能力和幂等收据仍由服务验证。只有精确确认成功后才会接续任务。

该设计是本仓库的通用 Host 增强，不声称 MCP Apps 标准已规定这种证明。
不使用未协商的自定义 iframe RPC，也不要求 App 访问 Host API；SDK 侧仍调用标准 `callServerTool`。
现有 Host 若未升级，只允许显示公开的能力说明，不能降级到无可信主体的任务写入。

## 4. Agent Task Provider 工具

命名空间 `galatea_task`，工具按 Host 动态工具目录显式登记。以下工具名称表示逻辑接口，
最终按当前 registry 的命名规则生成目录和 JSON Schema。

| 工具 | 关键参数 | 返回与校验 |
| --- | --- | --- |
| `create_task` | goal、inputs、constraints、command_id | 当前可信会话创建草稿；不扩大用户授权 |
| `get_current_task` | 无模型身份参数 | 按可信 Host/session/thread 查当前任务，无任务返回 null |
| `start_task` | goal、inputs（path 对象数组）、constraints、command_id | 在当前原生回合建立并开始准备，不创建会话、不排队后台 Agent；训练仍需独立确认 |
| `get_task` | task_id | Agent TaskContext：快照、计划规格、输入摘要、能力与授权摘要 |
| `replace_plan` | task_id、expected_plan_revision、reason、stages、command_id | 新版本及差异，检查计划规则 |
| `begin_stage` | task_id、stage_id、expected_plan_revision、command_id | Attempt、execution_scope_id、execution_token |
| `prepare_external` | task_id、attempt_id、execution_token、route、immutable_inputs、command_id | 先持久化执行意图，返回 intent_id 与外部幂等键 |
| `bind_external` | task_id、intent_id、operation_ref、command_id | 核验并绑定实际 operation，重复提交不会创建第二个绑定 |
| `report_result` | task_id、stage_id、attempt_id、execution_token、result、artifact_candidates、evidence_refs、command_id | 接受并进入 verifying，不直接承诺 completed |
| `wait` | task_id、attempt_id、execution_token、wait_spec、command_id | 等待输入、授权或外部操作的受控记录 |
| `propose_training` | task_id、stage_id、expected_plan_revision、proposal、command_id | 校验并保存不可变方案和确认待办；不要求先创建训练 Attempt，不创建治理授权 |
| `request_replan` | task_id、reason、command_id | 从合法状态进入 planning，不覆盖活跃执行 |
| `complete_task` | task_id、expected_plan_revision、summary、artifact_refs、command_id | 通过全部验收才完成 |
| `fail_task` | task_id、reason_code、evidence_refs、command_id | 无未知/活跃执行后方可失败封存 |

`artifact_candidates` 接受受限 workspace 文件引用或外部 Artifact 引用、角色、媒体类型和
可选的预期 digest；服务分配 Artifact/Evidence ID，由验证器核对实际内容。
Agent 不能提供 `verification=verified` 或直接写入规范化 Artifact 表。
验证完成后，声明的 output_slot 才绑定到产物；验证失败保留候选与原因供诊断。

Provider 的 Agent TaskContext 与 App 的纯快照区分：前者额外携带当前 StageSpec、
已安装 Skill/能力目录、可用于规划的输入元数据和授权缺口；敏感凭据不进入任何一种返回。
`begin_stage` 成功后附上该次执行可用的 resolved_inputs 和允许输出位置，
由 capability 将 InputRef 解析成受控本地路径或制品访问引用，不能让模型任意扩展根目录。

Provider 只从 Host `ToolContext` 读取 principal、session、thread、turn、call、catalog/config
digest、deadline 和 cancellation。模型不能通过参数传入或覆盖这些字段。
Provider 对 Task Service 的请求采用服务认证，附上可信上下文；服务验证绑定会话及允许动作。
Host 的 `actions` 只是一层能力限制，不能替代 Task 的用户范围和外部业务授权。

Host 的 journal `call_id` 与原生 `native_call_id` 不一定相同，必须分字段保存与映射，
不能把 Provider 收到的 journal hash 直接拿去匹配原生工具事件。

执行令牌是服务端登记的不可预测能力引用，绑定 task/stage/attempt、plan_revision、
control_epoch 和租期。它只授权报告该次执行，不授权训练或新增路径。
令牌必须脱敏；失效令牌不能改变业务状态，旧结果可作为隔离的历史观察保存。

## 5. 命令与错误协议

Provider 调用映射到 `POST /internal/v1/tasks/{task_id}/commands`。创建使用
`POST /internal/v1/tasks`。所有命令体必须有 schema_version、command_id 和具体命令：

```json
{
  "schema_version": "galatea-task-command/v1",
  "command_id": "cmd_begin_inspect_1",
  "command": "begin_stage",
  "expected_plan_revision": 1,
  "payload": {"stage_id": "stage_inspect"}
}
```

可信上下文在认证封装中传递，不混入上述模型参数。成功返回：

```json
{
  "ok": true,
  "receipt_id": "receipt_demo_1",
  "task_id": "task_demo",
  "version": 3,
  "plan_revision": 1,
  "event_seq": 4,
  "result": {"stage_id": "stage_inspect", "attempt_id": "attempt_inspect_1"}
}
```

写请求以 `(principal_id, command_id)` 去重，digest 必须覆盖命令、task、前置版本和 payload。
相同命令键不同内容返回 `IDEMPOTENCY_CONFLICT`；明确版本冲突后重新读取再生成新命令键。
网络超时重试原键，不能换键绕过去重。跨主体的收据不能复用。

| code | HTTP 映射 | Agent / App 行为 |
| --- | --- | --- |
| `UNAUTHENTICATED` / `FORBIDDEN` | 401 / 403 | 停止，修复身份或权限，不自授予 |
| `NOT_FOUND` | 404 | 不泄露其他主体对象是否存在 |
| `CURSOR_EXPIRED` | 410 | 重取快照，使用其事件游标 |
| `VERSION_CONFLICT` / `PLAN_CONFLICT` | 409 | 获取快照并重新决策 |
| `IDEMPOTENCY_CONFLICT` | 409 | 纠正调用方缺陷，不覆盖原收据 |
| `INVALID_TRANSITION` / `INVALID_PLAN` | 422 | 返回可操作约束与失败字段 |
| `AUTHORIZATION_REQUIRED` | 403 | 进入等待批准，列出所缺治理证据 |
| `EXECUTION_UNKNOWN` | 409 | 进入对账，不重新提交外部写入 |
| `STALE_EXECUTION` | 409 | 停止旧执行，隔离迟到结果 |
| `CAPABILITY_UNAVAILABLE` | 422 | 明确降级或阻塞，不伪造支持 |
| `RATE_LIMITED` / `UNAVAILABLE` | 429 / 503 | 根据 retry_after 有界重试 |

MCP 工具错误使用 `isError` 并在结构化内容给出相同 code、retryable、receipt_id 和提示。
不得在错误中回显密钥、原始敏感样本或其他主体路径。

## 6. 查询、事件与后台内部入口

Task Service 对内部适配器提供：

| 接口 | 用途 |
| --- | --- |
| `GET /internal/v1/tasks/{id}` | 授权快照，支持 ETag/If-None-Match |
| `GET /internal/v1/tasks/{id}/events?after_seq=N&limit=L` | 增量分页；响应 next_seq、has_more |
| `POST /internal/v1/observations` | Worker 幂等提交外部事实，要求观察主体与 operation 绑定 |
| `POST /internal/v1/verifications` | 登记验证结论，绑定证据 digest 和验证器版本 |
| `POST /internal/v1/runtime-events` | 批次消费 Host 事件，按 host/epoch/seq 去重 |
| `GET /internal/v1/receipts/{id}` | Provider / Worker 对账，限制原主体和作用域 |

MCP App 只能通过同服务工具包装查询。现有 Host 限制 App 不能直接调用另一个 MCP 服务；
需要的治理摘要由 Galatea 服务的适配器读取后返回，不能让 iframe跨服务拼接凭证。

## 7. 新增通用 Host 接续 API

`POST /internal/v1/continuations` 使用专门的服务认证，接受：

```json
{
  "schema_version": "codex-continuation/v1",
  "request_id": "cont_demo_observation_8",
  "host_session_id": "session_demo",
  "binding_id": "binding_demo",
  "not_before": "2026-10-19T03:00:00Z",
  "expires_at": "2026-10-20T03:00:00Z",
  "context": {
    "kind": "untrusted",
    "source": "galatea-task-observer",
    "task_id": "task_demo",
    "event_seq": 18,
    "summary": "外部操作已返回终态，请读取任务与证据后继续。"
  }
}
```

Task 启动前，集成通过新增 `POST /internal/v1/continuation-bindings` 登记通用 binding：
服务身份、Host 会话、来源任务引用、允许的接续类型、期限和最大次数。
Host 验证其引用的真实用户启动收据，绑定到该收据的会话；新项目则使用 §8 激活收据中
已批准的新会话。服务不能任意指定另一个 thread。
登记幂等，Host 返回 binding_id 后 Task Service 再放行启动 Outbox，取消时撤销该绑定。
该绑定是通用后台来源权限，Host 不解析 Galatea 计划或训练预算。

Host 验证服务可写的会话范围和持久化 binding，记录 inbox/receipt 后返回 `202 accepted`。
接受排队不代表原生 turn 已开始，也不代表业务成功。响应提供 receipt_id 与状态查询地址。
Host 在会话 ready 后，使用已有 `send_message` 请求日志机制启动唯一的原生 turn。
后台接续第一版不 steer 运行中的 turn；忙碌时保持排队，避免覆盖用户输入。

新增 `GET /internal/v1/continuations/{request_id}` 与取消排队接口供对账。
状态为 accepted、queued、dispatching、started、completed、cancelled、expired、unknown；
其中 completed 只表示该次原生 turn 终结。binding 被撤销后必须拒绝尚未启动的接续。
原生 timeout 的 unknown 按 [接续恢复](05-execution-and-continuation.md) 处理，不自动重放。

## 8. 原型工作台所需的新增接口契约

### 8.1 公开消息与文件投影

Host 通用事件导出增加允许披露的 user/assistant 消息、message_id、turn_id、内容 revision、
流式/终结状态和原生文件变更引用。Task Service 建立 WorkbenchView 投影并经同服务工具提供。
iframe 不直接请求 Host 内部 API，不读取父页面 DOM，不声称 App SDK 原生提供聊天历史。
get_workbench_events 响应 items、next_cursor、has_more、coverage，含 message upsert，
保证已存在消息的流式更新不被遗漏；同步缺口和游标过期显式返回。

get_file_change 返回相对路径、操作、base/current digest、可用 revision、分页 hunks 与检查结果，
所有内容在变更发生时留存。原生事件未含完整内容时，由受限工作区适配器获取并验证；
不能事后读取当前文件冒充原变更。读取不了则返回 unavailable_reason，不能构造示例 diff。

### 8.2 新项目和跨会话激活

launch_project 是“发起项目”的入口，create_task 保留为当前已绑定会话内建任务的接口。
launch_project 在同一事务登记项目初始化、Task 草稿、目录分配意图和 command receipt；
后续使用同一 activation_id 幂等推进，所有步骤可查询、可对账。

新增通用 Host 服务接口 POST /internal/v1/app-activations，接受注册集成身份、原调用收据、
activation_id、目标已登记 workspace_ref、existing_session_ref 或 create_session 意图、
App 资源身份、来源任务不透明引用与期限。Host 校验用户确认范围和目录允许范围，
由原生会话能力创建/恢复 session，不能信任 iframe 提供的 thread_id。
目录分配必须先完成且归属已验证，才将实际 cwd 绑定新会话。

Host 保存激活收据，经通用前端状态通道通知所属用户页面切换；新会话下重新建立 App 实例和调用证明。
切换期间旧 App 禁止写操作，旧 generation 的响应作废。返回的 activation_id 是查询身份，
不是允许 iframe 改绑的能力令牌。用户切到别处或页面关闭时不强行抢回焦点；后台完成激活准备，
下次打开从收据恢复。一次用户发起许可可包含该次新会话初始化与任务启动，不重复索要同一许可。

GET /internal/v1/app-activations/{id} 返回 validating/allocating/binding/ready/failed/unknown、
真实绑定和错误原因。未知原生会话创建结果先对账，不能重复创建会话。
activate_project_task 走同一激活机制，只解析已存在任务，无新的启动 continuation。
这些属于新增通用 Host 集成，不是 MCP Apps 标准会话切换能力；Host 不解析项目阶段或预算。

### 8.3 方案确认、调整与延后

Agent propose_training 保存方案并建立 kind=training_proposal 的 PendingRequest。
App submit_input 使用 request_id、expected_version、values.proposal_digest 与
values.decision=confirm/defer；Host 证明覆盖所有字段。服务检查请求归属、状态、期限、
方案 digest、计划/输入版本和实际治理授权，事务内写决定与后续 Outbox。

confirm 是执行确认，不能签发治理授权；已有效预授权才进入 ready/运行排队。
defer 关闭待办、暂停推进、保留代码与方案；无活跃操作时直接 paused，存在提交竞态则拒绝
作为延后处理，返回实际执行状态并提供取消入口，不能声称未提交。
“调整方案”先在 App 本地预填文本，再经 ui/message 交给 Agent；新方案 supersede 旧待办。
“重新查看方案”只读。对已 deferred 方案再次明确 confirm，可在同一幂等事务新建关联待办并确认，
前提是 digest、版本、授权仍有效；任一改变返回刷新后的卡片，等待针对新内容的确认。

interrupt_agent 使用 request_control 的可信写通道，依赖 pause 权限和 Host interrupt 能力；
持久化控制 epoch 与暂停请求后再发原生中断，具体状态见第 05 章。

`GET /internal/v1/continuation-bindings/{binding_id}` 是同一服务身份下的只读状态查询。
返回 binding 状态、绑定会话/当前线程、session_status、active_turn_id、last_turn_id、
last_turn_status 和 pending_continuation。最后一项包括 queued、dispatching、started 及
unknown；已有原生终态收据的记录不再算 pending。接口不返回提示词、启动收据、凭据或工具参数。
Task 必须同时检查绑定/会话身份、撤销/过期状态和队列，不得仅凭旧的 turn/completed 事件接续。
