# 项目 2–4 实施计划

> 本文件只规划未来实现。当前不运行训练、不修改 `train-model/llm-lora-playground/` 代码。
>
> 实现时按任务逐步写测试、运行最窄检查，再扩大范围；每个项目的验收门未通过前不进入下一项目。

## Global constraints

- 代码只放在 `train-model/llm-lora-playground/`；模型项目保持 `README.md`、`configs/`、`src/`、
  `scripts/`、`tests/`、`conda.yaml` 结构。
- 数据、adapter、checkpoint、模型缓存、执行 notebook 和 secrets 放在 `platform-data/` 或受控外部路径，
  不进入 Git。
- 默认模型为 `Qwen/Qwen3.5-0.8B`，记录 immutable revision、BF16、`cuda:0`、non-thinking；若架构不兼容则阻断。
- 项目 2–4 使用合成或公开数据；禁止读取当前真实微信数据目录。
- 所有参数、资源、数据身份、split、预处理、seed、objective metric/mode 来自 YAML 或显式环境变量。
- MLflow 通过 Tracking/Artifact API 使用；不读 `mlflow.db` 或服务端 MinIO 文件系统。
- 重试新建 Run/attempt，记录 `retry_of` 或 `resumed_from`，不覆盖已有成功工件。
- 任何“test 被调参使用”“mask 不正确”“artifact 哈希不一致”“恢复身份不一致”都属于阻断。

## 项目 2 任务

### Task 2.1：创建 Toy LoRA 配置和 schema

**Files**

- Create: `train-model/llm-lora-playground/configs/toy-lora-smoke.yaml`
- Create: `train-model/llm-lora-playground/configs/toy-lora-baseline.yaml`
- Create: `doc/train-llm/2026-09-05-project-2-4-toy-lora-ray/schemas/sample.schema.json`
- Create: `train-model/llm-lora-playground/tests/test_toy_config.py`

**Interfaces**

- `load_training_config(path: Path) -> TrainingConfig`
- `validate_training_config(config: TrainingConfig) -> list[str]`
- `canonical_config_digest(config: TrainingConfig) -> str`

**测试先行**

- 非 `bfloat16`、非 `cuda:0`、`num_gpus != 1`、`assistant_only_loss=false`、空 target modules、
  缺失 objective metric/mode 时必须失败。
- smoke 必须 `run_kind=smoke` 且 `max_steps=10`；baseline 必须 `epochs=1` 且不把 `max_steps` 误当最终预算。
- YAML 不能包含 token、密码、MinIO key 或 MLflow secret。

**实现后检查**

```bash
python -m pytest train-model/llm-lora-playground/tests/test_toy_config.py -q
python train-model/llm-lora-playground/scripts/train_lora.py --config configs/toy-lora-smoke.yaml --check-config
```

### Task 2.2：实现合成数据生成与校验

**Files**

- Create: `train-model/llm-lora-playground/scripts/generate_synthetic.py`
- Create: `train-model/llm-lora-playground/src/llm_lora_playground/datasets.py`
- Create: `train-model/llm-lora-playground/tests/test_synthetic_data.py`

**Interfaces**

- `generate_dataset(output_dir: Path, count: int, seed: int, version: str) -> DatasetManifest`
- `validate_sample(sample: Mapping[str, Any]) -> None`
- `load_samples(path: Path) -> Iterator[TrainingSample]`
- `compute_dataset_digest(path: Path) -> str`

**必须覆盖的测试**

- 固定 seed 产生相同 JSONL 和 digest；不同 seed 的 metadata 可区分；
- `sample_id` 唯一，scenario group 可计数，最后消息是非空 assistant；
- 未知 role、空 assistant、真实数据引用字段或重复 ID 被拒绝；
- 生成器不访问 `WECHAT_DATA_ROOT`，不联网，不写出项目源代码目录。

**检查命令**

```bash
python train-model/llm-lora-playground/scripts/generate_synthetic.py \
  --output-dir platform-data/llm-baselines/toy-lora/check \
  --count 8 --seed 42 --check-only
python -m pytest train-model/llm-lora-playground/tests/test_synthetic_data.py -q
```

### Task 2.3：实现 chat template 和 assistant-only loss mask

**Files**

- Create: `train-model/llm-lora-playground/src/llm_lora_playground/sft.py`
- Create: `train-model/llm-lora-playground/tests/test_loss_mask.py`
- Create: `train-model/llm-lora-playground/tests/test_chat_template_sft.py`

**Interfaces**

- `tokenize_conversation(tokenizer, messages, max_length, enable_thinking=False) -> TokenizedSample`
- `build_assistant_only_labels(input_ids, assistant_spans, pad_token_id) -> list[int]`
- `find_assistant_spans(tokenizer, messages, rendered) -> list[tuple[int, int]]`
- `SFTCollator` with deterministic padding and labels

**测试先行**

- spy tokenizer 必须收到 `apply_chat_template(..., add_generation_prompt=False)`；
- system/user/history/padding labels 全为 `-100`，assistant target 有效；
- 多轮输入只监督目标 assistant；截断不会产生越界或错误监督；
- 没有可识别 assistant span 时直接抛出 contract error，不退化为全序列 loss。

**实现后检查**

```bash
python -m pytest train-model/llm-lora-playground/tests/test_loss_mask.py \
  train-model/llm-lora-playground/tests/test_chat_template_sft.py -q
```

### Task 2.4：实现 LoRA 注入、训练和 adapter 保存

**Files**

- Create: `train-model/llm-lora-playground/src/llm_lora_playground/lora.py`
- Create: `train-model/llm-lora-playground/src/llm_lora_playground/checkpoints.py`
- Create: `train-model/llm-lora-playground/scripts/train_lora.py`
- Create: `train-model/llm-lora-playground/tests/test_lora_roundtrip.py`
- Create: `train-model/llm-lora-playground/tests/test_checkpoint_metadata.py`

**Interfaces**

- `build_lora_model(model_config, lora_config) -> PeftModel`
- `train(config, runtime=None, resume_from=None) -> TrainResult`
- `save_checkpoint(state, output_dir, metadata) -> CheckpointManifest`
- `load_adapter(base_model, adapter_dir, expected_identity) -> LoadedAdapter`
- `verify_checkpoint(manifest) -> None`

**实现约束**

- 目标模块名称与实际模型结构不符时打印候选列表并失败；
- 只保存 adapter，不复制完整 base model；
- checkpoint 目录带 run/attempt/step 唯一前缀；写全后再标记 `complete`；
- metadata 含 data/config/model/code/environment/seed、step、optimizer/scheduler/RNG state；
- 训练函数可在本地和 Ray Driver 调用，不能在函数内部假定 notebook 全局变量。

**测试与检查**

```bash
python -m pytest train-model/llm-lora-playground/tests/test_lora_roundtrip.py \
  train-model/llm-lora-playground/tests/test_checkpoint_metadata.py -q
python train-model/llm-lora-playground/scripts/train_lora.py \
  --config configs/toy-lora-smoke.yaml --check-config
```

### Task 2.5：项目 2 smoke 和 baseline（需用户确认后才可运行）

运行前提：Task 2.1–2.4 的测试通过，模型兼容性/GPU preflight 通过，且明确得到启动确认。

```bash
python train-model/llm-lora-playground/scripts/train_lora.py \
  --config configs/toy-lora-smoke.yaml --run
python train-model/llm-lora-playground/scripts/train_lora.py \
  --config configs/toy-lora-baseline.yaml --run
```

每次运行都要检查：10-step ≤10 分钟（不含首次下载）、1 epoch ≤30 分钟、loss 有合理变化、
adapter 可在全新进程加载、固定风格 fixture 相对 base 有可解释差异、失败训练没有覆盖成功 adapter。

## 项目 3 任务

### Task 3.1：冻结 1,000 条数据与 group split

**Files**

- Create: `train-model/llm-lora-playground/src/llm_lora_playground/split.py`
- Create: `train-model/llm-lora-playground/tests/test_split_integrity.py`
- Create: `train-model/llm-lora-playground/configs/reproducible-eval.yaml`

**Interfaces**

- `build_group_split(samples, group_key, ratios, seed) -> SplitManifest`
- `validate_split_manifest(samples, manifest) -> None`
- `split_manifest_digest(manifest) -> str`

**门禁**

- group 不得跨 split；近重复检测不得跨 split；
- manifest 稳定重算；样本计数、dataset/source/preprocess digest 一致；
- 一旦冻结，未经过版本升级不得改动 split 或测试样本。

### Task 3.2：实现 Base/Prompt-only/LoRA 对照

**Files**

- Modify: `train-model/llm-lora-playground/scripts/evaluate.py`
- Create: `train-model/llm_lora_playground/evaluation.py`
- Create: `train-model/llm-lora-playground/tests/test_evaluation_protocol.py`

**Interfaces**

- `evaluate_variant(variant, frozen_protocol, model_ref, adapter_ref=None) -> EvaluationResult`
- `compute_automatic_metrics(records) -> dict[str, float]`
- `run_fixed_style_checks(records, ruleset_version) -> dict[str, Any]`
- `freeze_candidate(candidate, protocol) -> FrozenCandidate`

三组共享相同 tokenizer、prompt、generation、seed、输入和长度限制。只使用 train/validation
选择 checkpoint；`test_evaluation_id` 只能由冻结后的单次命令生成。

### Task 3.3：MLflow tracking 与 Artifact round-trip

**Files**

- Modify/Create: `train-model/llm-lora-playground/src/llm_lora_playground/tracking.py`
- Create: `train-model/llm-lora-playground/scripts/roundtrip_artifact.py`
- Create: `train-model/llm-lora-playground/tests/test_artifact_roundtrip.py`

**Interfaces**

- `start_training_run(manifest) -> RunContext`
- `log_training_metrics(context, metrics) -> None`
- `log_artifact_with_sha256(context, path, artifact_path) -> ArtifactRecord`
- `download_and_verify_artifact(client, run_id, artifact_path, expected_sha256, output_dir) -> Path`
- `reproduce_evaluation(run_id, output_dir) -> RoundtripResult`

Run 必须记录完整身份、超参数、资源、objective metric/mode、adapter/config/manifest/report 工件。
下载后在新进程加载 base+adapter 并核对指标与 digest；失败时不得标记 `roundtrip_status=passed`。

### Task 3.4：项目 3 评估与 test-once（需用户确认后才可运行）

顺序必须是：生成/冻结数据 → 训练或收集三组结果 → validation 选候选 → 冻结 candidate → test 一次 →
Artifact round-trip。任何中途修改 prompt、split、阈值或候选都要让旧 test 结果失效。

## 项目 4 任务

### Task 4.1：实现 Ray runtime 与 job metadata

**Files**

- Create: `train-model/llm-lora-playground/configs/ray-job-smoke.yaml`
- Create: `train-model/llm-lora-playground/src/llm_lora_playground/ray_runtime.py`
- Create: `train-model/llm-lora-playground/src/llm_lora_playground/job_metadata.py`
- Create: `train-model/llm-lora-playground/tests/test_ray_metadata.py`

**Interfaces**

- `submit_job(config_path, address, runtime_env) -> RayJobHandle`
- `build_job_metadata(...) -> dict[str, Any]`
- `write_job_metadata_atomic(metadata, path) -> None`
- `update_checkpoint_pointer(metadata, checkpoint_record) -> dict[str, Any]`

metadata 必须包含 Ray Job ID、MLflow Run ID、config/data/code/environment digest、attempt、checkpoint URI/digest、
requested resources 和状态。写入必须原子，旧 metadata 不被覆盖成半成品。

### Task 4.2：实现 Driver/Worker 训练边界

**Files**

- Create/Modify: `train-model/llm-lora-playground/scripts/submit_train.py`
- Create: `train-model/llm-lora-playground/job/submit_train.py`
- Create: `train-model/llm-lora-playground/tests/test_driver_worker_boundary.py`

Driver 唯一创建/结束父 Run、发布共享 artifacts 和最终状态；Worker 只执行计算、报告 step 指标、
写唯一 checkpoint 并返回状态。测试要拒绝 Worker 重复创建或结束父 Run 的代码路径。

### Task 4.3：中断、恢复与安全重跑

**Files**

- Create: `train-model/llm-lora-playground/src/llm_lora_playground/recovery.py`
- Create: `train-model/llm-lora-playground/tests/test_recovery.py`

**Interfaces**

- `find_latest_complete_checkpoint(run_id, client) -> CheckpointRecord | None`
- `validate_resume_identity(checkpoint, current_config) -> None`
- `create_resume_attempt(previous_run_id, checkpoint, config) -> AttemptContext`
- `mark_attempt_status(attempt_id, status, reason=None) -> None`

恢复只允许从完整且 digest/identity 匹配的 checkpoint 开始；不匹配时干净重跑并关联 `retry_of`。
`--force` 不作为普通恢复接口。

### Task 4.4：项目 4 Ray smoke（需用户确认后才可运行）

执行一次：第 N step checkpoint → 可控取消 → API 检查 → 新 attempt 恢复 → 对比 loss/metadata → 报告。
检查 Ray Job 成功不被记录成最终 test evidence，且旧成功 adapter/Run 未被覆盖。

## 结束检查

实现全部完成后再运行：

```bash
python -m pytest train-model/llm-lora-playground/tests -q
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s tests -p 'test_*.py'
```

服务相关检查按仓库文档执行；不因项目 2–4 方案而启动训练或变更生产服务。
