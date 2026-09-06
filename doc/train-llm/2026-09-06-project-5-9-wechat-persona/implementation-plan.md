# 项目 5–9 实施计划

## 1. 计划原则

这是实现计划，不是训练授权。代码、契约和测试可以先完成；任何真实数据训练、持久评测、checkpoint/
adapter 生成或 test 访问都必须在对应的 readiness、授权和用户确认后，经固定 Ray Driver 执行。
每个任务都要产出可读报告和机器可验证的状态，不以“脚本能跑”作为完成标准。

## 2. 项目骨架（Task 0）

创建 `train-model/wechat-persona/`，至少包含 `README.md`、`conda.yaml`、`galatea.project.yaml`、
`configs/`、`src/wechat_persona/`、`scripts/`、`tests/`。`galatea.project.yaml` 声明：

```yaml
spec:
  task: causal-language-model-sft-lora
  executionBackend: ray
  objective: {metric: lora_vs_prompt_only_win_rate, direction: max}
  capabilities: {pauseResume: true}
```

训练、正式评估和恢复入口固定为 `scripts/submit_train.py`、`scripts/evaluate.py` 和 Ray job Driver；
`import_chat.py`、`build_dataset.py`、`build_memory_index.py` 的默认模式只能 check/plan，不得写正式
训练工件或 MLflow 质量 Run。项目配置必须显式包含 `dataset_id`、source/manifest/split digest、
consent scope、role、model revision、protocol、seed、资源和 `run.promotable`。

## 3. 任务分解

### Task 5：数据工程

1. `consent.py`：读取受控 ledger、核验用途/范围/期限/撤回状态，输出 consent digest；无效即失败。
2. `importers/`：实现 TXT/CSV/JSON/HTML 流式适配器、格式检测、源索引和错误报告。
3. `normalize.py`、`redact.py`：时间/Unicode/角色/媒体标准化，稳定脱敏标签和二次扫描。
4. `sessionize.py`：连续消息合并、session 边界、未来消息排除和 session manifest。
5. `review.py`、`scripts/review_app.py`：只显示脱敏内容，追加式审核事件，支持 keep/redact_keep/reject/uncertain。
6. `datasets.py`、`build_dataset.py`：按 frozen session split 导出三份正式 JSONL，复跑所有门禁。
7. `deletion.py`：生成 dry-run 影响图，再执行对象删除/失效和 receipt；备份也必须覆盖。

对应测试：`test_consent.py`、`test_importers.py`、`test_redaction.py`、`test_session_split.py`、
`test_review_export.py`、`test_deletion_lineage.py`、`test_no_raw_text_artifacts.py`。

### Task 6：RAG

1. `memories.py`：从人工批准候选提取 memory card；高敏默认排除，保留来源和 revision。
2. `rag.py`：owner-scoped BM25、可选本地 embedding、可选 reranker，索引 manifest 和版本化 tokenizer。
3. `scripts/build_memory_index.py`：只接受 `FORMAL_DATASET_READY` 或已确认的 memory-only 数据快照。
4. `evaluation.py`：冻结 query-memory labels，计算 Recall@K/MRR、支持率、无证据不确定率和删除残留。
5. 删除/重建：tombstone 或 copy-on-write 后 compact，Artifact API round-trip 通过才标记完成。

对应测试：`test_memory_schema.py`、`test_owner_isolation.py`、`test_rag_metrics.py`、`test_index_delete.py`、
`test_memory_prompt_policy.py`。

### Task 7：真实角色 LoRA

1. 将项目 2–4 的 mask、LoRA、checkpoint、tracking 和 Ray Driver 适配为同一 package 的共享实现；
   仅从配置注入真实 dataset/consent/role/预算。
2. 增加 2-sample/2-step preflight、10-step smoke、1 epoch baseline 配置及失败恢复/幂等性测试。
3. 实现五组变体的统一生成器和 validation-only candidate selector；冻结 prompt、index、generation。
4. `blind_review.py` 或受控审核 UI：匿名 A/B、随机位置、平局/不可接受、一致性统计。
5. `safety.py`：PII/canary、未授权事实、冒充、排他依赖、内疚操纵、越界拒答 challenge。
6. `scripts/evaluate.py --freeze-candidate` 生成 candidate freeze；`--test-once` 只接受冻结 ID。

对应测试：`test_training_gate.py`、`test_five_variants.py`、`test_blind_review.py`、`test_safety_gates.py`、
`test_candidate_freeze.py`、`test_test_once.py`。

### Task 8：容量与 QLoRA

1. 新增 1.7B BF16 LoRA 配置，继承项目 7 数据/protocol，禁止改变其它变量。
2. 增加 4B QLoRA 独立 environment/config，先执行 import/forward/backward/save round-trip fixture。
3. 记录 quantization、bitsandbytes、PyTorch/CUDA/driver、GPU 和显存诊断；兼容性失败不进入质量排名。
4. 统一 cost/quality report 和采用/停止决策模板；test-once 仍按候选分别绑定。

对应测试：`test_model_matrix.py`、`test_quantization_preflight.py`、`test_resource_metrics.py`、
`test_capacity_decision.py`。

### Task 9：原型

1. `runtime.py`：加载已接受的 model/adapter/index/protocol identity，拒绝实验性或撤回对象。
2. `scripts/chat_local.py`：安全策略 → 检索 → prompt → 生成 → 输出检查；记忆关闭和日志删除。
3. `screenplay.py`：事件卡抽取、场次编排、对白/旁白改写、时间码和事实 lineage。
4. `scripts/generate_screenplay.py`、本地审核页面：逐段编辑/删除/待确认状态。
5. 增加撤回监听/启动 preflight；上游状态失效时自动 disabled。

对应测试：`test_local_chat_policy.py`、`test_adapter_eligibility.py`、`test_screenplay_lineage.py`、
`test_screenplay_edit_delete.py`、`test_no_voice_or_face_assets.py`。

## 4. 配置和环境交付

配置文件只能引用环境变量中的 Tracking URI、受控 artifact prefix 和 ledger endpoint，不写密码、
MinIO key、HF token 或真实路径。`conda.yaml` 至少锁定 Python、PyTorch/CUDA、Transformers、PEFT、
TRL、Datasets、MLflow、Ray、sentence-transformers（若使用）和 bitsandbytes（仅 QLoRA 环境）。
先在项目环境执行架构加载 smoke，再固定 revision；不能以当前共享环境版本替代声明版本。

## 5. 交付顺序和停止点

```text
Task 0 -> 5.1–5.4 -> 5.5 review -> 5.6 formal dataset
                              ├─> Task 6 RAG
                              └─> Task 7 LoRA -> Task 8 capacity -> Task 9 prototypes
```

每次合并前运行契约测试和只读检查；真实运行前执行项目级 check/plan、Release、Galatea readiness 和
资源预算确认。任何 `blocked`、撤回、兼容性未知、跨 split、test 提前访问或 Artifact hash 不一致都
暂停依赖项目，不通过修改阈值绕过。

