# 项目 0+1 运行手册

本手册描述实现完成后的标准运行顺序。当前仓库还没有 `train-model/llm-lora-playground/` 实现；数据已交付，Qwen3.5-0.8B 模型下载/兼容性验证正在进行，因此在实现入口和兼容性 smoke 通过前不启动训练。

## 0. 变量与只读数据

```bash
cd /data/ai/chenzhangyue/code/galatea

export WECHAT_DATA_ROOT=/data/ai/chenzhangyue/code/data/data-deal/output/wechat_aa807aaad90dc4463964
export WECHAT_DATASET_ID=wechat_aa807aaad90dc4463964
export QWEN35_MODEL_PATH=/data/ai/chenzhangyue/code/model/Qwen3.5-0.8B
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
export MLFLOW_EXPERIMENT_NAME=llm-lora-playground
```

实现必须把 `WECHAT_DATA_ROOT` 当作只读路径。当前 staging 根同时存在多个历史数据集，
因此本次治理运行必须直接指向唯一数据集根
`/data/ai/chenzhangyue/code/data/data-deal/output/wechat_aa807aaad90dc4463964`，避免自动发现歧义。
模型路径默认来自 `configs/inference.yaml`，也可由实现读取 `QWEN35_MODEL_PATH` 覆盖；模型下载
完成后仍必须先通过 Transformers 架构兼容性 smoke。不要在数据目录创建临时文件；运行状态写入
`platform-data/llm-baselines/`，私人输出写入 `platform-data/llm-private/`。

用户交付前应确认 staging 中至少包含 `manifests/`、`reports/`、
`work/05_candidates/candidates.jsonl` 和 `datasets/*.jsonl`。本阶段只消费已脱敏派生数据，
不接受原始聊天导出作为输入。

## 1. 项目环境

遵循仓库 README 的项目隔离方式：

```bash
source /data/conda/etc/profile.d/conda.sh
conda env create --file train-model/llm-lora-playground/conda.yaml
conda activate llm-lora-playground-py312
python -m pip install -e train-model/llm-lora-playground
python -m pip check
```

如果先复用共享环境，只允许用于 `--check-config` 和版本兼容性 smoke；正式运行必须记录实际环境快照。
Qwen3.5 要求 Transformers 支持 `qwen3_5` / `Qwen3_5ForConditionalGeneration`。若环境仍报
`model type qwen3_5 is not recognized`，先升级到支持该架构的主线或已知兼容构建，不能静默降级
为 Qwen3 或把下载完成当作可运行。

## 2. 平台健康检查

```bash
systemctl is-active minio.service mlflow.service jupyterlab.service
curl -fsS -H 'Host: localhost' http://127.0.0.1:5000/health
curl -fsS http://127.0.0.1:9000/minio/health/live
ray status
nvidia-smi -L
```

MLflow 或 MinIO 不健康时，不创建 Run、不下载模型、不进入 fixture 推理。Ray 在本地 bounded inference 中不是必需，但 `ray status` 用于确认平台基线；Ray Head 未启动不阻断本地 `--check-config`。

## 3. 只读配置检查

```bash
python train-model/llm-lora-playground/scripts/infer.py \
  --config doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/configs/inference.yaml \
  --data-config doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/configs/data.yaml \
  --check-config
```

该命令只检查 YAML、环境变量、数据根/本地模型路径、模型/生成参数和目标资源，不访问聊天正文、不创建 MLflow Run、不加载模型。

## 4. 数据交付预检

```bash
python train-model/llm-lora-playground/scripts/infer.py \
  --config doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/configs/inference.yaml \
  --data-config doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/configs/data.yaml \
  --check-data \
  --output-dir platform-data/llm-baselines/preflight
```

该命令只读检查 staging 根或显式数据集根、布局解析、必需文件、source/split digest、
validation 计数、隐私/泄漏报告和 consent ledger 引用；不加载模型、不下载模型、不创建
MLflow Run。预期数据集根为 `/data/ai/chenzhangyue/code/data/data-deal/output/wechat_aa807aaad90dc4463964`，且 validation session/candidate 数为 137/759。

## 5. 数据 manifest 与 fixture 计划

```bash
python train-model/llm-lora-playground/scripts/infer.py \
  --config doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/configs/inference.yaml \
  --data-config doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/configs/data.yaml \
  --plan-fixtures \
  --output-dir platform-data/llm-baselines/plans
```

预期计划结果：dataset ID/source/config digest 与配置一致；validation session/candidate 数为 137/759；选出恰好 20 个稳定排序的 `sample_id`；每条输入的最后一个 assistant 目标被排除；privacy/leakage 报告可读；输出 fixture manifest 的 SHA-256。

如果 `datasets/validation.jsonl` 为空，不应误报为数据不存在；方案明确从 `work/05_candidates/candidates.jsonl` 构造诊断 fixture。该路径若缺失或 digest 不一致则阻断。

## 6. 环境/GPU smoke

```bash
python train-model/llm-lora-playground/scripts/infer.py \
  --config doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/configs/inference.yaml \
  --data-config doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/configs/data.yaml \
  --smoke-only
```

该命令依次完成：GPU 可用性、BF16 张量计算、版本快照、当前 GPU 进程只读快照、Qwen3.5 processor/model 加载、2 条通用文本 prompt 生成。它不读取聊天 fixture，不写训练数据，不终止现有进程。

## 7. 20 条推理基线

```bash
python train-model/llm-lora-playground/scripts/infer.py \
  --config doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/configs/inference.yaml \
  --data-config doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/configs/data.yaml \
  --run \
  --output-dir platform-data/llm-baselines
```

正式运行前必须已经完成第 2–6 步。入口创建一个独立 MLflow Run，记录运行清单、fixture digest、模型 revision、环境和聚合指标。每条 fixture 失败都要保留失败类别；任一失败时 Run 状态不得标记为成功。

## 8. 只读结果检查

```bash
python train-model/llm-lora-playground/scripts/infer.py \
  --config train-model/llm-lora-playground/configs/inference.yaml \
  --data-config doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/configs/data.yaml \
  --report RUN_ID
```

`--report` 通过 MLflow Tracking/Artifact API 读取 Run，不读取 `mlflow.db` 或 MinIO 文件系统。报告至少显示：成功数、延迟 p50/p95、tokens/s、峰值显存、model revision、dataset/split digest 和验收状态。

## 9. 可选 Ray Job 包装

项目 0+1 默认使用本地 bounded execution。若需要验证脚本可被平台调度，使用同一配置提交 Ray Job，但仍只申请 1 GPU：

```bash
ray job submit \
  --address http://127.0.0.1:8265 \
  --runtime-env-json='{"working_dir":"train-model/llm-lora-playground","excludes":["notebooks/**","tests/**"]}' \
  -- python scripts/infer.py \
  --config configs/inference.yaml \
  --data-config ../../doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/configs/data.yaml \
  --run
```

Ray 版本、GPU 数和实际 job ID 必须进入 Run Manifest。driver 是唯一 MLflow Run/Artifact 写入者；没有必要为了 20 条输入启动多进程 DDP。

## 10. 失败排查顺序

1. 先看 `reports/data-preflight.json`：解析根、布局、文件、digest、授权和计数是否一致；
2. 再看 `reports/preflight.json`：服务和资源是否一致；
3. 再看 `reports/environment.json`：PyTorch runtime 是否支持当前 GPU/BF16；
4. 若模型加载失败，确认 Transformers 版本达到 4.51.0 以上且 revision 可解析；
5. 若模板参数失败，查看 tokenizer 的 `apply_chat_template` 签名，不要删除 `enable_thinking=false` 静默重试；
6. 若显存不足，记录其他进程并降低本项目的 `max_new_tokens` 或停止本次运行，不杀其他进程；
7. 若 MLflow/MinIO 不健康，保留本地诊断并阻断，不把本地 JSON 冒充已追踪 Run；
8. 重试必须产生新 Run ID，并设置 `retry_of`，不能覆盖旧输出。

## 11. 完成后的交付物

```text
platform-data/llm-baselines/<run_id>/
├── manifests/run_manifest.json
├── manifests/fixture_manifest.json
├── reports/preflight.json
├── reports/environment.json
├── reports/metrics.json
└── reports/inference_records.jsonl
```

生成文本如需保留，放在受控的 `platform-data/llm-private/`，不进入 Git，也不默认上传 MLflow。
