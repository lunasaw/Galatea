# 数据 → 训练 → 模型：端到端实施手册

> 适用环境：当前服务器（Ubuntu 22.04、32 CPU、约 92 GiB 内存、1 张 RTX PRO 5000 Blackwell、约 48 GiB 显存）  
> 技术栈：JupyterLab + Ray + PyTorch + MLflow + 本地 MinIO  
> 目标读者：算法工程师、平台工程师、运维工程师  
> 默认任务：通用 PyTorch 监督训练

当前已验证的软件版本：Python 3.12.12、PyTorch 2.10.0+cu130、Ray 2.53.0、MLflow 3.14.0、JupyterLab 4.6.2、MinIO `RELEASE.2025-09-07T16-13-09Z`。JupyterLab、MLflow 和 MinIO 由 systemd 管理；Ray 2.53.0 已安装，但 Head 需要在提交训练前启动。

---

## 1. 最终要实现什么

这套流程要把一次训练变成可重复、可追踪、可恢复的工程任务：

```text
原始数据
  ↓
数据校验与版本化
  ↓
训练集 / 验证集 / 测试集
  ↓
Ray Job 调度
  ↓
PyTorch 训练
  ├── 指标 → MLflow
  ├── Checkpoint → 本地磁盘 / MinIO
  └── 日志 → Ray
  ↓
模型评测
  ↓
MLflow Model Registry
  ↓
候选模型 candidate
  ↓ 通过质量门禁
生产模型 champion
```

完成后，每个模型都应该能够回答以下问题：

- 使用了哪个数据版本？
- 使用了哪份训练配置？
- 对应哪个代码版本？
- 训练指标和评测指标是多少？
- 模型文件保存在哪里？
- 训练失败后从哪里恢复？
- 当前生产模型为什么是这个版本？

---

## 2. 组件职责

| 组件 | 负责 | 不负责 |
|---|---|---|
| JupyterLab | 数据探索、代码开发、小样本验证 | 正式训练任务的长期可靠运行 |
| Ray Jobs | 提交、停止、查询训练任务 | 保存实验历史 |
| Ray Core / Train | CPU、GPU资源调度和训练进程执行 | 模型版本管理 |
| PyTorch | 模型、Loss、Optimizer、训练循环 | 集群资源管理 |
| MLflow Tracking | 参数、指标、标签、Artifact、Run状态 | GPU调度 |
| MLflow Model Registry | 模型版本、别名和晋级记录 | 存储原始数据 |
| 本地 MinIO | 原始数据、处理数据、Checkpoint、模型的持久化 | 训练逻辑 |

一句话概括：

> JupyterLab负责开发，Ray负责执行，PyTorch负责训练，MLflow负责记录，MinIO负责长期保存。

## 2.1 前置平台服务

开始数据和训练流程前，下面四个服务必须已经运行。

### MLflow Server

MinIO 的 S3 凭据只配置在 MLflow Server 的受保护环境中。当前 systemd 已通过 `/etc/minio/mlflow-s3.env` 注入，不要把下面的占位符写进训练代码：

```bash
export AWS_ACCESS_KEY_ID="mlflow"
export AWS_SECRET_ACCESS_KEY="<从 /etc/minio/mlflow-s3.env 读取>"
export AWS_DEFAULT_REGION="us-east-1"
export MLFLOW_S3_ENDPOINT_URL="http://127.0.0.1:9000"
```

启动：

```bash
mlflow server \
  --host 127.0.0.1 \
  --port 5000 \
  --backend-store-uri sqlite:////data/ai/chenzhangyue/code/galatea/platform-data/mlflow/mlflow.db \
  --serve-artifacts \
  --artifacts-destination s3://mlflow-artifacts
```

验证：

```bash
curl -fsS http://127.0.0.1:5000/health
```

### MinIO

```bash
systemctl is-active minio.service
curl -fsS http://127.0.0.1:9000/minio/health/live
```

MinIO API 使用 `127.0.0.1:9000`，Console 使用 `127.0.0.1:9001`；Console 的 code-server 地址是 `https://coder.vdian.net/GC5026/proxy/9001/`。Bucket、账号、策略和备份见 [`minio-start.md`](./minio-start.md)。

### Ray Head

```bash
ray start --head \
  --node-ip-address=<服务器内网IP> \
  --dashboard-host=127.0.0.1 \
  --dashboard-port=8265 \
  --num-cpus=<CPU总核心数减去2至4> \
  --num-gpus=1 \
  --object-store-memory=8589934592
```

验证：

```bash
ray status
```

输出中必须能看到1个GPU资源。

### JupyterLab

```bash
jupyter lab \
  --ip=127.0.0.1 \
  --port=8888 \
  --no-browser \
  --ServerApp.root_dir=/data/ai/chenzhangyue/code/galatea
```

四个服务均不应未经认证直接暴露到公网。远程访问使用SSH隧道或带认证的HTTPS反向代理。

---

## 3. 推荐的代码仓库结构

```text
ai-training-project/
├── README.md
├── requirements.txt
├── configs/
│   ├── train-dev.yaml
│   └── train-prod.yaml
├── manifests/
│   └── dataset-v001.csv
├── notebooks/
│   ├── 01-data-inspection.ipynb
│   └── 02-one-batch-test.ipynb
├── src/
│   ├── __init__.py
│   ├── config.py
│   ├── data.py
│   ├── model.py
│   ├── train_loop.py
│   ├── evaluate.py
│   └── registry.py
├── scripts/
│   ├── validate_data.py
│   ├── build_manifest.py
│   ├── train.py
│   ├── evaluate_model.py
│   └── promote_model.py
└── tests/
    ├── test_data.py
    ├── test_model.py
    └── test_smoke_training.py
```

原则：

- Notebook只用于探索和小规模验证。
- 正式训练逻辑放进 `src/`。
- 可执行入口放进 `scripts/`。
- 配置和代码分离。
- 大型数据、Checkpoint和模型不提交到Git。
- Git中只保存Manifest、配置、代码和测试。

基础依赖可以写入 `requirements.txt`：

```text
ray[default,train,data]
mlflow
boto3
PyYAML
pandas
```

PyTorch不要仅依据 `nvidia-smi` 显示的CUDA兼容上限选择版本；应使用明确支持当前Blackwell GPU和驱动的官方安装组合。

---

## 4. 存储目录与MinIO规划

### 4.1 服务器本地目录

```text
/data/ai/chenzhangyue/code/galatea/
├── cats-and-dogs/  # 当前示例项目和Notebook
├── data-cache/     # MinIO数据的本地缓存
├── checkpoints/    # 训练中的本地Checkpoint
└── platform-data/
    ├── minio/data/ # MinIO对象数据
    └── mlflow/     # MLflow SQLite与历史本地Artifact目录
```

创建目录：

```bash
mkdir -p /data/ai/chenzhangyue/code/galatea/data-cache
mkdir -p /data/ai/chenzhangyue/code/galatea/checkpoints
mkdir -p /data/ai/chenzhangyue/code/galatea/platform-data/minio/data
mkdir -p /data/ai/chenzhangyue/code/galatea/platform-data/mlflow
```

### 4.2 MinIO目录

```text
MinIO
├── s3://training-data/
│   └── datasets/
│       ├── raw/<dataset-name>/<source-date>/
│       ├── processed/<dataset-name>/<dataset-version>/
│       └── manifests/<dataset-name>/<dataset-version>.csv
└── s3://mlflow-artifacts/
    └── <由MLflow管理的实验和Run目录>/
```

本文在机器可读配置中统一使用 `s3://` URI。训练数据、Manifest 和预处理结果使用 `s3://training-data/...`；MLflow Artifact 使用 `s3://mlflow-artifacts/...`。MinIO Endpoint 固定为 `http://127.0.0.1:9000`。

远程恢复Checkpoint和模型统一作为MLflow Artifact进入 `mlflow-artifacts/`。MLflow Model Registry保存模型版本和Run之间的关系，通常直接引用其中的模型，不要求额外复制一份到独立 `models/` 目录。

约束：

- `raw/`中的原始数据只追加，不覆盖。
- `processed/`按数据版本写入。
- 每次训练固定引用一个明确的数据版本。
- 不使用`latest`作为训练输入。
- 模型文件必须能通过MLflow Run ID追溯。
- 当前是单机单盘 MinIO，不提供主机故障容错；对象数据、MLflow SQLite 和密钥必须备份到异机或独立存储。

---

## 5. 第一步：数据进入

### 5.1 原始数据落地

原始数据进入MinIO：

```text
业务系统 / 人工上传 / 数据库导出
                ↓
MinIO s3://training-data/datasets/raw/<dataset-name>/<source-date>/
```

使用S3兼容客户端上传的示例：

先从 `/etc/minio/training-data-s3.env` 注入训练数据账号。该文件是 root-only；Notebook 和普通用户应由管理员通过受控方式获得等价的短期环境变量，不复制长期密钥到项目目录：

```bash
set -a
source /etc/minio/training-data-s3.env
set +a
```

```bash
aws \
  --endpoint-url=http://127.0.0.1:9000 \
  s3 sync \
  /path/to/raw-data/ \
  s3://training-data/datasets/raw/<dataset-name>/<source-date>/
```

上传完成后不要直接开始训练，应先生成数据Manifest。

### 5.2 Manifest数据契约

推荐CSV结构：

```csv
sample_id,uri,label,split,sha256
000001,s3://training-data/datasets/raw/demo/2026-07-30/000001.bin,0,train,4e8a...
000002,s3://training-data/datasets/raw/demo/2026-07-30/000002.bin,1,train,91ab...
000003,s3://training-data/datasets/raw/demo/2026-07-30/000003.bin,0,val,1fd2...
000004,s3://training-data/datasets/raw/demo/2026-07-30/000004.bin,1,test,83c0...
```

字段定义：

| 字段 | 含义 | 要求 |
|---|---|---|
| `sample_id` | 样本唯一标识 | 全数据集唯一 |
| `uri` | 数据文件位置 | 使用明确的 `s3://training-data/...` 路径，不使用临时URL |
| `label` | 监督标签 | 必须符合标签字典 |
| `split` | 数据分区 | 只能是`train`、`val`、`test` |
| `sha256` | 文件校验值 | 防止文件被静默修改 |

如果是图像、文本或表格任务，可以增加：

- `width`、`height`
- `language`
- `group_id`
- `event_time`
- `source`
- `annotation_uri`

### 5.3 数据版本

数据版本至少由以下内容决定：

```text
dataset_version =
  原始数据范围
  + Manifest内容
  + 标签字典
  + 切分规则
  + 预处理代码版本
```

推荐命名：

```text
dataset-v001
dataset-v002
dataset-20260730-a1b2c3d
```

训练配置中必须保存：

```yaml
data:
  dataset_name: demo-classification
  dataset_version: dataset-v001
  manifest_uri: s3://training-data/datasets/manifests/demo/dataset-v001.csv
```

---

## 6. 第二步：数据校验

训练前必须完成以下检查：

### 6.1 文件级检查

- URI是否存在。
- 文件是否可以读取。
- SHA256是否一致。
- 文件格式是否正确。
- 是否存在0字节文件。
- 是否有损坏样本。

### 6.2 标签级检查

- 标签是否属于允许集合。
- 是否存在空标签。
- 标签分布是否严重不均衡。
- 同一`sample_id`是否出现冲突标签。

### 6.3 数据切分检查

- `train`、`val`、`test`是否都有数据。
- 同一个样本不能出现在多个Split。
- 同一个用户、设备或业务实体的数据不能跨Split泄漏。
- 时间序列任务不能使用未来数据训练过去模型。

### 6.4 校验输出

数据校验应生成报告：

```json
{
  "dataset_version": "dataset-v001",
  "total_samples": 100000,
  "train_samples": 80000,
  "val_samples": 10000,
  "test_samples": 10000,
  "invalid_samples": 0,
  "duplicate_samples": 0,
  "label_distribution": {
    "0": 50120,
    "1": 49880
  },
  "status": "PASSED"
}
```

门禁：

```text
数据校验失败 → 禁止提交正式训练
```

---

## 7. 第三步：数据预处理

### 7.1 使用Ray并行处理

适合放进Ray的任务：

- 图片解码和缩放。
- 文本清洗、分词。
- 音视频抽帧。
- 特征计算。
- Embedding生成。
- 大量小文件转换为Parquet。

示例：

```python
import ray

ray.init(address="auto")


@ray.remote(num_cpus=2)
def preprocess_sample(sample):
    result = decode_and_transform(sample["uri"])
    return {
        "sample_id": sample["sample_id"],
        "features": result,
        "label": sample["label"],
        "split": sample["split"],
    }


result_refs = [
    preprocess_sample.remote(sample)
    for sample in manifest_records
]

processed_records = ray.get(result_refs)
```

注意：

- 预处理任务通常声明CPU，不要无意占用GPU。
- 大数据不要一次性`ray.get()`全部拉回Driver。
- 数据量大时优先使用Ray Data流式处理。
- 处理结果写入MinIO的版本目录。
- 训练和验证必须使用相同的预处理定义。

### 7.2 预处理产物

```text
MinIO datasets/processed/<dataset-name>/<dataset-version>/
├── train/
├── val/
├── test/
├── schema.json
└── validation-report.json
```

训练只读取已经通过校验的版本。

### 7.3 PyTorch Dataset适配层

`src/data.py`只负责把Manifest记录转换为模型需要的Tensor：

```python
import pandas as pd
from torch.utils.data import DataLoader, Dataset


class ManifestDataset(Dataset):
    def __init__(self, manifest_path, split, decode_sample, transform=None):
        manifest = pd.read_csv(manifest_path)
        self.rows = manifest[manifest["split"] == split].reset_index(drop=True)
        self.decode_sample = decode_sample
        self.transform = transform

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, index):
        row = self.rows.iloc[index]
        sample = self.decode_sample(row["uri"])

        if self.transform is not None:
            sample = self.transform(sample)

        return sample, int(row["label"])


def build_dataloaders(config):
    manifest_path = materialize_manifest(
        config["data"]["manifest_uri"],
        config["data"]["local_cache_dir"],
    )
    train_transform = build_train_transform(config)
    eval_transform = build_eval_transform(config)

    train_dataset = ManifestDataset(
        manifest_path,
        split="train",
        decode_sample=decode_sample,
        transform=train_transform,
    )
    val_dataset = ManifestDataset(
        manifest_path,
        split="val",
        decode_sample=decode_sample,
        transform=eval_transform,
    )

    loader_options = {
        "batch_size": config["training"]["batch_size"],
        "num_workers": config["data"]["num_workers"],
        "pin_memory": True,
    }

    train_loader = DataLoader(
        train_dataset,
        shuffle=True,
        **loader_options,
    )
    val_loader = DataLoader(
        val_dataset,
        shuffle=False,
        **loader_options,
    )

    return train_loader, val_loader
```

需要按任务类型实现的两个边界：

- `materialize_manifest()`：通过MinIO的S3兼容接口把Manifest下载到本地缓存。
- `decode_sample()`：图像任务解码图片，文本任务读取文本，表格任务构造数值特征。

数据解码逻辑不能散落在Notebook和训练循环中，否则训练与评测容易使用不同处理规则。

---

## 8. 第四步：训练配置

`configs/train-prod.yaml`：

```yaml
project:
  name: demo-classification
  experiment_name: demo-classification-prod
  registered_model_name: demo-classifier

data:
  dataset_name: demo-classification
  dataset_version: dataset-v001
  manifest_uri: s3://training-data/datasets/manifests/demo/dataset-v001.csv
  local_cache_dir: /data/ai/chenzhangyue/code/galatea/data-cache/demo/dataset-v001
  num_workers: 8

model:
  architecture: baseline-model
  num_classes: 2

training:
  epochs: 20
  batch_size: 32
  learning_rate: 0.0001
  seed: 20260730
  mixed_precision: true
  checkpoint_every_epochs: 2

resources:
  num_cpus: 8
  num_gpus: 1

mlflow:
  tracking_uri: http://127.0.0.1:5000
  artifact_store: s3://mlflow-artifacts

output:
  checkpoint_dir: /data/ai/chenzhangyue/code/galatea/checkpoints
```

要求：

- 正式训练不在代码中硬编码超参数。
- 每个MLflow Run保存完整配置文件。
- 配置变更必须进入Git。
- 随机种子必须明确。
- 数据版本必须明确。

---

## 9. 第五步：训练代码接入Ray和MLflow

### 9.1 训练入口

`scripts/train.py`：

```python
import argparse
import os
from pathlib import Path

import mlflow
import mlflow.pytorch
import ray
import torch
import yaml

from src.data import build_dataloaders
from src.model import build_model
from src.train_loop import evaluate, train_one_epoch


def load_config(path):
    with open(path, "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


@ray.remote(max_retries=1)
def run_training(config, config_path):
    mlflow.set_tracking_uri(config["mlflow"]["tracking_uri"])
    mlflow.set_experiment(config["project"]["experiment_name"])

    run_tags = {
        "dataset_version": config["data"]["dataset_version"],
        "training_type": "supervised",
        "executor": "ray",
    }

    with mlflow.start_run(tags=run_tags) as run:
        mlflow.log_params({
            "architecture": config["model"]["architecture"],
            "epochs": config["training"]["epochs"],
            "batch_size": config["training"]["batch_size"],
            "learning_rate": config["training"]["learning_rate"],
            "seed": config["training"]["seed"],
            "dataset_version": config["data"]["dataset_version"],
        })

        mlflow.log_artifact(config_path, artifact_path="config")

        torch.manual_seed(config["training"]["seed"])

        train_loader, val_loader = build_dataloaders(config)
        model = build_model(config).cuda()
        optimizer = torch.optim.AdamW(
            model.parameters(),
            lr=config["training"]["learning_rate"],
        )

        best_val_loss = float("inf")
        checkpoint_root = (
            Path(config["output"]["checkpoint_dir"])
            / run.info.run_id
        )
        checkpoint_root.mkdir(parents=True, exist_ok=True)

        for epoch in range(config["training"]["epochs"]):
            train_metrics = train_one_epoch(
                model,
                train_loader,
                optimizer,
                config,
            )
            val_metrics = evaluate(
                model,
                val_loader,
                config,
            )

            metrics = {
                "train_loss": train_metrics["loss"],
                "val_loss": val_metrics["loss"],
                "val_accuracy": val_metrics["accuracy"],
            }
            mlflow.log_metrics(metrics, step=epoch)

    checkpoint = {
                "epoch": epoch,
                "model_state_dict": model.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "config": config,
                "mlflow_run_id": run.info.run_id,
            }

            latest_path = checkpoint_root / "latest.pt"
            torch.save(checkpoint, latest_path)

            checkpoint_interval = config["training"]["checkpoint_every_epochs"]
            if (epoch + 1) % checkpoint_interval == 0:
                # MLflow Server proxies this upload to the local MinIO bucket.
                mlflow.log_artifact(
                    str(latest_path),
                    artifact_path=f"recovery/epoch-{epoch + 1:04d}",
                )

            if val_metrics["loss"] < best_val_loss:
                best_val_loss = val_metrics["loss"]
                best_path = checkpoint_root / "best.pt"
                torch.save(checkpoint, best_path)

        mlflow.log_artifact(
            str(checkpoint_root / "best.pt"),
            artifact_path="checkpoints",
        )

        mlflow.pytorch.log_model(
            model,
            artifact_path="model",
        )

        return {
            "run_id": run.info.run_id,
            "best_val_loss": best_val_loss,
            "local_checkpoint": str(checkpoint_root / "best.pt"),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    config = load_config(args.config)

    os.environ.setdefault(
        "MLFLOW_TRACKING_URI",
        config["mlflow"]["tracking_uri"],
    )

    ray.init(address="auto")
    training_ref = run_training.options(
        num_cpus=config["resources"]["num_cpus"],
        num_gpus=config["resources"]["num_gpus"],
    ).remote(config, args.config)

    result = ray.get(training_ref)
    print(result)


if __name__ == "__main__":
    main()
```

### 9.2 关键规则

#### 必须声明GPU

```python
training_ref = run_training.options(
    num_cpus=config["resources"]["num_cpus"],
    num_gpus=1,
).remote(config, config_path)
```

如果直接调用`model.cuda()`但没有声明`num_gpus=1`，Ray无法知道任务占用了GPU。

#### 不返回整个模型

Ray任务只返回：

- MLflow Run ID。
- 最终指标。
- Checkpoint路径。
- 模型版本。

不要通过Ray Object Store返回几十GB模型。

#### 指标不要每个Batch都上报

推荐：

- 每个Epoch上报一次。
- 或每50至500个Step上报一次。

#### 多GPU只让Rank 0写MLflow

未来扩展到多GPU时：

```python
if world_rank == 0:
    mlflow.log_metrics(metrics, step=epoch)
```

---

## 10. 第六步：提交Ray Job

### 10.1 开发阶段

在JupyterLab完成：

- 随机读取10条数据。
- 检查输入输出形状。
- 运行一个Batch。
- 检查Loss是否为有限数值。
- 检查显存占用。
- 确认Checkpoint可以保存和加载。

开发阶段不要直接启动数小时训练。

### 10.2 Smoke Test

第一次提交只使用：

- 100至1000条样本。
- 1个Epoch。
- 较小Batch Size。

```bash
export RAY_ADDRESS=http://127.0.0.1:8265
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
set -a
source /etc/minio/training-data-s3.env
set +a

ray job submit \
  --address=http://127.0.0.1:8265 \
  --working-dir /data/ai/chenzhangyue/code/galatea \
  -- python scripts/train.py --config configs/train-dev.yaml
```

Smoke Test通过标准：

- Ray Job成功结束。
- GPU被正确占用和释放。
- MLflow出现新的Run。
- 参数和指标完整。
- 本地Checkpoint可以加载。
- Artifact能够通过MLflow Server上传到MinIO。

### 10.3 正式训练

```bash
ray job submit \
  --address=http://127.0.0.1:8265 \
  --working-dir /data/ai/chenzhangyue/code/galatea \
  -- python scripts/train.py --config configs/train-prod.yaml
```

提交前确认 Head 已启动并且 8265 仅对本机或受控内网开放：

```bash
ray status
curl -fsS http://127.0.0.1:8265/api/version
```

查询任务：

```bash
ray job list --address=http://127.0.0.1:8265
```

查看日志：

```bash
ray job logs <Ray-Job-ID> --address=http://127.0.0.1:8265
```

停止任务：

```bash
ray job stop <Ray-Job-ID> --address=http://127.0.0.1:8265
```

---

## 11. 第七步：训练过程记录

一次MLflow Run至少记录：

### 参数

- 数据版本。
- 模型结构。
- Batch Size。
- Learning Rate。
- Epoch数量。
- 随机种子。
- Optimizer。
- 混合精度配置。

### 指标

- Train Loss。
- Validation Loss。
- Accuracy、F1、AUC等业务指标。
- 每个Epoch耗时。
- 最佳指标及对应Epoch。

### 标签

- Git Commit。
- 数据版本。
- 运行环境。
- GPU型号。
- 任务发起人。
- 训练类型。

### Artifact

- 完整训练配置。
- 数据校验报告。
- 最佳Checkpoint。
- 最终模型。
- 评测报告。
- 混淆矩阵、曲线图。

一次模型的可复现身份：

```text
MLflow Run ID
+ Git Commit
+ Dataset Version
+ Training Config
+ Python / PyTorch环境
```

---

## 12. 第八步：Checkpoint与恢复

### 12.1 Checkpoint内容

至少保存：

```python
checkpoint = {
    "epoch": epoch,
    "model_state_dict": model.state_dict(),
    "optimizer_state_dict": optimizer.state_dict(),
    "scheduler_state_dict": scheduler.state_dict(),
    "config": config,
    "mlflow_run_id": run_id,
    "dataset_version": dataset_version,
}
```

### 12.2 保存策略

```text
每个Epoch：
  保存本地 latest.pt

指标变好：
  保存本地 best.pt

每隔N个Epoch：
  上传可恢复Checkpoint到MinIO

训练结束：
  上传best模型、final模型和配置
```

### 12.3 恢复训练

如果本地Checkpoint已经丢失，先从MLflow Artifact下载：

```python
import os
import mlflow

existing_run_id = os.environ["RESUME_RUN_ID"]

checkpoint_path = mlflow.artifacts.download_artifacts(
    run_id=existing_run_id,
    artifact_path="recovery/epoch-0002/latest.pt",
    dst_path="/data/ai/chenzhangyue/code/galatea/checkpoints/recovered",
)
```

再恢复模型和Optimizer状态：

```python
checkpoint = torch.load(
    checkpoint_path,
    map_location="cuda",
)

model.load_state_dict(checkpoint["model_state_dict"])
optimizer.load_state_dict(checkpoint["optimizer_state_dict"])

start_epoch = checkpoint["epoch"] + 1
```

恢复时必须验证：

- 数据版本没有变化。
- 模型结构没有变化。
- Optimizer配置兼容。
- Checkpoint文件校验通过。
- 原MLflow Run ID可查询。

可以继续写原Run：

```python
with mlflow.start_run(run_id=existing_run_id):
    ...
```

也可以创建新Run，并增加标签：

```python
mlflow.set_tag("resumed_from_run_id", existing_run_id)
```

推荐第二种，恢复关系更清晰。

---

## 13. 第九步：模型评测

训练完成不等于模型可以使用。

评测必须使用固定测试集：

```text
dataset-v001 / split=test
```

评测输出：

```json
{
  "run_id": "MLFLOW_RUN_ID",
  "dataset_version": "dataset-v001",
  "model_uri": "runs:/MLFLOW_RUN_ID/model",
  "metrics": {
    "accuracy": 0.932,
    "f1": 0.928,
    "latency_p95_ms": 18.4
  },
  "quality_gate": "PASSED"
}
```

模型质量门禁示例：

```yaml
quality_gate:
  min_accuracy: 0.92
  min_f1: 0.90
  max_latency_p95_ms: 25
  require_data_validation_passed: true
```

门禁规则：

```text
评测不通过 → 模型只能保留在Run中
评测通过   → 可以注册为candidate
```

---

## 14. 第十步：注册模型

### 14.1 注册到MLflow Model Registry

```python
import mlflow

run_id = "<MLflow-Run-ID>"
model_name = "demo-classifier"
model_uri = f"runs:/{run_id}/model"

model_version = mlflow.register_model(
    model_uri=model_uri,
    name=model_name,
)

print(model_version.version)
```

### 14.2 设置candidate别名

```python
from mlflow import MlflowClient

client = MlflowClient()

client.set_registered_model_alias(
    name="demo-classifier",
    alias="candidate",
    version=model_version.version,
)
```

### 14.3 晋级为champion

只有通过人工或自动评审后才能执行：

```python
client.set_registered_model_alias(
    name="demo-classifier",
    alias="champion",
    version=model_version.version,
)
```

模型消费者使用：

```text
models:/demo-classifier@champion
```

不要在业务代码中写死：

```text
models:/demo-classifier/17
```

使用别名可以在不修改业务代码的情况下切换或回滚模型。

---

## 15. 模型交付契约

最终模型交付物至少包括：

```text
模型版本
├── 模型权重
├── 模型输入输出Signature
├── 输入样例
├── 推理预处理配置
├── 标签字典
├── Python依赖
├── 数据版本
├── 训练配置
├── 评测报告
├── MLflow Run ID
└── Git Commit
```

建议记录模型输入输出：

```python
from mlflow.models import infer_signature

sample_input = input_batch[:1].cpu().numpy()
sample_output = model(input_batch[:1].cuda()).detach().cpu().numpy()

signature = infer_signature(
    sample_input,
    sample_output,
)

mlflow.pytorch.log_model(
    model,
    artifact_path="model",
    signature=signature,
    input_example=sample_input,
)
```

默认不保存完整训练数据，也不保存每个Batch的输入输出Tensor。

---

## 16. 失败处理

| 失败场景 | 处理 |
|---|---|
| 数据文件不存在 | 数据校验失败，禁止训练 |
| 数据格式损坏 | 隔离坏样本并重新生成数据版本 |
| GPU显存不足 | 降低Batch Size、启用混合精度或梯度累积 |
| 主内存不足 | 降低DataLoader Worker和预取数量 |
| Ray任务失败 | 查看Ray日志，修复后创建新Run |
| MLflow暂时不可用 | 不应继续不可追踪的正式训练 |
| MinIO上传失败 | 保留本地Checkpoint并重试上传 |
| Ray Head重启 | 从远程Checkpoint恢复训练 |
| 指标异常 | 标记Run失败，不注册模型 |

正式训练入口必须捕获异常并记录：

```python
try:
    run_training()
except Exception as error:
    mlflow.set_tag("failure_type", type(error).__name__)
    raise
```

---

## 17. 安全要求

- JupyterLab、Ray Dashboard、MLflow只监听本机或内网。
- 外部访问使用SSH隧道或带认证的HTTPS反向代理。
- MinIO使用独立账号和最小权限策略：MLflow只访问`mlflow-artifacts`，训练账号只访问`training-data`。
- MinIO密钥不写入代码、配置文件、Notebook和Git；只保存在`/etc/minio/*.env`或受控密钥系统。
- MLflow Server负责代理上传MinIO时，训练脚本不持有MinIO长期密钥。
- 不在日志中打印密钥、Token和签名URL。
- 训练代码和数据Manifest需要代码评审。

---

## 18. 测试与质量门禁

### 18.1 单元测试

- Manifest字段解析。
- 数据Split互斥。
- 模型输入输出形状。
- Checkpoint保存和加载。
- 配置字段完整性。

### 18.2 Smoke Test

- 100至1000条样本。
- 1个Epoch。
- 完整走通Ray、MLflow和MinIO。

### 18.3 恢复测试

1. 运行3个Epoch。
2. 手动停止任务。
3. 从Checkpoint恢复。
4. 确认Epoch和Optimizer状态连续。
5. 确认MLflow恢复关系可追踪。

### 18.4 模型质量门禁

- 数据校验通过。
- Smoke Test通过。
- 测试集指标达到阈值。
- 推理输入输出Signature完整。
- 模型Artifact可从MinIO读取。
- 回滚到上一champion版本经过验证。

---

## 19. 单GPU资源规则

当前服务器只有1张GPU：

```text
CPU数据预处理：可以并行
GPU正式训练：并发固定为1
第二个GPU任务：等待
```

建议：

- 为操作系统和平台服务预留12至16 GB内存。
- Ray Object Store初始设置8至12 GB。
- 为系统预留2至4个CPU核心。
- 每个正式训练任务声明`num_gpus=1`。
- 不依赖`num_gpus=0.5`隔离大型训练。
- 没有Swap时不要按可用内存满配。

如果出现多人排队、任务优先级和定时需求，再增加Prefect或Airflow；当前阶段不需要为了一个GPU引入复杂平台。

---

## 20. 完整操作清单

### 数据阶段

- [ ] 原始数据写入MinIO `datasets/raw/`。
- [ ] 生成Manifest。
- [ ] 计算文件校验值。
- [ ] 固定训练、验证、测试切分。
- [ ] 执行数据质量检查。
- [ ] 生成不可变数据版本。
- [ ] 预处理结果写入MinIO版本目录。

### 训练准备

- [ ] 训练配置进入Git。
- [ ] Git Commit已记录。
- [ ] JupyterLab单Batch验证通过。
- [ ] Smoke Test通过。
- [ ] Ray中可看到1个GPU资源。
- [ ] MLflow Server健康检查通过。
- [ ] MinIO Artifact上传测试通过。

### 正式训练

- [ ] 使用Ray Job提交。
- [ ] 任务声明`num_gpus=1`。
- [ ] MLflow记录参数、指标和标签。
- [ ] 本地持续保存`latest.pt`。
- [ ] 最佳模型保存为`best.pt`。
- [ ] 远程Checkpoint已上传MinIO。

### 模型产出

- [ ] 固定测试集评测完成。
- [ ] 质量门禁通过。
- [ ] 模型Signature和输入样例完整。
- [ ] 模型注册为`candidate`。
- [ ] 人工或自动评审完成。
- [ ] 模型晋级为`champion`。
- [ ] 上一champion版本仍可回滚。

---

## 21. 第一阶段实施顺序

建议按照下面的顺序执行，不要同时建设所有功能：

### 第1天：打通最小闭环

```text
本地小数据
→ Ray Job
→ PyTorch训练
→ MLflow参数和指标
→ 本地Checkpoint
```

### 第2天：接入本地MinIO

```text
本地MinIO数据
→ 本地缓存
→ 训练
→ MLflow Server
→ MinIO Artifact
```

### 第3天：加入质量门禁

```text
数据校验
→ Smoke Test
→ 正式训练
→ 测试集评测
→ candidate
```

### 第4天：验证恢复与回滚

```text
中断训练
→ MinIO Checkpoint恢复
→ 继续训练
→ champion切换
→ 回滚上一版本
```

---

## 22. 验收标准

平台首期完成的标准不是“页面可以打开”，而是下面的端到端链路能够重复执行：

```text
指定数据版本
→ 提交Ray Job
→ 独占一张GPU训练
→ MLflow可查看参数和曲线
→ MinIO可找到Checkpoint和模型
→ 测试集评测通过
→ 模型注册为candidate
→ 审批后切换champion
→ 能恢复训练并回滚模型
```

最终应做到：

> 任意一个生产模型，都可以从模型版本反查MLflow Run、训练配置、代码版本和数据版本，并可以使用本地 MinIO 中的 Checkpoint 重新恢复或复现。
