# 方案 03：Python Codex adapter 实施计划

> 后续实现使用 superpowers:executing-plans；先锁定源码已证实的公开接口并做 fake smoke。

**目标：**把原版 Codex 封装成 Runner 可启动、恢复和中断的一次决策。
**架构：**独立 Python worker 使用官方 openai_codex；SDK 启动原版 app-server。
**技术栈：**Python、openai-codex 与匹配 Runtime、JSON Schema；无 Node/TS adapter。
**共同约束：**遵守 [实施索引](README.md)，不改 Codex Core，不访问插件或会话内部数据库。

## 1. 文件和边界

相对于独立 training-agent-runtime：

| 文件 | 职责 |
| --- | --- |
| pyproject.toml、依赖锁 | 固定 SDK、匹配 Runtime 与 Python |
| src/training_agent/runtime/types.py | 中立 TurnRequest、TurnOutcome |
| src/training_agent/runtime/codex.py | `run_turn(request, save_identity) -> TurnOutcome` |
| src/training_agent/runtime/worker.py | 单 Turn worker；受限 IPC 和生命周期 |
| src/training_agent/runtime/output.py | JSON/Schema 和状态校验 |
| src/training_agent/runtime/usage.py | 同 Thread 用量快照差值、unknown |
| tests/test_codex_adapter.py、test_usage.py | fake SDK、恢复/失败/用量 |
| deploy/runtime-lock.json | SDK/Runtime/协议/Skill/配置摘要 |

TurnRequest 包含 campaign_id、request_revision、decision_seq、workspace、thread_id、
已验证 skill_path、prompt 和 output_schema。凭据不在该对象，来自外层受控进程环境。
TurnOutcome 包含 thread_id、turn_id、status、final_json、usage_snapshot、error。
`save_identity(thread_id, turn_id)` 必须在开始消费可能有副作用的执行前尽早落盘；
thread_id 在启动 Turn 前保存，turn_id 在 thread.turn 返回后立即保存。

## 2. SDK 接线

完整调用示意见 [Python Runtime](../03-codex-runtime-and-skills.md)，本地依据：
[api.py](../../../../codex/sdk/python/src/openai_codex/api.py)、
[client.py](../../../../codex/sdk/python/src/openai_codex/client.py)、
[_run.py](../../../../codex/sdk/python/src/openai_codex/_run.py)。

执行顺序：构造 Codex/CodexConfig → thread_start 或精确 thread_resume → 保存 thread.id →
thread.turn(..., output_schema=...) → 保存 handle.id → handle.run() 或单独 stream()。
流消费者只有一个；控制入口可以调用 handle.interrupt()。长期运行可采用 AsyncCodex 对应接口，
但首版单 worker 用同步接口即可，超时由外部 supervisor 保证。

使用 `SkillInput(name="training-campaign", path=...)` 与 TextInput；不将 Skill 全文硬编码成 Prompt。
显式设置 `ApprovalMode.deny_all` 和 `Sandbox.workspace_write`，阻止无人值守权限升级等待，
已经允许的动作仍需 MCP 检查预算和项目范围。平台 request_approval 回到用户入口。

## 3. Python 状态与事件

| 事件或结果 | adapter 处理 |
| --- | --- |
| start/resume 返回 Thread | 立即保存准确 ID；不使用 TS thread.started 名称 |
| `item/completed` | 暂存 item/进度，不当最终业务成功 |
| `thread/tokenUsage/updated` | 更新同 Thread 累计快照，重复事件去重 |
| `turn/completed` | 检查其 TurnStatus，completed/failed/interrupted 不能混用 |
| run() 抛错或流提前关闭 | 标记失败/未知副作用，由 Runner 查询 MCP |
| TurnResult.completed + 非空 final_response | JSON 解析、Schema 校验，再交给 Runner 校验事实 |
| interrupted/无最终答复 | 不接受 complete；使用可验证用量，否则 unknown |

Python 收集器对 failed/缺完成事件抛错；不能复制 TS“退出 0、EOF、turn.completed”归约逻辑。
usage.total 是 Thread 累计，保存前后差值；不是每轮直接累计 total 或把 last 当整轮。
用量缺失、累计回退、无法归因时暂停自主新 Turn，不把 usage_unknown 当 0。

## 4. 环境、超时和会话

CodexConfig.env 合并父环境。supervisor 必须先用显式 env 白名单/专用容器启动 worker，
SDK 随后继承这个受限环境；不能传入“安全 env”同时让父环境带 Ray/S3 管理员凭据。
配置、Skill 和状态按角色挂载，workspace-write 不是网络/进程/租户全隔离。

worker/app-server 的进程组受外层监督。先 interrupt，短暂等待，仍未退出则终止整个执行组；
重启前验证旧组已清理，无法确认就阻止替代 Turn。只结束 Codex 不停止 Ray Job。
恢复要求会话卷、workspace、Runtime/Skill 锁一致，不能仅传一个 Thread ID 到空白主机。

## 5. 任务与验收

- [ ] R1：fake SDK 验证 start/resume 参数、SkillInput/output_schema 和 thread_id 的持久化顺序。
- [ ] R2：failed 抛错、interrupted、空输出、无完成事件、坏 JSON 均不接受成功建议。
- [ ] R3：两轮 total 从 100 到 160 只增加 60；重复 160 不增加；unknown 不写零。
- [ ] R4：临时 worker 环境预置伪管理员秘密，确认 supervisor 清理后 SDK 子进程不可见。
- [ ] R5：测试 timeout → interrupt → 整组退出；不清理则拒绝新 worker。
- [ ] R6：在无 Galatea checkout 环境，实际发布 SDK + fake MCP + 独立 Skill 完成 start/run/resume smoke。

从 Runner 仓库运行：`python -m unittest discover -s tests -p 'test_codex_adapter.py' -v`，
再执行 test_usage.py。fake 测试不调模型；R6 只在明确的最小模型调用预算内运行。
退出条件：锁定发布版接口与源码预期一致，完整结果和失败路径都能驱动 Runner 对账。
