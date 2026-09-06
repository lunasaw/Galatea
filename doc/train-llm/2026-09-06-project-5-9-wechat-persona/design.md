# 项目 5–9 总体设计

## 1. 目标、边界和不可逆决策

这组项目把“真实聊天数据工程、可删除的关系记忆、风格适配、模型容量选择和本地原型”拆开。
每个项目只引入一个主要变量，并通过同一套数据身份、评测协议和治理状态机连接起来。

目标不是复刻真人或把关系事实写死进权重，而是验证：

1. 获准的导出数据可以被标准化、脱敏、按 session 无泄漏切分并撤回；
2. 关系事实可以在模型外以 owner-scoped、可过期、可删除的记忆层检索；
3. LoRA 只学习目标角色的表达风格和互动习惯；
4. 0.8B、1.7B、4B/QLoRA 的取舍由同一冻结评测集和资源证据决定；
5. 聊天和脚本应用明确标注 AI 身份，保留双方修改/删除和关闭记忆的控制权。

不实现加密数据库破解、自动登录或发消息、语音克隆/换脸、公网多租户、心理治疗替代或自动
模型推广。未经授权的真实数据永远停在 `blocked`，合成 fixture 不得冒充真实质量证据。

## 2. 项目结构与职责

新增一个项目根：`train-model/wechat-persona/`。项目 5–9 是同一 workload 的配置变体，不为
每个模型或实验复制项目根：

```text
train-model/wechat-persona/
├── README.md
├── conda.yaml
├── galatea.project.yaml
├── configs/
│   ├── import.yaml
│   ├── rag-bm25.yaml
│   ├── rag-embedding.yaml
│   ├── persona-lora-smoke.yaml
│   ├── persona-lora-baseline.yaml
│   ├── qwen3-1.7b-lora.yaml
│   ├── qlora-4b.yaml
│   ├── local-chat.yaml
│   └── screenplay.yaml
├── src/wechat_persona/
│   ├── consent.py
│   ├── importers/{base,text,csv,json,html}.py
│   ├── normalize.py
│   ├── redact.py
│   ├── sessionize.py
│   ├── review.py
│   ├── datasets.py
│   ├── memories.py
│   ├── rag.py
│   ├── training.py
│   ├── evaluation.py
│   ├── deletion.py
│   ├── screenplay.py
│   └── runtime.py
├── scripts/
│   ├── import_chat.py
│   ├── build_dataset.py
│   ├── review_app.py
│   ├── build_memory_index.py
│   ├── train.py
│   ├── evaluate.py
│   ├── chat_local.py
│   └── generate_screenplay.py
└── tests/
```

源代码、配置、schema、测试和文档进入 Git；原始聊天、身份映射、正式数据、索引、adapter、
checkpoint、生成文本和备份只写入受控 `platform-data/llm-private/wechat-persona/`，并由 MLflow
Artifact API 或受控删除流程管理。

## 3. 端到端状态机

```text
DISCOVERED
  -> CONSENT_VERIFIED
  -> IMPORTED
  -> NORMALIZED_REDACTED
  -> SESSIONIZED_SPLIT_FROZEN
  -> REVIEWED
  -> FORMAL_DATASET_READY
  -> RAG_INDEX_VALIDATED
  -> LORA_TRIAL_VALIDATED
  -> CANDIDATE_FROZEN
  -> TEST_ONCE_COMPLETED
  -> HUMAN_SAFETY_APPROVED
  -> LOCAL_PROTOTYPE_ENABLED
```

任一阶段可转 `BLOCKED` 或 `WITHDRAWN`，但不能跳跃。`BLOCKED` 的原因必须是结构化代码，例如
`consent_missing`、`unknown_speaker`、`cross_split_session`、`pii_scan_failed`、
`manual_review_pending`、`rag_delete_failed`、`blind_preference_below_gate` 或
`artifact_roundtrip_failed`。撤回会反向使下游数据、索引、Run、checkpoint 和 adapter 失效。

项目 5 是所有真实数据的前置；项目 6 的索引和项目 7 的训练可以并行开发，但真实运行必须在
项目 5 的 `FORMAL_DATASET_READY` 后。项目 8 依赖项目 7 的冻结协议；项目 9 依赖已通过质量、
隐私、安全和人工审核的候选，不接受实验性 adapter。

## 4. 统一身份和证据

每个阶段都记录以下不可变身份：

```text
project + task + run.role + governance_status
dataset_id + source_sha256 + manifest_sha256 + split_sha256
consent_scope + preprocessing_version + schema_version
model_id + model_revision + tokenizer_revision
system_prompt_version + chat_template_version + generation_digest
config_digest + code_revision + environment_digest + seed
execution_identity + release_id + readiness_digest
ray_submission_id + ray_job_id + mlflow_run_id + attempt_id
```

训练和持久评测只通过固定 Ray Driver 创建父 MLflow Run。Driver 发布配置、清单、报告、adapter、
checkpoint 和恢复元数据；客户端只能使用 Tracking/Artifact API。每个物料有 SHA-256，并在新进程
下载、校验和加载后才允许 `artifact.roundtrip_verified=true`。

质量证据必须区分 `training`、`validation` 和 `final_test`；候选选择只能读训练/验证。冻结候选
后，绑定 `test_evaluation_id` 的一次性声明才可访问 test；任何 prompt、split、模型、阈值或协议
变更都会作废旧 test 证据。

## 5. 数据流和存储分层

```text
受控原始导出 (raw, read-only)
  -> source manifest + importer report
  -> normalized messages (role/time/type)
  -> redacted messages + privacy report
  -> sessions + group split manifest
  -> review candidates (review-only)
  -> approved SFT datasets / memory cards / event cards
       |                    |                    |
       v                    v                    v
   Ray LoRA train       BM25/vector index     screenplay generator
       |                    |                    |
       +------------ common frozen evaluation -+
                            |
                    local chat / prototype
```

建议目录：

```text
platform-data/llm-private/wechat-persona/<dataset_id>/
├── consent/ raw/ normalized/ redacted/ sessions/
├── manifests/ reports/ review_exports/ datasets/
├── memories/ indexes/ event_cards/
├── runs/ checkpoints/ adapters/ backups/
└── deletion-ledger/
```

目录名只使用不可识别的 dataset ID；报告不回显原始正文、真实姓名或 canary 值。备份继承同一
保留期限和删除账本。

## 6. 评测矩阵和共同门禁

项目 6–8 必须在同一冻结输入、prompt、generation、seed、长度限制和硬件口径下比较：

| variant | Base | Prompt | RAG | LoRA | 主要问题 |
|---|---:|---:|---:|---:|---|
| `base` | ✓ | 基础 | 否 | 否 | 基础下限 |
| `prompt-only` | ✓ | 冻结优化 | 否 | 否 | 提示词收益 |
| `rag` | ✓ | 冻结优化 | BM25/embedding | 否 | 事实可检索性 |
| `lora` | ✓ | 冻结优化 | 否 | ✓ | 风格收益 |
| `rag+lora` | ✓ | 冻结优化 | 同上 | ✓ | 组合收益 |

主指标由项目配置显式声明。角色开放式质量默认使用盲测 `lora_vs_prompt_only_win_rate`；RAG
使用 `Recall@K`、MRR、证据支持率和无证据不确定率；扩容沿用角色主指标并报告延迟/显存/吞吐。
硬门包括 PII/canary 泄漏为 0、不安全行为为 0、空输出/崩溃不超过冻结阈值、删除后不可检索、
无跨 split session、Artifact round-trip 通过。辅助分数不能替代人工偏好、隐私或安全门。

## 7. 依赖关系、资源和停止条件

```text
项目5 数据/授权 ──┬─> 项目6 RAG ──┐
                  ├─> 项目7 LoRA ──┼─> 项目9 原型
项目2–4 Ray契约 ──┘                │
                                   └─> 项目8 容量/QLoRA
```

默认先用 0.8B BF16 单 GPU；1.7B 复用相同数据和协议；只有验证出容量瓶颈才尝试 4B QLoRA。每个
阶段配置显式声明 CPU/GPU/内存/placement、预算和停止条件。出现授权缺失、数据泄漏、质量硬门
失败、量化兼容性不明、测试集提前访问或撤回无法闭环时，停止后续项目并保留诊断证据。

