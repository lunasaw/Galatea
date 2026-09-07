# Python Runner 与最小恢复机制

更新：2026-09-07。本文描述当前首版行为；部署命令与演练步骤见
[端到端手册](08-e2e-integration-runbook.md)，实测范围见[本地验证记录](09-local-validation.md)。

## 1. Runner 只做外部调度

Runner 是独立 Python 程序，调用 `openai_codex` 启动/恢复一次决策，用 MCP 客户端查询平台。
它不训练模型、不连接平台内部数据库、不加载旧 TS 插件，也不接管 Codex 自身的工具和会话实现。

首版一个 Runner 进程、一个活跃 Campaign、一次一个 Codex Turn、串行 Job。
训练提交成功后结束 Turn 和本轮 SDK/app-server 连接，Ray 继续运行。
用普通定时轮询等待，状态变化后再 `thread_resume(thread_id)`。
不预先加入队列、webhook、租约数据库、多实例调度和每轮授权服务。

## 2. 各自拥有的持久状态

| 状态 | 位置 | 使用方式 |
| --- | --- | --- |
| 批准范围、Plan、操作与提交 ID、候选 | MCP 本地受保护任务 JSON 清单 | 只由单 MCP 写进程更新，Runner 经 MCP 读取 |
| 作业运行 | Ray | 官方 API 对账 |
| 指标、模型、证据与制品 | MLflow / MinIO | 官方 API；不复制完整实验记录到 Runner |
| Thread 与对话 | 原版 Codex 会话目录 | 只用 SDK 恢复 |
| 调度、Thread 映射、用量快照 | Runner 本地 JSON 状态文件 | 原子替换、进程锁；事件 JSONL 只作审计 |

```text
/var/lib/training-agent/
  runner.lock
  campaigns/<campaign-id>/state.json
  campaigns/<campaign-id>/events.jsonl
  workspaces/<campaign-id>/request.json
专用运行账号的 Codex 会话目录：独立持久化，SDK 管理
```

Runner 的 state.json 至少保存 schema_version、campaign_id、request_revision、decision_seq、
runner_state、thread_id、turn_id、workspace、runtime/skill 摘要、operation_ids、
last_observation_digest、next_check_at、retry_count、usage_snapshot、cancel_requested、last_error。
只保存引用和小摘要，日志滚动限额，不放凭据、原始数据或大权重。

同目录临时文件写完、fsync，再原子 replace 并 fsync 目录。状态损坏时停止决策，保留原件并对账；
不能重置为空任务重新训练。进程锁保护整个 Runner 生命周期，第二个实例直接失败。
本地卷锁不支持跨主机自动接管，不能放到 NFS 后宣称高可用。

## 3. 状态与完整时序

| 状态 | 动作 |
| --- | --- |
| ready | 查询 MCP 最新快照和未完成操作；有新决策需求才启动 Turn |
| agent_running | 已持久登记 decision_seq，保存 thread_id/turn_id，监督一个 worker |
| waiting_external | 只读对账；仍 queued/running/unknown 时不调用模型 |
| waiting_evidence | 执行成功但证据未就绪时只读轮询；证据核验完成后才恢复决策 |
| awaiting_input / awaiting_approval | 保存具体缺项；收到有效答复/平台批准后恢复 |
| retry_wait | 对临时故障有限退避；先查询已发生副作用 |
| completed | 已交付 accepted 或 best-effort，停止自动调度 |
| cancelling | MCP 已登记取消，停止新提交，等待所有活动操作处理完 |
| blocked / cancelled | 不再决策，但仍要处理遗留运行操作 |

每轮开始先写“即将启动”和 decision_seq，随后创建 SDK client、start/resume Thread，
**成功保存 thread_id 后才运行 Turn**。对话引用任务 JSON 和平台事实，不传平台管理员凭据。
Turn 完成或出错均先关闭/确认旧 worker 结束，再重新读取 MCP 操作清单，然后解释模型输出。

训练完成只是一种唤醒条件。Artifact 未就绪则继续等证据，普通 running 指标变化不触发每分钟推理。
当前外部作业和证据等待间隔固定为 60 秒；平台查询失败使用最多 300 秒的退避，没有抖动。
交付核验在 tick 内完成，首版没有单独持久化的 finalizing 状态或通知服务。
计时器可从 state.json 重建，不需要持久事件总线。

## 4. 崩溃窗口与处理

| 故障 | 恢复 |
| --- | --- |
| MCP 写好 pending、未准备提交就退出 | 后台从清单继续，以原 submission_id 执行 |
| 提交前已写 submitting，随后进程/网络失败 | 同 ID 查询 Ray；存在则接回，无法确认则 unknown，禁止新 ID |
| Ray 接受后响应丢失 | 原 ID 查询并核对集群/metadata，成功后补齐操作状态 |
| Codex 在 submit 后最终输出前崩溃 | Runner 从 campaign 操作清单找到任务，继续观察，不依赖模型返回 ID |
| Runner 在记录等待前崩溃 | 重启先对账全部操作，恢复 waiting_external |
| Ray 返回 404 但有可能执行过 | 考虑集群变化/历史清理；无法证明未执行则 blocked 对账，不盲目重发 |
| Thread 丢失 | 保留原身份并停止自动决策，先恢复专用账号会话卷；首版不自动创建替代 Thread |
| JSON 输出无效或被截断 | 先对账副作用，再最多一次有预算的输出修复 |
| 旧 Runner 死但 Codex 子进程仍活着 | supervisor 清理整个 worker 执行组；不能证明清理就不启动替代 Turn |

文件单写与确定性 ID提供本范围内的恢复基础，不宣称分布式 exactly-once。
不启用多 Runner 租约抢占，因而也不需要首版跨服务 fencing/DecisionGrant 体系。
幂等依据 `campaign + step + attempt`，不能用新的 decision_seq 绕过同一逻辑计算。

## 5. 输出、用量和取消

[输出 Schema](examples/agent-turn.schema.json)只约束形状。Runner 还要检查：

- campaign/revision/decision_seq 与当前启动记录一致，所有 operation/report 引用属于本任务。
- wait_external 引用真实未完成操作；若操作已完成，按最新证据继续，不能等待一个过期模型快照。
- complete 需要 MCP 核验的质量/完整性/可下载报告；没有平台证据就不标 completed。
- request_approval 只是请求，文字不能授予预算；已有授权直接继续。

Python SDK 的失败、中断、空结果和用量语义见 [Runtime 文档](03-codex-runtime-and-skills.md)。
用量按同 Thread 的累计快照差值记录，缺失为 unknown，不写成零。
总 Turn 数、单 Turn 时长由 Runner 强制限制；Token 观察阈值只能阻止后续 Turn。
用量不明时暂停新增模型调用，低成本平台观察不受模型额度影响。

取消先调用 MCP 的 cancel_campaign 持久阻止新提交，再 interrupt/结束 Codex worker、停止活动 Job，
直至实际终态。单独关闭 Codex 不等于停止 Ray。
Job deadline 由 workload 执行，MCP 自身后台检查超时，不依赖 Runner 醒着。
MCP/Ray 全部失联时不能保证零超时，必须停止新增执行并保留未知操作记录。

## 6. 训练恢复独立实现

Thread 恢复不保存 Python 栈、GPU 状态或 optimizer。
首版默认不开放 pause/resume；项目声明并验证 Checkpoint 合同后才增加工具。
完整恢复需要模型/Adapter、optimizer、scheduler、随机数、step、数据迭代位置和拓扑约束，
并绑定数据/split/config/代码/环境摘要，通过 MLflow Artifact API 核验。
仅部分权重恢复应标 warm-start，不能称为等价续训；重试仍占原预算并保留父 Job/Run 引用。
