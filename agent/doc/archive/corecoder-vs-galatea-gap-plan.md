# CoreCoder 对照下的巡推 Agent 基础能力补齐方案

> 目标：对照 `/data/ai/chenzhangyue/code/CoreCoder` 中已经跑通的 agent 基础机制，盘点 Galatea 当前“巡推 Agent”还需要补齐的底座能力。本文不讨论代码维护能力，不把目标转向 CodeMaintenanceAgent。

## 1. 结论

巡推 Agent 的核心不是“写代码”，而是：

```text
定期或按需巡检平台/项目/实验/模型状态
  -> 压缩和保留关键上下文
  -> 发现风险、机会或待办
  -> 生成推荐动作或推送审批请求
  -> 记录证据和状态
  -> 等待授权或进入下一轮巡检
```

对照 CoreCoder，Galatea 当前已经有 Claude SDK runtime、hooks、permission、budget、inspection MCP tools、stage schemas、workflow skeleton，但还缺一组更基础的 agent 运行能力：

- 上下文压缩要从“提示和裁剪”升级为“可验证的多层记忆管理”。
- Session 要从“抽象”升级为“可恢复、可 fork、可审计的巡检状态”。
- 工具输出要从“返回 JSON 文本”升级为“摘要 + 证据 URI + 可追溯原始来源”。
- 巡检循环要有“状态机、去重、节流、重试、升级策略”。
- 推送/推荐要有“置信度、证据、风险、审批和回滚计划”。
- Sub-agent 要用于专项分析，但不能成为执行或权限隔离边界。
- 预算、权限、失败分类和安全脱敏要成为每轮巡检的硬约束。

一句话：

**巡推 Agent 需要补的不是文件编辑工具，而是长上下文、长状态、长证据链和推荐治理闭环。**

---

## 2. 什么是“巡推 Agent”

本文把巡推 Agent 定义为：

- `巡`：巡检平台服务、训练项目、Ray Job、MLflow Run、Artifacts、数据/模型质量、资源和风险。
- `推`：推送可行动建议、告警、下一步计划、审批请求、模型候选状态或运维处理建议。

它不默认执行高风险动作：

- 不默认启动长训练。
- 不默认改 Registry alias。
- 不默认重跑大规模推理。
- 不默认删除或覆盖数据。
- 不默认修改代码。

它应该默认产出：

```json
{
  "status": "ok|warning|failed|needs_approval",
  "summary": "...",
  "findings": [],
  "recommendations": [],
  "evidence": [],
  "approval_requests": [],
  "next_check_at": "...",
  "state_update": {}
}
```

---

## 3. 对照 CoreCoder 后的基础能力差距

| 基础能力 | CoreCoder 已证明的机制 | Galatea 当前状态 | 巡推 Agent 需要补齐 |
| --- | --- | --- | --- |
| 主循环 | `LLM -> tool -> observe -> loop` | 有 SDK query/runtime，但巡检循环还不完整 | 巡检状态机、周期/按需触发、下一轮计划 |
| 上下文压缩 | 三层压缩：截工具输出、总结旧轮次、hard collapse | 有 hook 输出裁剪和 context usage advice | 可验证的多层上下文压缩、关键证据保留、摘要版本化 |
| 工具输出治理 | 长输出 head/tail 截断 | 有 `summarize_large_tool_output_hook` | 输出分级：模型摘要、证据 artifact、原始日志 URI |
| Session | save/load/list + 路径安全 | 有抽象和 memory store，durable 不完整 | 持久化巡检 session、resume/fork、运行快照 |
| 中断恢复 | KeyboardInterrupt 后补齐 pending tool replies | SDK 有 interrupt/stop_task | 巡检中断后的状态落盘、未完成工具调用补偿 |
| 成本与预算 | token/cost 累计和 CLI 展示 | 有 `BudgetPolicy` 和 SDK usage | 每轮、每日、项目级预算；超预算降级为只读摘要 |
| 并行/子 agent | 子 agent 独立上下文、不可递归 | 有 `AgentDefinition` / Task | 专项巡检子 agent、输出压缩、权限更窄、归因审计 |
| 错误处理 | 工具异常转文本，重试 transient LLM error | 有 failure hook 分类雏形 | 失败 taxonomy、重试策略、降级路径、人工升级 |
| 权限控制 | 简单危险命令拦截 | 有 policy/hook，比 CoreCoder 更强 | 巡检/推荐/审批/执行四级权限模型 |
| 可测试性 | scripted LLM + 工具单测 | 有部分 SDK/tool tests | 巡检循环离线测试、状态恢复测试、压缩保真测试 |

---

## 4. P0 必须补齐：上下文压缩和记忆管理

这是你提到的重点，也是巡推 Agent 最容易失控的地方。

### 4.1 现状问题

Galatea 当前有：

- `ContextCompressionConfig`。
- `summarize_large_tool_output_hook`。
- `compact_context_hook`。
- `get_context_usage()` / `check_context_usage()`。

但它们还偏“提示和裁剪”，缺少完整机制：

- 没有明确的长期记忆层和短期工作记忆层。
- 没有压缩前后保真校验。
- 没有 summary 的版本、来源和覆盖范围。
- 没有把 Ray/MLflow/Artifact 证据从自然语言上下文中分离出来。
- 没有判断“哪些内容绝不能压缩丢失”。

### 4.2 建议的三层上下文

巡推 Agent 应该明确拆成三层：

| 层 | 内容 | 保存位置 | 进入模型上下文方式 |
| --- | --- | --- | --- |
| 工作上下文 | 当前用户目标、最近工具观察、未解决 finding | Claude session / runtime memory | 原文保留 |
| 巡检摘要 | 项目状态、历史 finding、最近推荐、未完成审批 | agent session store | 压缩摘要进入上下文 |
| 证据索引 | Ray job id、MLflow run id、artifact URI、manifest digest、日志 URI | MLflow/Artifact/session state | 只把 ID/URI/摘要进入上下文 |

### 4.3 压缩策略

建议采用 CoreCoder 式分层策略，但改成巡推语义：

1. **工具输出裁剪**：超过阈值的日志只保留 error head/tail、状态码、URI、摘要。
2. **巡检轮次总结**：把较早轮次压成 `PatrolSummary`，保留 finding、推荐、证据 ID。
3. **证据外置**：原始日志、完整 MLflow run table、Ray logs 写 artifact 或仅保留查询 URI。
4. **硬压缩保护**：压缩前强制检查必保字段是否仍存在。

必保字段：

- `patrol_run_id`
- `project_name`
- `stage`
- `ray_job_id`
- `submission_id`
- `mlflow_run_id`
- `experiment_name`
- `artifact_uri`
- `manifest_digest`
- `model_artifact_uri`
- `registry_action`
- `approval_request_id`
- `finding_id`
- `recommendation_id`
- `next_check_at`
- `unresolved_errors`

### 4.4 需要新增的结构

建议新增 `PatrolMemory`：

```json
{
  "patrol_run_id": "...",
  "project_name": "...",
  "window": {
    "started_at": "...",
    "ended_at": "..."
  },
  "summary": "...",
  "open_findings": [],
  "closed_findings": [],
  "recommendations": [],
  "approval_requests": [],
  "evidence_index": [],
  "next_check_at": "..."
}
```

建议新增压缩验收：

- 压缩后所有 open findings 仍可定位 evidence。
- 压缩后所有 approval request 仍可定位原始证据。
- 压缩后 unresolved errors 不丢失。
- 压缩后不包含敏感 token、样本或长日志。

---

## 5. P0 必须补齐：巡检状态机

### 5.1 为什么需要

当前 `WorkflowStateMachine` 是 data/training/inference 阶段型的；巡推 Agent 需要的是循环型状态机。

建议状态：

```text
idle
  -> collect_context
  -> inspect
  -> classify_findings
  -> recommend
  -> request_approval?
  -> persist_state
  -> schedule_next
  -> idle
```

失败分支：

```text
inspect_failed
  -> retry_later | degraded_summary | needs_human
```

### 5.2 巡检对象

巡检对象至少分层：

- 平台服务：JupyterLab、Ray、MLflow、MinIO、systemd、ports、health endpoints。
- 项目结构：`train-model/<project>` 是否满足 contract。
- 数据状态：manifest、split、preprocessing version、schema drift。
- 训练状态：Ray job、MLflow runs、failed runs、checkpoint artifacts。
- 推理状态：model artifact、smoke inference、serve plan、quality gates。
- 治理状态：approval request、registry candidate/champion/production alias。
- 资源状态：GPU/CPU/memory、Ray cluster capacity、pending jobs。

### 5.3 去重和节流

巡推 Agent 不能每轮重复推同一个建议。

需要补齐：

- `finding_fingerprint`：基于对象、错误类型、证据 digest。
- `recommendation_fingerprint`：基于建议动作、目标对象、风险级别。
- `cooldown_until`：同类建议冷却时间。
- `severity_escalation`：同一 finding 连续出现才升级。
- `resolved_at`：恢复后自动关闭 finding。

---

## 6. P0 必须补齐：证据链和 Artifact 索引

### 6.1 当前问题

巡推 Agent 不能只说“我看到 Ray 异常”或“建议重训”。它必须能回答：

- 观察来自哪个工具？
- 原始证据在哪里？
- 证据是否兼容、是否足够？
- 推荐动作依赖哪些证据？
- 如果推荐错了，如何回滚或人工复核？

### 6.2 建议新增 EvidenceIndex

```json
{
  "evidence_id": "ev_...",
  "kind": "service_health|ray_job|mlflow_run|artifact|quality_gate|log_excerpt",
  "source_tool": "inspect_ray_status",
  "source_uri": "...",
  "digest": "sha256:...",
  "summary": "...",
  "created_at": "...",
  "sensitivity": "public|internal|sensitive",
  "retention": "short|normal|long"
}
```

### 6.3 工具输出规范

每个巡检工具返回三类内容：

```json
{
  "summary_for_model": "small text",
  "evidence": [],
  "raw_ref": {
    "uri": "mlflow-artifacts:/... or ray://... or local state key",
    "digest": "sha256:..."
  }
}
```

这样可以避免把完整 logs、run tables、样本塞进模型上下文。

---

## 7. P0 必须补齐：推荐与推送治理

### 7.1 推荐对象

建议把“推”拆成结构化 recommendation，而不是自然语言建议：

```json
{
  "recommendation_id": "rec_...",
  "type": "rerun_smoke|inspect_failed_run|request_training_approval|request_promotion_review|fix_config|wait",
  "target": {
    "project_name": "...",
    "run_id": "...",
    "artifact_uri": "..."
  },
  "severity": "info|warning|critical",
  "confidence": 0.0,
  "evidence_ids": [],
  "risk": "low|medium|high",
  "requires_approval": false,
  "cooldown_until": "...",
  "rollback_plan": null
}
```

### 7.2 推送通道

第一版可以不接外部通知系统，但要抽象 channel：

- CLI summary。
- Notebook display。
- Markdown report artifact。
- MLflow tag/comment/artifact。
- 未来 webhook / email / IM。

### 7.3 审批边界

推荐和执行分开：

| 动作 | 巡推 Agent 默认权限 |
| --- | --- |
| 生成健康报告 | 允许 |
| 生成 recommendation | 允许 |
| 生成 approval request | 允许 |
| 启动小预算 smoke | 需要用户明确授权或策略允许 |
| 启动长训练 / GPU trial | 需要审批 |
| 创建 Registry version | 需要审批 |
| 修改 alias / production traffic | 必须单独审批 |
| 删除数据 / 覆盖 artifact | 默认禁止 |

---

## 8. P1 需要补齐：Session / resume / fork

### 8.1 巡推 Agent 的 session 不是聊天记录

需要保存：

- 上一轮巡检范围。
- open findings。
- recommendation 状态。
- approval 状态。
- 上次工具调用摘要。
- 压缩后的 PatrolMemory。
- 证据索引。
- 下一轮检查时间。

### 8.2 建议新增 PatrolSessionStore

```json
{
  "session_id": "...",
  "agent_type": "patrol-push",
  "project_scope": ["ray-cats-and-dogs"],
  "created_at": "...",
  "updated_at": "...",
  "patrol_runs": [],
  "open_findings": [],
  "recommendations": [],
  "approval_requests": [],
  "memory": {},
  "budget": {},
  "last_context_summary": "..."
}
```

### 8.3 Resume 语义

Resume 时不能简单恢复自然语言对话，而要：

1. 加载 open findings。
2. 重新验证关键证据是否仍有效。
3. 刷新动态状态，例如 Ray job status。
4. 标记已恢复的 finding。
5. 只把必要 summary 放回模型上下文。

### 8.4 Fork 语义

Fork 用于：

- 比较两种推荐策略。
- 分析不同 objective metric。
- 对同一失败 run 走不同诊断路径。

Fork 不应污染主巡检链路。

---

## 9. P1 需要补齐：失败分类与重试策略

巡推 Agent 要把失败变成可治理状态，而不是只抛异常。

建议 failure taxonomy：

| 类型 | 示例 | 策略 |
| --- | --- | --- |
| transient | MLflow/Ray 短暂不可达、timeout | 指数退避、下轮重试 |
| permission | 工具被 deny、缺少审批 | 生成 approval 或降级只读 |
| evidence_missing | 缺 manifest、缺 run tag、缺 artifact digest | 停止推荐执行，要求补证据 |
| incompatible | Run 不可比较、metric 定义不一致 | 不给最优结论，只给数据质量建议 |
| data_risk | split 变化、schema drift | 阻断训练/推理推荐 |
| artifact_risk | checkpoint/model 回读失败 | 阻断 promotion |
| budget_exceeded | token/美元/计算资源超预算 | 压缩上下文、缩小范围、等待授权 |
| policy_blocked | 生产 alias、删除、覆盖等高危动作 | 必须人工审批 |

每个失败都应该记录：

- `failure_id`
- `failure_type`
- `recoverability`
- `retry_after`
- `evidence_id`
- `recommended_next_action`

---

## 10. P1 需要补齐：Sub-agent 和并行巡检

### 10.1 Sub-agent 用途

巡推 Agent 可以拆专项子任务：

- `ServiceHealthSubagent`：平台服务和 Ray 状态。
- `MLflowRunSubagent`：实验 run 兼容性和异常。
- `ArtifactSubagent`：artifact 回读和 digest。
- `QualityGateSubagent`：门禁和指标解释。
- `RiskReviewSubagent`：推荐动作风险审查。

### 10.2 约束

- 子 agent 的工具集必须更窄。
- 子 agent 不应递归 spawn。
- 子 agent 输出必须压缩成 finding/evidence/recommendation。
- hooks 必须记录 `agent_id` / `agent_type`。
- 子 agent 不能执行 promotion / alias apply。

### 10.3 并行边界

可并行：

- 多项目只读巡检。
- 服务健康和 MLflow 查询。
- Artifact metadata 检查。

不可并行或需加锁：

- 同一 approval request 写入。
- 同一 stage state 更新。
- 同一 Ray job submission。
- Registry 相关动作。

---

## 11. P1 需要补齐：预算和资源控制

巡推 Agent 预算不只是 LLM token，也包括平台资源。

建议预算分层：

| 预算 | 说明 |
| --- | --- |
| LLM token budget | 每轮、每 session、每日上限。 |
| LLM cost budget | 超过后只保留 deterministic 工具摘要。 |
| tool call budget | 每轮最大工具调用数，避免循环。 |
| wall-clock budget | 每轮巡检最大耗时。 |
| Ray query budget | 状态查询频率节流。 |
| artifact read budget | 最大下载大小和次数。 |
| notification budget | 同类告警冷却，避免刷屏。 |

超预算策略：

- 缩小项目范围。
- 降低日志细节。
- 只输出 open critical findings。
- 推迟下一轮。
- 请求用户扩大预算。

---

## 12. P1 需要补齐：权限模型

建议四级权限：

| 等级 | 名称 | 允许动作 |
| --- | --- | --- |
| L0 | inspect | 只读巡检、读取状态摘要。 |
| L1 | recommend | 生成 recommendation、写巡检报告。 |
| L2 | request_approval | 创建 approval request，不执行。 |
| L3 | apply | 人工审批后执行训练、promotion、alias 等动作。 |

默认配置：

- 巡推 Agent 常态运行只拿 L0 + L1。
- 需要用户明确指定时才拿 L2。
- L3 不应在常态 Agent allowed_tools 中。

权限校验应同时检查：

- tool name。
- project scope。
- resource budget。
- action risk。
- approval id。
- evidence completeness。

---

## 13. P2 需要补齐：可观测性和审计

### 13.1 审计事件

每轮应记录：

- `patrol_run_started`
- `tool_called`
- `tool_summarized`
- `finding_created`
- `finding_updated`
- `recommendation_created`
- `approval_requested`
- `context_compacted`
- `budget_exceeded`
- `patrol_run_completed`

### 13.2 指标

建议记录：

- 每轮耗时。
- 工具调用数。
- token 和 cost。
- open finding 数。
- critical finding 数。
- recommendation 采纳率。
- false positive / dismissed 数。
- 重复告警抑制数。
- 压缩前后上下文大小。

### 13.3 存储

- 轻量状态：session store。
- 审计报告：MLflow artifact 或 `platform-data/agent-state`。
- 关键证据：MLflow Artifact API / Ray job logs URI。
- 不要直接读写 `mlflow.db`。
- 不要把敏感样本、密钥、长日志写进 transcript。

---

## 14. P2 需要补齐：离线测试和回放

CoreCoder 的 scripted LLM 思路对巡推 Agent 也很重要。

需要补齐：

- scripted patrol scenario。
- fake MLflow client。
- fake Ray job status。
- fake artifact API。
- context compaction golden tests。
- recommendation de-dup tests。
- approval boundary tests。
- resume/fork tests。

推荐测试场景：

1. Ray 不可用 -> 生成 warning finding，不启动训练。
2. MLflow run 缺 dataset digest -> 不给最优结论。
3. Artifact 回读失败 -> 阻断 promotion recommendation。
4. 同一失败连续三轮 -> severity 升级。
5. 同一 recommendation 在 cooldown 内 -> 不重复推送。
6. 上下文超过阈值 -> 压缩后 finding/evidence 不丢。
7. 权限 deny -> 生成 needs_approval 或降级计划。

---

## 15. 建议新增/完善的文档和契约

建议在 `agent/doc/` 继续补：

- `patrol-push-agent-contract.md`：巡推 Agent 输入输出契约。
- `patrol-memory-and-compaction.md`：上下文压缩、PatrolMemory、EvidenceIndex。
- `patrol-recommendation-governance.md`：recommendation、approval、cooldown、severity。
- `patrol-agent-test-plan.md`：离线回放和 fake clients。

如果只保留本文，也建议后续把第 4、5、6、7 章拆成正式契约。

---

## 16. 推荐落地顺序

### M1：上下文和记忆

交付：

- `PatrolMemory`。
- `EvidenceIndex`。
- 多层压缩策略。
- 压缩保真校验。

验收：

- 长日志不会进入模型上下文。
- open finding / approval / evidence 不因压缩丢失。

### M2：巡检状态机

交付：

- `PatrolRunStateMachine`。
- finding lifecycle。
- recommendation lifecycle。
- cooldown / dedupe。

验收：

- 同一风险不会重复刷屏。
- 恢复的问题能自动关闭或降级。

### M3：证据链

交付：

- 统一 evidence schema。
- 工具输出 `summary_for_model + raw_ref + evidence`。
- stage/session 中的 evidence index。

验收：

- 每条推荐都能追到 evidence。
- 缺证据时不能生成高置信推荐。

### M4：推荐和审批治理

交付：

- `Recommendation` schema。
- `ApprovalRequest` 与 recommendation 关联。
- 风险等级和 rollback plan。

验收：

- promotion / long training 只生成审批请求。
- 审批前不能 apply。

### M5：失败、预算、审计

交付：

- failure taxonomy。
- retry / backoff。
- LLM/tool/resource budget。
- audit events。

验收：

- transient 失败会重试或推迟。
- policy blocked 会明确进入 needs_approval。
- 每轮都有可审计摘要。

### M6：Sub-agent 和离线回放

交付：

- 专项 sub-agent 定义。
- fake Ray/MLflow/Artifact clients。
- scripted 巡检回放。

验收：

- 不接真实服务也能验证核心巡推循环。
- 子 agent 输出不会污染主上下文。

---

## 17. 最小 P0 清单

如果只做第一批，建议只做这 8 个：

1. `PatrolMemory`。
2. `EvidenceIndex`。
3. 多层上下文压缩和保真校验。
4. `PatrolRunStateMachine`。
5. `Finding` lifecycle。
6. `Recommendation` schema + dedupe/cooldown。
7. 工具输出 `summary_for_model + evidence + raw_ref`。
8. resume 时刷新动态状态而不是直接复用旧摘要。

---

## 18. 实施可行性复核和落地细化

> 复核时间：2026-08-13。复核范围：当前 `agent/` 目录中的 SDK runtime、hooks、
> policies、state、schemas、tools、workflows、agent definitions，以及 CoreCoder 的
> context/session/loop 参考实现。

### 18.1 总体判断

这组基础能力**实施可行**，当前仓库没有架构级阻塞。原因是：

- `GalateaSDKRuntime` 已经封装 Claude SDK 的 query/runtime、hooks、permission、
  budget、output schema、session store、resume/fork 和 sub-agent 参数。
- `HookManager`、`ToolExecutor`、`PermissionPolicy`、`BudgetPolicy` 已经可以支撑
  deterministic 工具治理和离线测试。
- `ArtifactRef`、`StageEvidence`、`ApprovalRequest`、`StageResult` 可以复用为
  Patrol 域对象的底层证据和审批结构。
- `WorkflowStateMachine` 和 `ExperimentStateManager` 已经证明状态可序列化、可落盘，
  但巡推 Agent 需要新建循环型状态机和独立 Patrol session。
- 现有 inspection tools 可以作为第一批只读巡检工具，但必须升级输出契约。

需要注意的是，P0 不应继续依赖“提示词提醒模型不要忘”。要先实现一层确定性的
`patrol` core，由代码负责保存、校验、去重、节流、证据索引和权限边界；LLM 只负责
摘要、解释和生成候选建议。

推荐实现原则：

```text
schema/state first
  -> deterministic validation
  -> tool envelope and evidence index
  -> patrol loop
  -> SDK/LLM integration
  -> sub-agent and external channels
```

### 18.2 当前代码支撑面

| 方案能力 | 当前支撑代码 | 可复用部分 | 仍需新增 |
| --- | --- | --- | --- |
| SDK runtime | `agent/core/sdk.py` | `AgentSDKConfig`、`GalateaSDKRuntime`、`output_schema`、`resume`、`fork_session`、hooks 接入 | Patrol 专用 runtime prompt、结构化输出 schema、巡检结果持久化 |
| 上下文压缩 | `agent/core/sdk.py`、`agent/hooks/builtin.py` | `ContextCompressionConfig`、`check_context_usage()`、`summarize_large_tool_output_hook`、`compact_context_hook` | `PatrolMemory`、保真校验、summary version/source/window、证据外置 |
| Session | `agent/state/store.py`、`agent/state/experiment.py` | `SessionStore` 抽象、`MemorySessionStore`、`SessionManager`、file persistence helper | `PatrolSessionStore`、file-backed store、patrol resume/fork、动态证据刷新 |
| Stage 证据 | `agent/schemas/common.py` | `ArtifactRef`、`StageEvidence`、`ApprovalRequest`、`StageResult` | `EvidenceRecord`、`Finding`、`Recommendation`、`PatrolRunResult` |
| 只读工具 | `agent/tools/inspection.py`、`agent/tools/server.py` | platform/project/MLflow/Ray inspection | `summary_for_model + evidence + raw_ref` envelope、digest、sensitivity、raw source URI |
| 工具执行测试 | `agent/tools/executor.py` | direct registry/executor、pre/post hooks、permission failure normalization | scripted patrol scenarios、fake Ray/MLflow/Artifact clients |
| 状态机 | `agent/workflows/state_machine.py` | transition history、to/from dict、pause/resume 模式 | `PatrolRunStateMachine`、循环状态、失败分支、schedule_next |
| 权限 | `agent/policies/permission.py` | tool allow/deny/ask、Claude SDK `can_use_tool` adapter | L0/L1/L2/L3 action policy、project scope、risk、approval id、evidence completeness |
| 预算 | `agent/policies/budget.py` | token/cost 计数和上限 | per-round/session/day、tool call、wall-clock、Ray query、artifact read、notification budget |
| 子 agent | `agent/agents/definitions.py` | `AgentDefinition`、窄工具集、dangerous tools disallow | Patrol 专项 sub-agent、归因审计、不可递归约束、输出压缩 |

结论：P0 基本是“新增 patrol 域对象和状态层”，不是重写 runtime。

### 18.3 P0 可行性矩阵

| P0 项 | 可行性 | 推荐实现位置 | 关键风险 | 验收重点 |
| --- | --- | --- | --- | --- |
| `PatrolMemory` | 高 | `agent/schemas/patrol.py` | 字段过宽导致长期兼容困难 | open findings、approval、evidence、next_check 可序列化 |
| `EvidenceIndex` | 高 | `agent/schemas/patrol.py` | 证据 ID 与 raw_ref 不稳定 | 每条 finding/recommendation 可追到 evidence |
| 多层压缩和保真校验 | 高 | `agent/state/patrol.py` 或 `agent/patrol/memory.py` | 只压自然语言而丢结构化证据 | 必保字段压缩前后不丢；敏感内容不入 summary |
| `PatrolRunStateMachine` | 高 | `agent/workflows/patrol.py` | 复用线性 workflow 会语义混乱 | idle 到 schedule_next 的循环和失败分支可测试 |
| `Finding` lifecycle | 高 | `agent/schemas/patrol.py` + `agent/policies/patrol.py` | fingerprint 设计不稳定导致误去重或刷屏 | 连续出现升级；恢复后 resolved |
| `Recommendation` + cooldown | 高 | `agent/schemas/patrol.py` + `agent/policies/patrol.py` | recommendation 与 approval/action 混在一起 | cooldown 内不重复推；高危动作只生成 approval |
| 工具输出 envelope | 高 | `agent/tools/patrol_output.py` + inspection tools | 破坏现有调用者兼容性 | 保留原始字段或提供 `legacy_payload` |
| Resume 刷新动态状态 | 中高 | `agent/state/patrol.py` + patrol runner | 真实 Ray/MLflow 不可用时阻塞恢复 | fake client 下可离线刷新和关闭 recovered finding |

### 18.4 推荐新增代码布局

建议先在现有 `agent/` 包下小步新增，不迁移旧代码：

```text
agent/
├── schemas/
│   └── patrol.py                 # PatrolStatus, EvidenceRecord, Finding, Recommendation, PatrolMemory
├── state/
│   └── patrol.py                 # PatrolSessionStore, FilePatrolSessionStore, resume/fork helpers
├── workflows/
│   └── patrol.py                 # PatrolRunStateMachine and transition validation
├── policies/
│   └── patrol.py                 # dedupe, cooldown, escalation, action permission, resource budget
├── tools/
│   └── patrol_output.py          # summary/evidence/raw_ref envelope helpers and digest helpers
├── patrol/
│   ├── __init__.py
│   ├── runner.py                 # one patrol round orchestration
│   ├── compaction.py             # PatrolMemory compaction and fidelity checks
│   └── clients.py                # Ray/MLflow/Artifact protocol interfaces and fake clients
└── test/
    ├── test_patrol_schemas.py
    ├── test_patrol_memory.py
    ├── test_patrol_state_machine.py
    ├── test_patrol_recommendations.py
    └── test_patrol_resume.py
```

其中 `agent/patrol/clients.py` 首版只定义 protocol 和 fake，不强依赖真实服务。真实
MLflow/Ray 工具继续通过现有 MCP tools 或后续 wrappers 接入。

### 18.5 关键 schema 草案

`StageEvidence` 适合阶段结果，但巡推 Agent 还需要可长期索引、可去重、可追溯的
evidence record：

```json
{
  "evidence_id": "ev_...",
  "kind": "service_health|project_structure|ray_job|mlflow_run|artifact|quality_gate|log_excerpt",
  "source_tool": "inspect_ray_status",
  "source_uri": "ray://cluster/status or mlflow-artifacts:/...",
  "raw_ref": {
    "uri": "state://patrol/<session>/<run>/raw/inspect_ray_status.json",
    "digest": "sha256:..."
  },
  "summary": "Ray cluster is unavailable: ...",
  "created_at": "...",
  "sensitivity": "public|internal|sensitive",
  "retention": "short|normal|long",
  "metadata": {}
}
```

`Finding` 应该是生命周期对象，不是一次性告警：

```json
{
  "finding_id": "fd_...",
  "fingerprint": "sha256:...",
  "target": {
    "kind": "service|project|mlflow_run|ray_job|artifact|registry",
    "id": "ray"
  },
  "type": "service_unavailable|evidence_missing|artifact_risk|budget_exceeded|policy_blocked",
  "severity": "info|warning|critical",
  "status": "open|resolved|dismissed|superseded",
  "summary": "...",
  "evidence_ids": ["ev_..."],
  "first_seen_at": "...",
  "last_seen_at": "...",
  "occurrence_count": 1,
  "resolved_at": null,
  "cooldown_until": null
}
```

`Recommendation` 应该引用 finding 和 evidence，并明确 approval 边界：

```json
{
  "recommendation_id": "rec_...",
  "fingerprint": "sha256:...",
  "type": "wait|rerun_smoke|inspect_failed_run|request_training_approval|request_promotion_review|fix_config",
  "target": {
    "project_name": "...",
    "run_id": null,
    "artifact_uri": null
  },
  "severity": "info|warning|critical",
  "confidence": 0.0,
  "finding_ids": ["fd_..."],
  "evidence_ids": ["ev_..."],
  "risk": "low|medium|high",
  "requires_approval": false,
  "approval_request_id": null,
  "cooldown_until": "...",
  "rollback_plan": null,
  "status": "proposed|pushed|accepted|dismissed|expired|applied"
}
```

`PatrolRunResult` 是每轮最终输出，供 CLI、Notebook、artifact report 或后续 webhook 消费：

```json
{
  "patrol_run_id": "...",
  "session_id": "...",
  "status": "ok|warning|failed|needs_approval",
  "project_scope": [],
  "summary": "...",
  "findings": [],
  "recommendations": [],
  "approval_requests": [],
  "evidence": [],
  "failures": [],
  "budget": {},
  "next_check_at": "...",
  "state_update": {}
}
```

### 18.6 工具输出 envelope 细化

现有工具返回 MCP text JSON。首版可以保持 MCP transport 不变，但 JSON 内容应统一为：

```json
{
  "summary_for_model": "short, safe summary",
  "evidence": [],
  "raw_ref": {
    "uri": "state://... or mlflow-artifacts:/... or ray://...",
    "digest": "sha256:..."
  },
  "legacy_payload": {}
}
```

规则：

- `summary_for_model` 必须小于固定阈值，不能包含密钥、样本、长日志。
- `evidence` 中每条记录必须有 `evidence_id`、`source_tool`、`summary`。
- `raw_ref.digest` 对写入 session store 或 artifact 的原始 JSON 必填。
- Ray logs、MLflow run tables、大量文件列表只进入 `raw_ref`，不直接进入模型上下文。
- 如果工具暂时不能写 artifact，应使用 `state://patrol/...` 指向本地 patrol state。
- 当 MLflow 本身故障时，不能依赖 MLflow artifact 作为唯一证据存储。

### 18.7 Patrol session 与 Claude session 的边界

不要把 Claude SDK 的 resume/fork 等同于巡推 Agent 的 resume/fork。

| 类型 | 主要内容 | 用途 |
| --- | --- | --- |
| Claude session | 对话 transcript、SDK 上下文、最近模型消息 | 让 LLM 连续交互 |
| Patrol session | open findings、recommendations、approval、evidence index、memory、budget、next check | 让巡检状态可恢复、可审计 |

Patrol resume 必须执行：

1. 加载 `PatrolSession`。
2. 读取 open findings 和未完成 approval。
3. 对动态 evidence 做 refresh，例如 Ray job status、service health、MLflow run status。
4. 对恢复的 finding 写 `resolved_at`，对仍出现的 finding 更新 `last_seen_at`。
5. 只把压缩后的 `PatrolMemory` 和必要 evidence summary 放入 LLM 上下文。

Patrol fork 必须复制状态但隔离后续写入：

- `forked_from` 指向主 session。
- 继承 evidence index，但新 recommendation、finding 状态不能回写主链路。
- 可用于比较 objective metric、诊断路径或推荐策略。

### 18.8 权限模型落地方式

现有 `PermissionPolicy` 继续负责 tool-level allow/deny。巡推 Agent 还需要
`PatrolActionPolicy` 负责 action-level 校验：

| 校验项 | 示例 |
| --- | --- |
| tool name | 是否允许调用 `inspect_mlflow_experiment`、`submit_ray_training_job` |
| action level | L0 inspect、L1 recommend、L2 request_approval、L3 apply |
| project scope | 是否在 `project_scope` 内 |
| resource budget | smoke/long train/GPU/trial 是否超预算 |
| risk | low/medium/high 是否允许自动处理 |
| approval id | L3 是否有未过期且匹配 action 的 approval |
| evidence completeness | promotion 是否有 artifact recovery、quality gate、rollback plan |

首版默认策略：

- 常态 patrol runtime：只开放 L0 + L1。
- `request_approval` 可以作为 L2 单独开关。
- L3 apply 工具不进入常态 `allowed_tools`。
- promotion、alias、删除、覆盖、长训练、GPU trial、自动调参全部需要显式审批。

### 18.9 失败分类和重试落地方式

现有 `classify_tool_failure_hook` 只返回自然语言 guidance。建议新增 `PatrolFailure`：

```json
{
  "failure_id": "fl_...",
  "failure_type": "transient|permission|evidence_missing|incompatible|data_risk|artifact_risk|budget_exceeded|policy_blocked",
  "recoverability": "retryable|needs_input|blocked|non_retryable",
  "retry_after": "...",
  "evidence_id": "ev_...",
  "recommended_next_action": "retry_later|degraded_summary|request_approval|needs_human",
  "message": "..."
}
```

策略：

- `transient`：指数退避，保留同一 finding，延迟 `next_check_at`。
- `permission`：如果 action 是 L2/L3，生成 approval request 或降级只读。
- `evidence_missing`：禁止高置信 recommendation，只建议补证据。
- `incompatible`：不做“最优 run”结论，只给兼容性报告。
- `artifact_risk`：阻断 promotion。
- `budget_exceeded`：压缩上下文、缩小 scope、只输出 critical finding。
- `policy_blocked`：进入 `needs_approval`，不能绕过 policy。

### 18.10 详细实施任务拆解

#### A. Schema 和 evidence core

交付：

- `agent/schemas/patrol.py`。
- `EvidenceRecord`、`RawRef`、`Finding`、`Recommendation`、`PatrolMemory`、
  `PatrolRunResult`、`PatrolFailure`。
- ID/fingerprint/digest helper。

验收：

- 所有对象可 Pydantic validate、JSON dump/load。
- finding/recommendation fingerprint 对字段顺序稳定。
- evidence 缺 `raw_ref.digest` 时按规则 fail 或降级。

#### B. Patrol session store 和 compaction

交付：

- `FilePatrolSessionStore`，默认写到可配置目录，首选 `platform-data/agent-state`
  或测试中的 `/tmp`。
- `compact_patrol_memory()` 和 `validate_compaction_fidelity()`。
- 必保字段校验：`patrol_run_id`、`project_name`、`ray_job_id`、`submission_id`、
  `mlflow_run_id`、`artifact_uri`、`manifest_digest`、`model_artifact_uri`、
  `registry_action`、`approval_request_id`、`finding_id`、`recommendation_id`、
  `next_check_at`、`unresolved_errors`。

验收：

- 长日志不进入 compacted summary。
- open finding、approval request、unresolved error 压缩后仍可定位 evidence。
- summary 带 version、source run ids、window。
- fake secret/token 会被脱敏或阻断写入 summary。

#### C. Patrol state machine 和 lifecycle

交付：

- `PatrolRunStateMachine`。
- 状态：`idle`、`collect_context`、`inspect`、`classify_findings`、`recommend`、
  `request_approval`、`persist_state`、`schedule_next`。
- 失败分支：`inspect_failed`、`retry_later`、`degraded_summary`、`needs_human`。
- finding/recommendation dedupe、cooldown、severity escalation、resolved handling。

验收：

- 同一 evidence digest 重复出现不会重复推送。
- 同一 finding 连续三轮可升级 severity。
- 恢复的服务健康 finding 自动 `resolved`。
- cooldown 内 recommendation 不进入推送列表。

#### D. Tool envelope 和只读 patrol runner

交付：

- `agent/tools/patrol_output.py`。
- 包装 `inspect_project_structure`、`check_service_health`、`inspect_mlflow_experiment`、
  `inspect_ray_status`、`list_training_projects` 的输出。
- `PatrolRunner.run_once()`：不依赖 LLM 也能完成一轮只读巡检和结构化输出。

验收：

- 每个工具输出都有 `summary_for_model`、`evidence`、`raw_ref`。
- Ray 不可用时生成 warning finding，不启动训练。
- MLflow 不可用时生成 service finding，并使用 local state raw_ref。

#### E. SDK/LLM 集成

交付：

- Patrol 专用 `AgentSDKConfig` 构造 helper。
- `PatrolRunResult` JSON schema 作为 `output_schema`。
- prompt 中只注入 compacted `PatrolMemory` 和 evidence summary。
- LLM 输出只作为 recommendation candidate，最终仍经 policy 校验。

验收：

- 结构化输出不合 schema 会失败。
- LLM 试图建议 promotion apply 时被 action policy 改写为 approval request。
- 超过 token/cost 时降级为 deterministic summary。

#### F. Sub-agent、审计和推送通道

交付：

- `ServiceHealthSubagent`、`MLflowRunSubagent`、`ArtifactSubagent`、
  `QualityGateSubagent`、`RiskReviewSubagent` 定义。
- audit event writer。
- CLI summary、Notebook display、Markdown report artifact channel。

验收：

- hooks 能记录 `agent_id` / `agent_type`。
- 子 agent 输出被压缩为 finding/evidence/recommendation。
- 子 agent 无 L3 apply 权限。
- Markdown report 不含敏感样本、密钥或长日志。

### 18.11 离线测试计划

首批测试应尽量不依赖真实 Ray/MLflow/MinIO：

| 场景 | 输入 | 预期 |
| --- | --- | --- |
| Ray 不可用 | fake Ray status unavailable | warning finding；recommendation 为 wait/inspect；不提交训练 |
| MLflow run 缺 dataset digest | fake run summary 无 digest tag | `evidence_missing` finding；不给 best-run 结论 |
| Artifact 回读失败 | fake artifact client raises | `artifact_risk` finding；阻断 promotion recommendation |
| 连续失败升级 | 同一 fingerprint 连续三轮 | occurrence_count 增加，severity 按策略升级 |
| cooldown 抑制 | 同一 recommendation 在 cooldown 内 | 不重复 push，记录 suppressed count |
| 上下文超阈值 | 大日志 + open finding + approval | 压缩后 evidence/ref 不丢，大日志不入 summary |
| 权限 deny | fake L3 action without approval | `needs_approval` 或 `policy_blocked`，不 apply |
| Resume 刷新 | open service finding 后 fake status recovered | finding 标记 resolved，summary 更新 |
| Fork 隔离 | fork session 后生成不同 recommendation | 主 session 不被污染 |
| 敏感内容 | tool raw output 包含 token/password | summary 脱敏，raw_ref 标记 sensitive |

### 18.12 主要风险和决策

| 风险 | 影响 | 决策 |
| --- | --- | --- |
| 把状态放进自然语言上下文 | 压缩后丢证据、丢审批状态 | 结构化 Patrol session 为权威，LLM 上下文只是视图 |
| 依赖 MLflow artifact 保存所有证据 | MLflow 故障时无法记录 MLflow 故障证据 | 支持 local `state://` raw_ref，MLflow artifact 是增强通道 |
| 复用线性 workflow | 巡检循环和失败分支语义混乱 | 新增 `PatrolRunStateMachine` |
| 只用 tool permission 管 L3 动作 | 高危动作可能通过允许工具绕过 | 新增 action-level `PatrolActionPolicy` |
| 一开始接真实服务 | 测试不稳定、开发慢 | fake clients 和 deterministic runner 先行 |
| 过早做 webhook/email | 增加运维和安全面 | 第一版只做 CLI/Notebook/Markdown/MLflow artifact |

### 18.13 修订后的推荐顺序

原 M1 和 M3 实际应合并推进，因为没有 evidence index 就无法验证 memory compaction
是否保真。建议改成：

1. **Evidence + Schema**：先定义 evidence、finding、recommendation、patrol result。
2. **Memory + Session**：实现 PatrolMemory、file store、compaction fidelity。
3. **State Machine + Lifecycle**：实现 run states、dedupe、cooldown、resolved/escalation。
4. **Tool Envelope + Deterministic Runner**：让无 LLM 巡检先跑通。
5. **Recommendation Governance**：approval、risk、rollback、action policy。
6. **Budget + Failure + Audit**：失败 taxonomy、retry/backoff、多层预算、审计事件。
7. **SDK/LLM Integration**：结构化输出、上下文视图、候选建议后校验。
8. **Sub-agent + Replay + Channels**：专项子 agent、离线回放、Markdown/MLflow 报告。

第一批最小可交付建议压缩为：

- `agent/schemas/patrol.py`
- `agent/state/patrol.py`
- `agent/workflows/patrol.py`
- `agent/policies/patrol.py`
- `agent/tools/patrol_output.py`
- `agent/patrol/runner.py`
- `agent/test/test_patrol_memory.py`
- `agent/test/test_patrol_state_machine.py`
- `agent/test/test_patrol_recommendations.py`

### 18.14 Go / No-Go 验收线

进入真实 SDK/LLM 集成前，至少满足：

- 不连接真实 Ray/MLflow/MinIO 也能通过 fake clients 跑完整 `run_once()`。
- 每条 recommendation 都能追溯到 evidence。
- 缺 evidence 时不能产生高置信 recommendation。
- promotion、alias、删除、覆盖、长训练不会在无 approval 情况下进入 apply。
- session 落盘后可 resume，且动态状态会刷新。
- compacted memory 不包含长日志、样本、token、password。
- 所有 P0 测试可通过 `python -m unittest` 离线运行。

---

## 19. 一句话建议

**当前巡推 Agent 最该补的是“长状态 + 长证据链 + 上下文压缩保真 + 推荐治理”，不是代码工具。CoreCoder 可借鉴的是这些底层 agent 机制，而不是它的文件编辑能力。**
