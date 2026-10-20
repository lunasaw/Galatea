# 03 领域模型与状态机

## 1. 标识与版本

所有标识使用服务生成的不可预测 ID。`task_id` 是业务主键，不能以标题、路径或会话 ID 代替。
客户端可以携带关联 ID，但服务必须检查其权限和真实绑定。

| 对象 | 关键字段 | 约束 |
| --- | --- | --- |
| Project | id、owner、display_name、slug、workspace_ref、initialization_state | 项目导航与受限目录登记，不等同于 Task 或会话 |
| Task | id、owner、goal、constraints、status、version、plan_revision、control_epoch | 任务聚合与最终交付条件 |
| TaskBinding | task_id、host_id、session_id、thread_id、workspace_ref | 由可信 Host 上下文建立，不能由模型改绑 |
| PlanRevision | task_id、revision、reason、actor、created_at、stage_specs | 追加式不可变计划；保留完整版本及差异 |
| Stage | id、kind、title、depends_on、required、status、acceptance、current_attempt_id | ID 在计划修订中保持稳定 |
| Attempt | id、stage_id、number、status、execution_scope、input_digest、timestamps | 一次实际执行；重试新建 Attempt |
| ExternalExecutionIntent | id、attempt_id、route、immutable_inputs、idempotency_key、state | 外部提交前持久化的恢复锚点 |
| ExternalOperation | provider、operation_id、job_id、run_id、status、last_observed_at | 外部服务拥有执行事实，Task Service 只记录观察 |
| Evidence | id、source、verification、digest、observed_at、refs | 事实或产物的可验证依据 |
| Artifact | id、kind、display_name、media_type、size、digest、locator_ref | 对外只给受控引用，不返回凭证和任意文件路径 |
| TaskEvent | task_id、seq、type、payload、causation_id、occurred_at | 每任务单调序号，追加式，允许脱敏修正事件 |
| CommandReceipt | principal、command_id、payload_digest、state、result | 同键同内容返回相同业务结果，不同内容拒绝 |
| Outbox / Inbox | message_id、dedupe_key、lease、attempts、delivery_state | 可靠投递与消费去重 |

三个版本各有用途：

- `version`：整个 Task 的乐观锁，每次提交业务变更递增。
- `plan_revision`：只在计划内容改变时递增，日志和进度不会引发计划冲突。
- `control_epoch`：暂停、取消、接管等控制操作递增，使旧执行令牌失效。

事件游标 `seq` 与版本不同。日志事件可以增加 `seq`，不必修改业务聚合版本。

## 2. Task 状态

| 状态 | 含义 | 常见后继 |
| --- | --- | --- |
| `draft` | 目标已保存，尚未开始 | planning、cancelled |
| `planning` | Agent 正在规划或修订 | running、waiting_input、waiting_approval、paused、cancelling、failed |
| `running` | 存在可执行或正在执行的阶段 | 各等待状态、paused、cancelling、completed、failed |
| `waiting_input` | 缺少用户必须补充的信息或执行决定 | planning、running、paused、cancelling |
| `waiting_approval` | 缺少权限或业务授权 | planning、running、paused、cancelling |
| `waiting_external` | 外部操作已受理，等待可验证事实 | running、paused、blocked、cancelling |
| `paused` | 不启动新的阶段 | planning、running、cancelling |
| `blocked` | 未知执行结果、接续失败或证据不足，需要对账 | running、planning、cancelling、failed |
| `cancelling` | 已阻止新工作，仍在确认活跃执行停止 | cancelled、blocked |
| `completed` | 所有必要验收通过，交付已封存 | 无 |
| `failed` | 已确认无法继续且没有未解决的活跃执行 | 无 |
| `cancelled` | 取消完成、已知活跃执行均已处理 | 无 |

终态不可重新打开。用户调整已完成任务时新建后续 Task，并保留 `parent_task_id`。
不能为了简化 UI 把 `blocked` 显示成普通失败：它可能包含仍在运行但结果未知的作业。

Task 状态由命令与服务规则共同决定。Agent 可以请求完成或失败，不能提交任意状态值覆盖聚合。

## 3. Stage 与 Attempt 状态

Stage 状态：`pending`、`ready`、`running`、`waiting_input`、`waiting_approval`、
`waiting_external`、`verifying`、`completed`、`failed`、`skipped`、`cancelled`、`superseded`。

- `pending → ready`：所有依赖通过、输入存在、执行约束满足。
- `ready → running`：`begin_stage` 事务成功，创建 Attempt 并发放执行令牌。
- `running → waiting_external`：绑定可对账的外部 operation 与观察规则。
- `running/waiting_external → verifying`：提交结果证据，等待服务端检查。
- `verifying → completed`：验收全部通过；服务签发完成事件。
- `verifying → failed`：证据被确认无效；未知结果则阻塞而非直接失败。
- `failed → ready`：明确重试策略批准后，保留旧 Attempt，下一次 begin 创建新 Attempt。
- `pending/ready → skipped`：仅可选阶段且有理由；依赖方不得要求其未生成的产物。
- `pending/ready → superseded`：被新计划替代；旧版本与历史均保留。

Attempt 状态：`running`、`waiting_external`、`verifying`、`succeeded`、`failed`、
`cancelled`、`unknown`。`unknown` 不可以直接重试写操作，须先对账。

### 3.1 阶段规格与执行方式

计划写入使用 `StageSpec`，快照读取使用 `StageView`。Agent 不能把快照中的状态、
验证结论和当前 Attempt 原样作为可写字段提交。

`StageSpec` 包含 id、kind、title、depends_on、required、input_refs、output_slots、
acceptance 和 execution。`execution` 只能引用已登记 capability 与 Skill，示例：

```json
{
  "mode": "skill",
  "capability_ref": "data-transform-local/v1",
  "skill_refs": ["dataset-cleaning"],
  "retry_policy": {"max_attempts": 2, "requires_reconciliation": true}
}
```

Skill 名仅为契约示例，不声称仓库已经安装该 Skill。服务核对实际能力目录，
未安装时返回 CAPABILITY_UNAVAILABLE。kind 只决定展示，capability 决定执行边界；
不能将 `kind=data.transform` 的任意命令默认为安全数据处理。
资源估算可由 Agent 提出，最终允许范围由已登记 capability 与治理授权计算。

### 3.2 输入与输出引用

用户提交受限 `inputs` 描述符，如 workspace_ref 下的相对路径或已登记 Artifact ID。
Task 创建时解析真实路径、检查允许范围并生成不可变 InputRef；文件变化时生成新数据版本，
不能沿用旧 digest。大数据可先登记清单，再在检查阶段完成内容身份核验。

计划中的 `input_refs` 可以是 InputRef ID、现有 Artifact ID，或
`output:<stage_id>:<slot_name>`。后者必须同时声明阶段依赖，服务仅在上游完成且
相应 output_slot 绑定到有效 Artifact 时解析；不能把它当作普通文件路径执行。
`output_slots` 声明角色与媒体类型，实际输出映射在 report_result 中提交。
验收规则参数也要按 type 分别验证，不能因为快照 Schema 允许结构化参数就接受任意检查代码。

## 4. 计划修订规则

`replace_plan(expected_plan_revision, reason, stages)` 提交一个完整新计划，服务在事务中检查：

1. 阶段 ID 唯一、依赖存在、无环，数量不超过配置上限。
2. 完成、跳过、取消和已替代的历史记录不能被删除。
3. 已完成阶段的输入、执行规格、验收条件和证据不能原地修改。
4. 正在执行或等待外部结果的阶段保持冻结；先结束、取消或对账，再改变执行语义。
5. 新阶段的 capability、输入路径和成本符合任务范围；扩权只产生申请，不能直接执行。
6. 必需阶段不能仅通过改成可选来绕过失败；降低目标要求需要新的用户决定。
7. 如果上游输入改变，下游已有完成结果保持为历史事实，但不再算作新计划的有效交付；
   新增带新 ID 的替代阶段，并记录 `replaces_stage_id`。

当前实现支持已对账的失败阶段通过 `replaces_stage_id` 建立单一替代链。
原阶段与 Attempt 保持失败状态、规格和证据不变；替代阶段保留原执行能力、阶段类型、
必要性、验收规则和输出契约。禁止替代仍在执行或外部状态未知的阶段、分叉或循环替代，
也不能将失败的训练替换成本地报告。后续阶段应显式依赖替代阶段。
只有替代链末端完成服务验证，才能满足原必需阶段的交付义务；新训练仍须独立方案、
授权及执行确认。已经存在替代阶段的失败阶段不能再原地重试。
已完成工作因输入变更而失效的重算尚未实现，不得借用失败替代路径。

首版以完整计划替换避免复杂 JSON Patch 的局部竞态。返回差异摘要包含新增、替代、
依赖变更和范围变更。模型收到冲突后重新读取，不自动覆盖较新的计划。

## 5. 完成条件与证据等级

`acceptance` 是受控规则，不是任意可执行脚本。示例规则：

- `artifact_exists`：指定角色产物存在、可读、大小及 digest 匹配。
- `schema_matches`：数据字段或报告结构符合已登记 Schema。
- `metric_threshold`：具有可比数据与指标定义的结果满足阈值。
- `external_operation_terminal`：治理系统确认成功，并关联到对应输入和执行。
- `user_review_recorded`：存在绑定当前结果版本的可信用户审阅收据。

证据等级 `reported` 表示 Agent 报告，`verified` 表示可信适配器已验证，
`rejected` 表示被否定。不同阶段可以有不同验证器，但必需交付不能只依赖自然语言声明。
不把“模型说质量良好”当作 `metric_threshold` 的可核验结果。

验证器必须有资源限制、超时和允许的读取范围；默认不加载或反序列化不可信模型文件。
对小型报告检查 JSON Schema，对大型数据优先使用清单与抽样规则并明确验证覆盖度。

`complete_task` 需要满足：

- 当前计划全部必需阶段通过；可选阶段已完成、合理跳过或被替代。
- 没有活跃、未知、待停止的 Attempt 或外部操作。
- Task 级验收通过，必要交付物可访问。
- 最终摘要引用有效证据；训练结果遵守 [训练治理](07-training-and-evidence.md)。

## 6. 进度与时间

`progress` 为 0–1 的可选值，必须注明来源与观察时间。无法量化时使用不定进度，
禁止用前端计时器伪造百分比。阶段增加后总体百分比可能下降，UI 应展示计划修订原因。
首版主要展示“已完成阶段数 / 当前计划阶段数”，避免固定权重制造精确错觉。

外部状态超过新鲜度阈值时显示“状态待更新”，不能继续显示为实时运行事实。
所有持久化时间使用 UTC RFC 3339，界面按用户时区展示。

## 7. 存储与事务

首版 SQLite WAL，本地持久化卷；不要把数据库放在不保证锁语义的网络文件系统。
核心表：

```sql
CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    plan_revision INTEGER NOT NULL,
    control_epoch INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE task_events (
    task_id TEXT NOT NULL REFERENCES tasks(id),
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    causation_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    PRIMARY KEY (task_id, seq)
);
CREATE TABLE command_receipts (
    principal_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    result_json TEXT,
    PRIMARY KEY (principal_id, command_id)
);
CREATE TABLE outbox (
    id TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL,
    lease_owner TEXT,
    lease_until TEXT,
    fencing_token INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL
);
```

这是关键约束示意，迁移还需创建前述计划、阶段、尝试、证据、绑定、Inbox、执行意图和外部操作表，
增加租户范围索引与有效状态检查。快照是事务内更新的读取投影，不能与规范化记录分开提交。

一次业务命令按以下顺序执行：

1. 校验可信主体、作用域、令牌和命令内容；计算规范化 payload digest。
2. 开启短事务，读取命令收据；相同键不同 digest 返回冲突。
3. 检查 version/plan_revision/control_epoch、状态机与唯一活动阶段约束。
4. 原子写入领域记录、快照、新事件、Outbox 与命令收据，然后提交。
5. 网络调用、产物读取和模型接续放到事务外执行。

Worker 领取 Outbox 时使用租约和递增 fencing token。过期 Worker 的回写必须被拒绝，
幂等键在外部调用前生成并持久化；外部服务不支持幂等时，按操作身份对账，不能盲目重发。

## 8. 快照与事件

[快照 Schema](contracts/task-snapshot.schema.json) 定义给 App 的稳定读取接口，
不是数据库结构。默认包括当前计划、阶段状态、必要证据摘要和最后事件游标，
不包含完整日志、秘密、原始敏感样本或内部存储凭证。

事件统一结构：

```json
{
  "schema_version": "galatea-task-event/v1",
  "task_id": "task_demo",
  "seq": 18,
  "event_id": "evt_demo_18",
  "type": "stage.completed",
  "causation_id": "cmd_result_2",
  "occurred_at": "2026-10-19T03:10:00Z",
  "data": {"stage_id": "stage_clean", "attempt_id": "attempt_clean_1", "version": 9}
}
```

常用事件包括 `task.created`、`plan.revised`、`stage.started`、`stage.waiting`、
`stage.result_reported`、`evidence.verified`、`stage.completed`、`task.control_requested`、
`external.observed`、`continuation.requested`、`continuation.accepted`、`task.completed`。
事件按 `seq` 去重；回放不能触发新的外部写入。

## 9. 工作台读取模型与交互对象

第 06 章原型还需要项目导航、同源消息、文件差异、预算和会话绑定，不能仅靠 TaskSnapshot。
新增 `galatea-workbench-view/v1` 读取契约；这是待实现的 DTO 定义，不声称现有 JSON Schema
已经覆盖它。TaskSnapshot 保持纯业务快照，工作台组合该快照和以下独立投影。

| 对象 / 字段 | 内容与来源 | 一致性与权限 |
| --- | --- | --- |
| WorkbenchView | schema_version、project、binding、task_snapshot、timeline_page、pending_requests、capabilities、source_cursors、observed_at | 每部分标注来源/更新时间；聚合不承诺跨系统同一时刻 |
| ProjectView | project_id、name、slug、workspace_display_path、task_sessions、initialization_state | owner 范围查询；初始化为 validating/allocating/binding/ready/failed/unknown，不复用业务阶段 |
| BindingView | binding_id、project_id、task_id、host/session/thread、workspace_ref、activation_state | 由可信 Host 激活收据核验；iframe 不可改绑 |
| TimelineItem | id、seq、kind、source_ref、stage_id?、attempt_id?、occurred_at、content_revision | kind 为 public_message/process_card/plan_change/control_notice；按投影 seq 排序，源身份去重 |
| PublicMessageView | message_id、turn_id、role、safe_content、delivery_state、content_revision | Host 的 user/assistant 公开内容；流式更新原 ID，不复制为新消息 |
| ProcessCardView | card_id、stage_id、attempt_id、title、skill_refs、check_items、file_change_ids、progress、source_refs | Skill、进度、检查均有实际来源；未知值不可默认成功 |
| FileChangeView | change_id、relative_path、operation、base/current_digest、revision?、stage/attempt、hunks_page、checks | 不可变内容快照；受控分页，二进制/已清理/越界有明确状态 |
| TrainingProposal | proposal_id、revision、digest、task/stage、plan_revision、immutable_inputs、resources、hard_limits、estimate、readiness、expires_at | 提案不可变；估算不是授权，治理决定单独查询 |
| PendingRequest | request_id、kind、subject_digest、status、allowed_decisions、created_at、expires_at | training_proposal 请求绑定精确 proposal；状态 open/accepted/deferred/superseded/expired |
| UserDecision | request_id、decision、subject_digest、interaction_receipt、command_id、decided_at | 可信用户决定，仅用于该版本；不能由 Agent 自己确认 |
| training_decisions | 每个 proposal 的 authorized、can_authorize、can_confirm、reason、confirm_reason | 服务按当前委派权限、方案版本、有效期及就绪条件重算；不是授权凭据 |

TimelineItem 的 seq 是工作台投影追加顺序，不是 TaskEvent.seq；源事件晚到保留实际发生时间，
不回写已有顺序。消息流式修改增加 content_revision，通过工作台增量中的 upsert 传播。
get_workbench_events 使用独立更新游标，包含新增、更新和必要的脱敏移除事件，
不能用最大时间线 seq 跳过旧消息内容更新。每项保留源序号，缺失覆盖度可见。

TrainingProposal 至少绑定数据/split、Release、配置、角色、资源 profile、次数/重试上限、
时长预算与内存执行方式、final-test 范围。有限 `hard_limits.memory_bytes` 必须有真实
执行保障；仅有调度预留时，该字段明确为 `null`，并要求
`resources.memory.enforcement=scheduling_only` 与非空的执行方式说明。
这类方案必须另有 `allow_unbounded_memory=true` 且内存上限为 `null` 的管理员配置，
有限预算不能授权它。界面明确显示“未设置作业内存硬上限”。
Agent 提议方案，服务规范化并计算 digest；
治理适配器校验授权。固定项目路线允许管理员配置有限委派后，用户通过 App 的
`authorize` 决定授权精确方案，再通过单独的 `confirm` 决定执行；
两次决定均要求 Host 签名，授权本身不创建 Attempt 或后台接续。
用户授权记录保存在 Task 内并绑定主体、会话、proposal digest 和计划版本；
每次执行仍须重检管理员委派。缺少委派时不能仅靠确认按钮获得授权。
任一执行条件变化使旧待办 superseded。
提交外部操作时重检 proposal digest 与当前输入/授权，不能凭旧按钮回执执行新配置。
未通过的就绪检查不会被用户授权豁免；界面同时显示缺授权和缺就绪条件，
服务在确认、begin_stage 和 prepare_external 三处检查就绪状态。

有预授权但等待用户决定时 Task 为 waiting_input；缺治理授权时为 waiting_approval。
未启动的训练 Stage 可从 ready 进入 waiting_input/waiting_approval；决定满足后回到 ready，
实际 begin_stage 才创建 Attempt。defer 关闭待办并将 Task 置为 paused，不产生空的训练 Attempt。
只读“重新查看方案”不新建待办；用户再次明确确认时服务以原请求为关联创建新请求并重新校验。
如方案过期或变化，返回新卡片等待新确认，不能沿用旧 digest。

Workbench 的 UI 动作从 Task.allowed_actions 与 Host 能力共同计算。
interrupt_agent 是 pause 的增强控制语义：要求 Task 允许 pause 且 Host 支持 turn interrupt，
不往现有 TaskSnapshot v1 枚举中偷偷加入未声明字段。
项目初始化期间尚无可信会话绑定的草稿保存在初始化记录，不发布含假 session/thread 的 TaskSnapshot；
get_workbench 此时返回初始化视图、task_snapshot=null，绑定完成后才发布符合现有 Schema 的快照。
事件投影、消息、文件快照、项目初始化收据和方案/决定需要独立存储表与保留策略；
原生消息以 Host 为源，投影丢失可以重建，不能反向覆盖 Host 历史。
