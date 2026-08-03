# 服务访问说明

- MinIO 控制台：`http://127.0.0.1:9001`
- JupyterLab：`https://coder.vdian.net/GC5026/absproxy/8888/`
- Ray Head：`10.19.20.26:6379`
- Ray Dashboard：`http://127.0.0.1:8265`

MinIO 凭据由 `/etc/minio/minio.env` 管理，Jupyter Token 由当前运行实例生成。不要把密码、
Token 或其他会话凭据写入仓库；需要访问时从受保护的主机配置或服务日志中获取。

Ray Runtime Package 的只读凭据由 `/etc/minio/ray-runtime-s3.env` 管理，并通过
`ray-head.service` 的 `EnvironmentFile` 注入。安装和轮换步骤见 `doc/ray-start.md`。
