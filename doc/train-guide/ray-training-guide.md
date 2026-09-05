# Ray 训练接入与运行规范

本文规定本仓库训练项目如何使用 Ray Jobs、Ray Train 和 Ray Data。规范以
[`ray-cats-and-dogs`](../../train-model/ray-cats-and-dogs/README.md) 为已验证参考实现，适用于
不同任务、模型和训练框架；示例项目的图片尺寸、指标名、GPU 数量和超参数不是平台默认值。

MLflow Run、Artifact、最终测试和模型发布规则见
[`mlflow-training-integration-spec.md`](mlflow-training-integration-spec.md)。Ray Head、Runtime
Package 读取凭据和多节点部署见 [`ray-start.md`](../ray-start.md)，Dashboard HTTP 接口见
[`ray-api.md`](../ray-api.md)。

当前参考版本为 Python 3.12、Ray 2.53、PyTorch CUDA 13 和 MLflow 3.14。升级 Ray 后必须重新验证
Runtime Environment 继承、Train Callback、Checkpoint 恢复、State API 和 Job CLI，不能只根据 API
名称相同就假设行为不变。

## 1. 使用边界

平台与工作负载按以下职责分离：

| 组件 | 负责 | 不负责 |
| --- | --- | --- |
| Ray Jobs | 接收、启动、查询、停止一个参数化 Entrypoint | 保存实验历史或自动重试整个训练 Job |
| Ray Train | 创建训练 Worker、分片数据、同步训练、上报指标和恢复一次 `fit()` | 创建生产模型别名 |
| Ray Data | 分块读取、批量转换、流式供数和 Object Store 缓存 | 定义数据版本或决定测试集用途 |
| MLflow | Run、血缘、指标、最终 Artifact、Logged Model 和审计 | 调度 GPU 或替代 Ray Checkpoint |
| MinIO | 持久化 Runtime Package、数据快照和 MLflow Artifact | 充当训练客户端可直接读取的服务端文件系统 |
| Notebook | 检查、短 Smoke、提交和查看结果 | 承载正式训练生命周期 |

正式或长时间训练必须从 `train-model/<project>/scripts/` 下的参数化入口通过 Ray Jobs 提交。直接从
Shell 运行入口只适合开发检查和短 Smoke；它的 Driver 生命周期仍依赖当前终端。

不要用一个 `@ray.remote(num_gpus=1)` 函数包住完整训练循环来模拟 Ray Train。正式分布式训练应使用
框架对应的 Trainer，例如 `TorchTrainer`，以获得明确的 Worker 拓扑、数据分片、Checkpoint 和
Worker 组故障恢复语义。

## 2. 标准执行拓扑

```text
提交端
  |  Ray Jobs API: submission_id + entrypoint + runtime_env
  v
Job Driver / MLflow Run 所有者
  |-- 配置、Tracking、数据、代码身份和资源预检
  |-- Ray Data: path/label -> shuffle -> map_batches -> optional materialize
  |-- TorchTrainer.fit()
  |     |
  |     +-- Train Controller
  |            |-- Worker 0 --\
  |            |-- Worker 1 ----+-> 全局归约 -> train.report
  |            `-- Worker N --/              -> Rank 0 Checkpoint
  |                       |
  |                       `-> Controller Callback 写入既有 MLflow Run
  |
  |-- 最佳 Checkpoint、最终评测、模型和诊断 Artifact
  `-- Artifact API 回读后结束 MLflow Run
```

权威状态只有一份：Driver 创建和结束一个 MLflow Run。Worker 不调用 `mlflow.start_run()`、
`mlflow.log_*()`、`mlflow.end_run()` 或 Registry API，只计算、全局归约并调用 `ray.train.report()`。
Controller Callback 使用 Driver 提供的 `tracking_uri` 和 `run_id` 把 Rank 0 Report 中已经全局归约的
Epoch 指标写入同一个 Run。

### 2.1 必须区分的标识

| 标识 | 产生位置 | 用途 | 是否可替代其他标识 |
| --- | --- | --- | --- |
| Release ID | CI 构建 | 标识不可变代码包和构建环境 | 否 |
| Ray Submission ID | Job 提交端 | 查询、追踪、停止 Ray Job | 否 |
| Ray 内部 Job ID | Ray Runtime | 过滤当前 Job 的 Task、Actor 和 Timeline | 否 |
| MLflow Run ID | Driver | 查询实验、指标、Artifact 和结果状态 | 否 |
| Ray Checkpoint URI | Ray Train | 恢复同一次 `trainer.fit()` 的 Worker 组 | 否 |
| Idempotency Key | 训练入口 | 判断相同数据、代码、配置、Seed 和角色是否已成功 | 否 |

提交系统必须同时保留 Submission ID 和 MLflow Run ID。删除 Ray Job 记录不会删除 MLflow Run，删除
MLflow Run 也不会停止 Ray Job。

## 3. 项目接入结构

每个工作负载位于 `train-model/<project-name>/`，至少包含：

```text
train-model/<project-name>/
├── README.md
├── conda.yaml
├── configs/
│   ├── baseline.yaml
│   ├── smoke.yaml
│   └── <variant>.yaml
├── src/<python_package>/
│   ├── config.py
│   ├── data.py
│   ├── train.py
│   ├── worker.py
│   └── evaluate.py
├── scripts/
│   └── train.py
└── tests/
    └── test_*.py
```

Ray 相关职责建议这样落位：

| 位置 | 职责 |
| --- | --- |
| `scripts/train.py` | 解析参数；区分配置检查、计划和正式训练；延迟导入训练框架 |
| `config.py` | YAML 继承、环境覆盖、类型转换、未知键拒绝和跨字段约束 |
| `data.py` | 不可变 Manifest、确定性切分、Ray Dataset 构造和分片检查 |
| `runtime.py` | `working_dir`、`py_modules`、排除规则和 Controller 序列化边界 |
| `worker.py` | `train_loop_per_worker`、分布式指标、Checkpoint 和 `train.report()` |
| `train.py` | Driver 预检、`TorchTrainer`、MLflow Run、评测和收尾 |
| `tests/` | 不启动正式集群即可验证配置、序列化、数据和所有权边界 |

Notebook 只能调用这些入口，不复制训练循环。项目专属测试放在项目自己的 `tests/`，不能放到仓库级
`tests/`。

## 4. 配置契约

Ray 配置至少显式声明以下字段或等价字段：

| 配置 | 含义与规则 |
| --- | --- |
| `address` | `auto` 连接现有集群；只有明确本地运行时才使用本地 Runtime |
| `num_workers` | 同一个训练中的 Worker 数；大于 1 通常启用同步数据并行 |
| `use_gpu` | 是否为每个训练 Worker 请求 GPU |
| `cpus_per_worker` | 每个训练 Worker 的逻辑 CPU，不包含独立 Ray Data Task |
| `memory_per_worker_gb` | 调度内存请求，不是进程 RSS 的硬隔离上限 |
| `placement_strategy` | `PACK`、`SPREAD`、`STRICT_PACK` 或 `STRICT_SPREAD` |
| `data_num_blocks` | Ray Dataset 调度和分片粒度，不等于 Worker 数 |
| `data_decode_workers` | `map_batches` 最大 CPU Task 并发 |
| `data_decode_batch_size` | 每次数据转换调用处理的记录数 |
| `data_prefetch_batches` | 每个训练 Worker 的前瞻 Batch 数 |
| `data_cache_decoded` | 是否在训练前物化并缓存确定性预处理结果 |
| `max_failures` | 一次 `trainer.fit()` 内允许恢复 Worker 组的次数 |
| `storage_path` | Ray Train Controller 状态和 Checkpoint 的持久化位置 |
| `record_task_timeline` | 是否保存当前 Job 的 Ray Task Timeline |

配置解析必须拒绝未知键，并验证以下关系：

- `objective_metric` 与 `objective_mode` 必须匹配；测试指标不能作为 Trial 目标。
- `data_num_blocks >= data_decode_workers >= 1`，预取数量不得为负数。
- 多 Worker 的 `storage_path` 不得位于节点本地 `/tmp`。
- 只有经批准的最终角色可以读取测试集或记录最终模型。
- 覆盖参数只能修改已经存在的键，解析后的完整配置和摘要必须进入实验记录。

参考配置见
[`baseline.yaml`](../../train-model/ray-cats-and-dogs/configs/baseline.yaml)。`smoke.yaml`、
`distributed.yaml` 和 `champion.yaml` 只表达角色或拓扑差异，通过 YAML 继承保持共同语义一致。

## 5. 资源预算与放置

### 5.1 训练资源

对于同步数据并行：

```text
训练 GPU = num_workers                     # use_gpu=true 时
训练 CPU = num_workers * cpus_per_worker
有效全局 Batch = num_workers * per_worker_batch_size
```

增加 Worker 会同时改变全局 Batch 和优化语义。除非实验明确研究这个变化，否则应同时评估学习率、
梯度累积和 Batch 调整，不能只把 `num_workers` 从 1 改成 2。

Ray 的 GPU 是调度资源，不是显存比例。一个同步训练 Worker 通常请求一张完整 GPU；不要把一张实体
GPU 虚报成多张逻辑 GPU，也不要用多个 `0.5 GPU` Worker 模拟多卡 DDP。`nvidia-smi -L` 的实体
设备数应与 `ray status` 中可用于调度的 GPU 总数一致。

### 5.2 数据资源

启用解码缓存时，数据物化发生在训练前，峰值逻辑 CPU 需求近似为：

```text
max(data_decode_workers, num_workers * cpus_per_worker)
```

关闭缓存时，解码 Task 和训练 Worker 可以并发，需求近似为：

```text
data_decode_workers + num_workers * cpus_per_worker
```

这些公式不包含 Job Driver、Train Controller、Ray 系统进程和操作系统余量。正式提交还必须预留
Head、Object Store、MLflow 和 MinIO 的资源。

### 5.3 Placement

- `PACK` 适合单节点或强调数据局部性的训练。
- `SPREAD` 尽量分散 Worker，但不保证每个 Worker 位于不同节点。
- `STRICT_SPREAD` 才要求每个 Bundle 位于不同节点；资源不满足时任务会保持 Pending。
- `STRICT_PACK` 要求所有 Bundle 位于同一节点，适合有明确高速互联或本地数据要求的场景。

正式配置必须记录 Placement 策略。发现任务长时间 Pending 时先检查 Placement Group 和可用资源，
不要通过夸大节点资源解除调度阻塞。

## 6. 三种入口模式

参考入口提供三个互斥层级：

| 模式 | 外部访问 | 允许的状态变化 | 用途 |
| --- | --- | --- | --- |
| `--check-config` | 无 | 无 | YAML、类型、目标和资源声明检查 |
| `--plan` | MLflow 只读 API、完整数据 | 可在被忽略的本地缓存创建或复用 Manifest；不创建远程 Run | 数据、代码和幂等身份计划 |
| 正式训练 | Ray、MLflow、数据和 Artifact API | 创建独立 Attempt、Checkpoint 和 Artifact | Smoke、Trial 或最终训练 |

从仓库根目录执行无训练成本检查：

```bash
source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312

python train-model/ray-cats-and-dogs/scripts/train.py \
  --config train-model/ray-cats-and-dogs/configs/smoke.yaml \
  --check-config
```

`--plan` 会读取全量数据、计算内容摘要并调用 Tracking API，不能当作常量时间的轻量命令：

```bash
python train-model/ray-cats-and-dogs/scripts/train.py \
  --config train-model/ray-cats-and-dogs/configs/smoke.yaml \
  --plan
```

相同身份已有成功且 Artifact 回读通过的 Run 时，计划返回 `will_train=false`，正式入口默认复用结果。
`--force` 会在同一幂等身份下创建额外 Attempt，只能用于明确、经审查的重跑，不是普通故障重试开关。

## 7. Ray Job 提交方式

### 7.1 开发期本地上传

当前项目可以从仓库根目录把项目代码上传为 Runtime Environment：

```bash
ray job submit \
  --address http://127.0.0.1:8265 \
  --runtime-env-json='{"working_dir":"train-model/ray-cats-and-dogs","py_modules":["train-model/ray-cats-and-dogs/src/ray_cats_dogs"],"excludes":["notebooks/**","tests/**"]}' \
  -- python scripts/train.py --config configs/smoke.yaml --check-config
```

先提交 `--check-config`，再提交 `--plan`，最后才移除模式参数执行训练。正式训练应给提交端生成唯一且
可读的 Submission ID，并保存 Job 输出；不要依赖 Ray 自动生成的 ID 作为外部工作流幂等策略。

Runtime Environment 中：

- `working_dir` 提供入口和配置，并成为远程 Driver 的当前目录；
- `py_modules` 提供真正的 Python 包；
- `excludes` 排除 `.git`、Notebook、测试、缓存和字节码；
- 代码分发不等于依赖分发。各 Ray 节点仍必须安装与项目 `conda.yaml` 一致的 Python、Ray、框架、
  CUDA 和系统库。正式 Job 应通过节点镜像或固定 Conda 前缀提供这些依赖；不要在每个 Job 的
  `runtime_env` 中现场创建完整环境。首次部署可用一次动态 Conda warm-up Job，随后切换为
  `runtime_env.conda` 的绝对前缀。发布门禁应在提交 Trial/Champion 前逐节点执行框架导入和
  `pip check`，并把环境文件 SHA-256 与前缀写入 release manifest。

### 7.2 不可变 MinIO Release

正式发布使用项目的 `job/` 入口构建内容寻址的 `working-dir.zip`、Wheel、Runtime Env 和 Release
Manifest。完整参数和凭据配置见
[`job/README.md`](../../train-model/ray-cats-and-dogs/job/README.md)。

先做不连接 MinIO 或 Ray 的 Dry Run：

```bash
cd /data/ai/chenzhangyue/code/galatea/train-model/ray-cats-and-dogs

/data/conda/envs/attend-ray-py312/bin/python job/ci.py --dry-run
```

真实 `job/ci.py` 默认发布后提交 `check-config`，不会训练。只有显式指定才启动 Smoke：

```bash
/data/conda/envs/attend-ray-py312/bin/python job/ci.py \
  --mode train \
  --config configs/smoke.yaml
```

不要跳过 Smoke 直接提交 `baseline.yaml` 或 `distributed.yaml`。CI 输出中的 Release ID、
Submission ID、Entrypoint、Runtime Env 和 Manifest 路径应进入发布记录。

### 7.3 Submission ID 语义

- 自动 ID 代表一次新提交 Attempt，包含配置、模式、UTC 时间和随机 Token。
- 显式 ID 与相同 Release、Entrypoint 对应的 Job 正在运行或成功时可以幂等复用。
- 显式 ID 已对应不同 Release 或 Entrypoint 时必须拒绝。
- 显式 ID 对应的 Job 已 `FAILED` 或 `STOPPED` 时必须使用新 Attempt ID，不能覆盖旧记录。

Ray Jobs 不会因为客户端重复发送相同业务请求就自动保证训练幂等。提交端幂等、训练入口幂等和 MLflow
Run 身份是三个不同层级，必须分别实现。

## 8. Runtime Environment 与凭据

### 8.1 两条代码分发路径

| 场景 | `working_dir` | `py_modules` | 适用范围 |
| --- | --- | --- | --- |
| 开发期 | 本地项目目录，由 CLI 上传到 Ray | 本地包目录 | 单节点或受控短任务 |
| 正式发布 | 内容寻址的 `s3://.../working-dir.zip` | 内容寻址的 Wheel | 多次复用、CI/CD 和审计 |

参考实现还会把 Job Runtime Env 显式传给 Train Worker。Ray 2.53 的 Train Controller 使用内部
Runtime Env，不自动继承项目包；项目因此把 Worker 函数、输入管线和 Callback 按值序列化给
Controller，同时让 Worker 使用上传后的 `py_modules`。升级 Ray 时必须用项目测试和真实
`--check-config` Job 重新验证这一边界。

### 8.2 三类凭据不得混用

| 凭据 | 使用进程 | 最小权限 |
| --- | --- | --- |
| Runtime Package Publisher | CI 发布进程 | 对 Runtime Prefix 写入和校验 |
| Runtime Package Reader | 每台 Ray Head/Worker 的 Runtime Env Agent | 对 Runtime Prefix 只读 |
| MLflow Artifact | MLflow Server | 对 MLflow Artifact Bucket 读写 |

Runtime Env Agent 在 Driver 启动前下载代码，所以 Reader 凭据必须在执行 `ray start` 前由 Ray
Head 和每台 Worker 继承。把密钥放进 `runtime_env.env_vars` 太晚，也会把长期凭据写入 Job 元数据。

当前 MinIO Endpoint 为 `127.0.0.1:9000`，只适用于单节点 Ray。多节点部署必须使用所有节点可达的
受控私网或 HTTPS Endpoint，并在每个节点预装 `boto3` 和 `smart_open[s3]`。不能为解决
`RuntimeEnvSetupError` 把 Bucket 改成公开读取。

## 9. Ray Data 训练管线

参考项目的数据流如下：

```text
经过校验的 Manifest(path, label, split)
  -> 每个 split 创建 Ray Dataset
  -> 固定 Seed random_shuffle
  -> map_batches CPU 解码和缩放
  -> uint8 NCHW Tensor
  -> optional materialize 到 Object Store
  -> DataConfig 按 Worker 切分
  -> iter_torch_batches 预取和传输
  -> GPU 归一化、随机增强和训练
```

必须遵守以下规则：

- 先在 Ray 外固定数据身份和 Split，不能在每个 Worker 内独立随机切分。
- 使用 `map_batches`，不要为每个小样本创建一个 Remote Task 后一次性 `ray.get()` 全部结果。
- 只缓存确定性预处理结果；随机增强应在每个 Epoch 或 Batch 动态执行。
- 训练集与验证集分开命名，最终测试集不得传入 Trial 的 Trainer。
- 使用等分 Shard 时，提交前验证训练和验证样本数可被 Worker 数整除，避免静默丢样本。
- Block 太少会降低并行度，太多会增加调度和 Object Store 开销；用实际 Timeline 和等待指标调整。

`data_cache_decoded=true` 会把解码后 Tensor 物化到 Object Store，减少重复 I/O，但提高 Object
Store 占用。关闭缓存会让每个 Epoch 重新执行惰性转换，并可能让解码 Task 与 Worker 争用 CPU。

### 9.1 数据位置边界

当前参考实现始终从 `data.root/PetImages` 的文件路径读取图片。`data.source_uri` 只记录经过核对的
数据来源血缘；设置一个 `s3://` 值不会把实际读取切换到 S3。

因此多节点运行当前实现时，所有可能执行解码 Task 的节点必须以相同绝对路径只读挂载同一数据快照。
如果新项目需要直接从对象存储读取，必须在 `data.py` 中实现 Ray Data 的远程 Datasource，并记录
对象版本、Manifest 摘要和节点侧最小权限；不能只修改血缘 URI。

## 10. Ray Train Worker 规范

Driver 使用框架 Trainer 创建 Worker：

```python
trainer = TorchTrainer(
    train_loop_per_worker=train_loop_per_worker,
    train_loop_config=resolved_loop_config,
    scaling_config=ScalingConfig(
        num_workers=config.ray.num_workers,
        use_gpu=config.ray.use_gpu,
        resources_per_worker={
            "CPU": config.ray.cpus_per_worker,
            "memory": config.ray.memory_per_worker_bytes,
        },
        placement_strategy=config.ray.placement_strategy,
    ),
    datasets={"training": train_dataset, "validation": validation_dataset},
    dataset_config=DataConfig(
        datasets_to_split=["training", "validation"],
    ),
    run_config=RunConfig(
        storage_path=config.ray.storage_path,
        failure_config=FailureConfig(max_failures=config.ray.max_failures),
        checkpoint_config=CheckpointConfig(num_to_keep=1),
    ),
)
result = trainer.fit()
```

这只是结构示例；新项目必须按目标 Ray 版本核对构造参数。参考实现位于
[`train.py`](../../train-model/ray-cats-and-dogs/src/ray_cats_dogs/train.py)。

Worker 必须：

1. 从 `ray.train.get_context()` 获取 Rank 和 World Size。
2. 从 `ray.train.get_dataset_shard()` 获取自己的训练和验证分片。
3. 使用框架适配函数准备模型，例如 PyTorch 的 `prepare_model()`。
4. 按 Rank 派生 Seed，并记录无法保证逐位确定性的操作。
5. 在所有 Worker 上归约 Loss、样本数和混淆矩阵，再计算全局指标。
6. 每个 Worker 每个 Epoch 调用一次 `train.report()`，但只让 Rank 0 附带 Checkpoint。
7. 不直接写 MLflow、共享模型目录或 Registry。

局部 Rank 0 进度条可以用于观察训练推进，但不能作为选模依据。Checkpoint 选择和 MLflow 指标必须
来自所有 Worker 的全局归约结果。

## 11. Checkpoint 与恢复语义

一个可恢复 Ray Checkpoint 至少应包含：

- 当前模型和 Optimizer 状态；
- 验证集最佳模型或其可验证引用；
- 已完成 Epoch、最佳 Epoch、最佳目标值和 Early Stopping 状态；
- 模型、训练和输入尺寸配置；
- MLflow Run ID、幂等键和随机种子。

`FailureConfig(max_failures=N)` 的含义是：一次 `trainer.fit()` 中 Worker 组失败后，Ray Train 最多
从最近 Checkpoint 重启 N 次。它不覆盖以下失败：

- Job Driver 进程退出；
- Ray Head 丢失且未配置控制面高可用；
- 用户停止 Job；
- 机器重启后重新提交一个新 Job；
- Runtime Environment 在 Driver 启动前失败。

当前参考实现没有“从旧 Ray Job 的 Checkpoint 自动创建新 Job 并续训”的跨 Job 恢复入口。遇到
Driver、Head 或整 Job 失败时，应保留旧 Ray Job、MLflow Run 和 Checkpoint 证据，评估后创建新的
Submission ID 和 MLflow Attempt；不能把 `max_failures` 或 `--force` 描述成自动断点续训。

多节点 `storage_path` 必须是所有参与节点可访问的同一绝对挂载或共享 URI。Ray Checkpoint 用于运行中
恢复，训练结束后仍要由 Driver 把选定 Checkpoint 写入 MLflow Artifact，并通过 Artifact API 下载和
校验；不能要求客户端读取 Ray 节点临时目录或 MLflow Server 的 MinIO 文件系统。

## 12. 观测与诊断

### 12.1 提交后必须保存

- Ray Submission ID 和内部 Job ID；
- MLflow Run ID 和 Experiment；
- Release ID、Entrypoint 和解析后的配置摘要；
- 数据内容、Split、预处理和代码摘要；
- Ray Checkpoint URI 和最终 Artifact URI。

参考实现会在 Job 日志输出 `run-started` JSON，其中同时包含 MLflow Run ID、Ray Job ID 和幂等键。

### 12.2 常用检查

```bash
ray status
curl -fsS http://127.0.0.1:8265/api/version

ray job list --address http://127.0.0.1:8265
ray job status --address http://127.0.0.1:8265 '<submission-id>'
ray job logs --address http://127.0.0.1:8265 -f '<submission-id>'
```

停止 Job 会中断 Driver，执行前必须确认 Submission ID、Entrypoint 和状态：

```bash
ray job stop --address http://127.0.0.1:8265 '<submission-id>'
```

不要在集群有运行中 Job 时执行 `ray stop` 或重启 `ray-head.service`。

### 12.3 训练指标

至少记录以下类别：

- 训练和验证 Loss、主指标及任务需要的辅助指标；
- Epoch、学习率、全局样本数和每 Worker Batch 数；
- 训练、验证和完整 Epoch 耗时；
- 样本吞吐；
- 数据等待秒数和等待比例；
- 首次数据准备或物化耗时。

如果 GPU 利用率低且 `train_data_wait_fraction` 高，先检查解码并发、Block、预取和 Object Store；
如果等待比例接近零，再考虑 Batch、模型计算量或 GPU Kernel。不要只根据 GPU 利用率猜测瓶颈。

### 12.4 Task Timeline

参考实现通过 State API 仅查询当前 Job 的 Task，导出 Chrome Trace 格式，写入当前 MLflow Run 的
`ray/task-timeline.json` 和 `ray/task-timeline-metadata.json`，再通过 Artifact API 做 SHA-256
回读。可以在 Perfetto UI 或 `chrome://tracing` 打开。

State API 和 Ray 内部 Profiling 接口具有版本敏感性；当前实现查询上限为 10000 个 Task。大型 Job
需要检查元数据中的 Task 数和 API 截断行为，升级 Ray 后必须回归测试。

## 13. 最短排错顺序

| 现象 | 优先检查 | 常见边界 |
| --- | --- | --- |
| Job 长时间 `PENDING` | Job Message、Runtime Env 日志、Placement Group | 代码包下载、依赖安装或资源不足 |
| `RuntimeEnvSetupError` / `AccessDenied` | 每台 Ray 节点的 Reader 身份和 Endpoint | `runtime_env.env_vars` 不能给预启动 Agent 提供凭据 |
| Worker `ModuleNotFoundError` | `working_dir`、`py_modules`、Worker Runtime Env | 只在提交机 `pip install -e` 不能解决多节点导入 |
| Task 找不到本地数据 | 所有节点的数据挂载和绝对路径 | `source_uri` 只是血缘，不会改变读取位置 |
| Placement Group Pending | `ray status`、Dashboard、策略和每 Worker 资源 | `SPREAD` 与 `STRICT_SPREAD` 语义不同 |
| Worker/Actor 失败 | Job 日志、失败 Attempt、最新 Checkpoint | `max_failures` 只恢复 Worker 组 |
| Object Store 压力或 Spill | Block 数、缓存、对象大小、Dashboard Memory | 减小缓存或 Block 前先确认是否重复解码 |
| GPU 利用率低 | 数据等待比例、Task Timeline、Batch 和 Kernel | 不应先增加逻辑 GPU 数 |
| MLflow 无指标 | Run ID、Callback、Rank 0 Report、Tracking URI | Worker 不应自行创建 Run 作为补救 |
| Checkpoint 无法恢复 | `storage_path` 可达性、状态文件和摘要 | 节点本地 `/tmp` 不是多节点持久化 |
| Driver 或 Head 失败 | Job 状态、MLflow Run、Checkpoint 和系统日志 | 当前项目不支持跨 Job 自动续训 |

排错时先保存失败证据，再提交新 Attempt。不要删除失败 Job、覆盖 Release、复用失败 Submission ID，
也不要直接修改 MLflow 数据库或 MinIO 服务端文件。

## 14. 验收清单

### 14.1 代码与配置

- [ ] 正式入口位于 `scripts/`，Notebook 不包含训练实现。
- [ ] 配置显式记录 Worker、CPU、GPU、内存、Placement、数据并发、重试和存储。
- [ ] `--check-config` 不访问外部服务，`--plan` 不创建远程状态。
- [ ] Runtime Env 同时包含 Entrypoint 和可导入的项目包，并排除无关文件。
- [ ] 项目依赖由 `conda.yaml` 或等价环境固定，所有 Ray 节点兼容。

### 14.2 数据与训练

- [ ] 数据内容、Split、预处理和 Seed 可追溯，测试集不进入 Trial Trainer。
- [ ] Ray Data 使用批量转换，Block、并发、预取和缓存可配置。
- [ ] 多节点上的数据路径或远程 Datasource 已真实验证。
- [ ] Worker 使用框架 Trainer、分片 Dataset 和全局指标归约。
- [ ] Rank 0 负责 Checkpoint，所有 Worker 按相同节奏调用 `train.report()`。

### 14.3 运行与恢复

- [ ] 提交端保存 Submission ID、Release ID 和 Entrypoint。
- [ ] Driver 是唯一 MLflow Run 所有者，Worker 不直接写 MLflow。
- [ ] Ray Checkpoint 位置对所有参与节点可达，并包含完整恢复状态。
- [ ] Worker 组恢复、跨 Job 恢复和业务重跑的语义分别记录。
- [ ] 最终 Checkpoint、模型和诊断 Artifact 通过 MLflow API 回读。

### 14.4 安全与验证

- [ ] Dashboard/Jobs API 只对回环或受控网络开放，并按部署要求启用认证。
- [ ] Publisher、Runtime Reader 和 MLflow Artifact 身份分离且最小授权。
- [ ] 密钥不进入仓库、YAML、Runtime Env、Entrypoint、Metadata 或日志。
- [ ] 项目单元测试、四类配置检查和一个真实 `check-config` Ray Job 通过。
- [ ] Smoke 成功后才允许 Trial；批准配置后才执行最终测试和模型发布。

## 15. 参考实现验证

以下检查不会启动正式训练：

```bash
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s train-model/ray-cats-and-dogs/tests -p 'test_*.py'

for config in smoke baseline distributed champion; do
  /data/conda/envs/attend-ray-py312/bin/python \
    train-model/ray-cats-and-dogs/scripts/train.py \
    --config "train-model/ray-cats-and-dogs/configs/${config}.yaml" \
    --check-config
done
```

真实 Ray Jobs 链路先提交 `--check-config`。`--plan` 会访问 MLflow 和全量数据，Smoke 会创建 Run、
使用 GPU 并写 Artifact，必须分别在确认服务、数据、资源和运行授权后执行。本文的验证不授权自动启动
昂贵训练、最终测试或 Registry Alias 变更。
