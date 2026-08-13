# 训推一体化记忆与上下文压缩契约

> 状态：P0 实施契约，已对齐当前 `agent/schemas/patrol.py`、`agent/patrol/compaction.py`、
> `agent/tools/patrol_output.py` 和 `agent/state/patrol.py`。训推职责边界见
> [`patrol-push-agent-contract.md`](patrol-push-agent-contract.md)。

## 1. 目标

训推一体化 Agent 需要长期运行，不能依赖自然语言 transcript 保存关键状态。压缩目标是：

- 长日志、run table、样本不进入模型上下文。
- open finding、approval、unresolved error、evidence URI/digest 不丢。
- summary 有版本、来源、时间窗口和覆盖范围。
- 原始证据可通过 raw_ref 追溯。
- 压缩结果可测试、可审计、可脱敏。

## 2. 三层上下文

| 层 | 内容 | 保存位置 | 进入模型上下文方式 |
| --- | --- | --- | --- |
| 工作上下文 | 当前目标、最近工具观察、未解决 finding | Claude session / runtime memory | 原文或短摘要 |
| 训推摘要 | 数据清洗、训练、推理加速、全局检查状态、历史 finding、最近推荐、未完成审批 | Patrol session store | `PatrolMemory.summary` |
| 证据索引 | Ray job id、MLflow run id、artifact URI、digest、日志 URI | EvidenceIndex + Artifact/session state | ID/URI/摘要 |

权威顺序：Evidence/raw artifact > Patrol session > compacted memory > Claude transcript。

## 3. PatrolMemory

当前 Pydantic 对象是 `agent.schemas.patrol.PatrolMemory`；`summary_version` 在代码中是
`SummaryVersion` 对象，不是裸整数。

```json
{
  "patrol_run_id": "ptr_...",
  "project_name": "...",
  "window": {
    "started_at": "...",
    "ended_at": "..."
  },
  "summary_version": {
    "version": 1,
    "source_patrol_run_ids": []
  },
  "summary": "...",
  "open_findings": [],
  "closed_findings": [],
  "recommendations": [],
  "approval_requests": [],
  "evidence_index": [],
  "unresolved_errors": [],
  "next_check_at": "..."
}
```

规则：

- `summary` 只能是安全摘要，不存长日志或敏感样本。
- `summary_version.source_patrol_run_ids` 记录 summary 覆盖了哪些轮次。
- `open_findings`、`approval_requests` 只可压缩文本，不可丢 `id`、`status`、`evidence_ids`。
- `evidence_index` 中只保留轻量索引和 raw_ref，不保存完整原始内容。
- `unresolved_errors` 必须保留到 resolved 或 dismissed。

## 4. EvidenceRecord

```json
{
  "evidence_id": "ev_...",
  "kind": "service_health|project_structure|ray_job|mlflow_run|artifact|quality_gate|log_excerpt",
  "source_tool": "inspect_ray_status",
  "source_uri": "ray://cluster/status",
  "raw_ref": {
    "uri": "state://patrol/<session>/<run>/raw/inspect_ray_status.json",
    "digest": "sha256:..."
  },
  "summary": "Ray cluster is unavailable",
  "created_at": "...",
  "sensitivity": "public|internal|sensitive",
  "retention": "short|normal|long",
  "metadata": {}
}
```

规则：

- `evidence_id` 在一个 Patrol session 中唯一。
- `raw_ref.digest` 对写入 local state 或 artifact 的原始 JSON 必填。
- `source_uri` 可以是 `ray://`、`mlflow-artifacts:/`、`runs:/`、`state://` 或受控 API URI。
- 当 MLflow 自身故障时，必须允许 `state://` local raw_ref，不能依赖 MLflow artifact。
- `sensitivity=sensitive` 的 evidence 默认不进入 Markdown report 原文。

## 5. 工具输出 envelope

当前 Patrol runner 使用 `agent.tools.patrol_output.build_tool_envelope()` 把只读巡检 payload
包装为：

```json
{
  "summary_for_model": "short safe summary",
  "evidence": [],
  "raw_ref": {
    "uri": "state://... or mlflow-artifacts:/... or ray://...",
    "digest": "sha256:..."
  },
  "legacy_payload": {}
}
```

规则：

- `summary_for_model` 小于固定阈值，例如 2 KB。
- `legacy_payload` 用于兼容旧调用者，后续可移除或降级。
- 大日志和完整 run tables 只写 raw_ref。
- 每个 tool response 至少产生一条 evidence，除非工具没有观测结果且返回 `is_error=true`。
- `summary_for_model` 和 evidence summary 必须经过脱敏。

## 6. 压缩策略

采用四步策略：

1. **工具输出裁剪**：大输出保留状态码、错误类型、head/tail 摘要、raw_ref。
2. **轮次总结**：历史 patrol runs 压成 `PatrolMemory.summary`。
3. **证据外置**：原始 JSON、日志、run tables 写 local state 或 Artifact，只保留 URI/digest。
4. **硬保真校验**：压缩后必保字段和 evidence 可达性必须通过。

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

## 7. 保真校验

`validate_compaction_fidelity(before, after)` 至少检查：

- 所有 open finding 仍存在或有明确 superseded/resolved 记录。
- 所有 open finding 的 `evidence_ids` 仍可在 evidence index 中定位。
- 所有 approval request 仍可定位 proposed action 和 evidence。
- `unresolved_errors` 不丢失。
- `next_check_at` 存在且可解析。
- 所有 raw_ref 有 URI 和 digest。
- summary 不包含已知 secret pattern、token、password、access key、长日志片段。

失败策略：

- P0 默认抛出 validation error，不写入 corrupted compacted memory。
- 如果是预算耗尽导致无法做 LLM summary，则使用 deterministic fallback summary。
- 如果 evidence 缺 raw_ref，finding 可以保留，但高置信 recommendation 必须降级。

## 8. 脱敏规则

必须阻断或替换：

- `ANTHROPIC_API_KEY`、MinIO access/secret key、token、password、Bearer header。
- 训练样本原文、敏感标签、隐私样本路径。
- 大段日志、完整 stack dump、完整 DataFrame。

建议替换格式：

```text
[REDACTED:credential]
[REDACTED:sample]
[TRUNCATED:log chars=123456 raw_ref=state://...]
```

## 9. 存储位置

| 内容 | 首选 | 备注 |
| --- | --- | --- |
| Patrol session | `platform-data/agent-state` 或配置路径 | 运行状态，Git 忽略 |
| 测试状态 | `/tmp/galatea-agent-state` | 离线测试用 |
| 原始证据 | `state://patrol/...` 或 MLflow artifact | MLflow 故障时用 local state |
| Markdown report | MLflow artifact 或配置路径 | 不含敏感原文 |
| Claude transcript | SDK session store 或 transcript mirror | 非权威，需 retention |

当前 file-backed P0 的实际路径由 `PatrolRunner(state_dir=...)` 决定：

- session JSON：`<state_dir>/patrol-sessions/<session_id>.json`。
- raw payload：`<state_dir>/raw/<session_id>/<patrol_run_id>/*.json`。
- audit JSONL：`<state_dir>/patrol-audit/<session_id>.jsonl`，由 `FileAuditEventWriter` 使用。

## 10. 与 hooks 的关系

现有 hooks 继续做上下文卫生：

- `summarize_large_tool_output_hook`：防止大工具输出进入模型上下文。
- `compact_context_hook`：给 Claude SDK compaction 提供保留字段提示。

但 PatrolMemory 压缩不能只靠 hooks。权威压缩逻辑必须在 patrol core 中实现和测试。
