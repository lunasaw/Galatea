# Ray Head 启动与 Runtime Package 凭据维护

当前主机使用 Ray 2.53，Head 地址为 `10.19.20.26:6379`，Dashboard 端口为 `8265`。
正式和长时间任务通过 Ray Jobs API 提交。Ray Head 由 `ray-head.service` 管理，不再依赖
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
AWS_ENDPOINT_URL_S3=http://127.0.0.1:9000
```

要求如下：

```bash
chown root:root /etc/minio/ray-runtime-s3.env
chmod 0600 /etc/minio/ray-runtime-s3.env
```

## 3. 安装 systemd 服务

先验证并安装仓库中的 Unit：

```bash
cd /data/ai/chenzhangyue/code/galatea
systemd-analyze verify systemd/ray-head.service
install -o root -g root -m 0644 systemd/ray-head.service \
  /etc/systemd/system/ray-head.service
systemctl daemon-reload
systemctl enable --now ray-head.service
```

服务当前保留主机资源标签 `accelerator_type:GC50=4`，CPU 和 GPU 数量由 Ray 自动探测。
迁移主机或更换网卡时，先更新 Unit 中的 `--node-ip-address`，再验证和安装；不要让部署脚本
猜测生产地址。

Dashboard 当前按既有行为监听 `0.0.0.0:8265`。它必须由防火墙、受控私网或认证代理保护；
如果只从本机提交 Job，应改为 `--dashboard-host=127.0.0.1`。

## 4. 健康检查

```bash
systemctl --no-pager --full status ray-head.service
ray status
curl -fsS http://127.0.0.1:8265/api/version
```

确认 Ray 进程继承了变量时只检查变量名，不输出值：

```bash
RAYLET_PID=$(pgrep -o raylet)
tr '\0' '\n' < "/proc/$RAYLET_PID/environ" \
  | cut -d= -f1 \
  | grep -E '^AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY|DEFAULT_REGION|ENDPOINT_URL_S3)$'
```

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

当前 MinIO 只监听 `127.0.0.1:9000`，仅适用于单节点 Ray。增加 Worker 前必须提供所有节点
可达的受控私网或 HTTPS Endpoint，并在每台节点的 Ray 服务中配置同样的只读身份。每台
Worker 都要预装 `boto3` 和 `smart_open[s3]`，但不需要 Runtime Package 上传权限。
