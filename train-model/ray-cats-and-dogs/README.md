# Ray + MLflow 猫狗分类训练

本项目把 [`../cats-and-dogs/`](../cats-and-dogs/) 中的 Notebook 训练流程重构为可恢复、
可审计、可由 Ray Job 提交的正式训练项目。原项目保持不变；这里复用它的 TensorFlow CNN、
确定性数据切分、MLflow 数据血缘和测试集门禁思想，并把配置、实现、入口与测试分开。

当前阶段使用 Ray Train 做单 Worker 或多 Worker 数据并行训练。MLflow 的权威写入者始终
只有 Driver/Train Controller：Worker 只做计算、向 Ray 上报指标和 Checkpoint，不创建、
结束或直接写入 MLflow Run。

```text
scripts/train.py
  ├── 校验 YAML、MLflow、数据摘要与幂等键
  ├── 创建一个 MLflow Run
  └── Ray Train Controller
      ├── Worker 0 ─┐
      ├── Worker 1 ─┼── 全局验证指标 + 可恢复 Checkpoint
      └── ...       ┘
              │
              └── Driver 写入 MLflow/MinIO，Champion 才读取一次测试集
```

## 项目结构

```text
train-model/ray-cats-and-dogs/
├── README.md
├── conda.yaml
├── pyproject.toml
├── configs/
│   ├── baseline.yaml       # 单 Worker、验证集 Trial
│   ├── smoke.yaml          # 1 Epoch 链路检查，不读取测试集
│   ├── distributed.yaml    # 2 Worker Trial
│   └── champion.yaml       # 干净重训并执行一次最终测试
├── scripts/train.py        # 正式参数化入口
├── notebooks/
│   └── smoke-run-guide.ipynb  # 配置、计划、提交与结果查看
├── src/ray_cats_dogs/
│   ├── config.py
│   ├── data.py
│   ├── input_pipeline.py
│   ├── models/
│   ├── runtime.py
│   ├── worker.py
│   ├── tracking.py
│   ├── evaluate.py
│   └── train.py
└── tests/
```

Notebook 不是正式训练依赖，只用于检查、提交和查看结果；长任务仍由 `scripts/train.py`
或 Ray Job 执行。

## 1. 环境

从仓库根目录创建项目环境：

```bash
source /data/conda/etc/profile.d/conda.sh
conda env create --file train-model/ray-cats-and-dogs/conda.yaml
conda activate ray-cats-and-dogs-py312
python -m pip install -e train-model/ray-cats-and-dogs
python -m ipykernel install --user \
  --name ray-cats-and-dogs-py312 \
  --display-name "Python (ray-cats-and-dogs)"
python -m pip check
```

共享平台环境已经包含相同版本的 Ray、MLflow 和 TensorFlow 时，也可以先用它做配置检查：

```bash
source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312
```

## 2. 平台与数据前置检查

正式训练前确认 Ray、MLflow 和 MinIO 正常：

```bash
systemctl is-active minio.service mlflow.service
curl -fsS -H 'Host: localhost' http://127.0.0.1:5000/health
curl -fsS http://127.0.0.1:9000/minio/health/live
ray status
```

默认数据目录与原项目相同：

```text
/data/ai/chenzhangyue/code/data/cats-and-dogs/PetImages/
├── Cat/
└── Dog/
```

程序只读原图，逐文件验证并计算 SHA-256。Manifest 写入被 Git 忽略的
`platform-data/ray-cats-and-dogs/manifests/`，随后通过 MLflow Artifact API 保存。原始
数据集的两张已知坏图会被记录并排除，不会被删除或修改。

如果训练节点读取的是某个已校验的 MinIO 数据快照，可声明真实来源 URI；不要把本地缓存
伪装成未核对的对象版本：

```bash
export CATS_DOGS_DATASET_SOURCE_URI=\
s3://training-data/datasets/raw/microsoft-cats-vs-dogs/2026-07-30/PetImages
```

## 3. 先做不训练的检查

只校验配置继承、类型、目标指标和资源声明，不访问数据或服务：

```bash
python train-model/ray-cats-and-dogs/scripts/train.py \
  --config train-model/ray-cats-and-dogs/configs/smoke.yaml \
  --check-config
```

完整只读计划会访问 MLflow Tracking API、验证全量数据并计算幂等键，但不会创建
Experiment/Run、启动 Ray Worker 或训练模型。Experiment 尚不存在时会报告
`experiment_exists=false`，第一次正式训练再创建：

```bash
python train-model/ray-cats-and-dogs/scripts/train.py \
  --config train-model/ray-cats-and-dogs/configs/smoke.yaml \
  --plan
```

计划中的 `will_train=false` 表示相同数据、切分、代码、配置、Seed 和角色已经有一个成功
且 Artifact 回读通过的 Run。正式命令默认直接复用该 Run，不重复消耗算力。

也可以打开 [`notebooks/smoke-run-guide.ipynb`](notebooks/smoke-run-guide.ipynb)。Notebook
默认只执行平台检查、配置检查和只读计划；只有手动将参数单元中的 `RUN_SMOKE` 改为
`True` 才会通过 Ray Jobs API 提交 `scripts/train.py`。Notebook 不包含数据、模型或训练实现，
关闭 Kernel 不会改变已经提交的 Ray Job。

## 4. Smoke 与正式 Trial

Smoke Run 只训练 1 Epoch，仍然写入独立 MLflow Run 和可恢复 Checkpoint，但不读取测试集：

```bash
python train-model/ray-cats-and-dogs/scripts/train.py \
  --config train-model/ray-cats-and-dogs/configs/smoke.yaml
```

单 Worker 基线 Trial：

```bash
python train-model/ray-cats-and-dogs/scripts/train.py \
  --config train-model/ray-cats-and-dogs/configs/baseline.yaml
```

两 Worker Trial：

```bash
python train-model/ray-cats-and-dogs/scripts/train.py \
  --config train-model/ray-cats-and-dogs/configs/distributed.yaml
```

可以用严格的现有键覆盖做小范围实验，覆盖结果会进入配置摘要和 MLflow 参数：

```bash
python train-model/ray-cats-and-dogs/scripts/train.py \
  --config train-model/ray-cats-and-dogs/configs/baseline.yaml \
  --set training.learning_rate=0.0003 \
  --set model.dropout=0.4
```

不要把 `--force` 当作常规重试方式。它只用于明确需要在同一幂等身份下重新留一个 Attempt
的情况；普通 Worker 故障由 `ray.max_failures` 和最近 Checkpoint 自动恢复。

## 5. 通过 Ray Job 提交

正式或长时间训练建议交给 Ray Jobs API，而不是依赖终端或 Notebook Kernel：

```bash
ray job submit \
  --address http://127.0.0.1:8265 \
  --runtime-env-json='{"working_dir":"train-model/ray-cats-and-dogs","py_modules":["train-model/ray-cats-and-dogs/src/ray_cats_dogs"],"excludes":["notebooks/**","tests/**"]}' \
  -- python scripts/train.py --config configs/smoke.yaml
```

Notebook 提交使用同一个 `build_runtime_env(PROJECT_ROOT)`：`working_dir` 上传配置和入口，
`py_modules` 把真正的包目录 `src/ray_cats_dogs` 加入 Python 导入路径。Driver 会把上传后
生成的 Runtime URI 显式传给 Ray Train Worker。Ray 2.53 的 Train Controller 只保留内部
环境变量，因此 Worker 函数和 MLflow Callback 会按值序列化给 Controller；不依赖训练
节点预先执行 `pip install -e`，也不依赖所有节点共享源码绝对路径。

入口会同时记录 Ray Job ID、MLflow Run ID、数据/切分/代码摘要、完整资源与超参数，以及 Ray
Checkpoint URI。`configs/*.yaml` 默认将 Ray 执行态保存在
`platform-data/ray-results/`。跨主机集群必须让所有节点挂载同一绝对路径，或把
`ray.storage_path` 改成集群已配置凭据的共享 URI，例如 S3；训练客户端不应读取 MLflow
服务端的 MinIO 文件系统。

## 6. Champion 与最终测试

Trial 的唯一主目标是 `val_accuracy`，方向为 `max`。测试集不参与搜索、Early Stopping、
Checkpoint 选择或 Trial 排序。根据兼容数据版本、切分、预处理和验证协议选定配置后，
把审批后的参数写入或覆盖 `champion.yaml`，再从干净状态重训：

```bash
python train-model/ray-cats-and-dogs/scripts/train.py \
  --config train-model/ray-cats-and-dogs/configs/champion.yaml
```

只有 `role: champion` 可以设置 `evaluate_test: true` 和 `log_model: true`。Champion 会读取
验证集最佳 Checkpoint，对固定测试集评估一次，记录 Accuracy、Precision、Recall、F1、
ROC AUC、预测摘要和质量门禁，并生成 MLflow Logged Model。未通过门禁的 Run 仍完整保留，
但不会注册、设置或修改任何生产模型 Alias；发布必须另行人工审查和显式执行。

这套边界表示“验证集最佳的已观察配置”，不表示有限参数试验已经找到全局最优模型。

## 7. 资源和确定性说明

- `ray.num_workers`、每 Worker CPU/GPU/内存、Placement、重试次数及评测资源均在 YAML 明确声明。
- 每个 Worker 接收同一份模型、数据摘要、配置、Seed、MLflow Run ID 和幂等键。
- 训练集和验证集必须能被 Worker 数整除，避免 Ray `equal` Shard 静默丢弃余数样本。
- 文件切分和本地 Shuffle 使用固定 Seed；多 Worker 浮点归约、GPU Kernel 和部分图片算子仍可能产生细微非确定性，Run 不宣称逐位复现。
- 每个 Epoch 的 Ray Checkpoint 同时包含当前模型、验证集最佳模型和训练状态；失败恢复不会覆盖其他 MLflow Attempt。
- 最佳 Checkpoint 会通过 MLflow Artifact API 下载并核对 SHA-256；Logged Model 也会回读
  `MLmodel` 描述文件。任何回读失败都会让 Run 标记为失败。

## 8. 测试

项目测试不连接 MLflow、不启动 Ray 集群，也不执行训练：

```bash
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s train-model/ray-cats-and-dogs/tests -p 'test_*.py'
```

测试覆盖配置继承与越权测试集保护、确定性 Manifest、坏图隔离、模型输入输出，以及 Ray
Controller 只把 Rank 0 指标写入既有 MLflow Run 的所有权规则。

## 9. Runtime Env 排错

如果 Driver 能导入 `ray_cats_dogs`，但日志中的 `TrainController` 或 Worker 报
`ModuleNotFoundError: No module named 'ray_cats_dogs'`，先确认提交参数同时包含项目
`working_dir` 和 `src` 的 `py_modules`。项目会在开始训练前检查 Worker Runtime Env；缺少
`py_modules` 会直接给出配置错误，而不是创建 Worker 后再以 `ActorDiedError` 失败。

不要用只在当前节点执行 `pip install -e` 的方式掩盖问题；多节点任务应通过 Runtime Env
分发源码，依赖版本则由项目 `conda.yaml` 统一提供。
