# code-server 中 `proxy` 与 `absproxy` 的使用说明

本文记录当前机器上 code-server 4.101.1 的路径代理行为，以及在
`https://coder.vdian.net/GC5026/` 下暴露 JupyterLab 等本地 Web 服务时的配置方法。

## 1. 先明确：`absproxy` 是 code-server 专有特性

`absproxy` 是 code-server 提供的路由名称和实现，不是 HTTP、Nginx、Jupyter 或
通用反向代理规范中的标准术语。当前安装的 code-server 4.101.1 明确注册了以下两类
端口代理路由：

```text
/proxy/:port/...
/absproxy/:port/...
```

相关边界如下：

- `/proxy/<port>/` 和 `/absproxy/<port>/` 都是 code-server 的 URL 约定。
- `--abs-proxy-base-path` 是 code-server 的专有启动参数。
- “剥掉代理前缀再转发”和“保留完整路径再转发”是通用反向代理概念；Nginx、Traefik
  等软件可以实现类似行为，但不会因此自动提供 `/absproxy/<port>/` 路由。
- Jupyter 不理解 `absproxy` 本身。它只根据自己的 `ServerApp.base_url` 接受请求并
  生成 URL；这里必须由管理员让 Jupyter 的 base URL 与 code-server 的公开路径一致。
- 原生 VS Code Server、Coder Workspace、JupyterHub 或旧版 code-server 不一定支持
  这套路由和参数，不能直接照搬本文配置。

本文中的结论已针对本机 code-server 4.101.1 验证。升级、降级或换用其他产品前，
应先检查目标版本：

```bash
/data/ai/chenzhangyue/code-server/code-server-4.101.1-linux-amd64/bin/code-server --version
/data/ai/chenzhangyue/code-server/code-server-4.101.1-linux-amd64/bin/code-server --help \
  | rg 'abs-proxy-base-path'
```

在当前版本的实现中，路由注册位于 `out/node/routes/index.js`，实际路径构造位于
`out/node/routes/pathProxy.js`。因此，本文所说的 `absproxy` 应理解为：

> code-server 对“保留代理路径，并可补充外层 base path 后转发到本机端口”的专有实现。

## 2. 当前部署的路径层级

当前链路有两层代理：

```text
浏览器
  https://coder.vdian.net/GC5026/{proxy|absproxy}/<port>/...
      |
      | 平台入口处理外层 /GC5026
      v
code-server :8081
  /{proxy|absproxy}/<port>/...
      |
      | code-server 连接同一机器的 0.0.0.0:<port>
      v
本地 Web 服务，例如 JupyterLab :8888
```

当前 code-server 配置中的关键项是：

```yaml
# code-server config.yaml
bind-addr: 0.0.0.0:8081
abs-proxy-base-path: /GC5026
```

systemd drop-in 中还设置了：

```ini
[Service]
Environment="VSCODE_PROXY_URI=https://coder.vdian.net/GC5026/absproxy/{{port}}/"
```

这里有两个容易混淆的概念：

- `abs-proxy-base-path` 参与 `absproxy` 向后端转发时的路径构造。
- `VSCODE_PROXY_URI` 主要控制 code-server 的 Ports 面板生成什么外部链接；它不替后端应用配置 base path，也不会改变应用返回的 HTML。

`VSCODE_PROXY_URI` 是通用模板，当前模板使用 `absproxy` 是为了 JupyterLab；它不表示
所有被代理的端口都必须使用 `absproxy`。MinIO Console 是一个例外，见第 5.3 节。

## 3. 两种代理的核心区别

| 项目 | 普通 `proxy` | `absproxy` |
| --- | --- | --- |
| 外部 URL | `/GC5026/proxy/<port>/...` | `/GC5026/absproxy/<port>/...` |
| code-server 路由 | `/proxy/:port/...` | `/absproxy/:port/...` |
| 转发给后端的路径 | 去掉 `/proxy/<port>` | 保留代理路径，并可在前面补 `abs-proxy-base-path` |
| 后端认为自己挂载在哪里 | 通常是 `/` | 完整公开路径，例如 `/GC5026/absproxy/8888/` |
| 绝对重定向 `Location: /...` | code-server 会按内部代理前缀改写 | code-server 不改写，后端必须自己生成正确路径 |
| HTML 中的 `/static/...` 等绝对链接 | code-server 不改写，容易跳出代理路径 | 后端配置正确 base path 后可以生成正确链接 |
| 适合场景 | 使用相对 URL、默认挂载在 `/` 的简单服务 | Jupyter、Gradio 等可显式配置根路径的应用 |

两种模式都支持普通 HTTP 和 WebSocket，也都受 code-server 的认证与
`--disable-proxy` 配置控制。

### 普通 `proxy` 的转发示例

浏览器访问：

```text
https://coder.vdian.net/GC5026/proxy/3000/api/status
```

平台入口去掉外层 `/GC5026` 后，code-server 收到：

```text
/proxy/3000/api/status
```

code-server 去掉 `/proxy/3000`，后端 `3000` 端口最终收到：

```text
/api/status
```

因此后端应当按根路径 `/` 运行。普通模式不会扫描和改写 HTML、CSS、JS
响应体。如果页面写死了 `<script src="/static/app.js">`，浏览器会从域名根路径
请求 `/static/app.js`，而不是从 `/GC5026/proxy/3000/static/app.js` 请求，页面就可能
出现静态资源 404。

### `absproxy` 的转发示例

浏览器访问：

```text
https://coder.vdian.net/GC5026/absproxy/8888/lab
```

平台入口去掉 `/GC5026` 后，code-server 收到：

```text
/absproxy/8888/lab
```

code-server 根据 `abs-proxy-base-path: /GC5026` 补回外层前缀，Jupyter 最终收到：

```text
/GC5026/absproxy/8888/lab
```

因此 Jupyter 的 base path 也必须是：

```text
/GC5026/absproxy/8888/
```

`absproxy` 并不是替应用改写静态资源，而是把完整公开路径交给应用，让应用自己
生成带完整 base path 的重定向、静态资源 URL 和 WebSocket URL。

## 4. 普通 `proxy` 的写法

### 4.1 code-server Ports 面板链接

如果要让 Ports 面板生成普通代理链接，systemd drop-in 可写成：

```ini
# /etc/systemd/system/code-server.service.d/proxy.conf
[Service]
Environment="VSCODE_PROXY_URI=https://coder.vdian.net/GC5026/proxy/{{port}}/"
```

`{{port}}` 是 code-server 使用的占位符，不能写成固定端口，也不要删除两层花括号。

应用通常直接监听本机端口，不设置额外 base path。例如：

```bash
python -m http.server 3000 --bind 0.0.0.0
```

访问地址为：

```text
https://coder.vdian.net/GC5026/proxy/3000/
```

适用条件：

- 应用可以在 `/` 下运行。
- 页面资源和前端路由使用相对 URL，或应用本身兼容反向代理前缀。
- 应用没有大量写死的 `/static`、`/api`、`/ws` 等域名根路径。

### 4.2 不适合直接使用普通 `proxy` 的应用

如果应用必须知道外部挂载路径，或者会生成大量以 `/` 开头的绝对资源链接，普通
`proxy` 通常不合适。JupyterLab 就属于这种情况：普通代理会把路径前缀剥掉，但
Jupyter 又需要一个一致的 `base_url` 来生成登录、静态资源和 WebSocket 地址。

不要把 Jupyter 配成 `/GC5026/proxy/8888/` 来尝试解决这个问题，因为普通
`proxy` 转发时会把 `/proxy/8888` 剥掉，后端收到的路径与该 `base_url` 仍然不一致。

## 5. `absproxy` 的写法

### 5.1 code-server 配置

当前部署使用：

```yaml
# /data/ai/chenzhangyue/code-server/code-server-4.101.1-linux-amd64/config.yaml
bind-addr: 0.0.0.0:8081
auth: password
cert: false
abs-proxy-base-path: /GC5026
```

Ports 面板使用：

```ini
# /etc/systemd/system/code-server.service.d/proxy.conf
[Service]
Environment="VSCODE_PROXY_URI=https://coder.vdian.net/GC5026/absproxy/{{port}}/"
```

`abs-proxy-base-path` 只写平台在 code-server 前面的公共前缀 `/GC5026`，不要写成：

```yaml
# 错误示例
abs-proxy-base-path: /GC5026/absproxy/8888
```

`/absproxy/<port>` 由 code-server 根据实际请求自动保留。把它也写进
`abs-proxy-base-path` 会造成路径重复。

如果 code-server 直接部署在域名根路径，例如公开地址就是
`https://example.com/absproxy/8888/`，则不需要 `/GC5026` 这一层，通常应省略
`abs-proxy-base-path`。

### 5.2 JupyterLab 配置

当前 JupyterLab 的正确参数是：

```ini
ExecStart=/data/conda/envs/attend-ray-py312/bin/jupyter-lab \
  --no-browser \
  --allow-root \
  --ServerApp.ip=0.0.0.0 \
  --ServerApp.port=8888 \
  --ServerApp.port_retries=0 \
  --ServerApp.root_dir=/data/ai/chenzhangyue/code/galatea \
  --ServerApp.allow_remote_access=True \
  --ServerApp.quit_button=False \
  --ServerApp.base_url=/GC5026/absproxy/8888/ \
  --ServerApp.custom_display_url=https://coder.vdian.net/GC5026/absproxy/8888/
```

其中：

- `ServerApp.base_url` 决定 Jupyter 实际接受哪些路径，以及页面生成的资源和 API 路径。
- `ServerApp.custom_display_url` 只控制启动日志中显示的访问地址，不能替代 `base_url`。
- 两处都建议保留末尾 `/`。

公开访问地址为：

```text
https://coder.vdian.net/GC5026/absproxy/8888/
```

Jupyter 自身的 token/password 认证仍然存在。通过 code-server 认证不等于自动通过
Jupyter 认证；Jupyter 重启后，自动生成的随机 token 也会变化。

### 5.3 MinIO Console 配置

MinIO Console 当前必须使用普通 `proxy`，不要使用 `absproxy`：

```text
https://coder.vdian.net/GC5026/proxy/9001/
```

原因是普通 `proxy` 会把 `/proxy/9001` 前缀剥掉，MinIO 收到根路径 `/`、`/manifest.json`、
`/static/...` 和 `/api/v1/...`；`absproxy` 会把完整的
`/GC5026/absproxy/9001/...` 路径透传给 MinIO，而 MinIO Console 不支持这种挂载方式。

MinIO 的环境文件必须设置与公开地址一致的 Console URL：

```ini
# /etc/minio/minio.env
MINIO_BROWSER_REDIRECT_URL=https://coder.vdian.net/GC5026/proxy/9001/
```

修改后重启 MinIO：

```bash
sudo systemctl restart minio.service
curl -fsS http://127.0.0.1:9001/ | grep -o '<base href="[^"]*"'
```

预期为：

```html
<base href="/GC5026/proxy/9001/">
```

### 5.4 code-server 登录 Cookie 与 `401`

`proxy` 和 `absproxy` 都受 code-server `auth: password` 保护。访问根路径时，未认证请求
会重定向到 `/GC5026/login`；访问静态资源、`manifest.json` 或 API 时，未认证请求直接
返回 `401 Unauthorized`。因此“页面 HTML 已返回”不代表后续资源请求已经通过认证。

登录成功后，浏览器应保存以下 Cookie：

```text
名称：code-server-session
Domain：coder.vdian.net
Path：/GC5026
```

排查时打开浏览器开发者工具的 Network，检查失败的
`/GC5026/proxy/9001/manifest.json` 请求是否带有：

```http
Cookie: code-server-session=...
```

如果没有 Cookie，先清除 `coder.vdian.net` 的旧 Cookie，再在同一域名下打开
`https://coder.vdian.net/GC5026/login` 完成 code-server 登录；仅登录 Coder 平台或
MinIO Console 不等于登录 code-server。不要把 code-server 密码写入 URL、Notebook 或文档。

使用有效 Cookie 时，可以从服务器侧验证认证和代理链路：

```bash
curl -sk -b /path/to/cookies.txt \
  -o /dev/null \
  -w '%{http_code} %{content_type}\n' \
  https://coder.vdian.net/GC5026/proxy/9001/manifest.json
```

预期为 `200 application/json`。没有 Cookie 时预期为 `401 application/json`，这是
code-server 的正常安全行为，不应通过关闭认证来“修复”。

## 6. 修改配置后的生效方式

修改 code-server 的 YAML、systemd unit 或 drop-in 后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl restart code-server.service
sudo systemctl status code-server.service --no-pager -l
```

修改 Jupyter 的 unit 后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl restart jupyterlab.service
sudo systemctl status jupyterlab.service --no-pager -l
```

重启 Jupyter 会生成新的随机 token。旧浏览器 URL 中的 token 会返回 `401`，这不代表
服务已经挂掉。

## 7. 验证与排错

### 7.1 检查宿主机服务

```bash
systemctl status code-server.service jupyterlab.service --no-pager -l
journalctl -u code-server.service -n 100 --no-pager
journalctl -u jupyterlab.service -n 100 --no-pager
```

Jupyter systemd 服务使用独立运行目录 `/run/jupyterlab`。获取当前实例信息时应查看：

```bash
sudo ls -l /run/jupyterlab/
sudo sed -n '1,160p' /run/jupyterlab/jpserver-*.json
```

不要只依赖普通终端里的 `jupyter server list`。如果终端使用了不同的
`JUPYTER_RUNTIME_DIR`、用户或 PID 命名空间，它可能看不到 systemd 启动的实例。

### 7.2 从后端到外部逐层验证

`absproxy` 模式下，先验证 Jupyter 是否接受完整 base path：

```bash
curl -I http://127.0.0.1:8888/GC5026/absproxy/8888/
```

再验证 code-server 的代理入口：

```bash
curl -I http://127.0.0.1:8081/absproxy/8888/
```

最后在浏览器访问：

```text
https://coder.vdian.net/GC5026/absproxy/8888/
```

普通 `proxy` 模式下，后端检查路径应为根路径：

```bash
curl -I http://127.0.0.1:3000/
curl -I http://127.0.0.1:8081/proxy/3000/
```

### 7.3 常见现象

| 现象 | 常见原因 |
| --- | --- |
| 登录提交返回 `401` | Jupyter token 错误，或使用了重启前的旧 token |
| `/static/...`、`/api/...` 大量 404 | 应用生成了域名根路径，普通 `proxy` 无法改写响应体 |
| `/absproxy/8888/...` 404 | 后端要求 `/GC5026/absproxy/8888/...`，但 `abs-proxy-base-path` 缺失 |
| `/GC5026/GC5026/absproxy/...` | 外层前缀被重复添加 |
| Jupyter 首页能开但 CSS/JS 404 | Jupyter `base_url` 与浏览器公开路径不一致 |
| Ports 面板仍生成旧 URL | `VSCODE_PROXY_URI` 未修改，或 code-server 未重启 |
| `jupyter server list` 显示为空 | 查看命令与 systemd 服务使用了不同 runtime 目录或隔离环境 |

## 8. 选择建议

- 简单静态服务、使用相对资源路径的开发服务器：优先普通 `proxy`。
- 能显式配置 `root_path`、`base_url`、`url_prefix` 的应用：优先 `absproxy`。
- 当前 `/GC5026` 部署中的 JupyterLab：使用 `absproxy`，并让 Jupyter
  `base_url` 精确等于 `/GC5026/absproxy/8888/`。
- 不要混用外部 `/proxy/...` 地址和后端 `absproxy` base path，反之亦然。

## 9. 本机配置位置

```text
/data/ai/chenzhangyue/code-server/code-server-4.101.1-linux-amd64/config.yaml
/etc/systemd/system/code-server.service
/etc/systemd/system/code-server.service.d/proxy.conf
/etc/systemd/system/jupyterlab.service
/data/ai/chenzhangyue/code/galatea/systemd/jupyterlab.service
```

本文不会记录 code-server 密码或 Jupyter token；这些凭据应从当前运行实例或安全配置
中读取，不应提交到文档。
