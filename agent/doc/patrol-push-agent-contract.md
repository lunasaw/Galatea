# 巡推 Agent 契约

> 状态：P0 实施契约。本文定义 Patrol/Push Agent 的职责、输入输出、状态机、
> 权限边界和首版落地规则。历史推导已归档到 `archive/corecoder-vs-galatea-gap-plan.md`；
> 当前实现契约以本文和 Patrol 专题文档为准。

## 1. 定义

巡推 Agent 是长期运行或按需运行的只读优先 Agent：

- `巡`：巡检平台服务、训练项目、Ray Job、MLflow Run、Artifacts、数据/模型质量、资源和治理状态。
- `推`：推送 finding、recommendation、approval request、下一轮计划和可审计报告。

它不是 CodeMaintenanceAgent，也不是 AutoML 执行器。默认不修改源码、不启动长训练、不改 Registry alias、不删除或覆盖数据。

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

## 5. 巡检对象

| 对象 | 例子 | 首版工具形态 |
| --- | --- | --- |
| 平台服务 | JupyterLab、Ray、MLflow、MinIO、systemd、ports、health endpoints | `check_service_health`、`inspect_ray_status` |
| 项目结构 | `train-model/<project>` contract | `inspect_project_structure` |
| 数据状态 | manifest、split、preprocessing version、schema drift | 后续 data tools / fake client |
| 训练状态 | Ray job、MLflow runs、failed runs、checkpoint artifacts | `inspect_mlflow_experiment`、后续 run summary tool |
| 推理状态 | model artifact、smoke inference、serve plan、quality gates | 后续 artifact/inference tools |
| 治理状态 | approval request、registry candidate/champion/production alias | 后续 approval/registry tools |
| 资源状态 | GPU/CPU/memory、Ray cluster capacity、pending jobs | Ray status / resource tools |

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
| L0 | inspect | 开启 | 只读巡检、读取状态摘要、生成 evidence。 |
| L1 | recommend | 开启 | 生成 finding、recommendation、Markdown/CLI summary。 |
| L2 | request_approval | 关闭 | 创建 approval request，不执行高风险动作。 |
| L3 | apply | 关闭 | 审批后执行训练、promotion、alias 或其他变更。 |

默认 patrol runtime 只拿 L0 + L1。L3 工具不应在常态 `allowed_tools` 中。

## 8. Action policy

`PermissionPolicy` 继续管 tool-level allow/deny；巡推 Agent 还需要 `PatrolActionPolicy` 管 action-level：

- `tool_name` 是否允许。
- `project_scope` 是否匹配。
- `resource_budget` 是否足够。
- `risk` 是否超过当前 action level。
- `approval_id` 是否存在、未过期、匹配 proposed action。
- `evidence_completeness` 是否满足高风险推荐要求。

默认策略：

- 健康报告、finding、recommendation 允许。
- approval request 需要 L2。
- smoke 可由策略允许或要求用户明确授权。
- 长训练、GPU trial、Registry write、alias change、Serve deploy、删除/覆盖全部需要审批。
- 删除数据和覆盖 artifact 默认禁止，即使 L3 也应单独显式确认。

## 9. Session 边界

Patrol session 不是 Claude session：

| 类型 | 保存内容 | 权威性 |
| --- | --- | --- |
| Claude session | 对话 transcript、最近模型上下文 | 解释性、辅助恢复 |
| Patrol session | open findings、recommendations、approvals、evidence index、memory、budget、next check | 巡检状态权威 |

Resume 必须刷新动态状态，不能直接复用旧自然语言摘要。

Fork 可用于比较推荐策略、objective metric 或诊断路径，但不得污染主 session。

## 10. 首版 runner

建议先实现 deterministic `PatrolRunner.run_once()`，再接 LLM：

```text
load session
  -> build inspection plan
  -> call read-only tools/fake clients
  -> wrap evidence
  -> classify findings
  -> dedupe/cooldown recommendations
  -> compact memory
  -> persist session
  -> return PatrolRunResult
```

LLM 集成只在 deterministic runner 跑通后添加：

- 输入只包含 compacted memory 和 evidence summary。
- 输出只作为 recommendation candidate。
- 最终 recommendation 必须再经 policy 校验。

## 11. 验收线

进入真实 SDK/LLM 集成前必须满足：

- fake clients 下完整 `run_once()` 可离线运行。
- Ray 不可用只生成 warning finding，不提交训练。
- 每条 recommendation 可追溯到 evidence。
- 缺 evidence 不产生高置信 recommendation。
- 未审批高风险动作不会进入 apply。
- session 落盘后 resume 能刷新动态状态。
- compacted memory 不含长日志、样本、token、password。
