# Skill、MCP 与插件管理

本次对照 `codex-webui` 的设计，将扩展管理接入通用 Host 的设置页面。
Codex 继续负责发现、配置、认证、安装和执行；前端负责列表、筛选、操作与反馈。
所有请求复用 `/api/native/call`、幂等记录和固定 `0.154.0` JSON Schema 校验。

## 设计对照

参考代码：`codex-webui/src/{skills,mcp-servers,plugins}` 中的 controller/service，
以及 `web/src/components/integrations/{mcps-tab,plugins-tab,plugin-card,plugin-detail-sheet}.tsx`
和聊天中的 Skill selector。其后端主要将 HTTP 参数转换为原生 RPC；前端维护目录、
操作状态与关联能力的缓存刷新。

目标项目已经有原生协议桥，因此直接增加类型化的 `IntegrationsClient` 和独立管理面板，
复用现有会话、权限与错误处理。没有再增加一层与原生桥重复的 REST 接口或独立插件执行器。

| 能力 | 页面行为 | 原生协议 |
| --- | --- | --- |
| Skills | 按工作目录发现、搜索、仅看已启用、显示发现错误、启停、在对话中使用 | `skills/list`、`skills/config/write`、消息中的 `type: skill` |
| MCP | 读取完整分页、展示连接和认证状态、工具与资源、OAuth、刷新与重新加载 | `mcpServerStatus/list`、`mcpServer/oauth/login`、`config/mcpServer/reload` |
| MCP 配置 | 校验服务名及 JSON 对象，支持 command/args 或 HTTP(S) url，保存后重新加载 | `config/value/write` |
| 插件目录 | 按市场分组、搜索、已安装筛选、精选标记、市场错误 | `plugin/list` |
| 插件详情 | 展示描述、Skills、MCP、应用、能力与认证策略，提供网站和政策链接 | `plugin/read` |
| 插件变更 | 安装、卸载、同步、应用授权提示、部分同步失败明细 | `plugin/install`、`plugin/uninstall`、`plugin/reconcile` |

代码入口为 `frontend/src/integrations-data.ts`、`frontend/src/integrations.tsx`，
由 `native-ui.tsx` 的 Skills、MCP、插件设置分类挂载。

## 与固定协议的适配

- MCP reload 的参数必须是 JSON `null`。前端请求类型、NativeBridge 和 JsonRpcPeer 均保留
  显式 `null`；省略 RPC 参数仍使用 `{}`。传 `{}` 的 reload 在调用运行时前被 schema 拒绝。
- MCP 查询传 `detail: full`，保留当前 `threadId` 并读完所有分页。重复游标报错，避免无限查询。
  面板可见时每 5 秒刷新，后台标签暂停轮询。连接数只统计 `runtimeStatus: connected`；
  缺失或 null 状态显示「状态未知」，认证方式不被当成连接成功。
- 本地市场使用 `marketplacePath`；远程市场没有本地路径，使用 `remoteMarketplaceName`。
  安装和详情查询共用此选择逻辑，卸载使用原生 `pluginId`。
- 只在 `installPolicy: AVAILABLE` 且未被管理员禁用时提供安装。内置插件不提供卸载。
- `plugin/reconcile` 会修改安装状态，必须遵守与安装、卸载相同的正式 Release 配置不可变边界，
  开发模式成功后刷新配置绑定。页面同时禁用只读配置下的启停、保存、安装、卸载和同步。

## 状态与认证

列表按当前工作目录或线程重新加载，过期响应不会覆盖新上下文。Skill 或插件变更通过
`codex-integrations-changed` 通知刷新已挂载的能力目录及插件详情；切换到其他分类时重新查询。
安装返回的 `appsNeedingAuth` 保留在页面中，等待用户打开应用授权地址。

MCP OAuth 在点击时预留浏览器窗口，清除 opener，再请求授权地址。弹窗被拦截时仍显示
可打开和复制的授权链接。OAuth、插件网站、隐私政策、服务条款和应用链接仅接受有效的
HTTP(S) 地址；不接受脚本 URL、相对 URL 或带用户名密码的 URL。
认证完成由运行时状态更新确认，页面不会伪造登录成功。

Skill 的「在对话中使用」保持现有立即发送行为，同时传入原生 Skill 名称及路径。
停用的 Skill 无法从页面显式调用。执行与任务选择仍由 Codex 处理。

## 验证

`frontend/src/integrations-data.test.ts` 覆盖分页、线程上下文、重复游标、null reload、
远程市场选择、Skill 路径、安装策略与 URL 过滤。
`test_native_workspace.py` 与 `test_rpc_lifecycle.py` 覆盖 HTTP、桥接和真实 RPC 编码边界。

```sh
python -m unittest discover -s tests -p 'test_*.py'
cd frontend
npm test
npm run build
```

浏览器验收使用本地合成服务，无外部认证或安装副作用。在项目根目录分别运行：

```sh
python tests/console_browser_fixture.py 18679
CONSOLE_TEST_URL=http://127.0.0.1:18679 node tests/test_integrations_browser.cjs
```

可通过 `CHROMIUM_EXECUTABLE` 指定已有 Chromium，`CONSOLE_SCREENSHOTS` 指定截图目录。
验收覆盖 Skill 启用和原生输入、MCP 多页状态、OAuth 弹窗回退、失败反馈、远程插件详情、
安装/卸载、目录刷新、同步部分失败、只读配置和移动端布局。

`CODEX_TEST_RUNTIME_PACKAGE=<runtime-dir>` 启用 `test_generic_native.py`，使用真实 Codex
0.154.0、本地模拟模型服务和 MCP echo，验证 Skill 发现/启停/调用、null reload 和 MCP 执行。
远程市场和 OAuth 的外部服务可用性仍取决于部署账号、网络与原生功能开关；浏览器合成验收
验证交互与请求契约，未执行真实账号登录或远程插件安装。
