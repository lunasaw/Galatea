# 当前机器 MinIO / MLflow / Ray 部署

部署日期：2026-10-14。仓库目录：`/data/ai/chenzhangyue/galatea-model/galatea`。
本机部署 MinIO、MLflow 和独立的开发 Ray Head。

## 服务与持久化

| 服务 | 入口 | systemd unit |
| --- | --- | --- |
| MinIO S3 API | `http://10.26.0.140:18101` | `minio.service` |
| MinIO Console | `http://10.26.0.140:18102` | `minio.service` |
| MLflow Tracking / UI | `http://10.26.0.140:18103` | `mlflow.service` |
| Ray Head / GCS | `10.26.0.140:18104` | `ray-head.service` |
| Ray Dashboard / Jobs API | `http://10.26.0.140:8265` | `ray-head.service` |
| Ray Client | `ray://10.26.0.140:10001` | `ray-head.service` |

按本次部署要求，MinIO、MLflow、Ray Dashboard 和 GCS 均监听 `0.0.0.0`。
MinIO 使用账号认证；MLflow 未启用应用层认证。
三个 unit 均开机自启，异常退出后自动重启。

- MinIO：`RELEASE.2025-09-07T16-13-09Z`，二进制 `/usr/local/bin/minio`。
- MinIO Client：`RELEASE.2025-08-13T08-35-41Z`，二进制 `/usr/local/bin/mc`。
- MLflow：`3.14.0`；boto3：`1.43.59`。
- MinIO 数据：`platform-data/minio/data/`，运行用户 `minio:minio`。
- MLflow 元数据：`platform-data/mlflow/mlflow.db`，运行用户 `root:root`。
- MLflow 独立环境：`platform-data/mlflow/venv/`，Python 基于
  `/data/miniconda3/envs/attend-ray-py312/bin/python` 创建；不改动 Ray 环境的包。
- MLflow 代理 Artifact：`s3://mlflow-artifacts`，Endpoint 为 `http://127.0.0.1:18101`。
- `training-data` 和 `mlflow-artifacts` Bucket 均为私有并启用版本控制。

`platform-data/` 已被 Git 忽略，不能提交对象、数据库或运行环境。

## Ray Head

Ray 版本为 `2.53.0`，使用 `/data/miniconda3/envs/attend-ray-py312/bin/ray`。
节点 IP 为实际地址 `10.26.0.140`，运行目录为 `/data/ai/ray`，自定义资源为 `dev:4`。
CPU、GPU 和对象存储容量采用 Ray 自动检测值。

unit 设置 `RAY_REDIS_ADDRESS=127.0.0.1:6379`，将本机 GCS 的元数据持久化到
独立 Redis；`RAY_external_storage_namespace=galatea-dev` 隔离该集群的存储。
GCS 监听 `18104`，与 Redis 的 `6379` 分开。Worker 范围显式设为 `18200–18399`，
因为默认范围 `10002–19999` 会与 GCS `18104` 冲突。

服务通过 `--block` 常驻，由 systemd 管理该 unit 的进程组。运维使用
`systemctl restart ray-head.service` 或 `systemctl stop ray-head.service`；
共享机器上的全局 `ray stop` 会影响其他 Ray 实例。

```bash
systemctl is-active ray-head.service
curl -fsS http://10.26.0.140:8265/api/version
PATH=/data/miniconda3/envs/attend-ray-py312/bin:$PATH \
  ray status --address=10.26.0.140:18104
journalctl -u ray-head.service -n 50 --no-pager
```

GCS 启动日志已确认 `Using external Redis for KV storage: 127.0.0.1:6379`
和 `StorageType::REDIS_PERSIST`。Ray Head 进程也设置
`MLFLOW_TRACKING_URI=http://10.26.0.140:18103`。

如需将 Redis 换到其他机器，修改 unit 的 `RAY_REDIS_ADDRESS`；认证配置应放入
受保护的服务环境文件。

## 凭据与客户端配置

凭据文件位于 `/etc/minio/`，权限为 `0600 root:root`，不把密码写入文档或聊天：

| 文件 | 用途 |
| --- | --- |
| `/etc/minio/minio.env` | MinIO 管理员和监听地址、数据目录 |
| `/etc/minio/mlflow-s3.env` | MLflow 服务端访问 Artifact Bucket 的专用账号 |
| `/etc/minio/training-data-s3.env` | 直接访问训练数据的专用账号 |

同机训练客户端只需设置：

```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:18103
```

MLflow 代理 Artifact 上传、下载，客户端无需 MinIO 密钥。直接使用 boto3 读取数据时，
由受控启动脚本注入 `training-data-s3.env`，不要使用管理员账号。

## 安装来源

旧文档中的 `dl.min.io` 固定版本下载地址已返回 HTTP 410，本次改用官方 GitHub
同版本发布资产，并对照发布资产中的 `.sha256sum` 校验后安装：

- [MinIO Server 发布](https://github.com/minio/minio/releases/tag/RELEASE.2025-09-07T16-13-09Z)
- [MinIO Client 发布](https://github.com/minio/mc/releases/tag/RELEASE.2025-08-13T08-35-41Z)

二进制下载地址和 SHA256 记录在 `platform-data/deployment/minio-binaries.json`。
MLflow 实际安装依赖记录在 `platform-data/deployment/mlflow-requirements.lock`。

## 检查与管理

```bash
systemctl is-enabled minio.service mlflow.service ray-head.service
systemctl is-active minio.service mlflow.service ray-head.service
curl -fsS http://127.0.0.1:18101/minio/health/live
curl -fsS http://127.0.0.1:18101/minio/health/ready
curl -fsS http://127.0.0.1:18103/health
journalctl -u minio.service -u mlflow.service -n 50 --no-pager
```

修改仓库 unit 后校验并安装：

```bash
cd /data/ai/chenzhangyue/galatea-model/galatea
systemd-analyze verify systemd/minio.service systemd/mlflow.service systemd/ray-head.service
install -m 0644 systemd/minio.service /etc/systemd/system/minio.service
install -m 0644 systemd/mlflow.service /etc/systemd/system/mlflow.service
install -m 0644 systemd/ray-head.service /etc/systemd/system/ray-head.service
systemctl daemon-reload
systemctl enable --now minio.service mlflow.service ray-head.service
```

需要重启时执行 `systemctl restart minio.service mlflow.service`。
备份时同时保存 MLflow 元数据、MinIO 对象和受保护的配置文件。

本次验收已通过回环地址和两个内网 IP 的健康检查、页面访问、Bucket 版本控制、
专用账号权限隔离，以及不携带 MinIO 凭据的 MLflow 客户端 Artifact 上传、下载回读。
验证记录为 `platform-service-check` Experiment 的 Run
`8efd57d9f04e4e1b8332abb1c67b6f45`，结果保存在
`platform-data/deployment/service-check.json`；这是服务部署检查，不是训练记录。

## 远程访问

内网客户端可直接使用上表入口，Ray 节点设置：

```bash
export MLFLOW_TRACKING_URI=http://10.26.0.140:18103
```

MLflow 允许本机内网 IP `10.26.0.140`、`10.69.31.253`、主机名
`idc-bj08-5090-01` 和回环地址的 Host Header。使用其他域名时，需要更新 unit 中
的 `--allowed-hosts` 并重启服务。

在自己的电脑上执行以下命令，将 SSH 用户替换为实际账号：

```bash
ssh -N \
  -L 18101:127.0.0.1:18101 \
  -L 18102:127.0.0.1:18102 \
  -L 18103:127.0.0.1:18103 \
  用户名@10.26.0.140
```

然后打开 `http://127.0.0.1:18102` 或 `http://127.0.0.1:18103`。
通过转发访问时，设置该节点实际可达的 `MLFLOW_TRACKING_URI`；远程节点的
`127.0.0.1` 指向节点自身。
