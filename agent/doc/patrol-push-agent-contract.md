# 训推一体化 Agent 契约

> 状态：P0 实施契约，已对齐当前 `agent/patrol/`、`agent/schemas/patrol.py`、
> `agent/state/patrol.py`、`agent/workflows/patrol.py` 和 `agent/policies/patrol.py`。
> 历史推导只保存在 `archive/`，不再作为当前实现契约。

## 1. 定义

训推一体化 Agent 是面向 Galatea 训练到推理全生命周期的可审计 Agent。
它不再定义为“巡逻推送 Agent”；`Patrol*` 只是当前底层实现沿用的巡检、证据、
推荐、session 和审批治理组件命名。

核心职责：

- 数据清洗：检查数据源、manifest、split、preprocessing、schema/quality 风险，并生成可恢复的数据处理计划。
- 模型训练：校验训练配置，执行或申请执行 check-config、plan、smoke、正式训练和调参，并记录 MLflow/Ray 证据。
- 推理加速：验证模型 artifact 回读，执行 smoke/batch inference，生成推理优化、Ray Serve、promotion 和 rollback 计划。
- 全局检查：巡检平台服务、训练项目、Ray Job、MLflow Run、Artifacts、资源和治理状态。
- 文档更新：生成审计报告、运行记录和文档更新建议；修改源码文档必须经过明确的文档更新动作或人工授权。

当前 P0 已实现的是训推一体化 Agent 的全局检查和推荐治理底座：只读优先、可离线运行、
可追溯 evidence，并通过 `PatrolRunner`、`PatrolMemory` 和 `PatrolRunResult` 承载。

它不是 CodeMaintenanceAgent，也不是无人值守 AutoML 执行器。默认不修改源码、不启动长训练、
不改 Registry alias、不删除或覆盖数据。

## 2. 非目标

首版不做：

- 常驻无人值守 AutoML。
- 未审批长训练、GPU trial 或 Ray Tune 搜索。
- 未审批 Registry version 创建或 alias 变更。
- 未审批 Ray Serve 部署或 production traffic 切换。
- 裸 Bash 平台动作。
- 直接读取 `platform-data/mlflow/mlflow.db` 或 MinIO 服务端目录。
- 将大日志、样本、密钥、敏感标签写入 prompt、transcript、summary 或 report。

## 3. 默认输入

当前 `PatrolRunner` 构造参数覆盖 P0 必需输入：`project_root`、`state_dir`、
`session_id`、`project_scope`、`service_checks`、`mlflow_experiments`、`tool_overrides`、
`lifecycle_policy` 和 `next_check_delay_seconds`。下方 JSON 是面向后续 CLI/API 的配置形态。

```json
{
  "session_id": "patrol-...",
  "project_scope": ["ray-cats-and-dogs"],
  "inspection_scope": ["services", "projects", "ray", "mlflow", "artifacts", "governance", "resources"],
  "tracking_uri": "http://127.0.0.1:5000",
  "ray_address": "auto/local",
  "minio_endpoint": "http://127.0.0.1:9000",
  "budget": {
    "max_tool_calls": 30,
    "max_wall_clock_seconds": 120,
    "max_llm_cost_usd": 0.20,
    "max_artifact_read_mb": 50
  },
  "permissions": {
    "max_action_level": "recommend",
    "allow_request_approval": false,
    "allow_apply": false
  },
  "now": "2026-08-13T00:00:00Z"
}
```

输入规则：

- `project_scope` 为空时只巡检平台服务和项目列表，不自动深入所有项目。
- `tracking_uri`、experiment identity 和 project scope 必须来自显式配置或用户输入。
- `allow_apply=false` 是常态默认值；即使用户启用 L2 审批请求，也不代表可以 L3 apply。

## 4. 默认输出

当前权威输出对象是 `agent.schemas.patrol.PatrolRunResult`。

```json
{
  "patrol_run_id": "ptr_...",
  "session_id": "patrol-...",
  "status": "ok|warning|failed|needs_approval",
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

输出规则：

- `summary` 是安全摘要，不是权威状态。
- `findings`、`recommendations`、`approval_requests`、`evidence` 是权威结构化结果。
- 每条 recommendation 必须能追溯到 `evidence_ids`。
- 缺证据时不能生成高置信或高风险 recommendation。
- `status=needs_approval` 表示需要人工授权，不表示失败。

## 5. 工作对象

| 对象 | 例子 | 当前/后续工具形态 |
| --- | --- | --- |
| 数据清洗 | source manifest、split、preprocessing version、schema drift、坏样本/空值/重复样本 | 当前只做结构巡检；后续 data tools / Ray Data job |
| 模型训练 | training config、Ray job、MLflow runs、failed runs、checkpoint artifacts、objective metric | `inspect_mlflow_experiment`；后续 training/run summary tool |
| 推理加速 | model artifact、artifact recovery、smoke/batch inference、serve/optimization plan、quality gates | 后续 artifact/inference acceleration tools |
| 全局检查 | JupyterLab、Ray、MLflow、MinIO、systemd、ports、health endpoints、资源容量 | `check_service_health`、`inspect_ray_status` |
| 项目结构 | `train-model/<project>` contract、README、configs、scripts、tests | `inspect_project_structure` |
| 文档更新 | run report、README/guide 更新建议、契约变更记录 | 当前 Markdown report；后续 doc update approval/apply flow |
| 治理状态 | approval request、registry candidate/champion/production alias、policy block | 后续 approval/registry tools |

## 6. 状态机

循环型 `PatrolRunStateMachine`：

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

状态规则：

- `collect_context` 只加载 `PatrolSession`、open findings、active recommendations、pending approvals 和 compacted memory。
- `inspect` 调用只读工具，工具输出必须进入 evidence envelope。
- `classify_findings` 是 deterministic 优先，LLM 可补充解释但不能作为唯一分类依据。
- `recommend` 生成候选 recommendation，随后必须经过 dedupe/cooldown/action policy。
- `request_approval` 只在 L2 被允许时创建 approval request；否则生成 `needs_approval` recommendation。
- `persist_state` 必须在返回结果前执行，保证中断后可恢复。
- `schedule_next` 必须考虑 severity、retry_after、cooldown 和预算。

## 7. 权限等级

| 等级 | 名称 | 默认 | 允许动作 |
| --- | --- | --- | --- |
| L0 | inspect | 开启 | 只读全局检查、读取状态摘要、生成 evidence。 |
| L1 | recommend | 开启 | 生成 finding、recommendation、数据清洗/训练/推理加速计划、Markdown/CLI summary。 |
| L2 | request_approval | 关闭 | 创建训练、推理、promotion 或文档更新 approval request，不执行高风险动作。 |
| L3 | apply | 关闭 | 审批后执行受控训练、推理加速、promotion、alias、文档 patch 或其他变更。 |

默认训推一体化 runtime 的当前 P0 只拿 L0 + L1。L3 工具不应在常态 `allowed_tools` 中。

## 8. Action policy

`PermissionPolicy` 继续管 tool-level allow/deny；训推一体化 Agent 还需要 `PatrolActionPolicy` 管 action-level：

- `tool_name` 是否允许。
- `project_scope` 是否匹配。
- `resource_budget` 是否足够。
- `risk` 是否超过当前 action level。
- `approval_id` 是否存在、未过期、匹配 proposed action。
- `evidence_completeness` 是否满足高风险推荐要求。

默认策略：

- 健康报告、finding、recommendation、运行报告和文档更新建议允许。
- approval request 需要 L2。
- 数据清洗 dry-run、check-config、plan 和小预算 smoke 可由策略允许或要求用户明确授权。
- 长训练、GPU trial、Ray Tune/search、Registry write、alias change、Serve deploy、源码文档 patch、删除/覆盖全部需要审批。
- 删除数据和覆盖 artifact 默认禁止，即使 L3 也应单独显式确认。

## 9. Session 边界

Patrol session 不是 Claude session：

| 类型 | 保存内容 | 权威性 |
| --- | --- | --- |
| Claude session | 对话 transcript、最近模型上下文 | 解释性、辅助恢复 |
| Patrol session | open findings、recommendations、approvals、evidence index、memory、budget、next check | 训推状态权威 |

Resume 必须刷新动态状态，不能直接复用旧自然语言摘要。

Fork 可用于比较推荐策略、objective metric 或诊断路径，但不得污染主 session。

## 10. 首版 runner

当前已实现 deterministic `PatrolRunner.run_once()`，LLM 只作为后续候选推荐/摘要层：

```text
load session
  -> build inspection plan
  -> call read-only tools or tool_overrides
  -> wrap evidence
  -> classify findings
  -> dedupe/cooldown recommendations
  -> persist session
  -> return PatrolRunResult
```

当前实现说明：

- `tool_overrides` 是离线测试和回放接口；仓库里没有正式的 Ray/MLflow/Artifact fake client
  类族，只有 `agent/patrol/clients.py` 的轻量 fake tool collection 草案。
- 原始 tool payload 写入 configured `state_dir` 下的 `raw/<session>/<run>/`，模型上下文只拿
  `summary_for_model`、`evidence` 和 `raw_ref`。
- session 权威状态写 `FilePatrolSessionStore`。
- `agent/patrol/channels.py` 负责 CLI/Markdown report 脱敏输出。

LLM 集成只在 deterministic runner 跑通后添加：

- 输入只包含 compacted memory 和 evidence summary。
- 输出只作为 recommendation candidate。
- 最终 recommendation 必须再经 policy 校验。

## 11. 验收线

进入真实 SDK/LLM 集成前必须满足：

- `tool_overrides` 下完整 `run_once()` 可离线运行。
- Ray 不可用只生成 warning finding，不提交训练。
- 每条 recommendation 可追溯到 evidence。
- 缺 evidence 不产生高置信 recommendation。
- 未审批高风险动作不会进入 apply。
- session 落盘后 resume 能刷新动态状态。
- compacted memory 不含长日志、样本、token、password。
