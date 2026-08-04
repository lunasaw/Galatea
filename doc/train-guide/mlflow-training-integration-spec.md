# 训练代码与 MLflow 集成规范

本文规定本仓库训练项目与 MLflow Tracking、Artifact 和 Logged Model 的交互边界。
[`ray-cats-and-dogs`](../../train-model/ray-cats-and-dogs/README.md) 是本文的参考实现，规范本身适用于
分类、回归、检测、分割、排序、推荐、预测和微调等不同工作负载，不限定 Ray、PyTorch 或某个指标。

本文描述的是当前代码已经实现的行为。MLflow Server 和 MinIO 的部署、凭据与备份分别见
[`mlflow-start.md`](../mlflow-start.md) 和 [`minio-start.md`](../minio-start.md)。

## 1. 目标与边界

一次正式训练必须能够通过一个 MLflow Run 回答以下问题：

- 使用了哪个数据版本、数据内容和固定切分；
- 使用了哪份解析后的配置、代码版本、环境和随机种子；
- 由哪个调度任务执行，申请了哪些资源；
- 训练、验证和最终测试指标分别是什么；
- 最佳模型如何选择，Checkpoint 和模型能否通过 Artifact API 恢复；
- Run 是成功、失败、复用还是仍在运行，失败发生在哪个阶段；
- 模型是否通过质量门禁，以及是否经过独立的人工发布动作。

MLflow 负责实验元数据、血缘、指标、Tag、Artifact 和 Logged Model。Ray 负责调度、分布式训练和
运行中故障恢复。二者的持久化用途不得混淆：Ray Checkpoint 用于同一训练 Attempt 的恢复，MLflow
Artifact 用于 Run 结束后的审计、下载和复现。

当前参考实现只生成 Logged Model，不创建 Registered Model、不创建 Model Version，也不设置
`candidate`、`champion` 或 `production` Alias。Registry 发布属于训练完成后的独立、显式、经审查操作。

## 2. 参考实现与职责

| 代码位置 | MLflow 职责 |
| --- | --- |
| [`scripts/train.py`](../../train-model/ray-cats-and-dogs/scripts/train.py) | 解析配置；正式训练前设置 Tracking URI、Experiment 和 Autologging |
| [`config.py`](../../train-model/ray-cats-and-dogs/src/ray_cats_dogs/config.py) | 解析 MLflow 配置和环境变量；限制测试集评测与模型记录权限 |
| [`tracking.py`](../../train-model/ray-cats-and-dogs/src/ray_cats_dogs/tracking.py) | Tracking 预检、身份与血缘记录、Artifact 回读、Ray 指标 Callback |
| [`train.py`](../../train-model/ray-cats-and-dogs/src/ray_cats_dogs/train.py) | 创建唯一权威 Run，编排训练、评测、模型记录和成功/失败状态 |
| [`worker.py`](../../train-model/ray-cats-and-dogs/src/ray_cats_dogs/worker.py) | 计算全局 Epoch 指标并通过 `train.report` 上报；不调用 MLflow |
| [`evaluate.py`](../../train-model/ray-cats-and-dogs/src/ray_cats_dogs/evaluate.py) | 从最佳 Checkpoint 执行一次最终测试并返回结果；不调用 MLflow |

交互关系如下：

```text
参数化入口 scripts/train.py
  |
  | set_tracking_uri / set_experiment / autolog
  v
Driver: run_training
  |-- 创建并拥有一个 MLflow Run
  |-- 记录配置、数据、代码、环境和资源
  |
  +--> Ray Train Controller
         |-- Worker 0 --\
         |-- Worker 1 ----+--> train.report(全局聚合指标, Checkpoint)
         |-- ... -------/
         |
         +--> RayMlflowCallback --> 写入 Driver 已创建的同一个 Run
  |
  |-- 记录最佳 Checkpoint、最终测试、Logged Model 和诊断 Artifact
  |-- Artifact API 回读验证
  `-- 设置结果 Tag，由 Run 上下文结束 FINISHED 或 FAILED 状态
```

## 3. 单一写入者规则

分布式训练必须只有一个 MLflow Run 所有者。参考实现采用以下规则：

1. Driver 创建、结束并返回父 Run ID。
2. Worker 不得调用 `mlflow.start_run()`、`mlflow.end_run()`、`mlflow.log_*()` 或 Registry API。
3. Worker 只通过 Ray `train.report()` 上报指标和 Checkpoint。
4. Controller 中的 `RayMlflowCallback` 使用已知 `tracking_uri` 和 `run_id` 写入已有 Run，不创建嵌套
   Run。
5. 多 Worker 报告中只选 Rank 0 的报告写入 MLflow。该报告中的训练与验证值已经过全部 Worker
   `all_reduce`，不是 Rank 0 本地分片值。
6. Callback 写指标时使用同步请求，避免 Controller 退出前仍有未发送的 Epoch 指标。

其他项目可以采用经过设计的嵌套 Run，但必须明确父子 Run 所有权、失败传播、指标归属和 Artifact
命名规则；不得让多个 Worker 并发覆盖同一 Run 的同一路径。

## 4. 配置契约

参考配置位于
[`configs/baseline.yaml`](../../train-model/ray-cats-and-dogs/configs/baseline.yaml)：

```yaml
mlflow:
  tracking_uri: http://127.0.0.1:5000
  experiment_name: ray-cats-and-dogs
  require_remote_artifacts: true
```

配置规则如下：

- `tracking_uri` 和 `experiment_name` 必须非空，不得在共享代码中假设 MLflow 与训练进程同机。
- 环境变量 `MLFLOW_TRACKING_URI` 和 `MLFLOW_EXPERIMENT_NAME` 优先于 YAML，适合 Ray Job 或不同环境
  注入；最终解析值必须进入 Run 参数和 `config/resolved-config.json`。
- `require_remote_artifacts: true` 时，Experiment 的 `artifact_location` Scheme 必须是
  `mlflow-artifacts` 或 `s3`。本地 `file:` Artifact 必须在训练前拒绝。
- MinIO Endpoint、Access Key 和 Secret Key 不得写入训练配置。当前架构由启用 `--serve-artifacts`
  的 MLflow Server 代理对象存储，训练客户端只持有 Tracking URI。
- 正式训练必须显式声明角色、主目标指标和方向。参考实现支持 `smoke`、`trial`、`champion`，并将
  `val_accuracy` 对应 `max`、`val_loss` 对应 `min`。
- 只有 `champion` 配置可以启用 `evaluation.evaluate_test` 或 `run.log_model`。该限制在配置加载阶段
  执行，不能依赖调用者自觉。

## 5. 三种入口模式

入口必须把无副作用检查与正式训练分开：

| 模式 | 访问 MLflow | 创建服务端状态 | 启动训练 |
| --- | --- | --- | --- |
| `--check-config` | 否 | 否 | 否 |
| `--plan` | 只读 Tracking API | 否 | 否 |
| 正式训练 | Tracking、Artifact、Trace API | 可能创建 Experiment 和 Run | 是 |

参考命令：

```bash
python train-model/ray-cats-and-dogs/scripts/train.py \
  --config train-model/ray-cats-and-dogs/configs/smoke.yaml \
  --check-config

python train-model/ray-cats-and-dogs/scripts/train.py \
  --config train-model/ray-cats-and-dogs/configs/smoke.yaml \
  --plan
```

`--plan` 会连接 Tracking Server、查找 Experiment 和历史 Run、校验完整数据并计算身份，但不得创建
Experiment、Run、Worker 或模型。正式入口在导入训练编排模块前执行：

```python
mlflow.set_tracking_uri(config.mlflow.tracking_uri)
mlflow.set_experiment(config.mlflow.experiment_name)
mlflow.autolog(log_models=False, silent=True)
```

参考实现还用 `mlflow.trace` 记录完整 Driver 工作流，并通过 `log_system_metrics=True` 记录系统指标。
Autologging 的附加字段可能随 MLflow 和框架版本变化，不应被其他系统当作稳定契约；本文明确列出的
参数、指标、Tag 和 Artifact 才是项目维护的接口。

## 6. 预检与 Experiment

正式训练在消耗 Ray 资源前必须完成 MLflow 预检：

1. 设置解析后的 Tracking URI，并通过 `MlflowClient.search_experiments()` 验证服务可用。
2. 按名称查找 Experiment；只读计划在不存在时返回 `None`。
3. 正式训练可以创建缺失的 Experiment。
4. 若要求远程 Artifact，检查 Experiment 的实际 `artifact_location`，不能只检查服务器启动参数。
5. 将 `experiment_id`、名称和 Artifact Location 作为预检结果传给 Run 编排。

已有 Experiment 的 Artifact Location 不会因为 MLflow Server 参数改变而自动迁移。发现 `file:`、错误
Bucket 或不可访问 URI 时必须停止训练，不能降级到 Driver 临时目录。

## 7. Run 身份、幂等与命名

### 7.1 身份材料

参考实现先计算四类摘要：

| 标识 | 当前计算依据 |
| --- | --- |
| `data.content_sha256` | 经过校验的有效文件内容 |
| `data.split_sha256` | 固定 Seed 生成的训练、验证、测试切分 |
| `code.source_sha256` | 项目 `src/`、`scripts/`、`conda.yaml` 和 `pyproject.toml` 的内容 |
| `config_digest` | 影响数据、模型、训练、关键 Ray 数据/拓扑和评测语义的解析配置 |

代码身份同时记录 Git Commit 和工作树是否为 Dirty。`source_sha256` 包含尚未提交的新源码，避免只记录
Git Commit 时把未提交代码误认为相同版本。

`idempotency_key` 是以下字段按固定顺序拼接后计算的 SHA-256：

```text
project_name
+ dataset content digest
+ split digest
+ source digest
+ config digest
+ random seed
+ run role
```

任何通用化实现都必须让数据内容、切分、预处理、代码、完整超参数、Seed 和评测角色参与身份；不能只用
Run Name 或提交时间判断是否为同一次实验。

### 7.2 历史 Run 判定

Driver 在当前 Experiment 中按 `tags.idempotency_key` 搜索历史 Run：

- `status=FINISHED`、`run.outcome=succeeded` 且 `artifact.roundtrip_verified=true` 才算可复用的成功
  Run；
- 存在可复用 Run 时默认返回 `already-succeeded`，不启动训练；
- 存在同身份的 `RUNNING` Run 时默认返回 `already-running`；
- `--force` 会创建同身份的新 Attempt，只应用于明确需要重复留档的情况；
- Worker 的临时失败由 Ray `max_failures` 和最近 Checkpoint 恢复，不应习惯性改用 `--force`。

不得对已经完成最终测试的 Champion 随意使用 `--force`，否则会在没有新数据或新协议的情况下重复读取
同一测试集。确需重新评测时，应先记录授权理由，并把它作为新的受审 Attempt 管理。

Run Name 采用 `<name_prefix>-<role>-<identity前8位>-a<attempt>`。Run Name 只用于阅读，幂等判断必须
使用完整 Tag。每个新 Attempt 都是新的 MLflow Run，不得覆盖失败 Run。

## 8. Run 生命周期与状态 Tag

Run 创建时必须至少记录以下 Tag：

| Tag | 含义 |
| --- | --- |
| `project` | 工作负载项目名 |
| `run.role` | `smoke`、`trial` 或 `champion` 等评测角色 |
| `run.outcome` | 初始为 `running`，收尾改为 `succeeded` 或 `failed` |
| `lifecycle.stage` | 当前训练生命周期阶段；参考实现为 `development` |
| `idempotency_key` | 完整幂等身份 |
| `dataset_version` | 人可读数据版本 |
| `code.git_commit` / `code.git_dirty` | Git 身份与工作树状态 |
| `ray.job_id` | 调度任务关联键 |
| `execution.type` | 执行方式；参考实现为 `ray-train` |
| `test.evaluated` | 是否实际读取最终测试集，初始为 `false` |
| `registry.promotion` | 当前实现固定为 `manual-only` |
| `ray.task_timeline.requested` | 是否要求导出当前 Job 的任务时间线 |

成功前必须完成所要求的 Artifact 回读，然后设置：

- `run.outcome=succeeded`；
- `artifact.roundtrip_verified=true`；
- `ray.checkpoint_uri=<最终 Ray Checkpoint URI>`；
- 执行最终测试时设置 `test.evaluated=true` 和 `quality_gate.passed=<true|false>`；
- 生成 Logged Model 时设置 `model.uri=<model URI>`。

设置成功 Tag 后，Driver 必须刷新异步日志再返回结果，避免进程退出时遗失收尾记录。

质量门禁未通过不等于训练执行失败：Run 仍可成功结束并保留完整证据，但后续发布流程必须拒绝它。
当前代码会在 `run.log_model=true` 时记录模型，不以质量门禁结果阻止 Logged Model 生成，因此不得把
“存在 Model URI”解释为“已批准上线”。

异常时上下文管理器负责把 MLflow Run 状态结束为 `FAILED`，代码还必须设置：

- `run.outcome=failed`；
- `failure.phase=<run-setup|ray-data-preparation|ray-training|...>`；
- `failure.type=<异常类型>`；
- Worker 异常摘要写入 `ray.last_worker_failure`；
- 若任务时间线导出失败，记录 `ray.task_timeline.logged=false` 和失败类型。

原始异常优先。训练已经失败时，任务时间线只做尽力保存，不得用诊断 Artifact 的次生异常覆盖根因。

## 9. 参数与输入血缘

### 9.1 Params

参考实现将解析后的完整配置递归展开为点分参数，例如：

```text
run.role
data.preprocessing_version
model.family
training.learning_rate
training.objective_metric
ray.num_workers
mlflow.tracking_uri
evaluation.minimum_test_accuracy
```

并补充以下稳定身份参数：

```text
data.dataset_version
data.content_sha256
data.split_sha256
code.source_sha256
code.git_commit
ray.job_id
run.idempotency_key
```

列表和元组以紧凑 JSON 记录，`null` 必须显式记录为字符串，不能静默漏掉未设置项。密码、Token、对象
存储密钥和私有凭据不得成为参数、Tag、Artifact 或日志。

### 9.2 MLflow Dataset Inputs

训练、验证和测试三个固定 Split 分别创建 MLflow Dataset Input：

| Context | Dataset Name | 内容 |
| --- | --- | --- |
| `training` | `microsoft-cats-vs-dogs-training` | 相对路径、标签、大小和 SHA-256 |
| `validation` | `microsoft-cats-vs-dogs-validation` | 相对路径、标签、大小和 SHA-256 |
| `test` | `microsoft-cats-vs-dogs-test` | 相对路径、标签、大小和 SHA-256 |

Input Digest 由对应 Split 的清单内容计算，Source 使用声明的数据源 URI。通用项目可以采用不同 Dataset
Name，但必须记录不可变来源、内容或 Manifest 摘要、Split 身份、Target 和使用 Context。记录测试集
Input 不代表读取测试标签做选择；是否执行测试必须由 `test.evaluated` 和角色约束单独表达。

## 10. 指标规范

训练、验证和测试指标必须使用不同前缀，不能复用同一名称：

| 阶段 | 当前指标 |
| --- | --- |
| 训练 | `train_loss`、`train_accuracy`、`train_precision`、`train_recall`、`train_f1`、Cat/Dog 与 Macro Precision/Recall/F1 |
| 验证 | `val_loss`、`val_accuracy`、`val_precision`、`val_recall`、`val_f1`、Cat/Dog 与 Macro Precision/Recall/F1 |
| 数据与性能 | 样本数、每 Worker Batch 数、吞吐、Epoch/训练/验证耗时、数据等待时间与比例、学习率、`world_size` |
| 选择结果 | `best_objective`、`best_epoch` |
| 最终测试 | `test_loss`、`test_accuracy`、`test_precision`、`test_recall`、`test_f1`、`test_roc_auc` |
| 准备阶段 | `data_preparation_seconds` |

Epoch 指标以 `epoch` 作为 MLflow `step`，但不把 `epoch` 和 `worker_rank` 本身重复写为 Metric。所有
用于 Checkpoint 选择的值必须是完整训练群组的聚合值。日志中的 Batch 进度可以展示本地即时值，但
不能用它替代 MLflow 中的全局 Epoch 指标。

主目标和方向必须随配置记录。Trial 只能用训练集和验证集做 Early Stopping、Checkpoint 选择和参数
比较；测试指标不得参与这些操作。不同任务可以改变指标名称，但必须保留 `train_*`、`val_*` 和
`test_*` 的语义隔离。

## 11. Artifact 规范

参考 Run 的显式 Artifact 结构如下；Autologging 可能附加其他内容：

```text
config/
  resolved-config.json
data/
  dataset-profile.json
  <manifest.csv>
environment/
  runtime.json
source/
  <提交使用的源 YAML>
  conda.yaml
  src/.../*.py
ray/
  available-resources-at-start.json
  data-pipeline.json
  task-timeline.json
  task-timeline-metadata.json
checkpoints/
  best/
    current-model.pt
    best-model.pt
    training-state.json
reports/
  model-selection.json
  final-test-evaluation.json       # 仅执行最终测试时
outputs/
  test-predictions.json            # 仅执行最终测试时
verification/
  artifact-round-trip.json
```

`environment/runtime.json` 记录 Python、平台、主机名及 Ray、MLflow、训练框架等关键包版本。源码 Artifact
用于审计，而 `code.source_sha256` 用于快速比较；二者都必须保留。

### 11.1 Checkpoint

每个 Epoch 的 Rank 0 Ray Checkpoint 包含：

- 当前模型和 Optimizer 状态，用于故障恢复；
- 验证目标最佳的 `best-model.pt`；
- 当前 Epoch、最佳 Epoch、最佳指标、无提升次数、目标与方向；
- MLflow Run ID 和幂等键。

Ray `CheckpointConfig(num_to_keep=1)` 只保留最近恢复点；最近 URI 记录在
`ray.latest_checkpoint_uri`。训练结束后，Driver 把最终恢复点中的最佳模型及训练状态上传到
`checkpoints/best/`，再通过 MLflow Artifact API 下载 `best-model.pt` 并核对 SHA-256。客户端不得
通过 MLflow Server 的 MinIO 文件系统路径做校验。

### 11.2 Logged Model

Champion 从最佳 Checkpoint 重建模型并记录：

- 输入样例和推理签名；
- 可恢复模型所需的项目代码路径；
- 类别语义、预处理版本、输入范围、输出语义和 CUDA Runtime；
- `MLmodel` 描述文件的 Artifact API 回读检查。

参考实现设置 `MLFLOW_RECORD_ENV_VARS_IN_MODEL_LOGGING=false`，避免把运行进程环境变量收集到模型
元数据。不得把该设置当作秘密扫描的替代品，提交前仍需确保代码、配置和日志不含凭据。

### 11.3 诊断与回读

启用任务时间线时，只查询当前 `ray.job_id` 的 Task，生成 Chrome Trace Event JSON，保存时间线、Task
数量、事件数量和 SHA-256。上传后必须下载并校验摘要。

正常结束前还要上传并下载 `verification/artifact-round-trip.json`，核对 Run ID 和完整内容。此检查验证
通用 Artifact 链路；Checkpoint、时间线和 Logged Model 仍各自执行专用检查。任一强制回读失败都应
让 Run 失败，不能只打印 Warning 后宣告成功。

## 12. 最终测试与质量门禁

测试集只能在经过选择、从干净状态重训的 Champion 流程中读取一次：

1. 用验证目标选择最佳 Checkpoint；
2. 在独立 Ray Task 中加载该 Checkpoint；
3. 对固定测试集计算指标、分类报告、混淆矩阵和逐样本预测；
4. 记录预测 CSV 内容的 SHA-256；
5. 使用预先声明的阈值计算质量门禁；
6. 将结果写入 Metric、Tag、预测表和评测报告。

参考门禁为 `test_accuracy >= evaluation.minimum_test_accuracy`。通用项目必须在配置中声明门禁指标、
比较方向和阈值，并确保它们与任务一致。测试结果不足、协议不兼容或门禁失败时不得晋级 Registry
Alias。

## 13. Model Registry 发布边界

训练成功、质量门禁通过和 Logged Model 可下载，只说明模型具备发布候选资格，不等于已经发布。
发布流程必须是单独入口，并至少完成：

- 核对 Run 的数据版本、Split、预处理、代码、环境和审批记录；
- 核对 `run.outcome=succeeded`、Artifact 回读和适用的质量门禁；
- 从明确的 `model.uri` 创建 Model Version；
- 先设置候选 Alias，完成部署前验证后再显式切换生产 Alias；
- 记录操作者、时间、审批依据和可回滚版本。

训练入口不得自动创建或移动生产 Alias。分析、计划、训练或失败重试请求也不隐含发布授权。

## 14. API、安全与持久化要求

- 训练和分析客户端只能通过 MLflow Tracking、Artifact 和 Model Registry API 访问服务，不得读取、
  复制或查询 `mlflow.db`。
- 客户端不得读取服务端 MinIO 数据目录，也不得持有 MLflow Server 的长期对象存储凭据。
- Artifact URI、Ray Checkpoint URI 和数据 Source URI 是不同命名空间，必须分别记录，不得用本地
  临时路径冒充持久化位置。
- 参数、Tag 和 Artifact 不得包含 Secret、Token、私有 Endpoint、敏感样本或测试标签泄漏。
- 多节点 Ray 的 `storage_path` 必须是所有节点可访问的共享路径或 URI；节点本地 `/tmp` 不能作为
  分布式恢复存储。
- MLflow 元数据库与 Artifact 对象必须做一致性备份。只备份其中一侧不能恢复完整实验。

## 15. 新训练项目接入检查表

新项目接入 MLflow 时必须逐项确认：

- [ ] Tracking URI 和 Experiment 来自显式配置，并支持环境覆盖。
- [ ] `--check-config` 不访问外部服务，`--plan` 不创建任何服务端状态。
- [ ] 正式训练在资源申请前验证 Tracking Server 和 Artifact Location。
- [ ] Driver 是唯一权威 Run 所有者；Worker 不直接写 MLflow。
- [ ] 数据内容、Split、预处理、代码、解析配置、Seed 和角色共同构成幂等身份。
- [ ] 每个 Attempt 使用新 Run，成功复用条件包含 Artifact 回读结果。
- [ ] 完整配置、数据输入、源码、环境、资源和调度 Job ID 可追溯。
- [ ] Epoch 指标使用明确 Step，训练、验证和测试名称严格隔离。
- [ ] 主目标和 `min`/`max` 方向显式配置，测试集不参与选择。
- [ ] Ray 恢复 Checkpoint 与 MLflow 最终 Artifact 的职责和位置明确。
- [ ] 关键 Checkpoint、模型和诊断 Artifact 通过 MLflow API 回读验证。
- [ ] 失败 Run 保留阶段、异常类型和可用诊断，不覆盖历史 Attempt。
- [ ] 最终测试受角色约束，质量门禁结果与测试是否执行分别记录。
- [ ] Logged Model 与 Registry Model Version、Alias 晋级明确分离。
- [ ] 训练代码不直接接触 MLflow 数据库、MinIO 文件系统或服务端长期凭据。

## 16. 验证命令

文档对应的参考实现可以先运行不产生训练成本的检查：

```bash
source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312

python train-model/ray-cats-and-dogs/scripts/train.py \
  --config train-model/ray-cats-and-dogs/configs/smoke.yaml \
  --check-config

python -m unittest discover \
  -s train-model/ray-cats-and-dogs/tests -p 'test_*.py'
```

需要执行 `--plan` 时，先验证 MLflow 服务和数据目录；它不会启动训练，但会访问完整数据并调用
Tracking API：

```bash
curl -fsS -H 'Host: localhost' http://127.0.0.1:5000/health

python train-model/ray-cats-and-dogs/scripts/train.py \
  --config train-model/ray-cats-and-dogs/configs/smoke.yaml \
  --plan
```

不得为了验证本文而自动启动昂贵的正式训练，也不得自动创建或修改 Registry Alias。
