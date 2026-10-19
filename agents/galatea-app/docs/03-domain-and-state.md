# 03 领域模型与状态机

## 1. 标识与版本

所有标识使用服务生成的不可预测 ID。`task_id` 是业务主键，不能以标题、路径或会话 ID 代替。
客户端可以携带关联 ID，但服务必须检查其权限和真实绑定。

| 对象 | 关键字段 | 约束 |
| --- | --- | --- |
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
| `waiting_input` | 缺少用户必须补充的信息 | planning、running、cancelling |
| `waiting_approval` | 缺少权限或业务授权 | planning、running、cancelling |
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
