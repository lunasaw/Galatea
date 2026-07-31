# AI 训练一体化平台

本仓库是一套面向多项目、多模型和多框架的训练一体化平台，而不是单一模型工程。
平台以 **JupyterLab + Ray + MLflow + MinIO** 为核心，把交互开发、数据版本、分布式执行、
实验追踪、模型管理和 Artifact 持久化连接成可重复、可审计、可恢复的训练流程。

当前仓库中的 Cats vs Dogs 只是 TensorFlow/Keras 示例工作负载；平台同样可承载 PyTorch、
scikit-learn、XGBoost/LightGBM、Ray Train 及其他能够接入 MLflow 的训练项目。
项目可以覆盖分类、回归、检测、分割、排序、推荐、时序预测和大模型微调等任务，并在同一
Experiment 中管理多个基线、模型结构和参数方案。

## 平台目标

- **统一开发入口**：通过 JupyterLab 完成数据探索、Notebook 实验和小规模验证。
- **统一执行入口**：通过 Ray Jobs、Ray Core 或 Ray Train 调度 CPU/GPU 训练任务。
- **统一实验记录**：通过 MLflow Tracking API 保存参数、指标、标签、数据血缘和运行状态。
- **统一模型治理**：通过 MLflow Logged Models、Model Registry、质量门禁和别名管理候选模型。
- **统一对象存储**：通过 MinIO 保存数据版本、Checkpoint、模型、预测结果和可视化 Artifact。
- **统一运维方式**：通过 systemd、健康检查、受控环境文件和备份流程管理平台服务。

## 组件职责

| 组件 | 负责 | 不负责 |
| --- | --- | --- |
| JupyterLab | 数据探索、交互开发、Notebook、小样本验证 | 长时间正式训练的可靠调度 |
| Ray Jobs / Core / Train | 任务提交、资源调度、分布式执行、失败重试 | 保存实验历史和模型版本 |
| TensorFlow / PyTorch / 其他框架 | 模型、Loss、Optimizer、训练与评测逻辑 | 集群调度和平台治理 |
| MLflow Tracking | Run、参数、指标、Tag、Dataset Input、Artifact 元数据 | 分配 GPU 或保存原始业务数据 |
| MLflow Model Registry | 模型版本、说明、别名和晋级关系 | 训练任务执行 |
| MinIO | 训练数据、Checkpoint、模型和 Artifact 的 S3 兼容持久化 | 训练逻辑和实验选择 |
| systemd | JupyterLab、MLflow、MinIO 的服务生命周期 | Ray 训练业务逻辑 |

一句话概括：

> JupyterLab 负责开发，Ray 负责执行，训练框架负责计算，MLflow 负责记录和治理，
> MinIO 负责长期保存。

## 总体架构

```text
算法工程师 / 平台工程师
          |
          v
JupyterLab：探索、开发、配置、Smoke Test
          |
          v
数据校验与版本化 <----------------------- MinIO training-data
          |
          v
Ray Job / Ray Train：CPU、GPU、分布式执行
          |
          +--> TensorFlow / PyTorch / sklearn / Boosting
          |
          +--> 参数、指标、Dataset Input、日志 ----> MLflow Tracking API
          |
          +--> Checkpoint、模型、报告 ------------> MLflow Artifact API
                                                        |
                                                        v
                                              MinIO mlflow-artifacts
                                                        |
                                                        v
                              评测与质量门禁 -> Model Registry
                                                        |
                                               candidate / champion
```

训练客户端只通过 MLflow Tracking/Artifact API 访问平台，不直接依赖 MLflow 后端数据库，
也不需要持有 MLflow Artifact Bucket 的长期 MinIO 密钥。这样 MLflow 可以部署在当前节点，
也可以迁移到其他节点而不改变训练项目的元数据访问方式。

## 通用训练生命周期

每个训练项目都应遵循同一条主链路：

1. **数据进入**：原始数据以不可变版本写入 MinIO，并生成 Manifest、内容摘要和切分摘要。
2. **实验开发**：在 JupyterLab 中完成数据检查、单 Batch 和少量 Epoch 验证。
3. **任务提交**：把正式训练封装为可配置入口，通过 Ray 提交，而不是依赖 Notebook Kernel 长期运行。
4. **实验追踪**：每次训练创建独立 MLflow Run，记录代码、数据、配置、环境、指标和 Artifact。
5. **验证集选参**：Trial 只使用训练集和验证集；不得用测试集反复选择超参数。
6. **Champion 重训**：选定配置后从干净状态重训，再执行一次最终测试集评测。
7. **质量门禁**：检查主指标、分组指标、Artifact 可恢复性、数据血缘和代码可复现性。
8. **模型晋级**：人工审核后注册模型并更新 `candidate`、`champion` 等别名。

一次可审计训练至少应回答：使用哪个数据和切分、哪份配置和代码、由哪个 Ray Job 执行、
对应哪个 MLflow Run、指标为何满足门禁，以及模型和 Checkpoint 从哪里恢复。

## 当前部署形态

当前实现是一套单节点训练平台基线：

- Conda 环境：`/data/conda/envs/attend-ray-py312`
- 工作目录：`/data/ai/chenzhangyue/code/train`
- JupyterLab、MLflow、MinIO：由 systemd 管理
- Ray：已安装，Head 在提交训练前按需启动，当前没有仓库内 systemd Unit
- MLflow Backend Store：本机 `platform-data/mlflow/mlflow.db`
- MLflow Artifact Store：由 Tracking Server 代理写入 MinIO `s3://mlflow-artifacts`
- 运行数据：`platform-data/`，不进入 Git

Backend Store 是服务端实现细节。Notebook、Ray Worker、分析脚本和其他客户端必须使用
MLflow API，不应读取 `mlflow.db`。当前 MinIO 是单机单盘部署，不提供主机故障容错；
数据库、对象数据和密钥需要成组备份到其他主机或独立存储。

## 快速开始

### 1. 激活环境

```bash
source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312
cd /data/ai/chenzhangyue/code/train
```

### 2. 检查平台服务

```bash
systemctl is-active minio.service
systemctl is-active mlflow.service
systemctl is-active jupyterlab.service

curl -fsS -H 'Host: localhost' http://127.0.0.1:5000/health
curl -fsS http://127.0.0.1:9000/minio/health/live
ray status
```

如果 Ray Head 尚未启动，按照 [Ray 部署指南](doc/ray-start.md) 配置节点 IP、CPU、GPU、
Object Store Memory 和 Dashboard 后再提交正式任务。

### 3. 配置 MLflow 客户端

```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
export MLFLOW_EXPERIMENT_NAME="project-experiment-name"
```

远程 MLflow 使用对应的 HTTPS Tracking URI 和受控认证配置，不复制服务端数据库或 MinIO
长期密钥到训练项目。

### 4. 本地启动 JupyterLab（仅在未使用 systemd 时）

```bash
jupyter lab --no-browser --allow-root --ServerApp.root_dir="$PWD"
```

## 服务与端口

| 服务 | 默认端口 | 当前管理方式 | 说明 |
| --- | ---: | --- | --- |
| JupyterLab | 8888 | systemd | 交互开发入口，当前配置带 code-server 代理前缀 |
| MLflow Tracking | 5000 | systemd | Run、模型和 Artifact API |
| MinIO API | 9000 | systemd | S3 兼容对象接口 |
| MinIO Console | 9001 | systemd | 对象存储管理界面 |
| Ray Dashboard | 8265 | 按需启动 | Ray 状态、任务和资源观察 |

`systemd/` 中的用户、路径、监听地址、代理前缀和允许域名都是当前主机配置。安装前先验证：

```bash
systemd-analyze verify systemd/*.service
```

监听 `0.0.0.0` 的服务必须由防火墙、认证代理或受控内网保护，不能直接暴露到公网。

## 接入新的训练项目

新训练项目统一放在 `train-model/<project-name>/` 下；一个项目可以包含多个模型、算法和
参数变体。推荐结构如下：

```text
train-model/<project-name>/
├── README.md                 # 数据、目标指标、运行方式和门禁
├── notebooks/               # 探索与 Smoke Test
├── configs/                 # 开发、调优和正式训练配置
├── src/                     # 数据、模型、训练、评测和注册逻辑
├── scripts/                 # validate/train/evaluate/promote 入口
└── tests/                   # 数据、模型、选择逻辑和 Smoke Test
```

现有项目可以保留较轻量的平铺结构，但必须满足以下平台契约：

- 配置与训练代码分离，正式入口可以脱离 Notebook 执行。
- 数据来源、内容摘要、切分摘要和预处理版本进入 MLflow。
- 每个 Run 记录 `project`、模型变体、代码版本、Seed、资源和完整超参数。
- 指标区分训练、验证和最终测试语义，并明确主目标的优化方向。
- Checkpoint、模型、预测和报告通过 MLflow Artifact API 持久化。
- Ray 重试不会重复覆盖其他 Run，分布式训练只由一个权威进程写 MLflow。
- 探索任务不自动修改生产模型别名。
- 凭据、数据集、Checkpoint、生成模型和 `.ipynb_checkpoints/` 不进入 Git。

正式项目应优先采用参数化脚本提交 Ray Job。Notebook 负责调用和展示，不承载不可恢复的
长期训练状态。

## MLflow 实验分析与通用调优

工程内置通用 Skill：

```text
.codex/skills/mlflow-optimize-models/
```

它通过本地或远程 MLflow Tracking API 分析可比 Run、验证目标、参数覆盖、学习曲线、
泛化差距、质量门禁和 Artifact 状态，再把证据映射为参数或代码优化建议。它不预设模型
类型、训练框架或 `accuracy` 指标；目标可以是需要最大化的 AUC、F1、mAP，也可以是需要
最小化的 Loss、RMSE、MAE 等项目指标。它不会读取 `mlflow.db`，也不会默认使用测试指标选参。

直接运行分析脚本：

```bash
export MLFLOW_OBJECTIVE_METRIC="project_validation_metric"

python .codex/skills/mlflow-optimize-models/scripts/analyze_experiment.py \
  --tracking-uri "$MLFLOW_TRACKING_URI" \
  --experiment "$MLFLOW_EXPERIMENT_NAME" \
  --objective-metric "$MLFLOW_OBJECTIVE_METRIC" \
  --objective-mode max \
  --repo-root "$PWD"
```

按指标语义选择 `--objective-mode max` 或 `--objective-mode min`。不同任务、数据版本、切分
策略或指标定义的 Run 不应直接排名；先筛选可比 Run，再判断当前最佳结果和下一轮搜索空间。

也可以在 Codex 中使用 `$mlflow-optimize-models`，针对任意 Experiment 生成分析、调优和
代码修改方案。分析或代码优化请求本身不会自动启动 CPU/GPU 训练。

## 示例工作负载

### Cats vs Dogs 图像分类

[`train-model/cats-and-dogs/`](train-model/cats-and-dogs/) 是当前端到端示例，展示：

- TensorFlow/Keras 基础 CNN、数据增强和迁移学习调优空间；
- 内容寻址的数据版本与确定性训练/验证/测试切分；
- MLflow Run、Dataset Input、模型、Checkpoint、预测、图表和环境记录；
- MinIO Artifact 回读验证；
- 验证集驱动的自动调优与 Champion 重训。

示例用于验证平台能力，不定义平台支持的模型类型或框架边界。

## 仓库结构

```text
train/
├── .codex/skills/                 # 工程级 Codex Skills
├── train-model/                   # 多个训练项目和模型工作负载
│   └── cats-and-dogs/             # 当前 TensorFlow/Keras 示例
├── tests/                         # 平台示例和调优逻辑测试
├── doc/                           # 部署、运维和端到端实施手册
├── systemd/                       # JupyterLab、MLflow、MinIO Unit
├── platform-data/                 # 数据库、对象和运行状态（Git 忽略）
├── AGENTS.md                      # 仓库开发约定
├── CLAUDE.md                      # Claude Code 使用说明
└── README.md
```

## 验证

运行调优器单元测试：

```bash
/data/conda/envs/attend-ray-py312/bin/python -m unittest \
  tests/test_cats_dogs_tuner.py
```

Notebook Smoke Test 必须把 `EPOCHS` 设为 `1`、把 `RUN_AUTO_TUNING` 设为 `False`，并把
执行结果写到 `/tmp`，不要覆盖源 Notebook：

```bash
jupyter nbconvert --execute --to notebook \
  train-model/cats-and-dogs/cats-vs-dogs-classification.ipynb \
  --output-dir /tmp --output cats-vs-dogs-smoke.ipynb
```

## 文档

- [JupyterLab 安装与运维](doc/jupyter-start.md)
- [MLflow Tracking Server 安装与运维](doc/mlflow-start.md)
- [MinIO 安装与运维](doc/minio-start.md)
- [Ray 安装、启动与任务提交](doc/ray-start.md)
- [code-server 代理配置](doc/code-server-proxy.md)
- [数据到训练到模型的端到端实施手册](doc/data-to-training-to-model-imp-guide.md)
- [仓库开发规范](AGENTS.md)

## 安全与持久化

- 不提交 Token、对象存储密钥、环境文件或包含凭据的 Notebook 输出。
- `platform-data/` 只保存运行状态，不作为源码分发；MLflow 元数据与 MinIO 对象必须一致备份。
- 训练客户端只获取完成任务所需的最小权限，MinIO 长期密钥保留在受保护的服务端环境文件。
- 未认证服务优先绑定回环地址；必须监听所有网卡时，使用防火墙和认证代理。
- 数据版本不可覆盖，模型必须能够通过 MLflow Run ID 追溯到代码、配置和数据。
- 当前单节点部署不等于高可用；生产化前需要补充异机备份、恢复演练、监控和容量告警。
