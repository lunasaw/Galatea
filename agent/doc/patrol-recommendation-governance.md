# 巡推推荐与治理契约

> 状态：P0 实施契约。本文定义 Finding、Recommendation、ApprovalRequest、去重、
> 冷却、升级和回滚计划。巡推职责边界见 [`patrol-push-agent-contract.md`](patrol-push-agent-contract.md)。

## 1. 目标

巡推 Agent 的“推”不是一句自然语言建议，而是可治理对象：

- 每个 finding 可追溯到 evidence。
- 每个 recommendation 可追溯到 finding/evidence。
- 同类风险不会重复刷屏。
- 高风险动作只生成 approval request，不自动执行。
- dismissal、resolved、accepted、applied 都可审计。

## 2. Finding

```json
{
  "finding_id": "fd_...",
  "fingerprint": "sha256:...",
  "target": {
    "kind": "service|project|mlflow_run|ray_job|artifact|registry|resource",
    "id": "ray"
  },
  "type": "service_unavailable|evidence_missing|incompatible|data_risk|artifact_risk|budget_exceeded|policy_blocked",
  "severity": "info|warning|critical",
  "status": "open|resolved|dismissed|superseded",
  "summary": "...",
  "evidence_ids": [],
  "first_seen_at": "...",
  "last_seen_at": "...",
  "occurrence_count": 1,
  "resolved_at": null,
  "cooldown_until": null
}
```

规则：

- `fingerprint` 基于 target、type、关键 evidence digest 或稳定错误码。
- 同一 fingerprint 再次出现时更新 `last_seen_at` 和 `occurrence_count`，不新建 finding。
- 观测恢复时写 `resolved_at`，不删除历史 finding。
- 人工 dismiss 必须记录 reason 和 actor。
- superseded 表示被更具体或更新 finding 替代。

## 3. Recommendation

```json
{
  "recommendation_id": "rec_...",
  "fingerprint": "sha256:...",
  "type": "wait|rerun_smoke|inspect_failed_run|request_training_approval|request_promotion_review|fix_config|degraded_summary",
  "target": {
    "project_name": "...",
    "run_id": null,
    "artifact_uri": null
  },
  "severity": "info|warning|critical",
  "confidence": 0.0,
  "finding_ids": [],
  "evidence_ids": [],
  "risk": "low|medium|high",
  "requires_approval": false,
  "approval_request_id": null,
  "cooldown_until": "...",
  "rollback_plan": null,
  "status": "proposed|pushed|accepted|dismissed|expired|applied"
}
```

规则：

- `confidence` 是证据充分性和策略确定性的表达，不是模型自信文本。
- `requires_approval=true` 时必须有 approval plan 或明确 `approval_request_id`。
- `risk=high` 必须提供 rollback plan 或说明为什么只请求人工复核。
- `status=applied` 只能由显式 apply 流程设置，巡推常态不能设置。
- 同一 recommendation fingerprint 在 cooldown 内不得重复推送。

## 4. ApprovalRequest

可复用 `agent/schemas/common.py` 中的 `ApprovalRequest`，巡推层需要补充关联字段：

```json
{
  "approval_id": "apr_...",
  "type": "long_training|gpu_trial|force_attempt|model_promotion|registry_alias_change|destructive_data_action",
  "risk": "medium|high",
  "summary": "...",
  "requested_by_patrol_run_id": "ptr_...",
  "recommendation_id": "rec_...",
  "proposed_action": {},
  "evidence_ids": [],
  "rollback_plan": "...",
  "expires_at": null,
  "status": "requested|approved|denied|expired|applied"
}
```

规则：

- approval request 不等于执行授权；apply 时仍要重新验证 approval、scope、risk、evidence。
- `expires_at` 过期后不能 apply。
- proposed action 必须结构化，不能只有自然语言。
- production alias / traffic 切换必须单独 approval，不能和训练 approval 合并。

## 5. 去重和冷却

### 5.1 Finding fingerprint

推荐字段：

```text
target.kind + target.id + finding.type + normalized_error_code_or_evidence_digest
```

例子：

- `service:ray:service_unavailable:timeout`
- `mlflow_run:<run_id>:evidence_missing:dataset_digest`
- `artifact:<uri>:artifact_risk:sha256:<digest>`

### 5.2 Recommendation fingerprint

推荐字段：

```text
recommendation.type + target project/run/artifact + risk + primary finding fingerprint
```

例子：

- `wait:ray:low:fd_...`
- `request_training_approval:ray-cats-and-dogs:high:fd_...`
- `request_promotion_review:<run_id>:high:artifact_recovery_pass`

### 5.3 Cooldown

默认建议：

| Severity | 默认 cooldown | 说明 |
| --- | --- | --- |
| info | 24h | 避免刷屏 |
| warning | 4h | 服务/训练问题可较快复查 |
| critical | 30m | 可高频提醒但仍需抑制重复 |

cooldown 可被以下情况打破：

- severity 升级。
- 新 evidence digest 出现。
- target 发生变化。
- 用户主动 request check。

## 6. Severity 升级

建议规则：

| 条件 | 升级 |
| --- | --- |
| 单次轻微缺证据 | info |
| 影响训练/推理决策 | warning |
| 阻断 promotion 或可能影响生产 | critical |
| 同一 warning 连续 3 轮未恢复 | critical |
| data split/schema 风险 | warning 或 critical |
| 未审批 L3 apply 尝试 | critical + policy_blocked |

severity 降级：

- 观测恢复后 finding -> resolved。
- 用户 dismiss 后不再推送，但保留审计。
- 后续 evidence 表明影响缩小，可新建 superseding finding 或更新 severity，并记录 reason。

## 7. 推荐类型和策略

| 类型 | 何时生成 | 审批 |
| --- | --- | --- |
| `wait` | transient、服务短暂不可用、预算不足但无紧急风险 | 不需要 |
| `degraded_summary` | 超预算或部分工具不可用 | 不需要 |
| `inspect_failed_run` | MLflow/Ray 有 failed run/job，证据足够 | 不需要或 L0 |
| `rerun_smoke` | 小预算 smoke 可帮助验证，策略允许 | 可能需要 |
| `fix_config` | 项目配置缺失或不满足 contract | 不自动改代码 |
| `request_training_approval` | 长训练/GPU/search/force attempt | 需要 L2/L3 |
| `request_promotion_review` | 候选模型满足门禁但需人工 promotion | 需要 L2/L3 |

## 8. 质量门禁

高风险 recommendation 必须满足：

- 有 compatible evidence cohort。
- 有 dataset/split/preprocess identity。
- 有 objective metric 和 direction。
- 未使用 final test 指标做搜索。
- artifact 可回读。
- rollback plan 明确。
- approval request 指向完整 evidence。

缺任一项时：

- 降低 confidence。
- 转为 `evidence_missing` finding。
- 只推荐补证据或人工复核。

## 9. 推送通道

首版通道：

- CLI summary。
- Notebook display。
- Markdown report artifact。
- MLflow tag/comment/artifact。

未来通道：

- webhook。
- email。
- IM。

通道规则：

- 通道只负责呈现，不改变 finding/recommendation 状态权威。
- 推送成功/失败应记录 audit event。
- 通道内容不包含敏感样本、密钥或长日志。
- cooldown 应在推送前检查。

## 10. 回滚计划

需要 rollback plan 的动作：

- Registry alias change。
- Ray Serve 部署或 traffic 切换。
- 大规模 batch inference 写出。
- 覆盖数据或 artifact 的任何动作。
- 长训练导致资源占用或队列影响。

rollback plan 至少包含：

- 当前状态快照 URI。
- 目标对象和变更范围。
- 恢复命令或人工步骤。
- 验证方式。
- 失败时升级联系人或人工步骤。

## 11. 审计事件

推荐记录：

- `finding_created`
- `finding_updated`
- `finding_resolved`
- `finding_dismissed`
- `recommendation_created`
- `recommendation_suppressed`
- `recommendation_pushed`
- `approval_requested`
- `approval_decision_recorded`
- `policy_blocked`
- `apply_attempted`
- `apply_completed`

每个事件至少记录 `patrol_run_id`、`session_id`、`project_scope`、`actor`、`target`、`evidence_ids`。
