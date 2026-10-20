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

Ray Runtime Package 只读凭据由 `/etc/minio/ray-runtime-s3.env` 管理；仓库当前
`ray-head.service` 已通过 `EnvironmentFile` 引用该文件。新主机安装前按
`doc/ray-start.md` 准备对应的只读身份，不能替换为发布者或 MLflow 服务端凭据。

## Codex Agent

Agent Runtime 统一使用 Codex。Web Agent 的 unit 模板位于
[`agents/codex-app-server/deploy/codex-agent.service`](https://github.com/lunasaw/open-codex/blob/6ef38b998b465eadf1295b8d30378747c62d2ba8/deploy/codex-agent.service)，
Console 默认监听 `0.0.0.0:18880`，需受控内网或带认证的反向代理；由 Host 启动原生
`codex app-server --listen stdio://` 子进程。

部署前准备专用账号、模型配置/凭据、Runtime、扩展配置和接纳记录，并用实际主机路径生成 unit。
安装、配置、接纳和服务运行边界见
[Codex Host 配置与部署](https://github.com/lunasaw/open-codex/blob/6ef38b998b465eadf1295b8d30378747c62d2ba8/docs/06-config-deployment.md)及
[生产接纳记录](https://github.com/lunasaw/open-codex/blob/6ef38b998b465eadf1295b8d30378747c62d2ba8/docs/12-production-readiness.md)。模板不是已验收的运行配置。

Host 仅接受 `codex-agent/v2`，不安装或启动 Galatea Service。需要平台工具时，独立部署
`services/galatea-mcp/` 并在原生 `codex-home/config.toml` 配置 MCP；Skill 根与可选 Python
Provider 在 `agent.json` 配置。旧业务 Host 的配置、状态和接纳记录不自动迁移。
当前职责与训练授权边界见 [Codex 与 Galatea 架构](../doc/agent-galatea.md)。
