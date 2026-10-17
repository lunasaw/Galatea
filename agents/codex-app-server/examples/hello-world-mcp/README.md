# Hello World MCP App

一个独立的 stdio MCP 示例：调用 `draw_hello_world` 后，在对话中显示 SVG 问候卡片。
默认显示 **Hello, World!**，输入名字后可通过 MCP 工具重新绘制。
工具只返回问候文字，不写文件或访问网络。

## 构建

需要 Python 3.11+、Node.js 20+。在本目录运行：

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm --prefix ../../frontend ci
node build.mjs
```

页面使用 Host 前端已锁定的 `@modelcontextprotocol/ext-apps` SDK；构建产物
`dist/app.html` 将脚本、样式和 SVG 全部内联，不需要 CDN。
直接在浏览器中打开该文件也可以预览；独立预览里的重新绘制在浏览器本地执行。

## 接入 MCP Host

将以下配置加入所用 Host 的 `CODEX_HOME/config.toml`，替换为实际绝对路径：

```toml
[mcp_servers.hello_world]
command = "/absolute/path/hello-world-mcp/.venv/bin/python"
args = ["/absolute/path/hello-world-mcp/server.py"]
```

在工作台的「设置 → MCP」重新加载，随后调用：

```text
draw_hello_world({})
draw_hello_world({"name": "世界"})
```

也可以在「设置 → MCP → hello_world → 工具与资源」中展开工具并手动调用。
现有聊天若未刷新工具目录，可新建聊天后请求「用 hello_world 画一个 Hello World」。
该示例是 MCP 服务，入口在 MCP 设置中，不属于 Apps 连接器目录。

工具使用 `_meta.ui.resourceUri` 关联 `ui://hello-world/app.html`；
资源 MIME 为 `text/html;profile=mcp-app`。页面用官方 `App.connect()` 握手，
接收工具结果和主题通知，通过 `callServerTool()` 执行重新绘制。
不支持 MCP Apps 的客户端仍能得到文本问候。

## 验证

构建后运行真实 stdio 协议测试：

```sh
.venv/bin/python -m unittest discover -s tests -v
```

浏览器检查（复用 Host 前端的 Playwright；需已安装 Chromium）：

```sh
node tests/browser.cjs
```

接入边界见 [MCP Apps Host](../../docs/18-mcp-apps.md)。
