# MLflow Tracking Server 安装、启动与运维

本文记录当前训练节点上 MLflow Tracking Server 的完整部署方式。命令和路径均以
`/data/ai/chenzhangyue/code/train` 为准，覆盖本地 SQLite、MinIO Artifact、前台试运行、
systemd 常驻、客户端接入、备份和故障排查。

文档基线日期：2026-07-30。

> 当前服务没有启用 MLflow 应用层登录认证。`allowed-hosts` 用于防止 Host Header / DNS
> rebinding 攻击，不等同于用户认证。不得把 5000 端口直接暴露到公网。

## 1. 当前部署基线

| 项目 | 当前值 |
| --- | --- |
| Conda | `/data/conda` |
| 环境 | `attend-ray-py312` |
| Python | 3.12.12 |
| MLflow | 3.14.0 |
| 监听端口 | 5000 |
| Backend Store | `sqlite:////data/ai/chenzhangyue/code/train/platform-data/mlflow/mlflow.db` |
| Artifact Store | 本机 MinIO `s3://mlflow-artifacts`，Endpoint `http://127.0.0.1:9000` |
| S3 环境文件 | `/etc/minio/mlflow-s3.env`（`0600 root:root`） |
| unit 源文件 | `/data/ai/chenzhangyue/code/train/systemd/mlflow.service` |
| unit 安装位置 | `/etc/systemd/system/mlflow.service` |

当前服务采用 SQLite 元数据加本机 MinIO Artifact：

```text
Jupyter / Python 训练脚本
          |
          | MLFLOW_TRACKING_URI=http://127.0.0.1:5000
          v
MLflow Tracking Server :5000
          |                         |
          | 参数、指标、Run 元数据   | Artifact 上传/下载代理
          v                         v
platform-data/mlflow/mlflow.db   s3://mlflow-artifacts/
```

- SQLite 数据库保存 Experiment、Run、参数、指标、Tag 和 Artifact URI 等元数据。
- MinIO Bucket 保存模型、Checkpoint、图表和报告等文件。
- `--serve-artifacts` 让客户端通过 MLflow Server 上传和下载 Artifact，客户端不需要直接
  持有 MinIO 凭据。
- SQLite 数据库和 MinIO 对象必须一起备份；只备份 `mlflow.db` 不能恢复模型文件。

## 2. 部署前检查

确认 Conda、训练目录、已有数据和端口状态：

```bash
/data/conda/bin/conda --version
/data/conda/bin/conda env list
test -d /data/ai/chenzhangyue/code/train && echo "train directory exists"
ls -lah /data/ai/chenzhangyue/code/train/platform-data/mlflow 2>/dev/null || true
systemctl is-active minio.service || true
ss -lntp | grep -E ':5000\\b' || true
df -h /data/ai/chenzhangyue/code/train
```

如果 `mlflow.db` 已存在，它就是当前实验元数据，安装过程中不要删除或覆盖。若 5000
已被占用，应先确认占用者是否为现有 `mlflow.service`，不要同时对同一个 SQLite
数据库启动第二个测试实例。

## 3. 创建环境并安装

JupyterLab 和 MLflow 当前共用 `attend-ray-py312` 环境。先加载 Conda shell 支持：

```bash
source /data/conda/etc/profile.d/conda.sh
conda env list
```

仅在 `attend-ray-py312` 尚不存在时创建环境：

```bash
conda create -n attend-ray-py312 python=3.12.12 pip -y
```

激活环境并安装当前固定版本：

```bash
conda activate attend-ray-py312
python -m pip install --upgrade pip
python -m pip install "mlflow==3.14.0" boto3
python -m pip check
```

验证安装位置和版本，确保没有误用 base 环境中的命令：

```bash
command -v python
command -v mlflow
python --version
mlflow --version
```

预期 `python` 和 `mlflow` 都位于
`/data/conda/envs/attend-ray-py312/bin/`，MLflow 输出版本 `3.14.0`。

## 4. 创建持久化目录

当前 systemd 服务以 `root:root` 运行：

```bash
sudo install -d -o root -g root -m 0750 \
  /data/ai/chenzhangyue/code/train/platform-data/mlflow
```

`mlflow.db` 不需要提前创建，MLflow 首次连接时会初始化数据库。Artifact Bucket 和
最小权限账号必须先按 [`minio-start.md`](./minio-start.md) 创建。

如果以后把 unit 的 `User` 改为普通账号，必须确保它能读取 `/etc/minio/mlflow-s3.env`
并通过 MinIO API 访问 `mlflow-artifacts`。

## 5. 前台试运行

确认 MinIO 已运行、现有 `mlflow.service` 已停止且 5000 未被占用，然后在前台启动测试实例：

```bash
source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312
set -a
source /etc/minio/mlflow-s3.env
set +a

mlflow server \
  --host 127.0.0.1 \
  --port 5000 \
  --backend-store-uri sqlite:////data/ai/chenzhangyue/code/train/platform-data/mlflow/mlflow.db \
  --serve-artifacts \
  --artifacts-destination s3://mlflow-artifacts \
  --allowed-hosts localhost,localhost:5000,127.0.0.1,127.0.0.1:5000
```

这里的 SQLite URI 有四个 `/`：`sqlite:///` 是协议部分，第四个 `/` 是绝对路径开头，
不能删成相对路径。

另开终端验证健康检查和页面：

```bash
curl -fsS -H 'Host: localhost' http://127.0.0.1:5000/health
curl -I -H 'Host: localhost' http://127.0.0.1:5000/
```

`/health` 应返回 `OK` 和 HTTP `200`。验证完成后在启动终端按 `Ctrl+C` 停止前台实例，
再部署 systemd 服务。

### `--serve-artifacts` 与 `--default-artifact-root`

当前常驻服务使用：

```text
--serve-artifacts --artifacts-destination s3://mlflow-artifacts
```

这表示 Artifact 由 MLflow Server 代理到本机 MinIO。`--default-artifact-root <URI>` 则主要指定新建
Experiment 的默认 Artifact URI；如果不启用代理，客户端可能需要直接访问该 URI。
客户端不能直接获得 Server 的 MinIO 凭据，因此当前部署统一采用 `--serve-artifacts`。

修改这些参数只影响新建 Experiment 的默认位置，不会自动迁移已有 Experiment。已有
Experiment 的 `artifact_location` 和历史文件应单独检查，不能只改启动命令。

## 6. 安装 systemd 常驻服务

仓库已提供与当前机器一致的 unit：

```text
/data/ai/chenzhangyue/code/train/systemd/mlflow.service
```

它包含以下关键设置：

| 设置 | 作用 |
| --- | --- |
| `User=root` | 与当前数据库和 Artifact 权限保持一致；也要求严格限制网络访问 |
| 固定 `PATH` | 始终使用 `attend-ray-py312` 中的 MLflow |
| `--host 0.0.0.0` | 允许同机代理或内网接入；依赖防火墙和上层认证保护 |
| `--backend-store-uri` | 将实验元数据持久化到当前 SQLite 数据库 |
| `--serve-artifacts` | 由 Tracking Server 处理 Artifact 上传和下载 |
| `--artifacts-destination` | Artifact 实际写入本机 MinIO `s3://mlflow-artifacts` |
| `/etc/minio/mlflow-s3.env` | 仅由 MLflow Server 读取的 MinIO 凭据 |
| `--allowed-hosts` | 只接受 unit 中列出的 Host Header；它不是登录白名单 |
| `Restart=on-failure` | 异常退出后自动重启 |

安装前复核 unit 中的用户、路径、监听地址和允许的主机名。服务器域名或地址变化时，
应先更新 `--allowed-hosts`：

```bash
sed -n '1,240p' /data/ai/chenzhangyue/code/train/systemd/mlflow.service
```

确认无误后安装并立即启动：

```bash
sudo install -m 0644 \
  /data/ai/chenzhangyue/code/train/systemd/mlflow.service \
  /etc/systemd/system/mlflow.service
sudo systemctl daemon-reload
sudo systemctl enable --now mlflow.service
```

检查启动状态、日志和端口：

```bash
sudo systemctl status mlflow.service --no-pager -l
sudo journalctl -u mlflow.service -n 100 --no-pager
ss -lntp | grep -E ':5000\\b'
curl -fsS -H 'Host: localhost' http://127.0.0.1:5000/health
```

## 7. 访问与客户端地址

### 7.1 同机 Jupyter 或训练脚本

JupyterLab 和 MLflow 在同一台服务器上时，客户端统一使用回环地址：

```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
mlflow experiments search
```

不要把 UI 地址或代理前缀误写进同机训练脚本，除非客户端确实只能经过该代理访问。

### 7.2 SSH 隧道（推荐的远程访问方式）

在本地电脑执行：

```bash
ssh -N -L 5000:127.0.0.1:5000 用户名@服务器地址
```

需要同时访问 JupyterLab 时，可以在同一条 SSH 连接中转发两个端口：

```bash
ssh -N \
  -L 8888:127.0.0.1:8888 \
  -L 5000:127.0.0.1:5000 \
  用户名@服务器地址
```

当前 systemd 模式下的 Jupyter URL 带有固定 base path，不能只打开 8888 根路径；详见
[`jupyter-start.md`](./jupyter-start.md)。

然后在本地浏览器打开：

```text
http://127.0.0.1:5000
```

本地 Python 客户端也可将 `MLFLOW_TRACKING_URI` 设为
`http://127.0.0.1:5000`，流量会通过 SSH 隧道到达服务器。

### 7.3 通过 code-server 代理（可选）

MLflow 当前没有像 Jupyter `ServerApp.base_url` 那样配置完整应用 base path。不要直接
照搬 Jupyter 的 `/GC5026/absproxy/8888/` 设置。若需要经 code-server 暴露 MLflow：

1. 优先从普通 `proxy` 模式验证，因为 MLflow 后端仍按根路径 `/` 运行。
2. 确保浏览器域名已加入 MLflow `--allowed-hosts`。
3. 逐项验证首页、静态资源、Experiment API、Artifact 上传和页面内跳转。
4. 代理必须有认证；`allowed-hosts` 和 CORS 配置都不能代替认证。

code-server 的 `proxy` / `absproxy` 行为见
[`code-server-proxy.md`](./code-server-proxy.md)。在路径代理未经完整验证前，以 SSH 隧道
作为可靠入口。

## 8. 完整验证

### 8.1 服务健康检查

```bash
curl -i -H 'Host: localhost' http://127.0.0.1:5000/health
```

预期关键响应为：

```text
HTTP/1.1 200 OK

OK
```

### 8.2 创建测试 Run 并上传 Artifact

下面的脚本会创建 `platform-smoke-test` Experiment、一个 Run、一项参数、一项指标和
一个文本 Artifact，可用于同时验证数据库和 Artifact 代理链路：

```bash
source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000

python - <<'PY'
import mlflow

mlflow.set_experiment("platform-smoke-test")
with mlflow.start_run(run_name="install-check") as run:
    mlflow.log_param("source", "mlflow-start.md")
    mlflow.log_metric("health", 1.0)
    mlflow.log_text("artifact upload ok\n", "checks/result.txt")
    print(f"run_id={run.info.run_id}")
    print(f"artifact_uri={mlflow.get_artifact_uri()}")
PY
```

验证结果：

```bash
mlflow experiments search
mc alias set local http://127.0.0.1:9000 \
  "$(sed -n 's/^MINIO_ROOT_USER=//p' /etc/minio/minio.env)" \
  "$(sed -n 's/^MINIO_ROOT_PASSWORD=//p' /etc/minio/minio.env)"
mc ls --recursive local/mlflow-artifacts
```

最后在 MLflow UI 中打开 `platform-smoke-test`，确认参数、指标和
`checks/result.txt` 均可查看。该测试会保留一条真实记录；需要删除时应在 UI 中操作，
不要直接改 SQLite 数据库或手工删除单个 Artifact 对象。

## 9. 训练代码接入

同机训练脚本的最小写法：

```python
import mlflow

mlflow.set_tracking_uri("http://127.0.0.1:5000")
mlflow.set_experiment("cats-vs-dogs")

with mlflow.start_run(run_name="baseline") as run:
    mlflow.log_params({"epochs": 10, "batch_size": 32})

    for epoch in range(10):
        train_loss = 1.0 / (epoch + 1)
        mlflow.log_metric("train_loss", train_loss, step=epoch)

    mlflow.log_artifact("checkpoint.pt", artifact_path="checkpoints")
    print(run.info.run_id)
```

使用规则：

- 在 Notebook、脚本和 Ray Worker 中显式设置同一个 Tracking URI 和 Experiment。
- 参数通常每个 Run 记录一次；逐 Epoch 指标使用递增的 `step`。
- 不要在每个 Batch 上传大模型，Checkpoint 先写本地，再按合理周期上传。
- 分布式训练只让 Rank 0 写同一个 MLflow Run，避免重复指标和文件覆盖。
- 保存 Run ID，用它关联训练日志、指标、模型和恢复流程。

## 10. 数据检查与备份

### 10.1 查看占用空间

```bash
du -sh /data/ai/chenzhangyue/code/train/platform-data/mlflow/mlflow.db
mc du --recursive local/mlflow-artifacts
```

### 10.2 一致性备份

最稳妥的本地备份方式是短暂停止服务，同时复制数据库和 MinIO 对象。以下命令会造成短暂
不可用，应安排在没有训练写入时执行：

```bash
MLFLOW_BACKUP_DIR=/data/ai/chenzhangyue/code/train/platform-data/backups/mlflow-$(date +%Y%m%d-%H%M%S)
sudo install -d -o root -g root -m 0750 "${MLFLOW_BACKUP_DIR}"

sudo systemctl stop mlflow.service
sudo cp -a \
  /data/ai/chenzhangyue/code/train/platform-data/mlflow/mlflow.db \
  "${MLFLOW_BACKUP_DIR}/"
sudo install -d -m 0750 "${MLFLOW_BACKUP_DIR}/minio"
mc mirror --overwrite local/mlflow-artifacts "${MLFLOW_BACKUP_DIR}/minio/mlflow-artifacts"
sudo systemctl start mlflow.service

sudo find "${MLFLOW_BACKUP_DIR}" -maxdepth 2 -ls
curl -fsS -H 'Host: localhost' http://127.0.0.1:5000/health
unset MLFLOW_BACKUP_DIR
```

本地副本仍会与原数据同时受磁盘故障影响。重要实验还应把备份同步到另一台机器或对象
存储，并定期做恢复演练。恢复时先停止 MLflow，核对数据库和 MinIO 对象备份完整性，再
恢复两者；不要在服务写入期间直接替换文件。

## 11. 升级与数据库迁移

升级 MLflow 前必须先执行第 10 节备份，并阅读目标版本的变更说明。典型顺序是：

```bash
sudo systemctl stop mlflow.service

source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312
python -m pip install --upgrade "mlflow==3.14.0"
python -m pip check
mlflow db upgrade \
  sqlite:////data/ai/chenzhangyue/code/train/platform-data/mlflow/mlflow.db

sudo systemctl start mlflow.service
sudo systemctl status mlflow.service --no-pager -l
curl -fsS -H 'Host: localhost' http://127.0.0.1:5000/health
```

上例固定为当前版本，用于重装或对齐环境。真正升级时替换成已经验证的目标版本。数据库
迁移可能不可逆，不应跳过备份，也不要让旧版和新版 MLflow 同时连接同一个数据库。

## 12. 日常运维

```bash
# 查看状态和持续日志
sudo systemctl status mlflow.service --no-pager -l
sudo journalctl -u mlflow.service -f

# 修改 unit 后生效
sudo systemctl daemon-reload
sudo systemctl restart mlflow.service

# 停止或启动
sudo systemctl stop mlflow.service
sudo systemctl start mlflow.service
```

## 13. 常见故障

| 现象 | 检查与处理 |
| --- | --- |
| 服务报 `Address already in use` | 用 `ss -lntp` 确认 5000 占用者；停止重复的前台实例或旧服务 |
| 请求被拒绝或出现 Host Header 错误 | 检查请求的 `Host` 是否在 `--allowed-hosts`；域名变化后更新 unit 并重启 |
| `/health` 不返回 `200` | 查看 `systemctl status` 和 `journalctl`，检查进程、端口和启动参数 |
| `database is locked` | 查找是否有多个服务连接同一 SQLite 文件；单节点高并发需求应迁移 PostgreSQL |
| `Permission denied` | 检查 MLflow 是否能读取 `/etc/minio/mlflow-s3.env`，以及 MinIO 用户对 Bucket 的策略 |
| 指标存在但 Artifact 上传失败 | 检查 `--serve-artifacts`、MinIO 服务、S3 凭据、Bucket 策略、磁盘空间和 Experiment 的 Artifact URI |
| 改了 Artifact Bucket 但旧 Run 仍指向原位置 | 启动参数不会迁移已有 Experiment；按其 `artifact_location` 检查和规划迁移 |
| UI 能开但脚本记录到别处 | 打印 `mlflow.get_tracking_uri()`；未设置 URI 时客户端可能使用本地 `./mlruns` |
| 页面经路径代理后静态资源或 API 404 | 代理前缀与 MLflow 根路径不兼容；先改用 SSH 隧道，再单独验证代理配置 |
| 磁盘持续增长 | 检查 Artifact、日志和已删除 Run；先做备份，再按 MLflow 支持的方式清理 |

## 14. 安全与可靠性检查清单

- 不把 5000 端口直接加入公网安全组；优先使用 SSH 隧道或带认证的反向代理。
- 不把 `allowed-hosts`、CORS 或仅监听内网误认为用户认证。
- 不在 Notebook、脚本或文档中保存数据库凭据、对象存储密钥等 Secret。
- 定期同时备份 SQLite 数据库和 MinIO 对象，并把重要备份复制到异机或对象存储。
- 监控数据库、MinIO Bucket 和系统日志占用，防止磁盘写满导致训练记录丢失。
- SQLite 适合当前单节点轻量使用；多实例、高并发或平台化部署应迁移到 PostgreSQL。
- 变更版本、存储 URI 或代理方式后，必须重测健康检查、Run 写入和 Artifact 上传下载。
