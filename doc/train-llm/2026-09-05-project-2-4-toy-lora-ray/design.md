# 项目 2–4 设计：Toy LoRA、可复现实验与 Ray Job

## 1. 范围、非目标与设计原则

### 1.1 范围

本设计覆盖同一训练入口从本地最小 SFT/LoRA 到 MLflow 公平评估，再到单 GPU Ray Job
checkpoint 恢复的完整契约。训练数据为可公开分享的合成或公开数据；默认角色是虚构的、
“温柔但简短的咖啡店店员”，不复制真实伴侣的语言。

### 1.2 非目标

- 不读取、转换或训练真实微信聊天；真实数据仍由 consent ledger、人工审核和非空 SFT 导出门禁控制。
- 不做全参数微调、QLoRA、量化库兼容性学习或模型扩容；4B/QLoRA 属于后续独立项目。
- 不用测试集进行选参、早停、提示词迭代或人工反馈循环。
- 不把模型注册为生产模型、不更新 alias、不做公网服务或自动发消息。
- 不让 Ray Job 复制训练逻辑；Ray 只负责提交、资源、生命周期和恢复编排。

### 1.3 设计原则

1. **一个变量一阶段**：项目 2 先验证 LoRA 生命周期，项目 3 再验证比较协议，项目 4 最后验证调度恢复。
2. **assistant-only supervision**：system/user 仅作条件输入，目标 assistant token 才参与 loss。
3. **身份先于指标**：数据、split、预处理、模型 revision、配置、代码和环境必须有不可变摘要。
4. **验证集选参，测试集一次**：测试集在候选配置冻结后只评估一次，任何违规都会使测试结果作废。
5. **API-only artifact access**：MLflow Tracking/Artifact API 是唯一服务接口；客户端不读 `mlflow.db` 或 MinIO 挂载目录。
6. **发布与训练分离**：训练只产出实验工件；任何生产 alias 变更都需要独立的人工 review/promotion。
7. **失败可恢复但不可覆盖**：checkpoint 先写入唯一 attempt 前缀，校验成功后才更新 manifest 指针。

## 2. 阶段关系与状态机

```text
PLANNED
  │ --check-config / schema / data generator check
  ▼
CONTRACT_VALIDATED
  │ project 2: 2-sample/2-step check
  ▼
SMOKE_PASSED ───────────────┐
  │ 10 steps                 │ 失败：FAILED_DIAGNOSTIC
  ▼                          │
BASELINE_PASSED              │
  │ freeze data/eval protocol│
  ▼                          │
EVAL_PROTOCOL_FROZEN         │
  │ base/prompt/LoRA + one test
  ▼                          │
PROJECT_3_ACCEPTED           │
  │ submit same train fn to Ray
  ▼                          │
RAY_SMOKE_RECOVERED ──► PROJECT_4_ACCEPTED
```

状态只能向前推进；失败重试使用新的 `run_id`/`attempt_id`，并通过 `retry_of` 关联旧状态。
已有成功 adapter、checkpoint 或测试报告不可被覆盖。

## 3. 项目 2：合成数据 Toy LoRA

### 3.1 目标和产物

目标是用最小规模数据学习 SFT、LoRA 和 checkpoint 生命周期，而不是追求泛化最优。

主要产物：

- 合成数据生成器、生成参数和数据 manifest；
- `toy-lora-smoke.yaml`（10 steps）与 `toy-lora-baseline.yaml`（1 epoch）；
- tokenizer/chat template 处理结果和 assistant-only labels；
- 只含 adapter 参数的 checkpoint、训练曲线和 checkpoint metadata；
- 新进程加载 base+adapter 的 round-trip 证据；
- 固定风格测试中 base 与 base+adapter 的差异摘要。

### 3.2 合成数据契约

生成约 300–500 条 JSONL 样本；每条样本满足 [`sample.schema.json`](schemas/sample.schema.json)：

```json
{
  "sample_id": "toy-s000123",
  "scenario_id": "coffee_order_07",
  "messages": [
    {"role": "system", "content": "你是温柔但简短的咖啡店店员。"},
    {"role": "user", "content": "今天想喝点不苦的。"},
    {"role": "assistant", "content": "可以试试燕麦拿铁，口感温和。"}
  ],
  "metadata": {
    "style_label": "warm_brief",
    "generator_version": "toy-v1",
    "seed": 42
  }
}
```

必需约束：

- `sample_id` 全局唯一、稳定排序；`scenario_id` 表示可分组切分的场景/模板；
- 角色只允许 `system`、`user`、`assistant`，且每条样本最后一条消息是目标 assistant；
- assistant 回复非空，长度有上限，不能包含真实姓名、地址、电话、账号、秘密或可识别伴侣对白；
- 生成器输出 `dataset_manifest.json`，记录 generator version、seed、样本数、文件 SHA-256 和 schema version；
- 数据放在 `platform-data/llm-baselines/toy-lora/`，不进入 Git；
- 生成器不能默认联网调用外部模型。若未来使用公开数据，必须记录 `source_uri`、许可证、原始文件 digest 和清洗版本。

### 3.3 Chat template 与 loss mask

使用 Qwen tokenizer 的 `apply_chat_template`，由 tokenizer 负责控制特殊 token、generation prompt
和 assistant 边界；禁止手写 Qwen 特殊 token。建议关闭 thinking，保持项目 0+1 的 `enable_thinking=false`。

实现应获得 assistant token span，再建立与 `input_ids` 等长的 labels：

```text
system tokens  ───────────────► -100
user/history tokens ──────────► -100
assistant target tokens ───────► input_ids[token]
padding/special non-target     ─► -100
```

必须测试：

- system/user 的所有 labels 都是 `-100`；
- assistant 至少有一个有效 label；
- label 与 input 对齐，padding 不参与 loss；
- 多轮消息只监督目标角色的 assistant 回复，不把 user 回复当作标签；
- 截断不会留下半个目标或把被截断的 user token 误标为 assistant。

若 tokenizer/TRL 的 collator 无法可靠提供 assistant mask，应实现项目内的显式 mask builder，
并在训练前以一个人工可读的 token/span fixture 阻断验证；不能静默退化为全序列 loss。

### 3.4 LoRA 与训练配置

所有参数来自 YAML；不得在脚本中硬编码实验值。起始配置仅为学习基线：

```yaml
model:
  id: Qwen/Qwen3.5-0.8B
  local_path: /data/ai/chenzhangyue/code/model/Qwen3.5-0.8B
  revision_policy: resolve_remote_commit_before_run
  dtype: bfloat16
  device: cuda:0
  max_sequence_length: 512
  enable_thinking: false

data:
  uri: platform-data/llm-baselines/toy-lora/dataset.jsonl
  dataset_id: toy-lora-synthetic-v1
  preprocessing_version: toy-sft-v1
  train_examples: 300
  validation_examples: 50
  test_examples: 50
  split_strategy: scenario_group
  split_seed: 42
  packing: false
  assistant_only_loss: true

lora:
  rank: 8
  alpha: 16
  dropout: 0.05
  target_modules: [q_proj, v_proj]
  bias: none

training:
  epochs: 1
  max_steps: null
  per_device_train_batch_size: 4
  gradient_accumulation_steps: 4
  learning_rate: 0.0002
  warmup_ratio: 0.03
  scheduler: cosine
  max_grad_norm: 1.0
  optimizer: adamw_torch
  seed: 42
  eval_steps: 25
  save_steps: 25

resources:
  num_gpus: 1
  cpus: 4
  memory_gb: 8

objective_metric: validation_loss
objective_mode: min
```

`toy-lora-smoke.yaml` 只覆盖 `max_steps: 10`、小 batch/少量样本和明确的 `run_kind: smoke`；
`toy-lora-baseline.yaml` 使用完整 1 epoch。两者必须保留各自 canonical config digest。
目标模块不存在时，启动时打印实际可选模块并失败；不得静默跳过 LoRA 注入。

### 3.5 Checkpoint 生命周期

每个 checkpoint 放在唯一的 `run_id/attempt_id/step-{N}/` 前缀，至少包含：

- PEFT adapter 权重和 adapter config；
- tokenizer/config 参考（不重复上传 base model）；
- optimizer/scheduler/RNG state（若支持恢复）；
- `checkpoint_metadata.json`：step、epoch、base model revision、config/data/code digest、seed、created_at；
- 每个文件 SHA-256 和总 manifest SHA-256。

训练完成后，只有在文件写全、哈希通过、metadata 状态为 `complete` 时，才将 checkpoint 指针
写入 Run manifest。失败 checkpoint 标记 `incomplete`，不能成为恢复默认点或覆盖已完成 adapter。

## 4. 项目 3：可复现实验与评估

### 4.1 冻结数据与切分

扩展到约 1,000 条样本，优先按 `scenario_id`/对话组切分：同一场景、模板族或近重复样本不能
跨 train/validation/test。若使用时间字段，则采用确定性 chronological split；不对既有评估人群
静默重排。split manifest 至少包含：

```text
dataset_id
source_uri / source_sha256
preprocessing_version
split_strategy
split_seed
train/validation/test sample_id 或 scenario_id 清单摘要
split_manifest_sha256
```

建议的起始比例为 80/10/10，但最终数量由去重和完整 group 约束决定。任何样本无法唯一归组、
schema 不合法、存在近重复跨 split 或 manifest 重算不一致时，状态为 blocked。

### 4.2 三组对照与固定 Prompt

项目 3 的最小比较矩阵：

| 组别 | Base 权重 | 风格 Prompt | LoRA adapter | 用途 |
|---|---:|---:|---:|---|
| Base | ✓ | 否 | 否 | 基础下限 |
| Prompt-only | ✓ | ✓ | 否 | 测量提示词收益 |
| LoRA | ✓ | ✓（相同） | ✓ | 测量 adapter 增益 |

三组必须使用相同 tokenizer、generation 参数、输入问题、随机种子和输出长度上限。Prompt-only
与 LoRA 之间唯一主要变量是 adapter；不要为 LoRA 单独修改 system prompt。可选的 RAG 组属于后续项目，
不在项目 3 的最小验收范围内。

### 4.3 评估协议

配置显式声明：

```yaml
objective_metric: validation_loss
objective_mode: min
test_evaluation_policy: once_after_candidate_freeze
```

自动指标包括：validation loss、生成长度、格式遵循率、重复率、固定风格关键词/结构符合率。
风格测试由版本化 fixture 和规则组成，例如“简短”“温和”“角色一致”“不编造未提供事实”。
规则测试仅作辅助门禁，不通过临时改测试集、改阈值或改采样方式来让某个 Run 通过。

选择流程：

1. 只用 train/validation 诊断训练是否收敛、比较参数和选择候选 checkpoint；
2. 将候选配置、checkpoint、prompt、metric definition 和 split manifest 标记为 frozen；
3. 由独立命令对 frozen candidate 执行 test 一次，写入 `test_evaluation_id`；
4. 若候选或协议改变，原 test 结果作废并生成新的冻结版本；
5. 不把 Ray 恢复结果、smoke 结果或本地未追踪结果当成最终 test evidence。

### 4.4 MLflow 记录与 Artifact round-trip

每个 Base/Prompt-only/LoRA Run 至少记录：

- `dataset_id`、`source_uri`、source/manifest SHA-256、`split_manifest_sha256`；
- `preprocessing_version`、schema version、样本计数和去重报告；
- model ID 与 immutable revision、tokenizer revision、dtype/device；
- canonical config digest、git commit、环境 snapshot、seed、资源声明；
- 完整 LoRA 与训练超参数、generation 参数、objective metric/mode；
- train/validation metrics、评估协议版本和候选状态；
- adapter、checkpoint metadata、config、split manifest、metrics/report 的 Artifact 路径与 SHA-256。

Artifact round-trip 必须由独立进程完成：

1. 用 MLflow Tracking API 按 Run ID 找到 Run；
2. 用 MLflow Artifact API 下载 adapter、manifest 和评估配置到临时目录；
3. 对每个文件重新计算 SHA-256，与 Run 中记录比较；
4. 加载本地 base revision + adapter，使用冻结 prompt/generation 配置运行评估；
5. 比较指标、样本计数、评估协议和 `test_evaluation_id`；不一致则 `roundtrip_status=failed`。

客户端不得读取服务端 `mlflow.db`、MinIO bucket 路径或长期对象存储凭据。

## 5. 项目 4：Ray Job 单 GPU 调度

### 5.1 调度边界

Ray Job 只包装 `scripts/train_lora.py` 暴露的同一 `train(config, runtime)` 函数；本地和 Ray
执行的 canonical config、seed、数据 digest、训练函数版本和 adapter 输出格式必须一致。

固定资源声明：

```yaml
resources:
  num_gpus: 1
  cpus: 4
  memory_gb: 8
placement:
  accelerator_type: null
  worker_count: 1
```

单 GPU 阶段不启动 DDP 或多 Worker 数据并行。若平台实际资源不满足声明，Job 在训练前失败，
不偷偷降级到 CPU 或改变 batch/precision。

### 5.2 Driver/Worker 与 MLflow 边界

推荐流程：

```text
Ray Job Driver
  ├─ 校验 config/data/model/environment
  ├─ 创建父 MLflow Run（唯一 owner）
  ├─ 写入 job metadata 与资源声明
  └─ 调用同一 train() ──► Worker/训练进程
                           ├─ 计算 forward/backward
                           ├─ 汇报 step metrics（经 Driver 或受控 callback）
                           ├─ 写唯一 checkpoint 前缀
                           └─ 返回 checkpoint 状态
  └─ 校验最终 checkpoint、上传 artifacts、结束父 Run
```

Worker 不创建/结束同一个父 Run，不重复上传共享 manifest，不发布模型 alias。若必须使用
nested Run，必须在配置和文档中明确 owner、命名和聚合规则；项目 4 默认不使用 nested Run。

### 5.3 Job metadata 与 attempt

每次提交生成一个不可变 metadata 文件，符合 [`job-metadata.schema.json`](schemas/job-metadata.schema.json)，
至少包含：

```text
ray_job_id
mlflow_run_id
project / run_kind
config_digest
dataset_manifest_digest
code_revision
environment_digest
attempt_id
parent_attempt_id（恢复时）
checkpoint_uri
checkpoint_digest
requested_resources
status / failure_reason
created_at / updated_at
```

`attempt_id` 每次中断后递增或重新生成；`run_id` 默认也新建，并通过 `retry_of`/`resumed_from`
关联旧 Run。只有校验完整的 checkpoint 才能写入 `checkpoint_uri`，且写入采用临时 manifest →
原子完成标记的方式。

### 5.4 可控中断与恢复演练

项目 4 只要求 smoke 级恢复：

1. 使用 `ray-job-smoke.yaml`，在第 N 个 step（建议 N=5）完成 checkpoint；
2. 由可控 flag 或 Driver 取消 Job，产生 `status=interrupted`；
3. 通过 Ray Job API、MLflow Tracking API 和 Artifact API 检查 Job/Run/checkpoint 状态；
4. 新建 attempt，读取最近一个完整 checkpoint，继续到目标 step 或安全重跑；
5. 比较恢复前后的 config/data/model identity、loss history 和 adapter manifest；
6. 确认旧 Run/adapter 未被覆盖，失败 attempt 不会成为最终候选；
7. 记录恢复耗时、恢复起点、丢失的最多 step 数和最终状态。

普通失败使用 Ray 的 `max_failures` 和最近完整 checkpoint；`--force` 不是常规重试机制。
如果 checkpoint 缺失、哈希不匹配、身份不一致或 adapter 目录部分写入，必须从干净状态创建
新的 Run 并标记原 attempt 为不可恢复。

## 6. 幂等、失败与发布规则

幂等键建议为：

```text
sha256(dataset_manifest_digest + split_digest + model_revision
       + canonical_config_digest + code_revision + seed + run_kind)
```

它用于检测“同一意图”的重复提交，但不复用旧 Run ID；重试始终生成新 Run，记录 `retry_of`。
输出路径包含 `run_id`/`attempt_id`，不使用固定 `latest/` 写入。成功 adapter 只读发布，未通过
验收的失败 Run 不得改变候选指针。

## 7. 资源、成本与停止条件

| 阶段 | 数据/步数 | 预算目标 | 停止条件 |
|---|---:|---:|---|
| 项目 2 预检查 | 2 samples / 2 steps | 分钟级 | mask、反向传播或保存任一失败即停 |
| 项目 2 smoke | 10 steps | 不含首次下载 ≤10 分钟 | adapter round-trip 或 checkpoint metadata 失败即停 |
| 项目 2 baseline | 1 epoch | ≤30 分钟 | loss 无合理变化、资源超限或失败工件即停 |
| 项目 3 比较 | 3 组 | 小时级 | split 泄漏、指标不兼容、round-trip 失败即停 |
| 项目 4 Ray smoke | 1 GPU / 中断一次 | 小时级 | Driver/Worker owner 不清或恢复覆盖工件即停 |

如 validation loss 下降但固定风格质量变差，以书面评审定义的约束为准；不通过增加 epoch、
反复查看 test 或改变测试集来掩盖问题。项目 4 的 Ray 成功只证明可调度/可恢复，不证明模型质量。

## 8. 交付物与后续边界

项目 4 通过后，至少有：

- 三份可读配置及 canonical digest；
- 合成数据 manifest、固定 split manifest 和评估协议版本；
- Base/Prompt-only/LoRA 的 MLflow Run ID 与可下载 artifacts；
- 一个成功 smoke adapter、一个 1 epoch baseline（若通过）；
- 一次 Artifact round-trip 和一次 Ray 中断恢复报告；
- 已知失败模式、资源实际使用和未解决风险记录。

这些成果才是进入真实微信数据工程、RAG 或真实角色 LoRA 之前的前置条件；它们不授权处理真实聊天，也不代表最终产品质量。
