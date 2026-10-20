# Ray + MLflow 猫狗分类训练（Codex App 复现）

> 本副本的端点示例使用回环地址，仅适用于服务同机访问。实际训练必须显式设置
> `GALATEA_RAY_PROFILE`、`RAY_ADDRESS`、`RAY_API_SERVER_ADDRESS` 和 `MLFLOW_TRACKING_URI`；
> 多节点使用所有节点可达的端点，机器私有配置与凭据不进入 Git。

本目录复现 [`../ray-cats-and-dogs/`](../ray-cats-and-dogs/) 的完整工程。
新项目和 MLflow Experiment 为 `ray-cats-and-dogs-codex-app`，Release、证据和
Ray 状态目录独立；Python 包名和锁定的 `ray-cats-and-dogs-py312` 环境保持一致。
本次复现参数、执行顺序和验证边界见 [复现说明](docs/reproduction.md)。
原文中的历史 Run ID、指标和旧临时集群配置仅是来源项目的历史参考，不是本项目训练结果。

本项目把 [`../cats-and-dogs/`](../cats-and-dogs/) 中的 Notebook 训练流程重构为可恢复、
可审计、可由 Ray Job 提交的正式 PyTorch CUDA 13 训练项目。它复用确定性数据切分、
MLflow 数据血缘和测试集门禁思想，并把配置、实现、入口与测试分开。

本页说明本项目的具体命令和配置。跨项目的 Ray Jobs、Ray Train、Ray Data、Runtime
Environment、资源和恢复规则以
[`doc/train-guide/ray-training-guide.md`](../../doc/train-guide/ray-training-guide.md) 为准；MLflow
所有权和 Artifact 契约见
[`mlflow-training-integration-spec.md`](../../doc/train-guide/mlflow-training-integration-spec.md)。

当前阶段使用 Ray Train 做单 Worker 或多 Worker 数据并行训练。MLflow 的权威写入者始终
只有 Driver/Train Controller：Worker 只做计算、向 Ray 上报指标和 Checkpoint，不创建、
结束或直接写入 MLflow Run。

单 GPU 基线使用流水线并行而不是让多个 DDP Rank 争抢同一张卡：16 个 Ray Data CPU Task
把 Manifest 分成 64 个 Block，并行读取和缩放图片；紧凑的 `uint8` NCHW Tensor 缓存在 Ray
Object Store。训练 Worker 以 4 Batch 预取，传入 GPU 后执行向量化随机仿射增强，并以 BF16、
Channels Last 和 TF32 训练。`iter_torch_batches(device="auto", pin_memory=True)` 在独立线程
完成 Tensor 拼装和主机到设备传输，使 CPU 数据准备与 GPU 计算各用适合自己的 Ray 资源。

正式训练入口先验证实际 Ray Driver 与 Galatea Release、Readiness 元数据的绑定，再启用
`mlflow.autolog()`。Tracking URI 优先读取 `MLFLOW_TRACKING_URI`，未设置时使用 YAML 中的
`mlflow.tracking_uri`；Trace 和 Run 都写入同一 `ray-cats-and-dogs-codex-app` Experiment。

```text
scripts/train.py
  ├── 校验 YAML、MLflow、数据摘要与幂等键
  ├── 创建一个 MLflow Run
  ├── Ray Data Task Pool ── 并行解码 ── Object Store Tensor Cache
  └── Ray Train Controller
      ├── Worker 0 ─┐
      ├── Worker 1 ─┼── 全局验证指标 + 可恢复 Checkpoint
      └── ...       ┘
              │
              └── Driver 写入 MLflow/MinIO，Champion 才读取一次测试集
```

## 项目结构

```text
train-model/ray-cats-and-dogs-codex-app/
├── README.md
├── galatea.project.yaml   # Galatea 项目能力、证据和质量门禁声明
├── conda.yaml
├── pyproject.toml
├── configs/
│   ├── baseline.yaml       # 单 Worker、验证集 Trial
│   ├── smoke.yaml          # 1 Epoch 链路检查，不读取测试集
│   ├── distributed.yaml    # 2 Worker Trial
│   └── champion.yaml       # 冻结 Trial 最佳权重，只执行一次最终测试
├── job/
│   ├── ci.py               # 构建、发布并默认执行 CD 配置检查
│   ├── cd.py               # 提交已发布的不可变 Runtime Environment
│   └── README.md            # MinIO Release、凭据和排错说明
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

Notebook 不是正式训练依赖，只用于检查、提交和查看结果；训练统一由 Galatea 授权后通过固定 Ray Job Driver 执行。

## 1. 环境

从仓库根目录在每个 Ray 节点预装项目环境（只做一次）：

```bash
source /data/miniconda3/etc/profile.d/conda.sh
export RAY_CONDA_HOME=/data/miniconda3
export CONDA_DEFAULT_CHANNELS=
export CONDA_PKGS_DIRS=/data/miniconda3/pkgs
export PIP_CACHE_DIR=/var/cache/ray/pip
/data/miniconda3/bin/conda env create \
  --file train-model/ray-cats-and-dogs-codex-app/conda.yaml \
  --prefix /data/miniconda3/envs/ray-cats-and-dogs-py312
/data/miniconda3/envs/ray-cats-and-dogs-py312/bin/python \
  train-model/ray-cats-and-dogs-codex-app/job/warmup.py
conda activate /data/miniconda3/envs/ray-cats-and-dogs-py312
python -m pip install -e train-model/ray-cats-and-dogs-codex-app
python -m ipykernel install --user --name ray-cats-and-dogs-py312 \
  --display-name "Python (ray-cats-and-dogs-codex-app)"
```

共享平台环境已经包含相同版本的 Ray、MLflow 和 PyTorch CUDA 13 时，也可以先用它做配置检查：

```bash
source /data/miniconda3/etc/profile.d/conda.sh
conda activate attend-ray-py312
python -c "import torch, ray; print(torch.__version__, torch.version.cuda); print(ray.__version__)"
```

## 2. 平台与数据前置检查

正式训练前确认 Ray、MLflow 和 MinIO 正常：

```bash
export GALATEA_RAY_PROFILE=dev
export RAY_ADDRESS=127.0.0.1:18104
export RAY_API_SERVER_ADDRESS=http://127.0.0.1:8265
export MLFLOW_TRACKING_URI=http://127.0.0.1:18103
systemctl is-active minio.service mlflow.service
curl -fsS -H 'Host: localhost' http://127.0.0.1:18103/health
curl -fsS http://127.0.0.1:18101/minio/health/live
curl -fsS "$RAY_API_SERVER_ADDRESS/api/version"
ray status --address="$RAY_ADDRESS"
nvidia-smi -L
```

这是当前机器的开发 profile。换机器或使用生产 Ray 时，先按
[`平台运行记忆`](../../doc/platform-runtime-memory.md) 核对目标 GCS、Jobs API、unit、节点、GPU
及 MLflow；不自动发现其他集群，也不使用全局 `ray stop`。

`ray status` 中的 GPU 总数必须与分配给该集群的实体设备数一致。当前开发集群仅使用 GPU 0、3、6。Ray 的 GPU 数是调度
资源而不是显存百分比；单卡节点应以 `--num-gpus=1` 启动 Ray。把一张实体卡声明成 4 张逻辑
GPU 会让调度器错误地把多个 DDP Rank 放到不存在或重复的设备上。

默认数据目录与原项目相同：

```text
/data/ai/chenzhangyue/galatea-model/train-data/microsoft-cats-vs-dogs/PetImages/
├── Cat/
└── Dog/
```

本副本复用源项目已冻结的 Manifest，仅校验训练/验证图片的 SHA-256；最终测试图片
留给取得 test-once claim 后的 Champion。首次导入新数据时，源项目的完整检查流程
会只读原图并计算 SHA-256。Manifest 写入被 Git 忽略的
`platform-data/ray-cats-and-dogs-codex-app/manifests/`，随后通过 MLflow Artifact API 保存。原始
数据集的三张已知坏图会被记录并排除，不会被删除或修改。

如果训练节点读取的是某个已校验的 MinIO 数据快照，可声明真实来源 URI；不要把本地缓存
伪装成未核对的对象版本：

```bash
export CATS_DOGS_DATASET_SOURCE_URI=\
s3://training-data/datasets/raw/microsoft-cats-vs-dogs/2026-07-30/PetImages
```

`CATS_DOGS_DATASET_SOURCE_URI` 只记录经过核对的来源血缘；当前数据加载仍读取 `data.root/PetImages`
的本地路径，不会因为设置该变量而切换成 S3 Datasource。多节点训练必须让所有解码节点以相同绝对
路径只读挂载同一快照，或先实现并验证真正的远程 Datasource。

## 3. 先做不训练的检查

只校验配置继承、类型、目标指标和资源声明，不访问数据或服务：

```bash
python train-model/ray-cats-and-dogs-codex-app/scripts/train.py \
  --config train-model/ray-cats-and-dogs-codex-app/configs/smoke.yaml \
  --check-config
```

完整只读计划会访问 MLflow Tracking API、核对冻结切分及训练/验证数据并计算幂等键，但不会创建
Experiment/Run、启动 Ray Worker 或训练模型。Experiment 尚不存在时会报告
`experiment_exists=false`，第一次正式训练再创建：

```bash
python train-model/ray-cats-and-dogs-codex-app/scripts/train.py \
  --config train-model/ray-cats-and-dogs-codex-app/configs/smoke.yaml \
  --plan
```

计划中的 `will_train=false` 表示相同数据、切分、代码、配置、Seed 和角色已经有一个成功
且 Artifact 回读通过的 Run。正式命令默认直接复用该 Run，不重复消耗算力。

## 4. 发布并通过 Galatea 提交训练

所有训练（包括 1 Epoch Smoke）必须走不可变 Release、Galatea 只读计划及证据绑定授权。
本地 `scripts/train.py` 仅允许 `--check-config` / `--plan`；通用 `ci.py` / `cd.py --mode train`
会拒绝训练。旧 Notebook 的通用提交单元同样不能启动训练。

在项目目录执行：

```bash
export GALATEA_RAY_PROFILE=dev
export RAY_ADDRESS=127.0.0.1:18104
export RAY_API_SERVER_ADDRESS=http://127.0.0.1:8265
export MLFLOW_TRACKING_URI=http://127.0.0.1:18103
python job/publish.py --env-file /etc/minio/training-data-s3.env \
  --output-dir /data/ai/chenzhangyue/galatea-model/platform-data/ray-cats-and-dogs-codex-app-release
# 用发布输出中的实际路径替换占位符。
RELEASE_MANIFEST=/data/ai/chenzhangyue/galatea-model/platform-data/ray-cats-and-dogs-codex-app-release/<release-id>/release.json
node job/governed.mjs plan configs/smoke.yaml "$RELEASE_MANIFEST" - smoke-01
# 以下提交适用于已获得训练授权的 full-access 会话。
node job/governed.mjs submit configs/smoke.yaml "$RELEASE_MANIFEST" danger-full-access smoke-01
node job/governed.mjs submit configs/baseline-repro-20261019.yaml "$RELEASE_MANIFEST" danger-full-access baseline-01
```

`governed.mjs` 调用仓库 Galatea Controller：重算 Readiness，把当前授权绑定到精确的证据摘要，
再渲染项目声明的固定 Driver。计划与提交回执保存在仓库外的
`platform-data/ray-cats-and-dogs-codex-app-evidence/`，分别包含 `target` 与 `planned`/`submitted` 字段。
提交器要求上述显式 profile、GCS、Jobs API 和 Tracking URI，不再使用硬编码服务地址；该 profile
是记录标签，尚不校验生产 ACL。目标 Head/Worker 的服务级 GCS 和 Tracking URI 必须与选择一致。
更改配置或源码后必须重新发布。

完整训练任务授权范围内的必要不可变 Release 上传使用上述 publisher 身份即可继续；单独分析或
改代码不授权发布/训练。Runtime reader 文件和 MLflow 服务端文件不能用于发布，密钥不得放进 Job。

基线为单 GPU、Batch 128、BF16，最多 36 Epoch，验证准确率连续 3 Epoch 不改善时提前停止。
Smoke 和 Trial 只用 train/validation，不进行最终测试或模型提升。

## 5. Ray 作业与产物

发布器使用固定的预装 Conda 前缀；`working_dir` 上传配置和入口，`py_modules` 把真正的包目录
`src/ray_cats_dogs` 加入 Python 导入路径。Driver 会把上传后
生成的 Runtime URI 显式传给 Ray Train Worker。Ray 2.53 的 Train Controller 只保留内部
环境变量，因此 Worker 函数和 MLflow Callback 会按值序列化给 Controller；不依赖训练
节点预先执行 `pip install -e`，也不依赖所有节点共享源码绝对路径。

训练进行时，Rank 0 Worker 会在 Ray Job 日志中输出训练和验证的 batch 级 `tqdm` 进度条。
进度条后缀的 loss/accuracy 是 Rank 0 当前 shard 的即时值，只用于观察任务是否推进；
Checkpoint 选择和 MLflow 中的 epoch 指标仍使用全部 Worker `all_reduce` 后的全局值。
每个 epoch 的 `train.report` 同时写入 MLflow，包括 loss、accuracy、Cat/Dog 与 macro
precision/recall/F1、样本吞吐、batch 数、学习率和各阶段耗时。Smoke Notebook 的监控
单元会轮询 Ray Job，并优先从 MLflow Metric History 展示这些指标；MLflow 尚未刷新时，会
回退到 Worker 输出的 epoch JSON。`FOLLOW_JOB=False` 可关闭持续轮询。

每个 Epoch 还会上报 `train_data_wait_seconds` 和 `train_data_wait_fraction`。如果 GPU 利用率
仍低而等待比例较高，应先调整 `ray.data_decode_workers`、`ray.data_num_blocks` 和 Object
Store；如果等待比例接近零，再增大模型或 Batch。`data_preparation_seconds` 单独记录首次
并行解码和 Tensor Cache 构建时间，不混入 Epoch 吞吐。

入口会同时记录 Ray Job ID、MLflow Run ID、数据/切分/代码摘要、完整资源与超参数，以及 Ray
Checkpoint URI。`configs/*.yaml` 默认将 Ray 执行态保存在
`platform-data/ray-cats-and-dogs-codex-app/ray-results/`。跨主机集群必须让所有节点挂载同一绝对路径，或把
`ray.storage_path` 改成集群已配置凭据的共享 URI，例如 S3；训练客户端不应读取 MLflow
服务端的 MinIO 文件系统。

`ray.max_failures` 只恢复同一次 `trainer.fit()` 内失败的 Worker 组。当前项目没有 Driver、Head 或
整 Job 失败后的自动跨 Job 续训入口；这种失败必须保留原 Job、Run 和 Checkpoint 证据，评估后用新
Submission ID 和新 MLflow Attempt 重提，不能用 `--force` 冒充断点恢复。

`ray.record_task_timeline: true` 默认让 Driver 在任务收尾时通过 Ray State API 导出当前
Ray Job 的 Dashboard 时间线，而不是导出可能混入其他 Job 的集群级 Timeline。Trace 保存为
当前 MLflow Run 下的 `ray/task-timeline.json`，元数据保存为
`ray/task-timeline-metadata.json`。Driver 会再次通过 MLflow Artifact API 下载 Trace 并核对
SHA-256；因此训练进程不直接访问 MinIO 文件系统，也不需要 MinIO 长期凭据。可以在 MLflow
Artifacts 页面下载 JSON，再用 Perfetto UI 或 `chrome://tracing` 打开。正常完成时 Trace
写入或回读失败会让 Run 失败；训练本身已失败时则尽力保留 Trace，同时不覆盖原始异常。

## 6. Champion 与最终测试

Trial 的唯一主目标是 `val_accuracy`，方向为 `max`。测试集不参与搜索、Early Stopping、
Checkpoint 选择或 Trial 排序。根据兼容数据版本、切分、预处理和验证协议选定配置后，
候选 Trial 必须在执行前声明 `run.promotable: true`；该字段仅表示候选资格，
不授权 Registry 或 Alias 操作。Champion 继承单 Worker baseline 参数，读取被批准 Trial 的
最佳 Checkpoint，不再更新参数。最终配置的 `evaluation.manifest_path` 必须显式指向已验证的
不可变 CSV；规划和 Driver 只读取 manifest 元数据，不重新解码测试图片。

```bash
node job/governed.mjs submit configs/champion-repro-20261019.yaml \
  "$RELEASE_MANIFEST" danger-full-access final-01 "$CANDIDATE_RUN_ID"
```

固定入口使用现有 Controller 重算 Trial 的 `training-optimization` 证据与授权，将候选证据绑定
到 Champion execution identity。它在 `data.cache_dir` 的父目录下 `governance/freezes/`
排他写入候选冻结记录。Driver 对照真实 Jobs API 元数据、数据/split/config 摘要，并通过
MLflow Artifact API 下载、校验冻结 Checkpoint SHA-256，复用 Checkpoint 回读与 CPU 前向验证。
共享治理客户端必须根据 Run 的 `info.artifact_uri` 解析代理根路径，包含 Experiment/Run
前缀；`run_id` 查询参数不能代替该路径。下载限制在配置的 HTTP 服务，拒绝外部 Artifact
主机或直接对象存储 URI，避免将 Tracking 凭据传给其他服务。

评测 Worker 在读取任何测试样本前，用 Checkpoint SHA-256、split SHA-256 和固定
`ray-cats-dogs-frozen-checkpoint-final-v1` 协议组成排他键，在同一持久卷的
`governance/test-uses/` 以 `O_EXCL` 原子创建并 fsync claim；submission identity 只作为绑定
字段。任何重复取得（包括相同 Job 重启、换 Attempt）都失败；失败或损坏的 claim 不释放。
Ray 评测 Task 的 `max_retries=0`，取得 claim 后先核对测试文件内容摘要再解码。
该实现要求单主机本地 POSIX 持久卷，不宣称 NFS/HA 或跨平台服务的全局 test-once；
备份、恢复和迁移必须连同冻结记录与 claim，禁止清空状态后重读测试集。

只有 `role: champion` 可以设置 `evaluate_test: true` 和 `log_model: true`。Champion 会读取
验证集最佳 Checkpoint，对固定测试集评估一次，记录 Accuracy、Precision、Recall、F1、
ROC AUC、预测摘要和质量门禁，并生成 MLflow Logged Model。模型会在 Logged Model 回读后
再次发布为 Run 下的 `model/` Artifact，并通过 Artifact API 回读 `model/MLmodel`；这样
Galatea 平台治理可以只依赖 Run ID 和正式 API 重算推广证据。未通过门禁的 Run 仍完整保留，
但不会注册、设置或修改任何生产模型 Alias；发布必须另行人工审查和显式执行。

### 模型独立加载与历史产物兼容

后续 Release 的 `code_paths` 直接指向 `src/ray_cats_dogs` 包目录，使 MLflow 自动加入
`model/code` 后能导入该包。Driver 在发布 Run-scoped 模型前，从 Artifact API 下载 Logged
Model，在空工作目录下以 `python -I` 启动新进程，用默认 `mlflow.pytorch.load_model`
加载并做零输入 CPU 前向检查，同时验证导入的包来自下载产物。该检查不读取数据集。

历史 Run `3cac081340b6498a85410b5d121cf17e` 使用旧 Release，产物布局为
`model/code/src/ray_cats_dogs`。在无本地项目路径的新进程中，默认 loader 会报
`ModuleNotFoundError: ray_cats_dogs`；历史模型没有被覆盖。下面的工具只通过 Tracking /
Artifact API 下载模型及 Checkpoint，显式加入该历史产物的 `code/src`，检查冻结 SHA-256、
模型全部权重一致性，并执行零输入 CPU 前向。它无需本地项目包或 MinIO 凭据。
仅对受信任的模型使用此工具，因为 MLflow PyTorch 模型包含可执行的 Python 序列化内容。

```bash
/data/miniconda3/envs/ray-cats-and-dogs-py312/bin/python -I \
  train-model/ray-cats-and-dogs-codex-app/scripts/load_model.py \
  --tracking-uri http://127.0.0.1:18103 \
  --run-id 3cac081340b6498a85410b5d121cf17e \
  --checkpoint-sha256 401643cfd1a551a97295c440fc868d6eadfaf378a061d63f8a2867554168fee8 \
  --output-dir /tmp/ray-cats-dogs-frozen-model
```

`load-verification.json` 的 `loader_mode=artifact-code-src-compatibility` 明确表示历史兼容
加载，并不表示默认 loader 已通过。后续新布局则使用 `loader_mode=default`。该修复仅影响
未来打包，必须发布新的不可变 Release 后才能用于正式执行；不修改历史 Run 或重读测试集。
应用也可导入此独立脚本的 `load_run_model(tracking_uri, run_id, checkpoint_sha256,
output_dir)`，取得 `(model, verification)`；`model` 已在 CPU 上加载并切换到 `eval()`。

这套边界表示“验证集最佳的已观察配置”，不表示有限参数试验已经找到全局最优模型。

## 7. 资源和确定性说明

- `ray.num_workers`、每 Worker CPU/GPU/内存、Placement、重试次数及评测资源均在 YAML 明确声明。
- `ray.data_decode_workers` 限制并行 `map_batches` Task 数；`data_num_blocks` 决定可调度粒度，
  `data_prefetch_batches` 控制训练 Worker 的前瞻读取。
- `ray.data_cache_decoded: true` 只缓存缩放后的 `uint8` Tensor，不缓存随机增强结果；随机水平
  翻转、旋转和平移在 GPU 上逐 Batch 生成，因此不同 Epoch 仍获得不同增强。
- 每个 Worker 接收同一份模型、数据摘要、配置、Seed、MLflow Run ID 和幂等键。
- 训练集和验证集必须能被 Worker 数整除，避免 Ray `equal` Shard 静默丢弃余数样本。
- 文件切分和缓存前的全局 Shuffle 使用固定 Seed；多 Worker 浮点归约、GPU Kernel 和随机增强仍可能产生细微非确定性，Run 不宣称逐位复现。
- 每个 Epoch 的 Ray Checkpoint 同时包含当前 PyTorch `state_dict`、验证集最佳模型和训练状态；失败恢复不会覆盖其他 MLflow Attempt。
- 最佳 Checkpoint 会通过 MLflow Artifact API 下载并核对 SHA-256；Logged Model 也会回读
  `MLmodel` 描述文件。任何回读失败都会让 Run 标记为失败。

## 8. 测试

项目测试不连接 MLflow、不启动 Ray 集群，也不执行训练：

```bash
/data/miniconda3/envs/attend-ray-py312/bin/python -m unittest discover \
  -s train-model/ray-cats-and-dogs-codex-app/tests -p 'test_*.py'
```

测试覆盖配置继承与越权测试集保护、确定性 Manifest、坏图隔离、模型输入输出，以及 Ray
Controller 只把 Rank 0 指标写入既有 MLflow Run 的所有权规则。`test_galatea_contract.py`
还验证项目声明与入口、MLflow Param/Tag、阶段 Artifact 和 Run-scoped 模型 URI 一致。
`test_model_artifacts.py` 用无训练的临时模型验证实际打包布局及独立进程的默认加载，
不创建 MLflow Run。

项目声明的主目标是 `val_accuracy`/`max`，并显式声明当前 `pauseResume: false`。通过 Codex
操作本项目时，先按项目 `job/README.md` 发布不可变 `release.json`，再使用固定治理入口
完成计划、证据绑定和提交。仓库入口使用独立的
[`galatea-governance`](../../packages/galatea-governance/README.md)；
Web Agent 当前是通用 Host，不会自动把本项目 YAML 转为 MCP Campaign。接入 Campaign 时需在
独立 [`Galatea MCP`](../../services/galatea-mcp/README.md) 登记对应契约，再按
[`Codex Host 部署说明`](https://github.com/lunasaw/open-codex/blob/6ef38b998b465eadf1295b8d30378747c62d2ba8/docs/06-config-deployment.md)配置 MCP 并验收。
两条治理路径的授权、Plan 和状态分别维护，见 [架构说明](../../doc/agent-galatea.md)。

## 9. Runtime Env 排错

如果 Driver 能导入 `ray_cats_dogs`，但日志中的 `TrainController` 或 Worker 报
`ModuleNotFoundError: No module named 'ray_cats_dogs'`，先确认提交参数同时包含项目
`working_dir` 和 `src` 的 `py_modules`。项目会在开始训练前检查 Worker Runtime Env；缺少
`py_modules` 会直接给出配置错误，而不是创建 Worker 后再以 `ActorDiedError` 失败。

不要用只在当前节点执行 `pip install -e` 的方式掩盖问题；多节点任务应通过 Runtime Env
分发源码，依赖版本则由项目 `conda.yaml` 统一提供。
