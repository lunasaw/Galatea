# Galatea：当前 Agent 与训练平台架构

> 状态：当前实现；范围：多项目、多模型、多框架训练平台；正式参考工作负载：
> PyTorch CUDA 13 + Ray Train + MLflow 的 Cats vs Dogs 项目。

本文描述仓库当前已经落地的 Agent 能力、训练项目契约、Ray/MLflow 所有权和部署边界。
规划中的能力会单独标记，不再把尚未存在的 Skill、Tool 或自治循环写成现有组件。

## 1. 架构目标与边界

Galatea 不是某个 Notebook、图像分类器或自动调参脚本，而是一套把交互开发、分布式执行、
实验追踪、Artifact 持久化和模型治理连接起来的训练平台。平台必须能够承载分类、回归、
检测、分割、排序、推荐、预测和模型微调，也不能把 PyTorch、Accuracy、单机 GPU 或
Cats vs Dogs 写死在共享能力中。

当前架构把职责分成三层：

| 层级 | 负责 | 不负责 |
| --- | --- | --- |
| 平台层 | JupyterLab、Ray、MLflow、MinIO、systemd、通用 Agent Skills | 某个模型的网络结构、指标名或数据切分 |
| 工作负载层 | 项目配置、数据、模型、训练、评测、Checkpoint 和项目测试 | 平台服务的部署和其他项目的业务逻辑 |
| Agent 层 | 读取仓库状态、选择 Skill、分析证据、修改代码并执行获授权的动作 | 绕过审批启动昂贵训练或修改生产模型 Alias |

正式训练的交付物不只是一个模型文件，而是一组可以复核的证据：

- MLflow Run ID、Ray Job ID 和 Checkpoint URI；
- 数据内容、Manifest、切分和预处理身份；
- 解析后的完整配置、代码摘要、Git 状态和环境版本；
- 训练与验证 Metric History；
- 最佳模型选择依据、最终评测报告和质量门禁；
- Artifact 回读、Checkpoint 恢复和 Logged Model 可加载证据；
- 是否读取最终测试集、是否执行 Registry 晋级等治理状态。

## 2. 当前平台拓扑

```text
开发者 / Codex Agent
        |
        +--> JupyterLab：探索、配置、短 Smoke、提交和查看结果
        |
        +--> 参数化 CLI / Ray Jobs API
                         |
                         v
                 Ray Job Driver
                   |          |
                   |          +--> MLflow Tracking API
                   |                   |
                   |                   +--> Run / Trace / Metric / Tag
                   |                   +--> Artifact Proxy
                   |                              |
                   |                              v
                   |                         MinIO Bucket
                   |
                   v
        Ray Data CPU Task Pool
        并行解码和缩放 -> uint8 Tensor Object Store Cache
                   |
                   v
              Ray Train Controller
                   |
                   +--> Worker 0：预取、GPU 增强、计算、Checkpoint、Ray Report
                   +--> Worker 1..N：预取、GPU 增强、计算、分布式归约
                   |
                   +--> Ray Checkpoint Storage
                              |
                              v
                 Driver 最终评测、Artifact 回读和门禁
                              |
                              v
                     Logged Model / 人工晋级
```

各组件当前职责如下：

| 组件 | 当前职责 |
| --- | --- |
| JupyterLab | 数据探索、Notebook 开发、配置检查、Job 提交和结果展示 |
| Ray Jobs | 让正式入口脱离 Notebook Kernel 和终端生命周期 |
| Ray Data | CPU 并行图片解码、缩放、Shuffle、Block 调度、Object Store 缓存和 Batch 预取 |
| Ray Train | Worker 调度、数据分片、分布式训练、失败重试和 Checkpoint |
| PyTorch | 当前参考工作负载的模型、Loss、Optimizer 和分布式计算 |
| MLflow Tracking | Experiment、Run、Trace、参数、指标、Tag、Dataset Input 和系统指标 |
| MLflow Artifact API | 配置、Manifest、源码、Checkpoint、报告、预测和 Logged Model 的统一访问入口 |
| MinIO | MLflow 代理后的 S3 兼容 Artifact 持久化；也可承载不可变训练数据快照 |
| systemd | JupyterLab、MLflow 和 MinIO 的本机服务生命周期 |

当前部署是单节点基线：

- 共享平台环境为 `/data/conda/envs/attend-ray-py312`；
- 仓库和服务工作目录为 `/data/ai/chenzhangyue/code/galatea`；
- JupyterLab、MLflow 和 MinIO 由 `systemd/` 中的 Unit 管理；
- Ray Head 按需启动，仓库当前没有 Ray systemd Unit；
- MLflow Backend Store 位于 `platform-data/mlflow/mlflow.db`；
- MLflow 使用 `--serve-artifacts` 代理 MinIO 中的 `s3://mlflow-artifacts`；
- Ray 本地执行状态默认写入 `platform-data/ray-results/`；
- `platform-data/` 是被 Git 忽略的运行状态，不是源码或客户端集成接口。

训练客户端只能通过 MLflow Tracking、Artifact 和 Model Registry API 访问 MLflow。客户端
不得打开 `mlflow.db`，也不应读取 MLflow 服务端的 MinIO 数据目录或持有长期对象存储密钥。

## 3. 仓库与项目边界

当前主要目录结构为：

```text
galatea/
├── .codex/skills/                  # 仓库级 Agent 能力
├── train-model/
│   ├── cats-and-dogs/              # 兼容的平铺式 PyTorch Notebook 工作负载
│   └── ray-cats-and-dogs/          # 当前正式 Ray Train 参考项目
├── tests/                          # 仅保留平台级和跨项目测试
├── doc/                            # 部署、运维和架构文档
├── systemd/                        # JupyterLab、MLflow、MinIO Unit
├── platform-data/                  # 数据库、对象、Manifest 和运行状态，Git 忽略
├── requirements.txt                # 共享平台服务依赖
├── AGENTS.md                       # 仓库训练与治理契约
└── README.md                       # 平台入口
```

成熟训练项目放在 `train-model/<project-name>/`，推荐并由
[`model-project-structure`](../.codex/skills/model-project-structure/SKILL.md) 约束为：

```text
train-model/<project-name>/
├── README.md
├── conda.yaml
├── configs/
│   ├── baseline.yaml
│   └── <variant>.yaml
├── src/<python-package>/
│   ├── data.py
│   ├── models/
│   ├── train.py
│   └── evaluate.py
├── scripts/
│   └── train.py
├── tests/
│   └── test_*.py
└── notebooks/                      # 可选，只用于探索、提交和展示
```

所有模型专用实现、配置、环境和测试都归项目所有。仓库根 `tests/` 只用于平台级或跨项目行为；
数据集、Checkpoint、模型、缓存和已执行 Notebook 不进入源码树。一个项目可以包含多个模型族
和参数变体，参数差异应优先通过 `configs/*.yaml` 表达，而不是创建多个工作负载根目录。

当前两个项目的定位不同：

| 项目 | 定位 | 结构状态 |
| --- | --- | --- |
| [`cats-and-dogs`](../train-model/cats-and-dogs/README.md) | PyTorch CUDA 13 Notebook、基础训练管线和历史调优兼容示例 | 保留现有紧凑平铺结构，不作为新项目模板 |
| [`ray-cats-and-dogs`](../train-model/ray-cats-and-dogs/README.md) | 参数化 Ray Job/Ray Train、MLflow 治理和恢复参考实现 | 符合 `configs/src/scripts/tests/notebooks` 正式项目结构 |

## 4. 当前 Agent 能力

仓库当前实际提供四个 Skill：

| Skill | 触发场景 | 当前作用 |
| --- | --- | --- |
| [`model-project-structure`](../.codex/skills/model-project-structure/SKILL.md) | 创建、重构或评审训练项目 | 约束项目目录、配置、源码、测试、环境和 Notebook 归属 |
| [`ray`](../.codex/skills/ray/SKILL.md) | 编写或诊断 Ray Core、Data、Train、Tune、Serve 等代码 | 提供 Ray 2.x API、调度、Runtime Env、恢复和集群参考资料 |
| [`searching-mlflow-docs`](../.codex/skills/searching-mlflow-docs/SKILL.md) | 查询当前 MLflow API 和集成方式 | 从 MLflow 官方文档获取最新接口和示例 |
| [`mlflow-optimize-models`](../.codex/skills/mlflow-optimize-models/SKILL.md) | 分析历史 Runs、诊断训练并设计下一步搜索 | 通过 Tracking/Artifact API 做框架无关、验证集驱动的证据分析 |

Agent 根据任务阶段选择最小必要能力：

```text
项目结构或测试归属问题
  -> model-project-structure

Ray API、Runtime Env、分布式训练或调度问题
  -> ray

MLflow API、Tracing、Tracking 或 Registry 用法问题
  -> searching-mlflow-docs

已有可比较 Runs，需要诊断或调优
  -> mlflow-optimize-models
```

Skill、脚本和外部动作的边界如下：

| 组件 | 作用 |
| --- | --- |
| Skill | 提供决策方法、工程不变量和验证顺序 |
| 项目脚本 | 确定性地加载配置、校验数据、训练、评测和记录证据 |
| Agent | 理解上下文、选择 Skill、修改代码、解释结果和请求必要授权 |
| 外部动作 | 提交训练、停止 Job、创建 Registry 版本或修改 Alias，必须符合用户授权 |

当前仓库没有独立的常驻“自主训练 Agent 服务”，也没有专用的 Ray 提交/停止 Tool。早期文档
设想的 `mlflow-onboarding`、`write-mlflow-training-code` 和
`write-ray-training-code` 尚未作为本仓库 Skill 落地；相关强制规则目前由
[`AGENTS.md`](../AGENTS.md)、项目实现和测试共同承担。不得把这些规划名称当作可调用的现有能力。

## 5. 正式 Ray 训练项目

### 5.1 配置与执行角色

`ray-cats-and-dogs` 使用 YAML 继承表达运行角色：

| 配置 | 角色 | Epoch | Worker | 每 Worker Batch | 测试集 | Logged Model |
| --- | --- | ---: | ---: | ---: | --- | --- |
| `baseline.yaml` | `trial` | 20 | 1 | 128 | 不读取 | 不记录 |
| `distributed.yaml` | `trial` | 20 | 2 | 128 | 不读取 | 不记录 |
| `smoke.yaml` | `smoke` | 1 | 1 | 128 | 不读取 | 不记录 |
| `champion.yaml` | `champion` | 20 | 2 | 128 | 最终评测一次 | 记录 |

配置加载顺序为 YAML 继承、环境变量覆盖、`--set` 严格键覆盖、类型转换和完整校验。当前可从
环境覆盖 `MLFLOW_TRACKING_URI`、`MLFLOW_EXPERIMENT_NAME`、数据目录、数据来源 URI 和
Ray Address。未知配置键会快速失败，避免拼写错误被静默忽略。

当前基线将计算和数据流水线一起配置：`mixed_precision: bf16`；96 个 Ray Data Block、24 个
CPU 解码 Task、64 张图片一个解码 Batch、4 个预取 Batch，并缓存解码后的 Tensor。配置校验
要求混合精度为 `none` 或 `bf16`，Block 数不少于解码 Worker 数，且所有并发、Batch 和预取
参数位于有效范围。训练 Worker 启用 BF16 前还会检查 CUDA 设备是否真正支持 BF16。

正式入口为：

```bash
python train-model/ray-cats-and-dogs/scripts/train.py \
  --config train-model/ray-cats-and-dogs/configs/smoke.yaml
```

入口提供三种执行级别：

| 模式 | 外部行为 |
| --- | --- |
| `--check-config` | 只加载、校验并打印配置与资源计划；不访问数据、MLflow 或 Ray |
| `--plan` | 检查 MLflow、扫描数据、计算身份和查询既有 Run；不创建 Experiment/Run，也不启动训练 |
| 默认训练 | 建立 MLflow 追踪、检查幂等性和资源，然后启动 Ray Train |

`--plan` 可能在被 Git 忽略的 `platform-data/ray-cats-and-dogs/manifests/` 中生成或复用本地
Manifest 缓存，但不会创建远程训练状态。`--force` 会在相同身份已经成功或正在运行时创建新
Attempt，只能用于明确需要重复尝试的场景。

### 5.2 执行链路

```text
scripts/train.py
  |
  +--> 加载 YAML、环境覆盖和 CLI Override
  +--> mlflow.set_tracking_uri / set_experiment
  +--> mlflow.autolog(log_models=False)
  +--> run_training Workflow Trace
          |
          +--> Tracking/Artifact Preflight
          +--> 数据校验、Manifest 和确定性切分
          +--> 代码、配置和幂等身份
          +--> 复用成功/运行中的相同 Run，或创建新 Attempt
          +--> ray.init 和集群资源检查
          +--> 一个 Driver 管理的 MLflow Run
                  |
                  +--> Ray Data TaskPool
                  |      +--> 固定 Seed 全局 Shuffle 和 Block 划分
                  |      +--> CPU 并行解码/缩放为 uint8 NCHW Tensor
                  |      +--> 可选 Object Store Materialize Cache
                  |
                  +--> TorchTrainer
                  |      +--> 预取 Ray Data training/validation shard
                  |      +--> GPU Batch 增强、BF16、Channels Last 和 TF32
                  |      +--> 1..N 个 GPU Worker
                  |      +--> 每 Epoch Ray Checkpoint
                  |
                  +--> Train Controller Callback 写全局 Epoch 指标
                  +--> 上传并 SHA-256 回读最佳 Checkpoint
                  +--> Champion 独占最终测试与 Logged Model
                  +--> Artifact API 最终回读
                  +--> succeeded/failed 结果标签
```

正式训练在导入训练编排模块前启用 `mlflow.autolog(log_models=False, silent=True)`，避免
Autolog 与显式 Champion 模型记录重复。`run_training` 使用 MLflow Workflow Trace 覆盖完整
Driver 流程；Trace 用于观察编排步骤，训练 Run 仍是参数、Metric、Artifact 和模型治理的
权威记录。`--check-config` 和 `--plan` 不启用训练 Autolog。

### 5.3 MLflow 所有权

当前实现不是“所有 Worker 都写同一个 Run”，而是明确区分所有权：

| 进程 | 可以做什么 | 禁止做什么 |
| --- | --- | --- |
| Ray Job Driver | 创建/结束 Run，记录输入、Tag、Artifact、最终评测和模型 | 把测试集用于 Trial 选择 |
| Ray Train Controller Callback | 从 Rank 0 Report 把全局 Epoch 指标写入既有 Run，记录最新 Checkpoint URI | 创建第二个无关联 Run |
| Worker 0 | 训练、全局归约、输出进度、生成 Checkpoint、调用 `train.report` | 直接开始/结束 MLflow Run 或上传共享 Artifact |
| Worker 1..N | 训练、全局归约并向 Ray 上报 | 发布局部模型或争用共享 Artifact 路径 |

Driver 是 Run 生命周期和最终 Artifact 的权威写入者。Train Controller Callback 只是使用已知
Run ID 写入 Rank 0 的全局指标；Worker 只执行计算和 Ray Report。这一边界保证多 Worker
训练仍然只有一条可审计的实验记录。

### 5.4 Runtime Environment

Ray Job 上传 `ray-cats-and-dogs` 项目目录作为 `working_dir`，并把
`src/ray_cats_dogs` 作为 `py_modules`。上传时排除 `.git`、Notebook、测试、缓存和字节码。
Driver 会把 Job 的 Runtime Env 显式传给 Train Worker。

Ray 2.53 的 Train Controller 使用内部 Runtime Env，不自动继承项目 `py_modules`。当前实现
因此把输入管线、Worker 函数和 MLflow Callback 按值序列化给 Controller，同时仍要求 Worker
收到上传后的项目包。这使训练不依赖所有节点预先执行 `pip install -e`，也不依赖共享源码
绝对路径。

本地单节点可以使用 `platform-data/ray-results/` 保存 Ray Checkpoint。跨主机集群必须把
`ray.storage_path` 指向所有节点可访问的同一绝对挂载或受控共享 URI；不能把 Worker 临时目录
或 `/tmp` 当作恢复存储。

### 5.5 数据流水线与 GPU 计算

当前单 GPU 基线使用流水线并行，不会把一张实体 GPU 虚报成多张逻辑 GPU 来制造 DDP Rank。
数据阶段和训练阶段如下：

```text
Manifest path + label
  -> Ray Data 固定 Seed Shuffle
  -> 96 Blocks / 最多 24 个 CPU Task 并行读取和缩放
  -> 紧凑 uint8 NCHW Tensor
  -> 可选 Object Store Materialize Cache
  -> iter_torch_batches 预取、Pinned Memory 和 device="auto"
  -> GPU float32 归一化
  -> GPU Batch 级随机翻转、旋转和平移
  -> Channels Last + BF16 Autocast + TF32 训练
```

随机增强不写入 Object Store Cache，因此不同 Epoch 仍会生成新的增强。缓存只保存解码和缩放
后的 `uint8` Tensor，降低 Object Store 容量和 CPU/GPU 传输开销。关闭缓存时，Ray Data 解码
Task 可以与训练 Worker 并发，资源预检会按训练 CPU 与解码 CPU 之和计算；启用缓存时先完成
数据物化，再进入训练，CPU 需求取两个阶段的较大值。

Ray 的 `GPU` 是设备调度资源，不是显存配额。提交前必须让 `ray status` 的 GPU 总数与
`nvidia-smi -L` 的实体设备数一致；单卡节点不能用多个逻辑 GPU 资源把多个 Worker 调度到同一
设备。扩大并行度应优先调整 Ray Data Worker、Block、Batch、预取和 Object Store，而不是
虚报 GPU 数量。

### 5.6 指标、进度和 Checkpoint

每个 Epoch 的训练与验证结果先在 Worker 间进行全局归约，然后由 Rank 0 Report 进入 MLflow。
当前记录包括：

- Loss、Accuracy、Precision、Recall 和 F1；
- Cat、Dog 和 Macro 分类指标；
- 样本数、Batch 数和每秒样本吞吐；
- Learning Rate、Epoch 总耗时、训练耗时、验证耗时和首次数据准备耗时；
- 训练/验证数据等待秒数与等待占比；
- Worker Rank、World Size、最佳目标值和 Checkpoint URI。

Rank 0 在 Ray Job 日志中显示 `tqdm` Batch 进度。进度条后缀是 Rank 0 当前数据分片的即时值，
只用于判断任务是否推进；MLflow 和模型选择使用全部 Worker 归约后的 Epoch 指标。Notebook
监控优先轮询 MLflow Metric History，尚未刷新时回退到 Worker 输出的 `epoch-complete` JSON。

`data_preparation_seconds` 单独衡量首次并行解码和 Tensor Cache 构建，不混入 Epoch 吞吐。
`train_data_wait_fraction` 或 `val_data_wait_fraction` 较高时，应先检查解码 Worker、Block、预取
和 Object Store；等待接近零但 GPU 利用率仍低时，再评估 Batch、模型规模或 GPU Kernel。

每个 Epoch 的 Rank 0 Checkpoint 包含当前模型、Optimizer、最佳模型和训练状态。Ray 只保留
最近一个可恢复 Checkpoint；训练完成后 Driver 将最佳 Checkpoint 上传到 MLflow，并下载
`best-model.pt` 核对 SHA-256。只有完成 Artifact 回读验证的 Run 才会被认作可复用成功结果。

## 6. 数据、代码与实验身份

当前参考实现逐文件验证本地 `PetImages/`，记录文件大小和 SHA-256，排除已知损坏图像，并
使用固定 Seed 生成确定性训练、验证和测试切分。数据身份包括：

- 数据来源 URI；
- 完整内容摘要和 Dataset Version；
- Manifest 与切分摘要；
- 每个 Split 的样本和类别分布；
- 图像尺寸和预处理版本；
- MLflow `training`、`validation`、`test` Dataset Inputs。

当前预处理身份为 `ray-data-uint8-gpu-augment-v3`：CPU 阶段只做确定性图片解码和缩放，训练
增强在 GPU 上按 Batch 执行。缓存前的全局 Shuffle 使用固定 Seed；BF16、TF32、cuDNN
Benchmark、多 Worker 浮点归约和随机 GPU 增强仍可能带来细微差异，因此 Run 不宣称逐位复现。

本地路径不是远端数据身份。只有训练节点读取经过核对的 MinIO 数据快照时，才应设置真实的
`CATS_DOGS_DATASET_SOURCE_URI`；不能把本地缓存伪装成对象存储版本。

代码身份覆盖项目 `src/`、`scripts/`、`conda.yaml` 和 `pyproject.toml` 的内容摘要，并记录
Git Commit 与 Dirty 状态。幂等键由以下字段共同决定：

```text
project
+ data content digest
+ split digest
+ source digest
+ resolved config digest
+ random seed
+ run role
```

相同幂等键已有满足以下全部条件的 Run 时，默认不重新训练：

1. MLflow 状态为 `FINISHED`；
2. `run.outcome=succeeded`；
3. `artifact.roundtrip_verified=true`。

相同身份正在运行时也默认复用其 Run ID。新的明确尝试使用递增 Attempt，不覆盖其他 Run、
Checkpoint 或 Artifact。

## 7. Trial、Champion 与模型治理

当前 Ray 项目的主验证目标由配置声明，示例支持最大化 `val_accuracy` 或最小化 `val_loss`。
这是示例工作负载的 Schema 限制，不是平台级指标限制；通用 Agent 和 MLflow 分析能力必须支持
任意命名、任意方向的项目指标。

Trial 和 Smoke：

- 只读取训练集和验证集；
- 使用验证指标做 Early Stopping 和 Checkpoint 选择；
- 不执行最终测试评测；
- 不记录可发布 Logged Model；
- 不创建 Registry 版本或修改 Alias。

Champion：

- 使用已经审查的配置从干净状态训练；
- 读取最佳验证 Checkpoint；
- 对固定测试集执行一次最终评测；
- 记录预测摘要、分类报告、混淆矩阵和质量门禁；
- 记录带 Signature、Input Example、代码路径和预处理元数据的 MLflow PyTorch Model；
- 下载 Logged Model Artifact 并确认存在 `MLmodel` 描述文件。

即使 Champion 通过门禁，生产模型 Alias 也不会自动改变。Registry 创建和 `candidate`、
`champion`、`production` 等 Alias 修改必须是单独、显式且经过审查的动作。

## 8. Agent 工作流与授权

当前 Agent 在一次用户会话中工作，不是持续运行的后台调优器。推荐工作流为：

```text
1. 发现
   读取 AGENTS.md、项目 README、配置、代码和工作区状态
        |
2. 静态验证
   校验项目结构、配置、数据/指标语义和外部服务边界
        |
3. 只读计划
   通过 --check-config 或 --plan 确认资源、数据身份和幂等状态
        |
4. 实现与测试
   修改最小范围代码，先运行项目级测试
        |
5. 获授权执行
   提交 Smoke、Trial 或 Champion，并持续观察 Ray/MLflow
        |
6. 证据分析
   使用 mlflow-optimize-models 比较兼容 Runs 并提出下一步
        |
7. 人工治理
   审查 Champion、质量门禁和 Artifact 后再决定 Registry 晋级
```

授权边界必须保持清晰：

| 动作 | 默认边界 |
| --- | --- |
| 读取代码、配置、Run 和 Artifact 元数据 | 可用于诊断和分析 |
| 修改用户要求范围内的代码和文档 | 需保留无关工作区改动 |
| 运行小型单元测试和不训练的配置检查 | 正常验证步骤 |
| 启动 CPU/GPU Smoke、Trial 或 Champion | 必须由用户请求或明确授权 |
| 停止正在运行的 Ray Job | 必须确认目标和授权 |
| 创建 Registry 版本或修改模型 Alias | 必须单独、显式授权 |

`mlflow-optimize-models` 只根据兼容的训练和验证证据分析最佳已观察结果、过拟合、欠拟合、
不稳定、预算和 Artifact 问题。分析或代码优化请求本身不授权启动训练，也不授权使用测试集
搜索或改变生产 Alias。

## 9. 失败、重试与恢复

训练失败时，Driver 在同一 MLflow Run 中记录 `run.outcome=failed`、`failure.phase` 和
`failure.type`。Train Controller Callback 记录最近 Worker 异常摘要，Ray Checkpoint 保存
最近训练状态。入口把 `SIGTERM` 转换为可处理的中断，使 Run 能留下失败证据，而不是静默消失。

Ray `max_failures` 控制同一训练任务内的恢复预算；当前 Smoke 禁止 Worker 重试，基线和正式
配置可以声明有限重试。恢复必须继续使用同一数据、代码、配置、Seed、Run ID 和幂等身份。
应用错误、资源不足、节点失败和人为停止不能被统一伪装成成功。

当前恢复边界仍受部署形态限制：本地 `platform-data/ray-results/` 只能保护同一共享文件系统
可见范围内的任务，不能提供主机级容灾。多节点或生产部署需要共享 Checkpoint URI、MLflow
元数据与 MinIO 对象的一致备份，以及经过验证的恢复演练。

## 10. 可观测性

当前可观测性分为四层：

| 层 | 当前证据 |
| --- | --- |
| Ray Job | Job ID、状态、日志、Batch 进度和 `epoch-complete` JSON |
| Ray Train | Worker、World Size、资源、Report、Checkpoint 和失败摘要 |
| MLflow Run | 参数、Dataset Input、Metric History、系统指标、Tag、Artifact 和模型 |
| MLflow Trace | `ray_cats_dogs_training` Workflow 的 Driver 编排轨迹 |

MLflow Run 是模型训练证据的权威来源；Trace 用于理解工作流调用和耗时，不能代替 Run 的数据
血缘、Metric History、Checkpoint 或质量门禁。未来若实现常驻 Agent 主循环，可再使用 MLflow
Tracing 记录 Skill 选择、Tool 调用和多轮决策，但当前仓库尚未实现这一 Agent 运行时。

## 11. 安全与持久化

平台和 Agent 必须保持以下不变量：

- 不把 Token、密码、对象存储密钥、私有 Endpoint 或环境文件提交到 Git；
- 不把敏感样本、测试标签或私有训练示例写入日志、截图或公开 Artifact；
- 不直接查询、复制或锁定 `mlflow.db`；
- 不绕过 MLflow Artifact API 读取服务端 MinIO 文件系统；
- 不让非权威 Worker 发布共享模型；
- 不让重试覆盖其他 Attempt；
- 不把 Notebook Kernel、Worker 临时目录或 `/tmp` 当作持久恢复状态；
- 不比较数据、切分、预处理、指标定义或评估协议不兼容的 Runs；
- 不使用最终测试集反复选参；
- 不把有限搜索结果描述为数学意义上的全局最优。

当前 JupyterLab、MLflow 和 MinIO Unit 包含主机特定用户、路径、端口、Allowed Hosts 和代理
配置。监听 `0.0.0.0` 的服务必须由防火墙、认证代理或受控内网保护。MLflow SQLite 元数据和
MinIO 对象必须成组备份；当前单机 MinIO 不构成高可用存储。

## 12. 验证入口

先运行最窄的项目测试。正式 Ray 项目测试不连接 MLflow、不启动 Ray 集群，也不执行训练：

```bash
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s train-model/ray-cats-and-dogs/tests -p 'test_*.py'
```

验证配置和资源计划：

```bash
/data/conda/envs/attend-ray-py312/bin/python \
  train-model/ray-cats-and-dogs/scripts/train.py \
  --config train-model/ray-cats-and-dogs/configs/smoke.yaml \
  --check-config
```

兼容的平铺项目应从项目根运行测试，使本地模块可导入：

```bash
cd train-model/cats-and-dogs
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s tests -p 'test_*.py'
```

仓库根测试入口只用于平台级和跨项目测试：

```bash
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s tests -p 'test_*.py'
```

Notebook Smoke Test 必须输出到临时目录，不能覆盖源 Notebook。服务变更需要运行：

```bash
systemd-analyze verify systemd/*.service
```

测试代码、分析代码或文档更新不隐含正式训练权限。GPU 训练、远程 Job、最终测试和 Registry
操作只在任务范围明确时执行。

## 13. 当前限制与演进方向

以下能力尚未成为当前架构的一部分：

- 通用的新项目 MLflow Onboarding Skill；
- 独立的 MLflow/Ray 训练代码生成与合规审计 Skill；
- 持久化 Agent Session 和自动阶段状态机；
- 专用 Ray Job 提交、停止、恢复 Tool；
- 自动预算管理和通用 Ray Tune 搜索编排；
- 自动 Registry 晋级或生产 Alias 变更；
- 多主机 Ray、MLflow 和 MinIO 高可用部署；
- 跨框架、跨任务的正式验收 Fixture 集。

合理的演进顺序是：

1. 先稳定项目契约、配置 Schema、Run 证据和审计脚本；
2. 再用回归、最小化目标和非图像任务验证框架无关性；
3. 然后封装有明确授权边界的 Ray Job Tools 和恢复流程；
4. 最后实现持久 Agent 状态、预算控制、Tracing 和 Agent Evaluation；
5. Registry 晋级始终保留显式审查门禁。

新增能力时必须清楚标注“已实现”“实验性”或“规划中”，避免再次把设计目标写成可用组件。

## 14. 当前结论

Galatea 当前已经具备一条可执行的正式参考链路：Agent 按仓库 Skill 和项目契约理解任务，
参数化入口通过 Ray Job/Ray Train 执行 PyTorch 训练，Driver 和 Train Controller 按明确
所有权写入单一 MLflow Run，MinIO 持久化 Artifact，幂等身份、Checkpoint 回读、测试集隔离
和人工 Registry 门禁共同保证训练可以审计和恢复。

平台的扩展点是新的 `train-model/<project-name>/` 工作负载和框架无关的 Agent 能力，而不是
复制 Cats vs Dogs 语义。未来自治程度可以提高，但所有自动化都必须建立在数据可比、证据完整、
资源受控和发布动作显式授权的基础上。
