# 训推一体化 Agent 测试计划

> 状态：P0 测试契约，已对齐当前 `agent/test/test_patrol_*.py`。本文定义训推一体化 Agent（当前 Patrol core）的离线回放、关键测试场景和 Go/No-Go 条件。相关契约见
> [`patrol-push-agent-contract.md`](patrol-push-agent-contract.md)、
> [`patrol-memory-and-compaction.md`](patrol-memory-and-compaction.md)、
> [`patrol-recommendation-governance.md`](patrol-recommendation-governance.md)。

## 1. 测试原则

- 优先离线、deterministic、无真实 Ray/MLflow/MinIO 依赖。
- 先测 patrol core，再测 SDK/LLM integration。
- 失败、审批、预算耗尽都是正常状态，不能只测 success path。
- 不用 test set 做搜索或推荐选择。
- 测试输出不写入源码目录中的 runtime data、checkpoint、artifact。

## 2. 推荐测试位置

当前已存在：

```text
agent/test/
├── test_patrol_schemas.py
├── test_patrol_memory.py
├── test_patrol_state_machine.py
├── test_patrol_recommendations.py
└── test_patrol_runner.py
```

后续可按需拆分：

```text
agent/test/
├── test_patrol_policy.py
├── test_patrol_resume.py
└── test_patrol_tool_output.py
```

测试中 runtime state 写 `/tmp`，例如 `/tmp/galatea-agent-state-test`。

## 3. Offline tool overrides

当前 `PatrolRunner` 使用 `tool_overrides` 做离线回放，例如在测试中覆盖
`check_service_health` 和 `inspect_ray_status`。仓库没有正式的
`FakeServiceHealthClient/FakeRayClient/FakeMLflowClient/FakeArtifactClient/FakeApprovalStore`
类族；`agent/patrol/clients.py` 只有轻量 `FakePatrolTools` 草案。

后续如要扩展成 fake clients，建议覆盖：

| Client | 用途 |
| --- | --- |
| `FakeServiceHealthClient` | 返回 active/inactive/timeout 的服务状态 |
| `FakeRayClient` | 返回 Ray cluster/job status、resource summary、logs URI |
| `FakeMLflowClient` | 返回 experiment/run summary、tags、metrics、artifact URI |
| `FakeArtifactClient` | 模拟 artifact metadata、digest、recovery pass/fail |
| `FakeApprovalStore` | 保存 approval request 和 decision |
| `FakeClock` | 控制 cooldown、retry_after、next_check_at |

离线 override/fake 必须能配置：

- transient failure。
- permission denied。
- missing evidence。
- incompatible run cohort。
- artifact recovery failure。
- budget exceeded。
- recovered state。

## 4. Schema 测试

覆盖：

- `EvidenceRecord` JSON dump/load。
- `Finding` 默认状态和时间字段。
- `Recommendation` risk/confidence/status validation。
- `PatrolMemory` 必保字段。
- `PatrolRunResult` status enum。
- fingerprint 对字段顺序稳定。
- raw_ref 缺 URI/digest 时失败或按策略降级。

## 5. Memory 和 compaction 测试

场景：

| 场景 | 预期 |
| --- | --- |
| 大日志进入 raw tool output | compacted summary 不含大日志，保留 raw_ref |
| open finding 有 evidence | 压缩后 evidence_id 仍可定位 |
| approval request 未完成 | 压缩后 approval_id、proposed_action、evidence_ids 不丢 |
| unresolved error 存在 | 压缩后仍存在 |
| fake secret/token/password | summary 脱敏或校验失败 |
| evidence 缺 digest | 高置信 recommendation 被降级 |
| 多轮 summary | summary_version 增加，source_patrol_run_ids 合并 |

## 6. State machine 测试

覆盖：

- `idle -> collect_context -> inspect -> classify_findings -> recommend -> persist_state -> schedule_next -> idle`。
- inspect failure 进入 `inspect_failed`。
- transient failure 进入 `retry_later` 并设置 `retry_after`。
- 部分工具失败时生成 `degraded_summary`。
- policy blocked 进入 `needs_human` 或 `needs_approval`。
- 非法 transition 被拒绝并记录 reason。
- `persist_state` 在返回前被调用。

## 7. Finding/recommendation lifecycle 测试

| 场景 | 输入 | 预期 |
| --- | --- | --- |
| 同一 finding 重复出现 | 相同 fingerprint | 不新建，`occurrence_count += 1` |
| 同一 warning 连续 3 轮 | 相同 fingerprint 三次 | severity 升级或记录 escalation |
| 服务恢复 | open finding 后 status active | finding -> resolved，写 `resolved_at` |
| dismissed finding | 用户 dismiss | cooldown/push 抑制但审计保留 |
| recommendation cooldown | 相同 fingerprint 未过期 | 不重复推送，记录 suppressed |
| 新 evidence digest | 同 target 新 digest | 可打破 cooldown |

## 8. Policy 测试

覆盖：

- L0 只允许 inspect。
- L1 允许 recommendation 和 report。
- L2 可以 request approval，但不能 apply。
- L3 apply 必须有 approval id、未过期、scope/risk/action 匹配。
- promotion alias change 未审批被 blocked。
- long training/GPU/search 未审批只生成 approval request。
- destructive data action 默认禁止。
- project_scope 不匹配被 blocked。
- 缺 artifact recovery evidence 阻断 promotion recommendation。

## 9. Tool envelope 测试

当前已在 `test_patrol_runner.py` 覆盖 `build_tool_envelope()` 的摘要、脱敏、digest 和
legacy payload。后续可继续覆盖现有只读工具包装：

- `inspect_project_structure` 输出 `summary_for_model/evidence/raw_ref/legacy_payload`。
- `check_service_health` inactive -> evidence kind `service_health`。
- `inspect_mlflow_experiment` missing experiment -> warning evidence。
- `inspect_ray_status` unavailable -> warning finding。
- raw output digest 稳定。
- summary 小于阈值。
- legacy payload 不破坏旧调用者。

## 10. Runner 场景测试

| 场景 | 预期 |
| --- | --- |
| Ray 不可用 | warning finding；recommend wait；不提交训练 |
| MLflow 不可用 | service finding；raw_ref 使用 local state |
| MLflow run 缺 dataset digest | `evidence_missing` finding；不给 best-run 结论 |
| Artifact 回读失败 | `artifact_risk` finding；阻断 promotion |
| 预算耗尽 | 输出 critical/open findings 的 degraded summary |
| 权限 deny | `needs_approval` 或 `policy_blocked`，不 apply |
| Resume 后服务恢复 | finding resolved，next_check 正常 |
| Fork 后不同策略 | 主 session 不被污染 |

## 11. SDK/LLM 集成测试

在 deterministic core 通过后再做：

- `PatrolRunResult` JSON schema 作为 `output_schema`。
- structured output 缺字段时 runtime 抛错。
- LLM 输出高风险 apply candidate 被 policy 改写为 approval request。
- LLM 输出无 evidence recommendation 被降级。
- token/cost 超预算时降级 deterministic summary。
- hook event 能记录 tool call、tool summarized、permission denied。

这些测试可用 scripted/mock SDK result，避免真实模型调用成为单元测试依赖。

## 12. 审计和报告测试

覆盖：

- 每轮记录 `patrol_run_started` 和 `patrol_run_completed`。
- tool call 记录 `tool_called` 和 `tool_summarized`。
- recommendation suppressed 记录 cooldown reason。
- approval request 记录 evidence ids 和 proposed action。
- Markdown report 不包含 secret、样本、长日志。
- report 中的 evidence link 可解析到 raw_ref。

## 13. 推荐验证命令

仓库当前测试位于 `agent/test/`。训推一体化 P0 / Patrol core 可先运行：

```bash
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s agent/test -p 'test_patrol_*.py'
```

已有 agent 测试：

```bash
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s agent/test -p 'test_*.py'
```

如果后续新增仓库级 `tests/`，再同步 README 和根 `AGENTS.md` 推荐命令。

## 14. Go / No-Go 条件

进入真实服务、真实 LLM 集成或 production push 前，必须满足：

- `tool_overrides` 离线完整 `run_once()` 通过。
- 每条 recommendation 都能追溯 evidence。
- 缺 evidence 时不能产生高置信 recommendation。
- promotion、alias、删除、覆盖、长训练无 approval 时不能 apply。
- session 落盘后可 resume，动态状态会刷新。
- compacted memory 不包含长日志、样本、token、password。
- P0 离线测试通过。

No-Go：

- 需要读取 `mlflow.db` 才能判断状态。
- 需要真实 MinIO 服务端目录才能定位 artifact。
- 需要裸 Bash 才能完成常态巡检。
- finding/recommendation 不能追溯 evidence。
- resume 只能恢复自然语言 transcript。
