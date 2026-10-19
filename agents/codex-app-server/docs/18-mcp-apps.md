# 通用 MCP Apps Host

Host 使用官方 `@modelcontextprotocol/ext-apps 2.0.3` 的 AppBridge 与资源内的 App 通信。
原生 Codex 固定为 `0.163.0`，初始化时声明 `io.modelcontextprotocol/ui` 扩展与
`text/html;profile=mcp-app`。本文描述本仓库实现，不把规范的全部可选能力视为已支持。

## 服务接入

在原生 MCP 配置中注册服务；工具元数据声明 `_meta.ui.resourceUri: "ui://…"`，
资源读取返回匹配 URI 的 HTML，推荐 MIME 为 `text/html;profile=mcp-app`。
工具可用 `_meta.ui.visibility: ["app"]` 标记仅供 App 调用；默认同时允许模型和 App，
`["model"]` 工具不向 App 暴露。Host 仍以原生工具目录为准，不接受 App 自行注册服务或工具。

## 会话中的自主展示

Agent 调用声明了 UI 资源的 MCP 工具时，Host 从原生 `mcpToolCall` 记录解析资源、参数和结果，
自动展开当前聊天的右侧 Apps 面板。无需点击 Apps、不新建会话、不从用户文本匹配训练关键词。
主聊天工具记录保留查看面板按钮，避免同一工具重复挂载内联 iframe。
刷新从会话记录恢复；失败工具不覆盖已展示的视图。该链路不依赖业务 activation。
快照单独保留当前水位的最后一次有效 `app_step`，避免长任务的调用移出历史分页后丢失面板；
会话回滚与作用域过滤同样适用于该记录。

Python Provider 可在成功的 `codex-agent.tools/v1` 回执中返回
`data.app_view: {server, resource_uri, arguments}`。Host 从原生 `dynamicToolCall` 的
结构化结果恢复同一视图；这是一项 Host 兼容扩展，不是 MCP 标准字段。
仅接受 `ui://` 资源，仍经已配置 MCP 服务与当前会话的资源/工具权限校验。
它允许业务工具本身触发展示，不要求模型额外发现打开页面的工具。

## Apps 入口

聊天右上角「打开 Apps」以及 `/apps` 打开右侧 **Apps** 标签，展示 Codex CLI 原生 Apps，以及
MCP 工具声明了 `ui://` 资源的 MCP Apps。目录只列出允许模型发现的入口，App-only 内部操作不作为
启动卡片展示。设置里的全局 Apps 页面保留原生连接器管理。原生 Apps 的目录、已安装状态和工具摘要直接来自 CLI 的
`app/list`、`app/installed`、`app/read`；有连接地址时可从卡片打开。MCP App 则在绑定的
Host 会话中调用对应工具，随后挂载同一套 Host 交互视图，因此可以使用全屏、画中画、右侧固定、
工具与资源桥、下载、权限确认、关闭重开和表单状态保留。

MCP 设置页面仍保留完整的工具、资源和手动调用诊断能力；它是调试入口，不再是 MCP App
唯一的展示入口。右侧入口优先绑定当前聊天；没有活动聊天时自动创建并选中一个会话，
以便项目激活事件可以切换至服务分配的项目会话。工具调用、应用上下文和消息都绑定到这个会话。
服务返回的 ready activation 在项目会话右侧自动展开，刷新后重新读取；普通聊天文本本身不创建 App 任务。

Codex CLI 原生 Apps 是连接器目录和工具元数据能力，不要求聊天上下文；页面支持目录、详情和连接。
本机 `0.163.0` CLI 的 `/apps` 用于浏览和管理连接器，已安装 App 可通过 `$` 插入聊天提示。
CLI 的目录入口本身不是通用 HTML 应用启动器。
原生 App 是否提供可交互页面由连接器自身决定，带 `ui://` 资源的交互页面走 MCP App Host。

Agent 工具项使用 `mcpAppUi.resourceUri` 创建视图，也兼容旧 `appContext.resourceUri`
和 `mcpAppResourceUri`。设置里的手动工具调用读取标准工具元数据，同时兼容
`_meta["ui/resourceUri"]`。App 使用 SDK `App.connect()` 初始化，接收工具参数、结果、
错误或取消通知，再通过 SDK 请求下列宿主能力。

## 已实现能力

| 能力 | 行为 |
| --- | --- |
| 初始化与生命周期 | 协议协商、20 秒初始化超时、重载、优雅关闭与重开；参数和终态各发送一次 |
| 工具与资源 | 限定原 MCP 服务和原聊天；工具列表、调用、资源列表/模板/读取；非只读工具确认后执行 |
| `ui/message` | 支持用户文字、内嵌图片、音频；确认后发回原聊天，空闲走 `turn/start`，运行中走带预期轮次的 `turn/steer` |
| `ui/update-model-context` | 支持文字与结构化数据；覆盖该视图上一份上下文，不触发模型；下一次普通消息或 App 消息带入原生 `additionalContext` |
| 显示模式 | inline、fullscreen、pip；Host 额外提供右侧固定；切换模式复用 iframe，不丢表单和图表状态 |
| Host 上下文 | 主题、语言、时区、样式变量、设备能力、工具身份、容器尺寸和显示模式；主题/尺寸变化发送通知 |
| 外链与下载 | HTTP(S) 外链、内嵌资源或所属 MCP 的资源下载，均经用户确认；限制 8 个文件、单文件 8 MiB、总计 16 MiB |
| 日志 | 最近 50 条、每条最多 4,000 字符，不自动进入模型上下文 |

模型上下文按 Host session、MCP 服务、资源 URI 和工具调用实例隔离，持久化到 Host 状态目录。
同一视图只保存最新选择，各实例互不覆盖；每个聊天最多 32 个实例、总计 256 KiB。
上下文标记为 `untrusted`，不会成为 developer instructions。显式关闭会清除该实例上下文；
切换聊天、刷新浏览器或重启 Host 不隐式删除已保存的选择。持久化选择不等于持久化页面运行状态。

消息最多 64 个内容块、编码后 1 MiB。原生输入和后端校验同时限制内容种类，
不接受 App 通过消息附带服务器本地文件路径。请求有幂等记录；结果未知时不自动重放，
避免重复发消息或重复 steer。上下文更新会串行提交，App 消息等待已收到的上下文更新完成。

## 容器与权限

资源在仅含 `allow-scripts` 的 opaque-origin iframe 中加载。Host 对资源返回的
`resourceDomains`、`connectDomains`、`frameDomains`、`baseUriDomains` 分别生成 CSP，
拒绝非法来源和指向 Host 自身的来源。资源不能访问父页面 DOM、同源存储、Host API，
也不能直接打开弹窗或下载文件；工具、链接、下载与消息都通过受限消息桥处理。
相机、麦克风、定位和剪贴板写入未授予。App 元数据不能提升权限。

右侧固定是当前聊天内的 Host 布局功能；折叠工具过程时保持可见。
右侧 Apps 标签与旧的画中画固定是不同布局；桌面 Apps 标签切换或收起面板复用 iframe，保留草稿。
窄屏使用全屏任务面板；切换桌面/窄屏布局会重新挂载，未提交草稿不保证跨布局保留。
切换聊天或离开页面会卸载视图；不承诺跨聊天保活或刷新后恢复表单草稿。
App 应将业务状态保存到所属服务，重新加载后从服务恢复。

未声明 sampling、App 自定义工具向模型注册、工具/资源 list-changed 转发、流式工具参数、
资源订阅推送、独立 cookie 域或额外浏览器设备权限。服务应根据初始化能力协商降级。
原生工具项提供完整参数时发送 `tool-input`，没有把不存在的增量事件伪造成流式输入。

## 职责边界与验证

Host 不定义 Galatea 阶段、预算、Release 或训练验收；业务 App 和 MCP 服务拥有这些契约。
App 调工具不会自动启动下一轮 Agent；需要继续分析时发 `ui/message`。
关闭浏览器后的任务完成唤醒需要服务端接续机制，本模块不新增 Agent Loop 或后台业务轮询。

`test_mcp_app_browser.cjs` 验证真实 Chromium 中的资源加载、协议握手、作用域和确认、
消息与上下文、下载、三种模式、固定后折叠、表单保留、手机布局和关闭重开。
`test_conversation_app_browser.cjs` 验证 Agent 工具事件自动展开、原会话绑定、刷新和移动端。
`test_app_sidebar_browser.cjs` 验证右侧入口、自动选中会话、标签/收起保留 iframe、激活跳转、刷新和窄屏。
`test_apps_launch_browser.cjs` 验证直接打开、绑定会话复用、会话失效恢复，
以及工具调用和 iframe 资源使用同一会话。
`test_mcp_app_context.py` 验证持久化、隔离、start/steer、去重与未知结果不重放；
`test_mcp_apps.py` 验证 CSP。`test_mcp_app_runtime.py` 可用真实固定版本二进制和本地
模拟 Responses 服务验证 App 选择以非可信用户上下文进入模型请求，不调用付费模型。
命令见 [测试验收](09-test-acceptance.md)。外部业务服务仍需独立联调验收。

2026-10-17 验收：Python 共 186 项，173 通过、13 项运行时测试默认跳过；
另外显式启用实际 `0.154.0` 二进制，App 上下文与原生审批/用户提问两项集成测试通过。
此前前端 97 项、TypeScript 与构建通过。后续入口修复后，前端 99 项、TypeScript 与构建通过，
独立 Apps 入口 Chromium 用例通过；运行中 Host 的 Hello World 实测无需选择聊天即可打开、
重新绘制及全屏保留输入。既有综合 Chromium 脚本在消息后的手机全屏步骤以及上下文响应等待
出现间歇超时，诊断运行曾完整通过，本轮不计为稳定通过。此处不将结果记为 Galatea 业务联调验收。
