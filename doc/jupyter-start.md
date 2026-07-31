# JupyterLab 安装、启动与运维

本文记录当前训练节点上 JupyterLab 的完整部署方式。命令和路径均以
`/data/ai/chenzhangyue/code/galatea` 为准，覆盖环境安装、前台试运行、systemd 常驻、
访问认证、健康检查和常见故障排查。

文档基线日期：2026-07-30。

> JupyterLab 可以执行任意 Python 和终端命令，权限等同于启动它的系统用户。当前
> unit 以 `root` 运行，因此不得把 8888 端口无认证地暴露到公网。

## 1. 当前部署基线

| 项目 | 当前值 |
| --- | --- |
| Conda | `/data/conda` |
| 环境 | `attend-ray-py312` |
| Python | 3.12.12 |
| JupyterLab | 4.6.2 |
| Jupyter Server | 2.20.0 |
| Notebook 根目录 | `/data/ai/chenzhangyue/code/galatea` |
| 配置目录 | `/data/ai/chenzhangyue/code/galatea/platform-data/jupyter/config` |
| 数据目录 | `/data/ai/chenzhangyue/code/galatea/platform-data/jupyter/data` |
| systemd 运行目录 | `/run/jupyterlab` |
| 监听端口 | 8888 |
| unit 源文件 | `/data/ai/chenzhangyue/code/galatea/systemd/jupyterlab.service` |
| unit 安装位置 | `/etc/systemd/system/jupyterlab.service` |

当前常驻服务通过 code-server 的 `absproxy` 路径访问，因此 Jupyter 的 base URL 是：

```text
/GC5026/absproxy/8888/
```

如果服务器地址、平台前缀或端口发生变化，必须同步修改 systemd unit 中的
`ServerApp.base_url` 和 `ServerApp.custom_display_url`。代理原理与 code-server 配置见
[`code-server-proxy.md`](./code-server-proxy.md)。

## 2. 部署前检查

确认 Conda、训练目录和端口状态：

```bash
/data/conda/bin/conda --version
/data/conda/bin/conda env list
test -d /data/ai/chenzhangyue/code/galatea && echo "train directory exists"
ss -lntp | grep -E ':8888\\b' || true
df -h /data/ai/chenzhangyue/code/galatea
```

如果 8888 已被其他进程占用，应先确认该进程是否就是现有 JupyterLab，不要同时启动
前台实例和 systemd 实例。

## 3. 创建环境并安装

先加载 Conda shell 支持：

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
python -m pip install \
  "jupyterlab==4.6.2" \
  "jupyter_server==2.20.0" \
  ipykernel
python -m pip check
```

验证安装位置和版本，确保没有误用 base 环境中的命令：

```bash
command -v python
command -v jupyter-lab
python --version
jupyter-lab --version
python -m jupyter server --version
```

预期 `python` 和 `jupyter-lab` 都位于
`/data/conda/envs/attend-ray-py312/bin/`。

JupyterLab 已自带当前环境的 Python 内核。需要显式命名内核时可执行：

```bash
python -m ipykernel install \
  --sys-prefix \
  --name attend-ray-py312 \
  --display-name "Python 3.12 (attend-ray-py312)"
jupyter kernelspec list
```

## 4. 创建持久化目录

当前 systemd 服务以 `root:root` 运行，并使用独立的配置、数据和运行目录：

```bash
sudo install -d -o root -g root -m 0750 \
  /data/ai/chenzhangyue/code/galatea/platform-data/jupyter/config \
  /data/ai/chenzhangyue/code/galatea/platform-data/jupyter/data
```

`/run/jupyterlab` 不需要手工创建；systemd 会根据 unit 中的
`RuntimeDirectory=jupyterlab` 在每次启动时创建，并在服务停止后清理。

如果以后把 unit 的 `User` 改为普通账号，还必须把 Notebook、配置和数据目录的属主及
读写权限一并调整。

## 5. 前台试运行

首次安装建议先以前台方式验证。该模式只监听服务器回环地址，适合 SSH 隧道，不使用
当前 code-server 的 base URL：

```bash
source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312

jupyter lab \
  --no-browser \
  --allow-root \
  --ServerApp.ip=127.0.0.1 \
  --ServerApp.port=8888 \
  --ServerApp.port_retries=0 \
  --ServerApp.root_dir=/data/ai/chenzhangyue/code/galatea
```

启动日志会给出带临时 token 的 URL。另开终端检查 HTTP 响应：

```bash
curl -I http://127.0.0.1:8888/
```

返回 `200` 或跳转到登录页的 `302` 均说明 HTTP 服务已响应。验证完成后在启动终端按
`Ctrl+C` 停止前台实例，再部署 systemd 服务。

## 6. 安装 systemd 常驻服务

仓库已提供与当前机器一致的 unit：

```text
/data/ai/chenzhangyue/code/galatea/systemd/jupyterlab.service
```

它包含以下关键设置：

| 设置 | 作用 |
| --- | --- |
| `User=root` | 与当前目录权限保持一致；也意味着必须严格限制访问 |
| 固定 `PATH` | 始终使用 `attend-ray-py312` 中的 JupyterLab |
| `JUPYTER_CONFIG_DIR` / `JUPYTER_DATA_DIR` | 将状态保存在项目的 `platform-data` 下 |
| `JUPYTER_RUNTIME_DIR=/run/jupyterlab` | 保存当前实例元数据，不与交互 shell 混用 |
| `ServerApp.ip=0.0.0.0` | 允许同机 code-server 代理接入；依赖防火墙和上层认证保护 |
| `ServerApp.port_retries=0` | 8888 被占用时直接失败，避免悄悄改用其他端口 |
| `ServerApp.base_url` | 与公开的 `absproxy` 路径保持一致 |
| `Restart=on-failure` | 异常退出后自动重启 |

安装前先复核 unit 中的用户、路径、域名和 `/GC5026` 前缀：

```bash
sed -n '1,240p' /data/ai/chenzhangyue/code/galatea/systemd/jupyterlab.service
```

确认无误后安装并立即启动：

```bash
sudo install -m 0644 \
  /data/ai/chenzhangyue/code/galatea/systemd/jupyterlab.service \
  /etc/systemd/system/jupyterlab.service
sudo systemctl daemon-reload
sudo systemctl enable --now jupyterlab.service
```

检查启动状态和最近日志：

```bash
sudo systemctl status jupyterlab.service --no-pager -l
sudo journalctl -u jupyterlab.service -n 100 --no-pager
ss -lntp | grep -E ':8888\\b'
```

## 7. 配置登录认证

### 7.1 使用自动生成的 token

Jupyter 默认在每次启动时生成 token。当前 systemd 实例的 URL 可通过相同运行目录
查询：

```bash
sudo env JUPYTER_RUNTIME_DIR=/run/jupyterlab \
  /data/conda/envs/attend-ray-py312/bin/jupyter server list
```

输出包含登录 token，视同密码处理：不要粘贴到聊天、工单、Notebook 或代码仓库中。
服务重启后旧 token 失效，使用旧地址收到 `401` 属于正常现象。

### 7.2 设置稳定密码（可选）

需要固定登录凭据时，在 Jupyter 当前配置目录中交互式设置密码：

```bash
sudo env \
  JUPYTER_CONFIG_DIR=/data/ai/chenzhangyue/code/galatea/platform-data/jupyter/config \
  JUPYTER_DATA_DIR=/data/ai/chenzhangyue/code/galatea/platform-data/jupyter/data \
  /data/conda/envs/attend-ray-py312/bin/jupyter server password
sudo systemctl restart jupyterlab.service
```

密码以哈希形式写入配置，但配置目录仍应保持仅服务账号可写。code-server 登录和
Jupyter 登录是两层独立认证，通过前者不会自动通过后者。

## 8. 访问方式

### 8.1 当前 code-server 路径代理

当前部署的浏览器入口是：

```text
https://coder.vdian.net/GC5026/absproxy/8888/
```

这里必须使用 `absproxy`，且公开路径必须与 Jupyter 的 `ServerApp.base_url` 完全一致，
包括末尾 `/`。

### 8.2 SSH 隧道

在本地电脑执行：

```bash
ssh -N -L 8888:127.0.0.1:8888 用户名@服务器地址
```

需要同时访问 MLflow 时，可以在同一条 SSH 连接中转发两个端口：

```bash
ssh -N \
  -L 8888:127.0.0.1:8888 \
  -L 5000:127.0.0.1:5000 \
  用户名@服务器地址
```

MLflow 本地入口和验证方法见 [`mlflow-start.md`](./mlflow-start.md)。

如果连接的是第 5 节的前台实例，打开：

```text
http://127.0.0.1:8888/
```

如果连接的是当前 systemd 实例，由于它启用了 base URL，应打开：

```text
http://127.0.0.1:8888/GC5026/absproxy/8888/
```

SSH 隧道只改变网络路径，不会移除 Jupyter 配置的 base URL。

## 9. 完整验证

### 9.1 服务与登录页

先验证后端接受完整 base URL：

```bash
curl -I http://127.0.0.1:8888/GC5026/absproxy/8888/
```

预期为 `200` 或 `302`，而不是 `404`。再从浏览器入口登录，确认 JupyterLab 页面、
文件列表和终端均能打开。

### 9.2 API 状态

在可信终端中输入 token；`read -s` 不会把它回显到屏幕：

```bash
read -rsp 'Jupyter token: ' JUPYTER_TOKEN; echo
curl -fsS \
  -H "Authorization: token ${JUPYTER_TOKEN}" \
  http://127.0.0.1:8888/GC5026/absproxy/8888/api/status
unset JUPYTER_TOKEN
```

成功时返回 JSON 状态并且 HTTP 状态为 `200`。

### 9.3 Notebook 内核

新建一个 Notebook，选择 `Python 3.12 (attend-ray-py312)` 或默认 Python 3 内核，执行：

```python
import sys
from pathlib import Path

print(sys.executable)
print(Path.cwd())
```

`sys.executable` 应指向 `/data/conda/envs/attend-ray-py312/bin/python`，工作目录应位于
`/data/ai/chenzhangyue/code/galatea` 内。

## 10. 日常运维

```bash
# 查看状态和持续日志
sudo systemctl status jupyterlab.service --no-pager -l
sudo journalctl -u jupyterlab.service -f

# 修改 unit 后生效
sudo systemctl daemon-reload
sudo systemctl restart jupyterlab.service

# 停止或启动
sudo systemctl stop jupyterlab.service
sudo systemctl start jupyterlab.service
```

升级前先记录版本。升级完成后重启服务，并重复第 9 节验证：

```bash
source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312
jupyter-lab --version
python -m pip install --upgrade "jupyterlab==4.6.2" "jupyter_server==2.20.0"
python -m pip check
sudo systemctl restart jupyterlab.service
```

如需升级到其他版本，应先在测试环境验证扩展、Notebook 内核和代理路径兼容性，再修改
固定版本号。

## 11. 常见故障

| 现象 | 检查与处理 |
| --- | --- |
| 服务报 `Address already in use` | 用 `ss -lntp` 确认 8888 占用者；停止重复的前台实例或旧服务 |
| 浏览器返回 `401` | token 错误或服务已重启；重新执行 `jupyter server list`，不要复用旧 URL |
| 入口或静态资源返回 `404` | 检查公开 URL、`base_url` 和 `/GC5026/absproxy/8888/` 是否完全一致 |
| 首页能开但 CSS、JS 或 WebSocket 失败 | 通常是误用了普通 `proxy`，或代理与 Jupyter base URL 不一致 |
| `jupyter server list` 为空 | 指定 `JUPYTER_RUNTIME_DIR=/run/jupyterlab`，并使用与 unit 相同的用户执行 |
| Notebook 中导入包失败 | 检查 `sys.executable`；依赖可能装到了 base 或其他 Conda 环境 |
| 无法创建或保存文件 | 检查 Notebook 根目录、目标文件及 `platform-data/jupyter` 的属主和权限 |
| systemd 启动后立即退出 | 查看 `journalctl`；重点检查环境可执行文件、目录权限、端口和 unit 参数 |

## 12. 安全检查清单

- 不把 token、密码或运行目录中的 JSON 文件提交到仓库。
- 不把 8888 端口直接加入公网安全组；优先使用 SSH 隧道或带认证的 code-server 代理。
- 保留 Jupyter 自身认证，即使外层代理已经要求登录。
- 定期检查 `journalctl`、磁盘空间和 Notebook 目录权限。
- 多用户环境不要继续以 `root` 运行；应改用专用低权限账号并重新规划目录权限。
- 域名、代理前缀或端口变化时，同时验证页面资源、API 和 WebSocket，而不只检查首页。
