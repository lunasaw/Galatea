# 通过 MinIO 发布 Ray Job

这个目录提供与当前仓库环境匹配的 CI/CD 发布入口：

1. `ci.py` 创建确定性的 `working-dir.zip`，构建 `ray_cats_dogs` Wheel，生成发布清单并上传
   MinIO；默认在成功后继续执行 CD。
2. `cd.py` 从发布清单读取 `s3://` Runtime Environment，通过 Ray Jobs API 提交任务。
3. `warmup.py` 在部署窗口验证每个节点的预装 Conda 环境；它不提交 Ray Job。

`publish.py` 和 `submit.py` 保留为兼容入口，分别等价于“仅 CI 发布”和“单独 CD”。重复发布
不会重新打包成本地 `gcs://_ray_pkg_*`，也不会让 Ray 隐式覆盖 MinIO 上已有的代码对象。
不传 `--submission-id` 时，每次执行会自动生成包含配置名、模式、UTC 时间和随机 Attempt
Token 的唯一 ID，例如 `ray-cats-dogs-baseline-train-20260803t093000z-a1b2c3d4`。显式传入
ID 时，同一 Release、Entrypoint 和 `submission_id` 对应的 Job 正在运行或已经成功，CD 会
返回已有 Job；失败或停止的显式 ID 不能复用。

默认执行完整流水线：

```bash
cd /data/ai/chenzhangyue/galatea-model/galatea/train-model/ray-cats-and-dogs-codex-app

/data/miniconda3/envs/attend-ray-py312/bin/python job/ci.py
```

默认 CD 模式为 `check-config`，会提交 Ray Job 验证 Runtime Env 和配置，但不会启动训练。
通用 CD 的 `--mode train` 已禁用；训练使用 `governed.mjs`，参见项目 README。

`governed.mjs` 由 Codex 仓库工作流调用，使用独立的
[`galatea-governance`](../../../packages/galatea-governance/README.md)校验 Release、
Readiness 和提交授权。首次使用先在仓库根目录安装该库的锁定依赖：

```bash
npm ci --prefix packages/galatea-governance
```

Web Agent 使用 [`Codex app-server Host`](https://github.com/lunasaw/open-codex/blob/b5d9a0d26b14a8d8c0c308b6229bba866ac4d70a/README.md)；
接入其 Campaign 工具集需要单独登记 workload 与批准配置。

新机器和同机多集群先读 [`平台运行记忆`](../../../doc/platform-runtime-memory.md)。本页通用
CI/CD 中的 loopback 默认值只适用于核对过的同机目标；选择其他集群时显式设置
`RAY_API_SERVER_ADDRESS` 或传 `--address`，不能据此推算生产地址。训练提交器要求显式
`GALATEA_RAY_PROFILE`、`RAY_ADDRESS`、`RAY_API_SERVER_ADDRESS` 和 `MLFLOW_TRACKING_URI`。
必要上传遵循当前任务已授予的目标/Prefix 范围，不重复请求同一授权；发布用 publisher，下载用
Head/Worker 继承的 reader，MLflow Artifact 使用服务端代理。

正式发布默认使用节点预装的固定 Conda 前缀
`/data/miniconda3/envs/ray-cats-and-dogs-py312`。`conda.yaml` 仍是权威来源，环境文件 SHA-256
和前缀会写入 `release.json`；Ray Job 的 `runtime_env.conda` 只携带绝对路径，不会在启动时
重新创建或下载 Torch/Ray。所有节点必须使用相同路径和内容。首次部署或新环境 hash 可显式
使用 `--runtime-mode conda` 让 Ray 执行一次动态环境预热；pip 仅用于兼容性 Smoke：

```bash
python job/ci.py --runtime-mode pip \
  --pip-requirements /path/to/requirements-ray-smoke.txt --no-cd
```

可用 `--pip-package 'transformers @ git+https://...@<commit>'` 重复追加固定包。生产
Trial/Champion 不应依赖共享环境中的包版本；节点需要设置 `RAY_CONDA_HOME=/data/miniconda3`。

### 部署窗口：每个节点预装并验证一次

```bash
source /data/miniconda3/etc/profile.d/conda.sh
export RAY_CONDA_HOME=/data/miniconda3
export CONDA_DEFAULT_CHANNELS=
export CONDA_PKGS_DIRS=/data/miniconda3/pkgs
export PIP_CACHE_DIR=/var/cache/ray/pip

/data/miniconda3/bin/conda env create \
  --file train-model/ray-cats-and-dogs-codex-app/conda.yaml \
  --prefix /data/miniconda3/envs/ray-cats-and-dogs-py312

/data/miniconda3/envs/ray-cats-and-dogs-py312/bin/python job/warmup.py
```

`warmup.py` 会导入 `ray`、`torch`、`mlflow`，输出版本/CUDA 信息并执行 `pip check`。多节点
部署必须在每个节点运行；某节点未预热时，任务调度到该节点仍可能触发冷启动。

如果不能预装，可在维护窗口先提交一次只做配置检查的动态 Conda Job，等待所有节点环境创建
成功后再恢复默认的 `preinstalled` 模式：

```bash
python job/ci.py --runtime-mode conda --mode check-config
```

不要把 `RAY_JOB_START_TIMEOUT_SECONDS=1800` 或 `3600` 当作长期方案；它只能临时保护一次冷启动。

## 当前环境默认值

| 配置 | 默认值 |
| --- | --- |
| Python | `/data/miniconda3/envs/attend-ray-py312/bin/python` |
| Ray Job Conda prefix | `/data/miniconda3/envs/ray-cats-and-dogs-py312` |
| Ray Dashboard | `http://127.0.0.1:8265` |
| MinIO S3 Endpoint | `http://127.0.0.1:18101` |
| 凭据文件 | `/etc/minio/training-data-s3.env` |
| Bucket | `training-data` |
| Object Prefix | `ray-runtime/ray-cats-and-dogs-codex-app` |
| 本地发布目录 | `/tmp/ray-cats-and-dogs-codex-app-job/<release-id>/` |
| Ray Submission ID | 自动生成；可用 `--submission-id` 显式覆盖 |

默认复用现有 `training-data` Bucket 和账号，所以无需创建新的 Bucket 策略即可演示。
生产环境应使用独立的 `ray-runtime` Bucket，并把上传账号和 Ray 节点只读账号分开。

## 1. CI 参数

完整流水线 Dry Run 不连接 MinIO 或 Ray：

```bash
cd /data/ai/chenzhangyue/galatea-model/galatea/train-model/ray-cats-and-dogs-codex-app

/data/miniconda3/envs/attend-ray-py312/bin/python \
  job/ci.py --dry-run
```

只构建并上传，不执行 CD：

```bash
/data/miniconda3/envs/attend-ray-py312/bin/python \
  job/ci.py --no-cd
```

上传 MinIO 后只校验 CD 请求、不连接 Ray：

```bash
/data/miniconda3/envs/attend-ray-py312/bin/python \
  job/ci.py --cd-dry-run
```

输出中的 `manifest_path` 指向 `release.json`。同目录还会生成：

```text
working-dir.zip
ray_cats_and_dogs_codex_app-0.1.0-1<source-sha256>-py3-none-any.whl
runtime-env.yaml
release.json
```

`working-dir.zip` 不包含 `job/`、`notebooks/`、`outputs/`、`tests/`、Notebook Checkpoint 或 Python Cache。
Wheel 作为 `py_modules` 使用，避免远程 Module ZIP 顶层目录解包语义导致导入路径错误。
Ray 的远程 Wheel 缓存只使用文件名，不包含 Bucket 或 Release 路径，因此文件名中的构建标签
绑定源归档 SHA-256，避免不同 Release 共用安装目录。相同源归档仍生成相同 Wheel 文件名。

## 2. CI 上传并自动执行 CD

确认 MinIO 健康并执行真实发布：

```bash
curl -fsS http://127.0.0.1:18101/minio/health/live

/data/miniconda3/envs/attend-ray-py312/bin/python \
  job/ci.py
```

脚本只解析凭据文件中的 AWS 变量，不执行其中的 Shell 内容，也不会输出密钥。对象路径包含
Runtime Package 内容摘要；如果同一个 Key 已存在，脚本只接受大小和 `sha256` Metadata
完全一致的对象，否则拒绝覆盖。

可通过环境变量或参数改用独立 Bucket：

```bash
RAY_JOB_S3_BUCKET=ray-runtime \
RAY_JOB_S3_PREFIX=projects/ray-cats-and-dogs-codex-app \
/data/miniconda3/envs/attend-ray-py312/bin/python \
  job/ci.py --env-file /etc/minio/ray-runtime-publisher.env
```

## 3. 让 Ray 节点可以下载

上传脚本读取凭据只会影响发布进程。Ray Runtime Environment Agent 在启动 Driver 或 Worker
之前下载 ZIP/Wheel，因此不能依赖 `runtime_env.env_vars` 提供 S3 凭据。

Head 和每台 Worker 必须在执行 `ray start` 前获得以下变量，并预装 `boto3` 和
`smart_open[s3]`：

```bash
AWS_ACCESS_KEY_ID=ray-runtime-reader
AWS_SECRET_ACCESS_KEY=<由受控密钥系统注入>
AWS_DEFAULT_REGION=us-east-1
AWS_ENDPOINT_URL_S3=http://127.0.0.1:18101
```

当前开发 Head 通过 `ray-head.service` 注入专用只读凭据，各 Worker 必须同样注入：

```bash
systemctl show ray-head.service \
  -p ActiveState \
  -p SubState \
  -p FragmentPath
```

已经运行的 Ray 进程不会获得后来注入的变量；需要在计划维护窗口中重启 Head 和 Worker，
不能只在执行 `cd.py` 的终端中 `source` 凭据文件。账号创建、Unit 安装和轮换步骤见
仓库的 `doc/ray-start.md`。

如果 Job 在 `PENDING` 阶段以 `RuntimeEnvSetupError` 失败，并包含以下信息：

```text
AccessDenied when calling GetObject
unable to access bucket ... working-dir.zip
```

说明发布端已经能写入对象，但 Ray Runtime Environment Agent 没有对应的读取权限。处理顺序是：

1. 确认 Ray Head 和每台 Worker 使用的账号对 Runtime Prefix 有 `s3:GetObject` 权限。
2. 在计划维护窗口停止相关 Ray 进程。
3. 注入 `AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`、Region 和
   `AWS_ENDPOINT_URL_S3` 后重新执行原来的 `ray start` 命令。
4. 重新提交相同 Release；不需要再次覆盖或改名 MinIO 对象。省略 `--submission-id` 会自动
   生成新的 Attempt ID；如果使用显式 ID，则改用类似 `<原-id>-retry-01` 的新值。

不要通过公开 Bucket 或把长期密钥写入 `runtime-env.yaml` 来绕过这个错误。

同机 MinIO 示例入口为 `http://127.0.0.1:18101`。`127.0.0.1` 仅适用于 MinIO 同机进程；
多节点必须使用所有 Ray 节点可达的端点。当前实际服务配置见
[`当前机器服务部署`](../../../doc/local-services-18100.md)。

## 4. 单独执行 CD Dry Run

先用上一步输出的真实路径做 Dry Run，不连接 Ray：

```bash
MANIFEST_PATH=/tmp/ray-cats-and-dogs-codex-app-job/<release-id>/release.json

/data/miniconda3/envs/attend-ray-py312/bin/python \
  job/cd.py \
  --manifest "$MANIFEST_PATH" \
  --dry-run
```

默认模式是 `check-config`，生成的 Entrypoint 为：

```bash
python scripts/train.py --config configs/smoke.yaml --check-config
```

## 5. 单独执行 CD

先提交无训练开销的配置检查：

```bash
/data/miniconda3/envs/attend-ray-py312/bin/python \
  job/cd.py \
  --manifest "$MANIFEST_PATH"
```

读取输出中的 `job_id`，然后查询：

```bash
/data/miniconda3/envs/attend-ray-py312/bin/ray job status \
  --address http://127.0.0.1:8265 <job-id>

/data/miniconda3/envs/attend-ray-py312/bin/ray job logs \
  --address http://127.0.0.1:8265 <job-id>
```

只读计划验证使用：

```bash
/data/miniconda3/envs/attend-ray-py312/bin/python \
  job/cd.py \
  --manifest "$MANIFEST_PATH" \
  --mode plan
```

训练统一使用项目 README 中的 `node job/governed.mjs`，先生成真实 Galatea Readiness，
再绑定授权提交固定 Ray Driver。通用 CD 仅允许配置检查和只读计划。

## 当前主机的数据与复现

数据源固定为 `shaunthesheep/microsoft-catsvsdogs-dataset` 的版本 1。
Kaggle 凭据保存在 `/root/.kaggle/access_token`（权限 0600），不写入仓库或命令行。
独立下载环境为 `platform-data/kaggle/venv`，数据下载缓存为 `train-data/.kagglehub`。

```python
import os
os.environ["KAGGLEHUB_CACHE"] = "/data/ai/chenzhangyue/galatea-model/train-data/.kagglehub"
import kagglehub
path = kagglehub.dataset_download("shaunthesheep/microsoft-catsvsdogs-dataset/versions/1")
print(path)
```

固定入口 `train-data/microsoft-cats-vs-dogs` 指向该版本缓存。下载共 25,000 张图片，
验证排除 3 张损坏图片后，训练/验证/测试分别为 22,497 / 1,250 / 1,250 张。

只读计划和提交均显式指定 Attempt；同一 Attempt 用于重试查询，新训练使用新 Attempt：

```bash
export GALATEA_RAY_PROFILE=dev
export RAY_ADDRESS=127.0.0.1:18104
export RAY_API_SERVER_ADDRESS=http://127.0.0.1:8265
export MLFLOW_TRACKING_URI=http://127.0.0.1:18103
node job/governed.mjs plan configs/smoke.yaml "$RELEASE_MANIFEST" - smoke-01
node job/governed.mjs submit configs/smoke.yaml "$RELEASE_MANIFEST" danger-full-access smoke-01
```
