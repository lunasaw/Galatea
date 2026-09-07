# 方案 05：Python Runner 实施计划

> 后续实现使用 superpowers:executing-plans，先用 fake runtime 和虚拟时钟验证等待与恢复。

**目标：**长训练等待不调用模型，状态变化后恢复准确 Thread，崩溃不重复 Job。
**架构：**单进程 Runner + 受监督 Codex worker，JSON 状态文件与普通定时轮询。
**技术栈：**Python、pathlib/JSON/文件锁、MCP Python client、openai-codex adapter；无 SQLite。
**共同约束：**遵守 [实施索引](README.md)，不增加独立控制 API/授权签发/消息队列。

## 1. 文件与接口

相对于独立 training-agent-runtime：

| 文件 | 接口/职责 |
| --- | --- |
| src/training_agent/state.py | `load_state(id) -> dict`、`save_state(id, snapshot) -> None`，锁与原子写 |
| src/training_agent/platform.py | MCP client，读取 Campaign/操作、cancel/stop，不直连 Ray/MLflow |
| src/training_agent/runner.py | `tick(now) -> None`，状态机、下一检查时间 |
| src/training_agent/reconcile.py | `reconcile(state, platform_snapshot) -> dict`，以事实归约 |
| src/training_agent/supervisor.py | 白名单 env、worker 启停、进程组清理与超时 |
| src/training_agent/requests.py | 受信本地 CLI 接受请求/答复/取消，不部署用户数据库 |
| src/training_agent/finalize.py | 校验报告/Artifact 引用和平台结果 |
| tests/test_runner.py、test_recovery.py、test_supervisor.py | 临时文件卷、fake MCP、fake SDK、虚拟时钟 |

state 字段见 [Runner 设计](../05-runner-and-recovery.md)。Runtime 接口使用方案 03 的
TurnRequest/TurnOutcome；协议包消费已发布 Schema，不能 import Galatea 内部 state 模块。
启动时持有 runner.lock，整个实例一个活跃 Campaign。第二实例失败；跨主机接管首版不支持。

## 2. 一次 tick

```text
load state; reject corrupt schema or unreconciled orphan worker
read Campaign + all operation summaries through MCP
if cancelled: stop worker and follow platform cancellation to terminal
if active/unknown operation exists: save waiting_external and next_check_at; return
if no new decision-relevant evidence or valid user response: return
persist starting + next decision_seq before spawning worker
start/resume Codex using the SDK adapter; persist thread/turn IDs
collect outcome; ensure worker/app-server termination
read platform snapshot again, even if model output failed
validate proposal shape and platform semantics
save new runner state and usage snapshot atomically
```

收到完成状态时可做下一决策；Artifact 尚未就绪时继续有界观察，不能因为 Ray success 自称交付。
相同快照摘要不会反复启动 Turn。进度数据变化不必唤醒模型，终态/证据就绪/用户答复才进入决策。
每次模型调用计入总次数；空转 continue 最多连续两次，输出修复最多一次，且不突破原预算。

## 3. 用户输入、取消与身份

首版本地受信 CLI 负责登记自然语言输入和用户答复，关联 campaign/request_revision。
CLI 不写 MCP 的运行清单；有效授权由平台受信配置入口更新，Runner 重新读取后才恢复。
若用户已有明确授权，登记后直接执行，不把每个工具调用变成新的审批。

取消通过 MCP cancel_campaign 先写停止标记；旧 Turn 即使继续调用也不能再提交。
随后 interrupt/清理 Codex worker、停止 Job 并对账。终态前保持 cancelling。
服务重启不自动取消正常 Ray Job，也不赋予新预算。

Runner/worker 都只能持有本任务的受限 MCP 身份；平台管理员和后端凭据不进入它们。
首版不依赖每轮短期 grant；恢复前必须证明旧 worker/app-server 已退出，不能靠文件锁过期猜测。

## 4. 文件恢复与结果语义

| 情况 | 处理 |
| --- | --- |
| submit 后 worker 崩溃 | MCP 清单发现原 operation，waiting_external，不启动修复模型来“找 ID” |
| start Thread 后、保存前失败 | 该 Thread 尚未执行 Turn；确认孤儿 Runtime 清理后可新建 |
| thread.turn 后 turn_id 未保存 | 可能有副作用；先清理旧 worker，再查 MCP，不盲目重复决策 |
| state.json 损坏/卷丢失 | 不当空任务，保存故障；依据受信输入和平台重建，缺依据则 blocked |
| complete 无平台报告 | 拒绝完成建议，保留已有操作和证据 |
| wait_external 时 Job 已终态 | 采用最新平台事实，不重复等待或提交 |
| usage 丢失 | unknown，暂停自主模型调用；继续只读跟踪 Job |

JSON 写入使用同目录 fsync/replace，日志不是恢复主数据。
输出校验包括 task/revision/decision_seq、action 组合、引用归属、报告是否由平台登记。
report_ref 不能是模型随意给出的本地/外部 URL。

## 5. 任务与验收

- [ ] U1：临时目录实现文件状态、全程锁和虚拟 timer，第二实例不得启动。
- [ ] U2：fake 平台 + runtime 完成 ready → wait → resume → delivery；两个 wait tick 不新增模型调用。
- [ ] U3：逐一注入 Thread/Turn/submit/输出/保存前崩溃，重启使用原文件卷和平台记录。
- [ ] U4：断言 submit_then_crash 场景只一个 Job，下一 tick 不调用模型而恢复观察。
- [ ] U5：测试损坏文件、未知用量、过期授权、取消、平台暂不可用，不增权不新建重复 ID。
- [ ] U6：进程测试确认孤儿清理和超时执行组边界；连接真实 SDK 后再次验证等待期间无模型调用。

从 Runner 根执行：`python -m unittest discover -s tests -p 'test_recovery.py' -v`，
再执行 test_runner.py/test_supervisor.py。测试不 sleep 几分钟，使用虚拟 clock。
回退停止新 Turn，保留 JSON/会话/操作引用和观察循环；不重新提交所有 Job。
