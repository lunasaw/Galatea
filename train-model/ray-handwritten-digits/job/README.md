# 通过 MinIO 发布 Ray Job

这个目录提供与当前仓库环境匹配的 CI/CD 发布入口：

1. `ci.py` 创建确定性的 `working-dir.zip`，构建 `ray_handwritten_digits` Wheel，生成发布清单并上传
   MinIO；默认在成功后继续执行 CD。
2. `cd.py` 从发布清单读取 `s3://` Runtime Environment，通过 Ray Jobs API 提交任务。

`publish.py` 和 `submit.py` 保留为兼容入口，分别等价于“仅 CI 发布”和“单独 CD”。重复发布
不会重新打包成本地 `gcs://_ray_pkg_*`，也不会让 Ray 隐式覆盖 MinIO 上已有的代码对象。
不传 `--submission-id` 时，每次执行会自动生成包含配置名、模式、UTC 时间和随机 Attempt
Token 的唯一 ID，例如 `ray-handwritten-digits-baseline-train-20260803t093000z-a1b2c3d4`。显式传入
ID 时，同一 Release、Entrypoint 和 `submission_id` 对应的 Job 正在运行或已经成功，CD 会
返回已有 Job；失败或停止的显式 ID 不能复用。

默认执行完整流水线：

```bash
cd /data/ai/chenzhangyue/code/galatea/train-model/ray-handwritten-digits

/data/conda/envs/attend-ray-py312/bin/python job/ci.py
```

默认 CD 模式为 `check-config`，会提交 Ray Job 验证 Runtime Env 和配置，但不会启动训练。
只有显式传入 `--mode train` 才会训练。

正式发布默认使用项目根目录的 `conda.yaml`，其内容会进入 Ray Job 的
`runtime_env.conda`，并在 `release.json` 记录模式、来源和 SHA-256。pip 仅用于显式的
兼容性 Smoke：

```bash
python job/ci.py --runtime-mode pip \
  --pip-requirements /path/to/requirements-ray-smoke.txt --no-cd
```

也可重复传入 `--pip-package '<package>==<version>'`；pip 与 Conda 不会同时出现在顶层
Runtime Env。Ray 节点应设置 `RAY_CONDA_HOME=/data/conda`。

## 当前环境默认值

| 配置 | 默认值 |
| --- | --- |
| Python | `/data/conda/envs/attend-ray-py312/bin/python` |
| Ray Dashboard | `http://127.0.0.1:8265` |
| MinIO S3 Endpoint | `http://127.0.0.1:9000` |
| 凭据文件 | `/etc/minio/training-data-s3.env` |
| Bucket | `training-data` |
| Object Prefix | `ray-runtime/ray-handwritten-digits` |
| 本地发布目录 | `/tmp/ray-handwritten-digits-job/<release-id>/` |
| Ray Submission ID | 自动生成；可用 `--submission-id` 显式覆盖 |

默认复用现有 `training-data` Bucket 和账号，所以无需创建新的 Bucket 策略即可演示。
生产环境应使用独立的 `ray-runtime` Bucket，并把上传账号和 Ray 节点只读账号分开。

## 1. CI 参数

完整流水线 Dry Run 不连接 MinIO 或 Ray：

```bash
cd /data/ai/chenzhangyue/code/galatea/train-model/ray-handwritten-digits

/data/conda/envs/attend-ray-py312/bin/python \
  job/ci.py --dry-run
```

只构建并上传，不执行 CD：

```bash
/data/conda/envs/attend-ray-py312/bin/python \
  job/ci.py --no-cd
```

上传 MinIO 后只校验 CD 请求、不连接 Ray：

```bash
/data/conda/envs/attend-ray-py312/bin/python \
  job/ci.py --cd-dry-run
```

输出中的 `manifest_path` 指向 `release.json`。同目录还会生成：

```text
working-dir.zip
ray_handwritten_digits-0.1.0-py3-none-any.whl
runtime-env.yaml
release.json
```

`working-dir.zip` 不包含 `job/`、`notebooks/`、`tests/`、Notebook Checkpoint 或 Python Cache。
Wheel 作为 `py_modules` 使用，避免远程 Module ZIP 顶层目录解包语义导致导入路径错误。

## 2. CI 上传并自动执行 CD

确认 MinIO 健康并执行真实发布：

```bash
curl -fsS http://127.0.0.1:9000/minio/health/live

/data/conda/envs/attend-ray-py312/bin/python \
  job/ci.py
```

脚本只解析凭据文件中的 AWS 变量，不执行其中的 Shell 内容，也不会输出密钥。对象路径包含
Runtime Package 内容摘要；如果同一个 Key 已存在，脚本只接受大小和 `sha256` Metadata
完全一致的对象，否则拒绝覆盖。

可通过环境变量或参数改用独立 Bucket：

```bash
RAY_JOB_S3_BUCKET=ray-runtime \
RAY_JOB_S3_PREFIX=projects/ray-handwritten-digits \
/data/conda/envs/attend-ray-py312/bin/python \
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
AWS_ENDPOINT_URL_S3=http://127.0.0.1:9000
```

当前单节点环境通过 `ray-head.service` 注入专用只读凭据：

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

当前 MinIO 只监听 loopback，所以 `127.0.0.1` 只适用于单节点 Ray。多节点部署必须换成所有
Ray 节点可达的受控私网或 HTTPS 地址，并用防火墙或认证代理保护，不能直接暴露到公网。

## 4. 单独执行 CD Dry Run

先用上一步输出的真实路径做 Dry Run，不连接 Ray：

```bash
MANIFEST_PATH=/tmp/ray-handwritten-digits-job/<release-id>/release.json

/data/conda/envs/attend-ray-py312/bin/python \
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
/data/conda/envs/attend-ray-py312/bin/python \
  job/cd.py \
  --manifest "$MANIFEST_PATH"
```

读取输出中的 `job_id`，然后查询：

```bash
/data/conda/envs/attend-ray-py312/bin/ray job status \
  --address http://127.0.0.1:8265 <job-id>

/data/conda/envs/attend-ray-py312/bin/ray job logs \
  --address http://127.0.0.1:8265 <job-id>
```

只读计划验证使用：

```bash
/data/conda/envs/attend-ray-py312/bin/python \
  job/cd.py \
  --manifest "$MANIFEST_PATH" \
  --mode plan
```

只有明确要启动训练时才使用：

```bash
/data/conda/envs/attend-ray-py312/bin/python \
  job/cd.py \
  --manifest "$MANIFEST_PATH" \
  --mode train \
  --config configs/smoke.yaml
```

`--set training.learning_rate=0.0003` 可以重复传入。不要把 `--force` 当作普通重试方式；它会
要求训练入口为同一个幂等身份创建新的 Attempt。

也可以由 CI 直接提交训练，但必须显式启用：

```bash
/data/conda/envs/attend-ray-py312/bin/python \
  job/ci.py \
  --mode train \
  --config configs/baseline.yaml
```

命令输出的 `cd.submission_id` 和 `cd.job_id` 是本次自动生成的 ID。需要让重复调用幂等地
复用同一个 Ray Job 时，仍可显式传入 `--submission-id ray-handwritten-digits-baseline-<attempt-id>`。
