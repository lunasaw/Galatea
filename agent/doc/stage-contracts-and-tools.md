# 阶段契约与工具设计

> 状态：设计草案 + 当前边界说明。本文为训推一体化 Agent 的数据清洗、模型训练、
> 推理加速和文档更新阶段定义稳定输入输出、工具清单和治理规则。当前代码只有通用
> `StageResult` / `ArtifactRef` / `StageEvidence` / `ApprovalRequest` 公共对象，以及
> Patrol 命名的训推 P0 巡检治理实现；专用执行工具和专用 schema 仍是 planned。

## 0. 当前/规划边界

| 范围 | 当前状态 | 代码/文档位置 |
| --- | --- | --- |
| 公共阶段对象 | 已实现 | `agent/schemas/common.py` |
| 训推 P0 巡检治理对象 | 已实现 | `agent/schemas/patrol.py`, `agent/patrol/`, `agent/state/patrol.py` |
| 数据清洗/模型训练/推理加速/文档更新专用 schema | 未实现 | 本文第 2/3/4/5 节为规划契约 |
| 数据清洗/模型训练/推理加速/文档更新专用工具 | 未实现 | 由后续 `agent/tools/*.py` 和 stage handlers 承载 |
| Approval/apply 流程 | 未实现 | 本文第 8 节为规划契约 |

## 1. 公共对象

### 1.1 ArtifactRef

```json
{
  "uri": "mlflow-artifacts:/... or s3://...",
  "digest": "sha256:...",
  "kind": "dataset_manifest|report|checkpoint|model|prediction|config|logs",
  "created_by": "stage_run_id or mlflow_run_id",
  "metadata": {}
}
```

规则：

- `digest` 对不可变产物必填。
- 客户端优先通过 MLflow Artifact API 读取 Artifact。
- 只有 Ray runtime release 等明确场景才直接使用 MinIO S3 URI。
- 不把本地临时路径当 durable artifact。

### 1.2 StageEvidence

```json
{
  "name": "split_integrity_check",
  "status": "pass|fail|warning",
  "summary": "...",
  "artifact": null,
  "metrics": {},
  "created_at": "2026-08-12T00:00:00Z"
}
```

### 1.3 StageResult 公共字段

```json
{
  "stage": "data|training|inference",
  "status": "success|failed|needs_approval|skipped",
  "stage_run_id": "...",
  "project_name": "...",
  "objective": "...",
  "evidence": [],
  "artifacts": [],
  "warnings": [],
  "errors": [],
  "requires_approval": false,
  "approval_request": null,
  "next_action": null
}
```

## 2. 数据清洗阶段契约

### 2.1 DataStageInput

```json
{
  "project_name": "ray-cats-and-dogs",
  "source_uri": "s3://training-data/raw/...",
  "target_dataset_version": "v001",
  "split_policy": {
    "name": "deterministic_hash_split",
    "train": 0.8,
    "validation": 0.1,
    "test": 0.1,
    "seed": 20260812
  },
  "preprocessing_profile": "baseline-image-224-v1",
  "resource_budget": {
    "max_cpus": 16,
    "max_memory_gb": 32,
    "max_runtime_seconds": 1800
  },
  "write_mode": "create_only"
}
```

### 2.2 DataStageResult

```json
{
  "stage": "data",
  "status": "success",
  "stage_run_id": "ray-cats-and-dogs-data-...",
  "project_name": "ray-cats-and-dogs",
  "dataset_uri": "s3://training-data/datasets/ray-cats-and-dogs/v001/",
  "manifest_uri": "mlflow-artifacts:/.../dataset_manifest.json",
  "manifest_digest": "sha256:...",
  "source_digest": "sha256:...",
  "split_id": "split-sha256-...",
  "preprocessing_version": "baseline-image-224-v1",
  "schema": {},
  "row_counts": {
    "train": 0,
    "validation": 0,
    "test": 0,
    "total": 0
  },
  "ray_job_id": "...",
  "mlflow_run_id": "...",
  "quality_report": {
    "uri": "mlflow-artifacts:/.../data_quality.json",
    "digest": "sha256:..."
  },
  "evidence": [],
  "warnings": [],
  "errors": [],
  "requires_approval": false,
  "next_action": "training"
}
```

### 2.3 Data tools

| Tool | 副作用 | 说明 |
| --- | --- | --- |
| `inspect_dataset_source` | 无 | 检查 URI、文件数、格式、大小、抽样 schema，不返回大样本。 |
| `compute_source_manifest` | 可写 manifest cache | 计算文件清单和内容摘要。 |
| `propose_ray_data_plan` | 无 | 生成 Ray Data pipeline plan，包含资源和输出路径。 |
| `submit_ray_data_job` | 提交 Ray Job | 执行受控 Ray Data job，返回 job handle。 |
| `get_ray_job_status` | 无 | 查询状态、最近日志摘要、错误类型。 |
| `validate_dataset_output` | 无或写报告 | 校验 split 稳定性、schema、数量、空值/坏样本。 |
| `log_dataset_manifest` | 写 MLflow Artifact | 记录 manifest/report 到 MLflow。 |

### 2.4 数据清洗阶段决策规则

- 源数据没有 immutable identity 时，不进入训练。
- 评估 population 已存在时，不允许静默 reshuffle。
- 输出路径必须 create-only；发现已存在且 digest 不一致时失败。
- 数据质量失败时可以提出修复 plan，但不能删除原始数据。
- Ray Data job 必须受 `resource_budget` 限制。

## 3. 模型训练阶段契约

### 3.1 TrainingStageInput

```json
{
  "project_name": "ray-cats-and-dogs",
  "dataset_manifest_uri": "mlflow-artifacts:/.../dataset_manifest.json",
  "dataset_manifest_digest": "sha256:...",
  "config_uri": "train-model/ray-cats-and-dogs/configs/smoke.yaml",
  "experiment_name": "ray-cats-and-dogs",
  "objective_metric": "validation.accuracy",
  "objective_mode": "max",
  "execution_mode": "check-config|plan|smoke|train",
  "resource_budget": {
    "max_gpus": 1,
    "max_cpus": 24,
    "max_runtime_seconds": 3600,
    "max_trials": 1
  },
  "allow_test_evaluation": false,
  "force_new_attempt": false
}
```

### 3.2 TrainingStageResult

```json
{
  "stage": "training",
  "status": "success",
  "stage_run_id": "ray-cats-and-dogs-training-...",
  "project_name": "ray-cats-and-dogs",
  "execution_mode": "smoke",
  "ray_job_id": "...",
  "mlflow_run_id": "...",
  "run_status": "FINISHED",
  "objective_metric": "validation.accuracy",
  "objective_mode": "max",
  "best_validation_metric": 0.0,
  "checkpoint": {
    "uri": "mlflow-artifacts:/.../checkpoint",
    "digest": "sha256:..."
  },
  "model_artifact": null,
  "comparable_runs": [],
  "quality_gate_status": "not_applicable|pass|fail",
  "used_test_set": false,
  "evidence": [],
  "warnings": [],
  "errors": [],
  "requires_approval": false,
  "next_action": "inference_smoke"
}
```

### 3.3 Training tools

| Tool | 副作用 | 说明 |
| --- | --- | --- |
| `validate_training_config` | 无 | 加载 YAML 继承、环境覆盖、严格 key 校验。 |
| `inspect_compatible_mlflow_runs` | 无 | 只比较数据/split/preprocess/metric/eval protocol 兼容的 Runs。 |
| `propose_training_config` | 无 | 基于 validation evidence 生成配置建议。 |
| `submit_ray_training_job` | 提交 Ray Job | 通过 Ray Jobs API 或项目 `job/cd.py` 提交。 |
| `get_ray_job_status` | 无 | 查询 job status/log summary。 |
| `get_mlflow_run_summary` | 无 | 通过 Tracking API 查询参数、指标、tags、artifacts。 |
| `verify_checkpoint_artifact` | 无 | 通过 MLflow Artifact API 回读 checkpoint。 |
| `summarize_training_result` | 写报告 | 生成训练报告和下一步建议。 |

### 3.4 模型训练阶段决策规则

- `execution_mode=check-config` 和 `plan` 可自动运行。
- `execution_mode=smoke` 可在小预算内自动运行。
- `execution_mode=train` 若超过预算、使用 GPU 长任务或 `max_trials>1`，必须审批。
- `force_new_attempt=true` 必须审批。
- 不允许用 final test 指标做搜索、early stopping 或模型选择。
- 分布式训练只允许权威 worker/driver 创建和最终化 parent MLflow Run，除非项目文档明确 nested Run 设计。
- Artifact 回读失败时，停止后续训练和 promotion。

## 4. 推理加速阶段契约

### 4.1 InferenceStageInput

```json
{
  "project_name": "ray-cats-and-dogs",
  "candidate_mlflow_run_id": "...",
  "model_artifact_uri": "mlflow-artifacts:/.../model",
  "dataset_manifest_uri": "mlflow-artifacts:/.../dataset_manifest.json",
  "inference_mode": "smoke|batch|serve-plan",
  "quality_gates": [
    {"metric": "final_test.accuracy", "op": ">=", "value": 0.9}
  ],
  "allow_registry_write": false,
  "allow_alias_change": false
}
```

### 4.2 InferenceStageResult

```json
{
  "stage": "inference",
  "status": "needs_approval",
  "stage_run_id": "ray-cats-and-dogs-inference-...",
  "project_name": "ray-cats-and-dogs",
  "candidate_mlflow_run_id": "...",
  "model_artifact_uri": "mlflow-artifacts:/.../model",
  "artifact_recovery_status": "pass",
  "smoke_inference_status": "pass",
  "batch_prediction_artifact": null,
  "serve_plan_artifact": {
    "uri": "mlflow-artifacts:/.../serve_plan.json",
    "digest": "sha256:..."
  },
  "quality_gate_status": "pass|fail|not_evaluated",
  "registry_action": "none|proposed_create_version|proposed_alias_change",
  "used_test_set": false,
  "evidence": [],
  "warnings": [],
  "errors": [],
  "requires_approval": true,
  "approval_request": {
    "type": "model_promotion",
    "summary": "Promote candidate after human review"
  },
  "next_action": "await_approval"
}
```

### 4.3 Inference tools

| Tool | 副作用 | 说明 |
| --- | --- | --- |
| `load_model_artifact_metadata` | 无 | 查询 MLflow artifact/model flavor metadata。 |
| `verify_model_artifact_recovery` | 无 | 通过 Artifact API 下载到临时目录并加载。 |
| `run_smoke_inference` | 写报告 | 小样本推理和 schema 校验，不泄露敏感样本。 |
| `submit_batch_inference_job` | 提交 Ray Job | Ray Data batch prediction。 |
| `generate_ray_serve_plan` | 写计划 artifact | 生成 deployment plan，不直接 serve.run。 |
| `evaluate_quality_gates` | 无或写报告 | 对照门禁。 |
| `request_model_promotion_approval` | 写 approval request | 不修改 alias。 |
| `apply_model_promotion` | Registry 写 | 默认禁用；仅审批后显式调用。 |

### 4.4 推理加速阶段决策规则

- Artifact 无法回读或加载时禁止 promotion。
- `allow_alias_change=false` 时只能生成 promotion plan。
- final test 只在 champion/final evaluation 场景使用一次。
- 推理输出、预测样本、错误样本不能泄露敏感训练内容。
- Ray Serve 部署计划必须包含资源、replica、健康检查、rollback 和安全暴露方式。

## 5. 文档更新阶段契约

### 5.1 DocumentationStageInput

```json
{
  "project_name": "ray-cats-and-dogs",
  "source_stage_run_ids": ["..."],
  "report_artifact_uri": "mlflow-artifacts:/.../train_inference_report.md",
  "target_docs": ["README.md", "doc/runbook.md"],
  "write_mode": "proposal|apply",
  "allow_source_doc_patch": false
}
```

### 5.2 DocumentationStageResult

```json
{
  "stage": "documentation",
  "status": "success|needs_approval|skipped",
  "stage_run_id": "ray-cats-and-dogs-docs-...",
  "project_name": "ray-cats-and-dogs",
  "report_artifact": {
    "uri": "mlflow-artifacts:/.../train_inference_report.md",
    "digest": "sha256:..."
  },
  "doc_update_plan": [],
  "patched_files": [],
  "evidence": [],
  "warnings": [],
  "errors": [],
  "requires_approval": true,
  "next_action": "await_approval|none"
}
```

### 5.3 Documentation tools

| Tool | 副作用 | 说明 |
| --- | --- | --- |
| `generate_stage_report` | 写报告 artifact | 汇总数据清洗、训练、推理加速和全局检查证据。 |
| `propose_doc_update` | 无 | 生成 README/runbook/contract 更新计划和 diff 摘要。 |
| `validate_doc_update` | 无 | 检查路径、敏感信息、链接和证据引用。 |
| `request_doc_update_approval` | 写 approval request | 源码文档 patch 前请求人工确认。 |
| `apply_doc_update` | 修改源码文档 | 默认禁用；仅审批后显式调用。 |

### 5.4 文档更新阶段决策规则

- 运行报告和 Markdown artifact 可自动生成，但不能包含密钥、样本、长日志或敏感标签。
- 源码文档 patch 必须限定在任务相关路径，并保留 evidence/run/artifact 引用。
- 文档更新不能掩盖失败、删除审计证据或把 planned 能力写成 current。
- 与代码行为不一致时，优先更新文档为真实状态；需要代码修改时转交 CodeMaintenanceAgent 或显式任务。

## 6. Ray 工具实现约定

Ray 是执行层，Agent 不直接计算大数据。

### 6.1 Ray Job handle

```json
{
  "job_id": "...",
  "submission_id": "...",
  "dashboard_url": "http://127.0.0.1:8265",
  "entrypoint": "python scripts/train.py --config configs/smoke.yaml --check-config",
  "runtime_env_uri": "s3://training-data/ray-runtime/.../release.json",
  "status": "PENDING|RUNNING|SUCCEEDED|FAILED|STOPPED",
  "logs_uri": "mlflow-artifacts:/.../ray_logs.txt",
  "submitted_at": "..."
}
```

### 6.2 Ray submission rules

- 使用 `JobSubmissionClient` 或项目现有 `job/cd.py` 逻辑。
- `submission_id` 必须唯一或明确幂等。
- 默认先跑 `--check-config`，再跑 `--plan`，最后才训练。
- runtime_env 走 MinIO immutable release，不把密钥写入 runtime-env YAML。
- Ray Head/Worker 获取 S3 只读凭据必须在 `ray start` 前完成。
- 失败重试使用新的 attempt id，不覆盖已有失败证据。

## 7. MLflow 工具实现约定

- 只通过 Tracking、Artifact、Model Registry API。
- 不打开、不复制、不查询 `platform-data/mlflow/mlflow.db`。
- Tracking URI 和 Experiment 来自显式配置或环境变量。
- Run 比较必须确认 task、dataset、split、preprocess、metric definition、eval protocol 兼容。
- 训练和 validation evidence 用于搜索；test evidence 只用于最终评测。
- Artifact 验证使用 MLflow Artifact API，不依赖服务端 MinIO 文件路径。

## 8. Approval 契约

Approval request 是结构化对象：

```json
{
  "approval_id": "...",
  "type": "long_training|force_attempt|model_promotion|doc_update|destructive_data_action",
  "requested_by_stage_run_id": "...",
  "risk": "low|medium|high",
  "summary": "...",
  "proposed_action": {},
  "evidence": [],
  "expires_at": null
}
```

需要审批的动作：

- 大预算训练或 GPU 长任务。
- Ray Tune / 自动参数搜索。
- `--force` 新 attempt。
- 删除、覆盖或重写数据/Artifact。
- MLflow Registry 写入和 alias 变更。
- Ray Serve 真实部署或流量切换。
- 源码文档 patch、覆盖历史报告或移除审计证据。
- 任意需要裸 Bash 的代码维护动作。

## 9. 相关文档

- 总体架构：[`current-agent-architecture.md`](current-agent-architecture.md)。
- Python 实现蓝图：[`python-agent-architecture.md`](python-agent-architecture.md)。
- SDK 权限和 hooks 规范：[`claude-sdk-development-guidelines.md`](claude-sdk-development-guidelines.md)。
- 训推推荐治理：[`patrol-recommendation-governance.md`](patrol-recommendation-governance.md)。
