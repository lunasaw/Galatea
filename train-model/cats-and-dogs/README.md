# 猫狗分类：MLflow + MinIO 企业级实验追踪

本目录采用“Python 模块负责实现、Notebook 负责实验编排与展示”的结构：

```text
train-model/cats-and-dogs/
├── conda.yaml                            # 可复现的独立 Conda 环境
├── cats_dogs_pipeline.py                  # 数据、模型、训练、评测、追踪实现
├── cats_dogs_tuner.py                     # MLflow 历史感知自动调优入口
├── cats-vs-dogs-classification.ipynb      # 配置、调用、实验记录、图表展示
└── README.md
```

每个模型变体对应一个独立 MLflow Run。同一次 Notebook 执行的基础模型和增强模型共享
`run_group_id`，可以对比，但不会覆盖彼此的参数或指标。

## 1. 运行环境

使用本项目独立的 Conda 环境：

```bash
source /data/conda/etc/profile.d/conda.sh
conda env create --file train-model/cats-and-dogs/conda.yaml
conda activate cats-and-dogs-py312
python -m pip check
```

环境已经存在时，使用 `conda env update --file train-model/cats-and-dogs/conda.yaml`
同步依赖。Conda 包和 pip 包均优先使用清华 TUNA 镜像。平台服务的公共依赖由
仓库根目录的 `requirements.txt` 管理，不写入模型依赖文件。

确认 TensorFlow 与设备可用：

```bash
python -c "import tensorflow as tf; print(tf.__version__); print(tf.config.list_physical_devices())"
```

## 2. 准备数据集

Notebook 默认读取：

```text
/data/ai/chenzhangyue/code/data/cats-and-dogs/PetImages/
├── Cat/       # 12,500 张图片
└── Dog/       # 12,500 张图片
```

下载并解压：

```bash
mkdir -p /data/ai/chenzhangyue/code/data/cats-and-dogs
kaggle datasets download \
  -d shaunthesheep/microsoft-catsvsdogs-dataset \
  -p /data/ai/chenzhangyue/code/data/cats-and-dogs \
  --force
unzip -q \
  /data/ai/chenzhangyue/code/data/cats-and-dogs/microsoft-catsvsdogs-dataset.zip \
  -d /data/ai/chenzhangyue/code/data/cats-and-dogs
```

原始数据集包含两张已知坏图：`PetImages/Cat/666.jpg` 和
`PetImages/Dog/11702.jpg`。Python 模块会验证每张图片、隔离坏图并生成 24,998 条有效
Manifest 记录，不修改原始数据。

## 3. 启动 MLflow 与 MinIO

当前部署由 MLflow Tracking Server 保存元数据，并由 Server 将 Artifact 代理写入
MinIO。Notebook 客户端不加载 `/etc/minio/mlflow-s3.env`，也不持有 MinIO 长期密钥。

```bash
systemctl is-active minio.service
systemctl is-active mlflow.service
curl -fsS -H 'Host: localhost' http://127.0.0.1:5000/health
```

设置客户端地址和 Experiment：

```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
export MLFLOW_EXPERIMENT_NAME=cats-vs-dogs-enterprise
```

Notebook 默认要求 Experiment 的 Artifact Location 为 `mlflow-artifacts:/` 或 `s3://`。
仓库中的 `systemd/mlflow.service` 使用：

```text
--serve-artifacts --artifacts-destination s3://mlflow-artifacts
```

因此客户端只访问 MLflow，模型、Checkpoint、Manifest、预测和图表由 MLflow Server
写入 MinIO。如果 MLflow 不可用，或 Experiment 仍指向本地 Artifact Store，执行会在
数据处理和训练前失败，不会静默产生不可追踪模型。

### 可选：记录 MinIO 输入数据血缘

Notebook 实际读取本地 `PetImages`。如果这份本地缓存是从 MinIO 某个不可变版本完整
物化得到的，可以声明真实来源：

```bash
export CATS_DOGS_DATASET_SOURCE_URI=\
s3://training-data/datasets/raw/microsoft-cats-vs-dogs/2026-07-30/PetImages
```

只有本地内容与该对象前缀完全一致时才设置。否则保留默认 `file://` 来源，避免伪造数据
血缘。无论来源如何，模块都会计算逐文件 SHA-256、全量内容摘要和切分摘要。

## 4. 运行 Notebook

从仓库根目录启动：

```bash
cd /data/ai/chenzhangyue/code/train
jupyter lab --no-browser --allow-root --ServerApp.root_dir="$PWD"
```

打开 `train-model/cats-and-dogs/cats-vs-dogs-classification.ipynb`，选择
`Kernel -> Restart Kernel and Run All Cells`。修改 `cats_dogs_pipeline.py` 后必须重启
Kernel，避免继续使用旧模块缓存。

Notebook 只做以下工作：

1. 从 Notebook 参数单元和环境变量构造配置，并执行 MLflow/Artifact Store 门禁；
2. 调用模块准备数据，展示数据摘要和样本；
3. 调用模块执行基础模型 Run，展示指标、曲线、预测和 Grad-CAM；
4. 调用模块执行增强模型 Run，展示指标、曲线和预测；
5. 按当前 `run_group_id` 查询并对比两个 MLflow Run。

Notebook 顶部带 `parameters` 标签的代码单元可直接调整：

```python
EPOCHS = None  # 使用环境变量；也可以直接改成 10
RUN_AUTO_TUNING = False
TUNER_EPOCHS = 8
TUNER_MAX_TRIALS = 8
TUNER_TARGET_VAL_ACCURACY = 0.95
```

`EPOCHS` 同时控制基础模型和增强模型的最大训练轮数。`None` 表示沿用
`CATS_DOGS_EPOCHS`（默认 `1`）；改为 `10` 后重新执行 Notebook 即可训练最多 10 个
Epoch，Early Stopping 仍可能提前结束。只有明确把
`RUN_AUTO_TUNING` 改为 `True`，Notebook 最后的自动调优单元才会运行。

## 5. 参数与快速验证

第一次运行保持一个 Epoch，完整走通数据、训练、MLflow 与 Artifact 回读：

```bash
export CATS_DOGS_EPOCHS=1
```

确认成功后再设置正式 Epoch。该变量同时控制两个模型，Early Stopping 可能提前结束：

```bash
export CATS_DOGS_EPOCHS=10
```

质量门禁默认要求测试准确率至少为 `0.80`，可按审批后的基线调整：

```bash
export CATS_DOGS_MIN_TEST_ACCURACY=0.85
```

未通过门禁的 Run 仍会完整保留用于审计，但 `quality_gate.passed=false`，不得进入发布
流程。Notebook 故意不自动注册或提升模型，防止探索性执行修改生产 Model Registry。

可用环境变量：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `MLFLOW_TRACKING_URI` | `http://127.0.0.1:5000` | Tracking Server 地址 |
| `MLFLOW_EXPERIMENT_NAME` | `cats-vs-dogs-enterprise` | Experiment 名称 |
| `CATS_DOGS_EPOCHS` | `1` | 每个模型最多 Epoch 数 |
| `CATS_DOGS_MIN_TEST_ACCURACY` | `0.80` | 测试准确率门禁 |
| `CATS_DOGS_DATA_DIR` | 固定数据目录 | 本地数据缓存根目录 |
| `CATS_DOGS_DATASET_SOURCE_URI` | 本地 `file://` | 真实、不可变的数据来源 URI |
| `CATS_DOGS_DATASET_VERSION` | 内容摘要 | 经审批的外部数据版本号 |
| `MLFLOW_RUN_GROUP_ID` | 每次自动生成 | 显式合并多个 Run 时使用 |

不要长期固定 `MLFLOW_RUN_GROUP_ID`，除非多个任务确实属于同一次受控实验。

## 6. 基于 MLflow 历史结果自动调优

自动调优器只复用与当前数据内容摘要和切分摘要完全一致的成功 Run。当前历史中只有两个
单 Epoch 结果时，它先把较好的增强模型作为暖启动证据，再探索 EfficientNetB0、
MobileNetV2 和自定义 CNN。积累至少四个可比结果后，调优器使用随机森林 UCB 代理模型
在尚未尝试的参数中选择下一组候选。

调优 Trial 仅记录验证集目标 `best_val_accuracy`，不会查看测试集。搜索停止后，最佳配置
会从头重训为一个 `tuning-champion` Run，此时才执行一次测试集评估并记录完整 MLflow
模型。这能避免用测试指标反复选参造成数据泄漏。

先查看历史基线和下一组候选，不启动训练：

```bash
python train-model/cats-and-dogs/cats_dogs_tuner.py --plan-only
```

启动调优：

```bash
export CATS_DOGS_TUNER_EPOCHS=8
export CATS_DOGS_TUNER_MAX_TRIALS=8
export CATS_DOGS_TUNER_TARGET_VAL_ACCURACY=0.95
python train-model/cats-and-dogs/cats_dogs_tuner.py
```

可配置项：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `CATS_DOGS_TUNER_EPOCHS` | `8` | 每个 Trial 和 Champion 的最大 Epoch |
| `CATS_DOGS_TUNER_MAX_TRIALS` | `8` | 同一 Study 的 Trial 总预算，失败也占预算 |
| `CATS_DOGS_TUNER_TARGET_VAL_ACCURACY` | `0.95` | 达到即停止的验证准确率目标 |
| `CATS_DOGS_TUNER_PATIENCE` | `3` | 连续无显著改进后的停止次数 |
| `CATS_DOGS_TUNER_MIN_IMPROVEMENT` | `0.002` | 计为有效改进的最小验证准确率增量 |
| `CATS_DOGS_TUNER_ARCHITECTURES` | 三种模型 | 逗号分隔的候选架构 |
| `CATS_DOGS_TUNER_PRETRAINED_WEIGHTS` | `imagenet` | `imagenet` 或 `none` |
| `CATS_DOGS_TUNER_STUDY_NAME` | 数据版本派生 | 显式恢复同一个调优 Study |

ImageNet 权重必须已在 Keras 缓存中，或训练节点能够在首次执行时下载。模型效果受数据、
预算、硬件和目标定义影响；`0.95` 是可配置的工程停止目标，不代表也不保证达到公开榜单
意义上的 SOTA。调优器不会自动注册或提升 Champion，仍需通过质量门禁和人工审批。

## 7. 每个 Run 的记录内容

| 类别 | 内容 |
| --- | --- |
| 输入 | 来源 URI、训练/验证/测试 Dataset Input、Manifest、内容与切分 SHA-256 |
| 参数 | 模型结构、Optimizer、Batch Size、Learning Rate、Epoch、Seed、增强策略 |
| 时序指标 | Train/Validation Loss 与 Accuracy、Learning Rate、Epoch 耗时、系统资源 |
| 测试指标 | Loss、Accuracy、Precision、Recall、F1、ROC AUC、质量门禁 |
| 输出 | 逐样本预测、评测报告、训练曲线、混淆矩阵、最佳 Checkpoint |
| 模型 | TensorFlow 模型、Signature、Input Example、依赖、预处理和标签元数据 |
| 审计 | Git 状态、模块和 Notebook 源码、运行环境、失败阶段、Artifact 回读结果 |

成功 Run 必须具有 `artifact.roundtrip_verified=true`。这表示客户端已经通过 MLflow
下载并核对刚写入的验证对象；结合远程 Artifact Store 门禁，可确认 MinIO 输出路径具备
基本可恢复性。

## 8. 常见问题

- `MLflow is unavailable`：启动 `minio.service` 和 `mlflow.service`，检查 `/health`
  后重试。正式训练不会在追踪服务不可用时继续。
- `Experiment Artifact Store is not remote`：当前 Experiment 是旧的本地 Artifact
  Location。确认服务参数后创建新 Experiment；修改服务不会迁移旧 Experiment。
- `FileNotFoundError`：确认目录名大小写为 `PetImages/Cat` 和 `PetImages/Dog`。
- Run 为 `FAILED`：查看 `failure.type`、`failure.phase`、MLflow Server 日志和 MinIO
  日志，不要手工把失败 Run 改为成功。
- GPU 不可用：CPU 仍能执行，但完整训练明显更慢。先用 `CATS_DOGS_EPOCHS=1` 验证。
- 修改 `.py` 后 Notebook 行为未变化：重启 Kernel，清除 Python 模块缓存后重新运行。
