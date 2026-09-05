# 项目 2–4 运行手册

本手册是实现完成后的标准顺序。当前仓库只交付方案，因此以下命令是未来命令；除非另有明确确认，
不得执行带 `--run`、训练、Ray 提交或 test 评估的步骤。

## 0. 变量与目录

```bash
cd /data/ai/chenzhangyue/code/galatea

export LLM_PROJECT_ROOT="$PWD/train-model/llm-lora-playground"
export LLM_TOY_DATA_ROOT="$PWD/platform-data/llm-baselines/toy-lora"
export QWEN35_MODEL_PATH=/data/ai/chenzhangyue/code/model/Qwen3.5-0.8B
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
export MLFLOW_EXPERIMENT_NAME=llm-lora-playground
```

合成数据、adapter、checkpoint、metadata 和评估报告写入 `platform-data/llm-baselines/`；
不得写入项目源码目录。真实微信数据路径不作为项目 2–4 的输入。

## 1. 环境和平台只读检查

```bash
source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312
python -m pip check

systemctl is-active minio.service mlflow.service jupyterlab.service
curl -fsS -H 'Host: localhost' http://127.0.0.1:5000/health
curl -fsS http://127.0.0.1:9000/minio/health/live
ray status
nvidia-smi -L
```

正式训练使用项目自己的 `conda.yaml` 或等价环境；共享环境仅可用于 schema、配置和不加载训练权重
的兼容性检查。GPU、BF16、Transformers `qwen3_5` 支持或 MLflow/MinIO 不满足时，保持 blocked。
不杀进程、不 reset GPU、不清理其他任务的显存。

## 2. 项目 2：只读契约检查（不训练）

### 2.1 配置检查

```bash
python "$LLM_PROJECT_ROOT/scripts/train_lora.py" \
  --config "$LLM_PROJECT_ROOT/configs/toy-lora-smoke.yaml" \
  --check-config
python "$LLM_PROJECT_ROOT/scripts/train_lora.py" \
  --config "$LLM_PROJECT_ROOT/configs/toy-lora-baseline.yaml" \
  --check-config
```

预期：模型、dtype/device、single-GPU resources、assistant-only loss、LoRA target modules、seed、
objective metric/mode、输出根和凭据检查通过；不创建 MLflow Run、不加载完整模型。

### 2.2 schema 和生成器检查

```bash
python "$LLM_PROJECT_ROOT/scripts/generate_synthetic.py" \
  --output-dir "$LLM_TOY_DATA_ROOT/check" \
  --count 8 --seed 42 --check-only
python -m pytest "$LLM_PROJECT_ROOT/tests/test_synthetic_data.py" \
  "$LLM_PROJECT_ROOT/tests/test_loss_mask.py" \
  "$LLM_PROJECT_ROOT/tests/test_lora_roundtrip.py" \
  "$LLM_PROJECT_ROOT/tests/test_checkpoint_metadata.py" -q
```

预期：数据只包含虚构咖啡店角色，digest 可复算；system/user labels 全为 `-100`，assistant labels
有效；adapter/checkpoint 测试覆盖失败不覆盖成功的路径。

## 3. 项目 2：生成正式 Toy 数据

获得启动确认后执行：

```bash
python "$LLM_PROJECT_ROOT/scripts/generate_synthetic.py" \
  --output-dir "$LLM_TOY_DATA_ROOT/v1" \
  --count 400 --seed 42 --version toy-v1
```

生成后只读核对 `dataset_manifest.json`：`dataset_id`、generator/preprocessing version、count、
scenario 分布、source（synthetic）、文件 SHA-256 和 schema version。若数据 digest 变化，生成
新的 config/data identity；不覆盖旧目录。

## 4. 项目 2：2-sample/2-step 预检查

```bash
python "$LLM_PROJECT_ROOT/scripts/train_lora.py" \
  --config "$LLM_PROJECT_ROOT/configs/toy-lora-smoke.yaml" \
  --data "$LLM_TOY_DATA_ROOT/v1" --dry-run --samples 2 --steps 2
```

该步骤必须验证 tokenizer chat template、assistant span、loss mask、梯度可计算、目标模块可注入、
adapter 可写入唯一临时目录。任一项失败即停止，不进入 10-step smoke。

## 5. 项目 2：10-step Toy LoRA smoke

> 需要用户明确确认；本方案交付时不执行。

```bash
python "$LLM_PROJECT_ROOT/scripts/train_lora.py" \
  --config "$LLM_PROJECT_ROOT/configs/toy-lora-smoke.yaml" \
  --data "$LLM_TOY_DATA_ROOT/v1" --run
```

运行结束检查：

- 不含首次下载控制在 10 分钟内；
- train/validation loss、learning rate、gradient norm 和 step 数存在；
- adapter 只含 PEFT 工件，完整 base 未被复制；
- 全新 Python 进程加载 base revision + adapter 成功；
- checkpoint metadata、文件 digest、run/config/data identity 一致；
- base 与 base+adapter 在固定风格 fixture 上存在可解释差异；
- 失败训练没有覆盖任何成功 adapter/Run。

## 6. 项目 2：1 epoch baseline

只有 smoke 全部通过后才执行：

```bash
python "$LLM_PROJECT_ROOT/scripts/train_lora.py" \
  --config "$LLM_PROJECT_ROOT/configs/toy-lora-baseline.yaml" \
  --data "$LLM_TOY_DATA_ROOT/v1" --run
```

目标是不含首次下载控制在 30 分钟内。此 Run 仍是学习 baseline，不是最终模型，不进行 Registry alias
更新。若 loss 不合理下降、风格检查不优于 base 或资源超限，保留诊断并修复契约/数据后新建 Run。

## 7. 项目 3：冻结数据和评估协议

### 7.1 生成约 1,000 条数据并分组切分

```bash
python "$LLM_PROJECT_ROOT/scripts/generate_synthetic.py" \
  --output-dir "$LLM_TOY_DATA_ROOT/v2" \
  --count 1000 --seed 42 --version toy-v2
python "$LLM_PROJECT_ROOT/scripts/evaluate.py" \
  --config "$LLM_PROJECT_ROOT/configs/reproducible-eval.yaml" \
  --build-split --data "$LLM_TOY_DATA_ROOT/v2"
```

冻结前检查：同一 `scenario_id`/近重复族没有跨 split；split manifest digest 可重复；source、
preprocessing、model revision、seed 和 sample/group count 一致。冻结后复制 manifest 到受控 artifact，
不要手工改 test 清单。

### 7.2 运行三组比较

```bash
for variant in base prompt-only lora; do
  python "$LLM_PROJECT_ROOT/scripts/evaluate.py" \
    --config "$LLM_PROJECT_ROOT/configs/reproducible-eval.yaml" \
    --variant "$variant" --split validation --run
done
```

三组必须共享 prompt、tokenizer、generation、seed、输入和 max tokens。候选只用 train/validation
证据选择；记录 validation loss、生成长度、格式/风格规则、重复率和运行指标。

### 7.3 冻结候选并只评估 test 一次

```bash
python "$LLM_PROJECT_ROOT/scripts/evaluate.py" \
  --config "$LLM_PROJECT_ROOT/configs/reproducible-eval.yaml" \
  --freeze-candidate --candidate-run RUN_ID

python "$LLM_PROJECT_ROOT/scripts/evaluate.py" \
  --config "$LLM_PROJECT_ROOT/configs/reproducible-eval.yaml" \
  --candidate RUN_ID --split test --test-once --run
```

输出必须带 `test_evaluation_id`、candidate/config/split digest。若后续改 prompt、阈值、checkpoint、
数据或评估规则，旧 test 结果作废，不能继续作为最终证据。

## 8. 项目 3：MLflow Artifact round-trip

```bash
python "$LLM_PROJECT_ROOT/scripts/roundtrip_artifact.py" \
  --run-id RUN_ID \
  --output-dir "$LLM_TOY_DATA_ROOT/roundtrip/RUN_ID"
```

脚本必须仅使用 MLflow Tracking/Artifact API：找到 Run、下载 adapter/manifest/report、计算 SHA-256、
在新进程加载 base+adapter、复跑冻结评估并比较指标/协议/digest。服务端 `mlflow.db` 和 MinIO 文件系统
不可读；哈希或结果不一致时 `roundtrip_status=failed`。

## 9. 项目 4：Ray Job 单 GPU smoke

### 9.1 提交同一训练入口

```bash
ray job submit --address http://127.0.0.1:8265 \
  --runtime-env-json='{"working_dir":"train-model/llm-lora-playground","excludes":["notebooks/**","tests/**"]}' \
  -- python scripts/submit_train.py \
    --config configs/ray-job-smoke.yaml \
    --data ../../platform-data/llm-baselines/toy-lora/v1 \
    --run
```

Job 必须显式申请 `num_gpus=1`、`cpus=4`、`memory_gb=8`，worker_count=1；Ray 只提交、管理资源和
生命周期，不复制 `train()`。Driver 是唯一父 MLflow Run owner；Ray Job ID、Run ID、config/data/code/
environment digest 和 checkpoint URI 写入 job metadata。

### 9.2 中断和恢复

```bash
python "$LLM_PROJECT_ROOT/scripts/submit_train.py" \
  --config "$LLM_PROJECT_ROOT/configs/ray-job-smoke.yaml" \
  --data "$LLM_TOY_DATA_ROOT/v1" --interrupt-after-step 5 --run

python "$LLM_PROJECT_ROOT/scripts/submit_train.py" \
  --config "$LLM_PROJECT_ROOT/configs/ray-job-smoke.yaml" \
  --data "$LLM_TOY_DATA_ROOT/v1" --resume-from CHECKPOINT_URI --run
```

演练后通过 Ray Job API 和 MLflow API 核对：旧 Run 为 interrupted/failed、checkpoint 为 complete、
新 attempt/Run 通过 `resumed_from` 关联、loss history 和身份一致、旧成功 adapter 未被覆盖。缺失或
身份不匹配时必须新建干净 Run；不使用 `--force` 覆盖。

## 10. 报告与故障排查

推荐每个 Run 的本地临时结果结构：

```text
platform-data/llm-baselines/<project>/<run_id>/
├── manifests/run_manifest.json
├── manifests/dataset_manifest.json
├── manifests/split_manifest.json
├── checkpoints/<attempt_id>/step-<n>/
├── reports/metrics.json
├── reports/evaluation.json
├── reports/recovery.json
└── reports/environment.json
```

排查顺序：

1. 配置/schema：确认 digest、资源和 secret 检查；
2. 数据 manifest：确认 count、group split、近重复和 source；
3. tokenizer/mask：检查 assistant span 和 `-100` labels；
4. 模型/LoRA：检查 `q_proj/v_proj` 是否真实存在、revision 是否 immutable；
5. checkpoint：检查 `complete` 标记和 SHA-256；
6. MLflow/Artifact：确认 API 健康和下载校验；
7. Ray：确认 Job ID、attempt、Driver owner、资源和恢复指针；
8. 重试：新建 Run/attempt，记录 `retry_of`，不覆盖旧目录。

## 11. 结束与后续

项目 2–4 全部验收后，归档配置、manifest、Run ID、Artifact round-trip、Ray 恢复报告和风险清单。
只有这一步完成，才讨论项目 5 的真实微信数据工程；真实数据授权、脱敏、人工审核和撤回流程不会因
项目 2–4 的成功而自动放行。
