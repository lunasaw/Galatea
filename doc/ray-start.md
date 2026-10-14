# Ray Head 启动与 Runtime Package 凭据维护

> 2026-10-14 当前机器使用本地 GCS `10.26.0.140:18104`、Dashboard `8265`，
> GCS 存储连接独立 Redis `127.0.0.1:6379`；当前部署见
> [当前机器服务部署](./local-services-18100.md#ray-head)。下文保留旧机器的部署基线，
> 当前 `systemd/ray-head.service` 已适配新机器。

多集群选择、授权上传和已验证新案例见 [平台运行记忆](platform-runtime-memory.md)。
以下旧地址不能用于当前目标的自动发现；每次操作明确选择 GCS、Jobs API 和对应 unit。

旧机器基线使用 Ray 2.53，Head 地址为 `10.19.20.26:6379`，Dashboard 端口为 `8265`。
当前机器端点见上方说明。正式和长时间任务通过 Ray Jobs API 提交。Ray Head 由 `ray-head.service` 管理，不再依赖
登录 Shell 手工执行 `ray start`。

## 1. 凭据边界

Ray 从私有 MinIO 下载 `working_dir` 和 `py_modules` 时，执行下载的是 Ray Runtime
Environment Agent。它在 Job Driver 启动前工作，因此 S3 凭据必须由 Ray Head/Worker
进程继承，不能写入 Job 的 `runtime_env.env_vars`。

使用三个不同的 MinIO 身份：

| 身份 | 权限 | 凭据位置 |
| --- | --- | --- |
| `training-data` | 训练数据和 Runtime Package 发布读写 | `/etc/minio/training-data-s3.env` |
| `ray-runtime-reader` | 仅下载 Runtime Package | `/etc/minio/ray-runtime-s3.env` |
| `mlflow` | 仅访问 MLflow Artifact Bucket | `/etc/minio/mlflow-s3.env` |

不要把任何一个环境文件复制到仓库、Notebook、Job YAML 或聊天记录。三个账号不应复用密码。

## 2. 创建最小权限读取策略

策略只允许列出和读取以下前缀：

```text
s3://training-data/ray-runtime/ray-cats-and-dogs/
```

策略内容：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetBucketLocation"],
      "Resource": ["arn:aws:s3:::training-data"]
    },
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": ["arn:aws:s3:::training-data"],
      "Condition": {
        "StringLike": {
          "s3:prefix": [
            "ray-runtime/ray-cats-and-dogs",
            "ray-runtime/ray-cats-and-dogs/*"
          ]
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": [
        "arn:aws:s3:::training-data/ray-runtime/ray-cats-and-dogs/*"
      ]
    }
  ]
}
```

使用 MinIO 管理员执行一次：

```bash
mc admin policy create local ray-runtime-reader-policy \
  /tmp/ray-runtime-reader-policy.json
mc admin user add local ray-runtime-reader '<由受控方式生成的随机密码>'
mc admin policy attach local ray-runtime-reader-policy --user ray-runtime-reader
```

将密码保存为 root-only 文件：

```text
AWS_ACCESS_KEY_ID=ray-runtime-reader
AWS_SECRET_ACCESS_KEY=<受控随机密码>
AWS_DEFAULT_REGION=us-east-1
AWS_ENDPOINT_URL_S3=http://10.26.0.140:18101
```

要求如下：

```bash
chown root:root /etc/minio/ray-runtime-s3.env
chmod 0600 /etc/minio/ray-runtime-s3.env
```

## 3. 安装 systemd 服务

先验证并安装仓库中的 Unit：

```bash
cd /data/ai/chenzhangyue/galatea-model/galatea
systemd-analyze verify systemd/ray-head.service
install -o root -g root -m 0644 systemd/ray-head.service \
  /etc/systemd/system/ray-head.service
systemctl daemon-reload
systemctl enable --now ray-head.service
```

服务使用资源标签 `dev:4`，并通过 `CUDA_VISIBLE_DEVICES=0,3,6` 和 `--num-gpus=3`
限定开发集群使用的显卡。其他显卡正在运行独立推理服务。
Unit 同时固定 `RAY_ADDRESS=10.26.0.140:18104` 和
`RAY_API_SERVER_ADDRESS=http://10.26.0.140:8265`，供 Ray Train 内部的 Controller、
PlacementGroupCleaner 和 State API 使用。只在提交终端设置地址不足以覆盖这些后台 Actor；
同机有多个 Ray 集群时，缺少服务级地址会使清理线程自动发现集群失败。
迁移主机或更换网卡时，先更新 Unit 中的 `--node-ip-address`，再验证和安装；不要让部署脚本
猜测生产地址。

Unit 同时设置 `RAY_CONDA_HOME=/data/miniconda3`、`CONDA_PKGS_DIRS=/data/miniconda3/pkgs` 和
`PIP_CACHE_DIR=/var/cache/ray/pip`。正式 Job 应使用节点预装的固定环境路径，让 Ray 只检查
环境是否存在；各项目的 `conda.yaml` 仍是权威来源。`runtime_env.conda` 动态映射只作为首次
部署/预热 fallback，pip 只作为显式兼容性 Smoke 覆盖，不能把共享环境中的包版本当作项目依赖契约。

每个节点在部署窗口执行项目的 `job/warmup.py`（或等价校验），确认 `ray`、`torch`、`mlflow`
导入和 `pip check` 通过后，才允许提交正式 Trial/Champion。这样首次安装不再位于 Ray Job 的
启动关键路径；`RAY_JOB_START_TIMEOUT_SECONDS` 只能作为一次冷启动的临时保护，不应永久依赖。

Dashboard 当前按既有行为监听 `0.0.0.0:8265`。它必须由防火墙、受控私网或认证代理保护；
如果只从本机提交 Job，应改为 `--dashboard-host=127.0.0.1`。

## 4. 健康检查

```bash
systemctl --no-pager --full status ray-head.service
/data/miniconda3/envs/attend-ray-py312/bin/ray status --address=10.26.0.140:18104
curl -fsS http://10.26.0.140:8265/api/version
```

确认 Ray 进程继承了变量时只检查变量名，不输出值：

```bash
RAY_HEAD_PID=$(systemctl show ray-head.service --property=MainPID --value)
python - "$RAY_HEAD_PID" <<'PY'
import pathlib
import sys

pid = int(sys.argv[1])
if pid <= 0:
    raise SystemExit("selected Ray service is not running")
names = {item.split(b"=", 1)[0].decode() for item in pathlib.Path(f"/proc/{pid}/environ").read_bytes().split(b"\0") if item}
print("\n".join(sorted(name for name in names if name.startswith("AWS_"))))
PY
```

只检查选定 unit 的 MainPID，避免 `pgrep -o raylet` 在同机多集群时选中业务进程。

## 5. 密钥轮换

轮换会要求 Ray 进程重新读取环境文件，因此必须安排在集群无运行中 Job 的维护窗口：

1. 用密码生成器创建新的高熵密码，不写入 Shell History。
2. 更新 MinIO 中 `ray-runtime-reader` 用户的密码。
3. 原子替换 `/etc/minio/ray-runtime-s3.env`，保持 `root:root` 和 `0600`。
4. 执行 `systemctl restart ray-head.service`。
5. 用只包含 `--check-config` 的 S3 Runtime Env Job 验证下载。
6. 确认验证成功后，销毁操作过程中的临时明文材料。

不要先改环境文件、隔很久再改 MinIO，也不要反过来长期保留不一致状态。单账号轮换期间会有
短暂下载窗口；需要零停机时使用两套只读账号交替轮换，并逐节点滚动重启 Worker。

## 6. 多节点集群

当前 MinIO 使用私网地址 `10.26.0.140:18101`。增加 Worker 前必须确认所有节点
可达的受控私网或 HTTPS Endpoint，并在每台节点的 Ray 服务中配置同样的只读身份。每台
Worker 都要预装 `boto3` 和 `smart_open[s3]`，但不需要 Runtime Package 上传权限。
