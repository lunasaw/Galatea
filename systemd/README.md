# 服务访问说明

当前机器（2026-10-14）的 MinIO / MLflow / Ray 使用以下入口，并已通过 systemd 常驻：

| 服务 | 当前地址 |
| --- | --- |
| MinIO S3 API | `http://10.26.0.140:18101` |
| MinIO Console | `http://10.26.0.140:18102` |
| MLflow Tracking / UI | `http://10.26.0.140:18103` |
| Ray Head / GCS | `10.26.0.140:18104` |
| Ray Dashboard / Jobs API | `http://10.26.0.140:8265` |
| Ray Client | `ray://10.26.0.140:10001` |

本机 GCS 使用独立 Redis `127.0.0.1:6379`，存储命名空间为 `galatea-dev`。
Worker 端口范围为 `18200–18399`，与 GCS 端口分离。

安装、环境、凭据文件和 SSH 转发见
[当前机器部署说明](../doc/local-services-18100.md)。以下其他服务地址为旧机器记录。

- MinIO 控制台：`http://127.0.0.1:9001`
- JupyterLab：`https://coder.vdian.net/GC5026/absproxy/8888/`
- Ray Head：`10.19.20.26:6379`
- Ray Dashboard：`http://127.0.0.1:8265`

MinIO 凭据由 `/etc/minio/minio.env` 管理，Jupyter Token 由当前运行实例生成。不要把密码、
Token 或其他会话凭据写入仓库；需要访问时从受保护的主机配置或服务日志中获取。

旧机器的 Ray Runtime Package 只读凭据由 `/etc/minio/ray-runtime-s3.env` 管理。
当前 unit 尚未配置该文件；需要从私有 MinIO 下载 Runtime Package 时，按
`doc/ray-start.md` 创建只读凭据并配置 `EnvironmentFile`。

## Codex Agent

Agent Runtime 统一使用 Codex。Web Agent 的 unit 模板位于
[`agents/codex-app-server/deploy/galatea-codex-agent.service`](../agents/codex-app-server/deploy/galatea-codex-agent.service)，
Console 默认监听 `127.0.0.1:18880`，使用 Bearer token；由 Host 启动原生
`codex app-server --listen stdio://` 子进程。

部署前准备专用账号、模型配置/凭据、Runtime、合同和接纳记录，并用实际主机路径生成 unit。
安装、只读检查、唯一业务状态 writer 和服务切换见
[Codex Host 配置与部署](../agents/codex-app-server/docs/06-config-deployment.md)及
[生产接纳记录](../agents/codex-app-server/docs/12-production-readiness.md)。模板不是已验收的运行配置。
