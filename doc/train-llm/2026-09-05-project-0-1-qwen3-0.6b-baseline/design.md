# 项目 0+1 设计：环境/GPU 基线与 Qwen3-0.6B 基础推理

## 1. 范围与原则

### 1.1 范围

本设计覆盖一次 bounded inference baseline：环境检查、GPU smoke、数据 manifest 校验、固定 fixture 构造、Qwen3-0.6B 推理、性能统计、MLflow 记录和验收报告。

### 1.2 不在范围内

- LoRA、QLoRA、SFT、梯度计算或 checkpoint；
- 训练数据人工审核流程的实现；
- RAG、embedding、reranker 和动画脚本；
- 在线服务、自动发消息、生产模型 alias；
- 修改 `/Users/weidian/project/luna-data/data-deal` 下任何文件；
- 把聊天正文、模型输出或原始身份映射提交到 Git。

### 1.3 设计原则

1. **数据身份优先**：每次运行先验证 dataset、source、config、split 和 pipeline digest。
2. **输入与标签分离**：模型只看到历史上下文，不看到候选记录中的目标回复。
3. **模板由 tokenizer 负责**：使用模型自带 chat template，不拼接 Qwen 特殊 token。
4. **运行指标与质量指标分离**：本阶段只建立运行性能基线，不宣称角色质量提升。
5. **服务 API 隔离**：MLflow Tracking/Artifact 只走 API，客户端不读 `mlflow.db` 或 MinIO 挂载目录。
6. **不因基线而扩大权限**：只读数据、保留现有 GPU 进程，不为清显存自动杀进程。

## 2. 端到端架构

```text
data.yaml + 外部 dataset root
          │
          ▼
manifest preflight ──► split/session 校验 ──► deterministic fixture builder
                                                    │
                                                    ▼
environment/GPU preflight ──► model/tokenizer loader
                                                    │
                                                    ▼
                                   2 条通用 prompt smoke
                                                    │
                                                    ▼
                               20 条 validation fixture 推理
                                                    │
                                                    ▼
                       latency/throughput/memory collector
                              │                         │
                              ▼                         ▼
                    local report + manifest      MLflow Run/Artifacts
```

实现时由 `scripts/infer.py` 作为唯一正式入口，内部包拆成以下职责：

| 模块 | 职责 | 不负责 |
| --- | --- | --- |
| `data.py` | 读取候选、验证 dataset/split、构造 fixture | 加载模型、记录 MLflow |
| `models/causal_lm.py` | tokenizer、chat template、模型加载和生成 | 数据切分、服务健康检查 |
| `runtime.py` | GPU/BF16/版本/进程只读快照 | 终止进程或安装系统驱动 |
| `tracking.py` | Run Manifest、MLflow 参数/指标/Artifact | 直接访问 MinIO 或数据库 |
| `infer.py` | 编排 preflight、smoke、20 条推理、报告 | 训练、注册模型 |

## 3. 数据契约

### 3.1 固定版本

默认配置锁定 `wechat_aa807aaad90dc4463964`。运行时必须读取并核对：

- `manifests/source_manifest.json` 的 `dataset_id`、`source_sha256`、`config_sha256`、`pipeline_version`；
- `manifests/split_manifest.json` 的策略、session 计数和候选计数；
- `reports/privacy_report.json` 的二次扫描结果；
- `reports/leakage_report.json` 的候选级检查结果。

旧版本 `wechat_c92b3b462d0f86db7369` 和 `wechat_337eba55797d9ed4959a` 仅作为历史产物，不参与默认运行。

### 3.2 fixture 生成

从 `work/05_candidates/candidates.jsonl` 流式读取记录，执行以下过滤和排序：

1. `session_id` 必须属于 split manifest 的 `validation` 集合；
2. `metadata.target_speaker == "target"`；
3. `messages` 至少包含 system、user/context 和一个 assistant 目标；
4. `messages[-1].role == "assistant"`，且该消息不进入模型输入；
5. 只保留已脱敏内容，不读取 `text_original_ref` 或任何受限原文引用；
6. 按 `sample_id` 字典序排序，取前 20 条；
7. 生成 `fixture_id = sha256(dataset_id + sample_id + prompt_policy_version)` 的稳定身份；
8. 记录 `sample_id`、`session_id`、split、输入消息 ID 哈希和目标长度统计，不在 Git 保存正文。

该策略不使用 machine score 进行调参，也不把 `review_status=uncertain` 改写成 approved。它只允许在已授权、已脱敏数据上进行运行性能诊断。

### 3.3 输入边界

模型输入为：

```json
[
  {"role": "system", "content": "仅基于已提供的脱敏对话生成回复；不猜测或复述私人事实。"},
  {"role": "user", "content": "self: ...\ntarget: ..."}
]
```

候选中的最后一条 assistant 目标回复只用于离线统计（例如参考长度），不得拼回 prompt。生成输出默认只写受控本地目录，MLflow 仅记录输出哈希和长度摘要。

## 4. 模型与生成契约

### 4.1 模型

```yaml
model_id: Qwen/Qwen3-0.6B
revision_policy: resolve_remote_commit_before_run
dtype: bfloat16
device: cuda:0
max_input_tokens: 512
enable_thinking: false
```

模型 revision 不能只记录 `main`。首次加载时解析为具体 commit，并把 revision 写入 Run Manifest。模型缓存可以复用，但缓存路径不作为模型身份。

### 4.2 chat template

实现必须调用 tokenizer 的 `apply_chat_template(..., tokenize=True, add_generation_prompt=True, return_tensors="pt")`（具体参数以实际 Transformers 版本签名为准），禁止手写 `<|im_start|>` 等特殊 token。生成后只解码新 token 区间，避免把 prompt 回显误计入输出。

### 4.3 thinking 关闭

Qwen3 的 thinking 开关以模型支持的官方 chat-template 参数实现；配置中统一使用 `enable_thinking: false`，并在一次 smoke 的记录中保存最终模板参数。若当前 Transformers 版本不支持该参数，入口必须失败并打印版本/签名信息，不静默忽略。

### 4.4 生成参数

默认值来自路线图，作为第一轮基线而非最优参数：

```yaml
generation:
  do_sample: true
  temperature: 0.7
  top_p: 0.9
  max_new_tokens: 128
  repetition_penalty: 1.0
  seed: 42
```

为避免 CUDA 非确定性被误解为模型变化，运行清单同时记录 `torch.backends` 相关设置和实际硬件。性能基线不要求 bitwise 一致，但 fixture 顺序、配置哈希和输入哈希必须一致。

## 5. 环境与 GPU 基线

### 5.1 版本记录

记录 Python、OS、GPU 型号/数量、驱动可见版本、`torch.__version__`、`torch.version.cuda`、Transformers、Datasets、PEFT、TRL、Accelerate、Ray、MLflow 和 CUDA 可用性。系统显示的 CUDA 版本不能替代 PyTorch 编译 runtime。

### 5.2 能力检查

按顺序执行：

1. `torch.cuda.is_available()`；
2. 枚举 GPU 名称、总显存和当前显存；
3. 在目标 GPU 上创建 BF16 张量并执行矩阵乘；
4. 读取当前进程之外的 GPU 进程摘要，仅用于报告；
5. 加载 tokenizer 和模型；
6. 用 2 条不含真实聊天的通用 prompt 完成最小生成。

任一步失败都不进入 20 条 fixture。显存不足时报告可用显存和已有进程，不执行 kill、reset 或清理其他用户进程。

### 5.3 资源声明

本阶段默认：1 GPU、4 CPU、8 GiB 系统内存、单进程本地执行。资源数必须在配置和 Run Manifest 中出现。后续 Ray Job 包装沿用相同资源声明，并由 driver 统一写 MLflow。

## 6. 指标与报告

### 6.1 每条记录

每条 fixture 记录：fixture/sample ID、输入 token 数、输出 token 数、首 token 延迟、总延迟、tokens/s、峰值显存、解码状态、异常类别和输出哈希。输出正文不进入 Git。

### 6.2 聚合指标

至少报告：

- `successful_count`、`decode_error_count`；
- 首 token 延迟 p50/p95；
- 总延迟 p50/p95；
- 输出 tokens/s 的均值、p50、p95；
- 全批次峰值显存；
- prompt/output token 长度均值和 p95；
- 2 条通用 smoke 与 20 条 fixture 是否全部通过。

本阶段不将 perplexity、人工偏好胜率或“像本人”作为主指标；这些属于后续 Prompt/LoRA 评估。

### 6.3 MLflow

Experiment 默认 `llm-lora-playground`，Tracking URI 从 `MLFLOW_TRACKING_URI` 读取。若未设置且配置也未提供，入口只允许 `--check-config`，不得创建隐式本地 Experiment。

Run tags 至少包含：`project`、`task=inference_baseline`、`execution_mode`、`dataset_id`、`pipeline_version`、`model_id`、`model_revision`、`code_revision`、`seed`、`inference_baseline_only=true`。

Artifacts：

- `manifests/run_manifest.json`；
- `manifests/fixture_manifest.json`；
- `reports/environment.json`；
- `reports/metrics.json`；
- `reports/inference_records.jsonl`（只含 ID、哈希和指标）；
- 可选的本地受控输出目录引用，不默认上传完整输出正文。

Artifact 回读必须通过 MLflow Artifact API 完成，并核对 SHA-256；客户端不得读取 MLflow 数据库或 MinIO 文件系统。

## 7. 失败、恢复与幂等

- 幂等键：`sha256(dataset_id + split_digest + fixture_digest + model_revision + config_digest + code_revision)`；
- 同一幂等键已有成功且 Artifact 回读通过的 Run 时，默认报告并复用，不重复消耗算力；
- 失败 Run 不覆盖旧 Run；重试使用新 Run ID，并用 `retry_of` tag 关联；
- fixture 构造中断时，保留已写入的临时分片，成功收尾后再原子发布清单；
- 模型加载失败、模板参数不兼容、数据 digest 不一致或 MLflow 不健康时，状态为 blocked，不产生“完成”报告；
- 只有完整 20 条记录和报告写入成功后才标记 `status=completed`。

## 8. 隐私与安全

用户已确认数据授权且数据已脱敏；本阶段仍把授权引用作为运行前置条件，因为现有 `source_manifest.json` 的 `authorization_status` 仍是 `not_verified_in_pipeline`。正式实现应从受控的 consent ledger 读取 scope 引用，不把授权正文复制到仓库。

运行日志不得打印聊天正文、目标回复、真实发送者标识、原始路径中的姓名或 token。错误日志使用 sample ID 和哈希。生成文本默认保存在 `platform-data/llm-private/` 下的受控目录，按授权保留期限清理。

## 9. 与后续 LoRA 的接口

本阶段冻结以下可复用身份：模型 revision、环境快照、数据/source/split digest、fixture 选择规则、prompt policy version、MLflow tag 命名和指标字段。项目 2 接入 Toy LoRA 时复用这些契约，只新增训练配置和 adapter artifact，不修改 baseline fixture 的定义。
