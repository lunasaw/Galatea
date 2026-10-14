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
- DeepSeek Harness：`http://127.0.0.1:9080`

MinIO 凭据由 `/etc/minio/minio.env` 管理，Jupyter Token 由当前运行实例生成。不要把密码、
Token 或其他会话凭据写入仓库；需要访问时从受保护的主机配置或服务日志中获取。

旧机器的 Ray Runtime Package 只读凭据由 `/etc/minio/ray-runtime-s3.env` 管理。
当前 unit 尚未配置该文件；需要从私有 MinIO 下载 Runtime Package 时，按
`doc/ray-start.md` 创建只读凭据并配置 `EnvironmentFile`。

## DeepSeek Harness

`deepseek-harness.service` 以 root 启动已构建的 Harness Web profile，监听回环地址
`127.0.0.1:9080`。Harness 的 API 凭据和浏览器会话记录由 `/root/.dsh/.credentials.yaml`
管理；Galatea 的 Ray 发布元数据写入被 Git 忽略的
`platform-data/ray-cats-and-dogs-job`。服务不会把密钥写入 unit 或仓库。该凭据文件必须
保持 `0600`，并由启动服务的同一系统用户拥有。

安装、校验并启动：

```bash
sudo install -m 0644 \
  /data/ai/chenzhangyue/code/galatea/systemd/deepseek-harness.service \
  /etc/systemd/system/deepseek-harness.service
sudo systemd-analyze verify /etc/systemd/system/deepseek-harness.service
sudo systemctl daemon-reload
sudo systemctl enable --now deepseek-harness.service
sudo systemctl status deepseek-harness.service --no-pager -l
```

服务日志中的 `dsh web:` 行包含当前启动令牌。通过 SSH 反向转发到
`172.25.40.127:10991` 时，在运行 Harness 的机器上执行：

```bash
ssh -N -o ExitOnForwardFailure=yes \
  -R 0.0.0.0:10991:127.0.0.1:9080 \
  luna@172.25.40.127
```

然后在远端访问服务日志给出的 token URL，并将地址中的端口替换为
`http://172.25.40.127:10991/`。反向转发要求远端 SSH 服务允许
`AllowTcpForwarding`；若需要让其他机器访问 `10991`，还需远端 `GatewayPorts` 和防火墙
放行。Harness 本身仍只监听回环地址，不直接暴露到网络。
