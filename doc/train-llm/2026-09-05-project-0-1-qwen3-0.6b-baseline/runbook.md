# 项目 0+1 运行手册

本手册描述实现完成后的标准运行顺序。当前仓库还没有 `train-model/llm-lora-playground/` 实现，因此下面的正式入口名称是预先冻结的接口；执行实现前只做方案评审，不运行模型下载或训练。

## 0. 变量与只读数据

```bash
cd /Users/weidian/project/luna/Galatea

export WECHAT_DATA_ROOT=/Users/weidian/project/luna-data/data-deal/output/wechat_aa807aaad90dc4463964
export WECHAT_DATASET_ID=wechat_aa807aaad90dc4463964
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
export MLFLOW_EXPERIMENT_NAME=llm-lora-playground
```

实现必须把 `WECHAT_DATA_ROOT` 当作只读路径。不要在该目录创建临时文件；运行状态写入 `platform-data/llm-baselines/`，私人输出写入 `platform-data/llm-private/`。

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

该命令只检查 YAML、环境变量、路径存在性、模型/生成参数和目标资源，不访问聊天正文、不创建 MLflow Run、不加载模型。

## 4. 数据 manifest 与 fixture 计划

```bash
python train-model/llm-lora-playground/scripts/infer.py \
  --config doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/configs/inference.yaml \
  --data-config doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/configs/data.yaml \
  --plan-fixtures \
  --output-dir platform-data/llm-baselines/plans
```

预期计划结果：dataset ID/source/config digest 与配置一致；validation session/candidate 数为 137/759；选出恰好 20 个稳定排序的 `sample_id`；每条输入的最后一个 assistant 目标被排除；privacy/leakage 报告可读；输出 fixture manifest 的 SHA-256。

如果 `datasets/validation.jsonl` 为空，不应误报为数据不存在；方案明确从 `work/05_candidates/candidates.jsonl` 构造诊断 fixture。该路径若缺失或 digest 不一致则阻断。

## 5. 环境/GPU smoke

```bash
python train-model/llm-lora-playground/scripts/infer.py \
  --config doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/configs/inference.yaml \
  --data-config doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/configs/data.yaml \
  --smoke-only
```

该命令依次完成：GPU 可用性、BF16 张量计算、版本快照、当前 GPU 进程只读快照、tokenizer/model 加载、2 条通用 prompt 生成。它不读取聊天 fixture，不写训练数据，不终止现有进程。

## 6. 20 条推理基线

```bash
python train-model/llm-lora-playground/scripts/infer.py \
  --config doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/configs/inference.yaml \
  --data-config doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/configs/data.yaml \
  --run \
  --output-dir platform-data/llm-baselines
```

正式运行前必须已经完成第 2–5 步。入口创建一个独立 MLflow Run，记录运行清单、fixture digest、模型 revision、环境和聚合指标。每条 fixture 失败都要保留失败类别；任一失败时 Run 状态不得标记为成功。

## 7. 只读结果检查

```bash
python train-model/llm-lora-playground/scripts/infer.py \
  --config train-model/llm-lora-playground/configs/inference.yaml \
  --data-config doc/train-llm/2026-09-05-project-0-1-qwen3-0.6b-baseline/configs/data.yaml \
  --report RUN_ID
```

`--report` 通过 MLflow Tracking/Artifact API 读取 Run，不读取 `mlflow.db` 或 MinIO 文件系统。报告至少显示：成功数、延迟 p50/p95、tokens/s、峰值显存、model revision、dataset/split digest 和验收状态。

## 8. 可选 Ray Job 包装

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

## 9. 失败排查顺序

1. 先看 `reports/preflight.json`：路径、digest、服务和资源是否一致；
2. 再看 `reports/environment.json`：PyTorch runtime 是否支持当前 GPU/BF16；
3. 若模型加载失败，确认 Transformers 版本达到 4.51.0 以上且 revision 可解析；
4. 若模板参数失败，查看 tokenizer 的 `apply_chat_template` 签名，不要删除 `enable_thinking=false` 静默重试；
5. 若显存不足，记录其他进程并降低本项目的 `max_new_tokens` 或停止本次运行，不杀其他进程；
6. 若 MLflow/MinIO 不健康，保留本地诊断并阻断，不把本地 JSON 冒充已追踪 Run；
7. 重试必须产生新 Run ID，并设置 `retry_of`，不能覆盖旧输出。

## 10. 完成后的交付物

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
