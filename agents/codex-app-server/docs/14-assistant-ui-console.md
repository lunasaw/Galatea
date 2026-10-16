# React 聊天工作台

更新：2026-10-15。

## ChatGPT Clone 与思考摘要（2026-10-15）

以相同固定提交的官方 `examples/chatgpt.tsx` 适配现有 Thread：居中的欢迎页、
768px 正文、单行圆角输入框、右侧用户气泡、回答下方复制/导出操作、灰阶侧栏，
并支持深色主题和手机布局。保留会话搜索、重命名、工具回执与详情面板。
上游出处和源码 Git blob 见 [组件来源](../frontend/UPSTREAM.md) 与
[清单](../frontend/upstream-components.json)。

思考区采用官方 Reasoning 组件。Host 将 `item/reasoning/summaryTextDelta`
按原生 item 和 `summaryIndex` 聚合为同一个 reasoning 步骤，`item/completed`
的公开 `summary` 替换增量文本；前端只读取这些公开字段，不读取私有 content
或 `item/reasoning/textDelta`。流式时自动展开、结束后自动收起，手动操作后尊重
用户选择。中断显示“思考已停止”；没有公开摘要时不生成虚构内容。

只有完整的 Host 开始/结束观察时间才展示耗时，表示 Host 观察到的时长。
旧进程的分离摘要事件仍能去重、显示和折叠，但不补造时间；新投影与计时在
Host 下次正常启动后生效。本次保留当前运行进程与会话，只更新直接读取的静态文件。
固定水位的旧消息分页仍只展示该页正文，不混入最新工具或思考状态。

验证：生产构建、10 项前端测试、26 项观察管线/存储/RPC 测试、8 项 Console
测试和 2 项 SSE 测试通过。Playwright 使用合成协议验证摘要流式、多段最终覆盖、
自动收起与手动展开、刷新去重、耗时、Markdown/复制/导出、手机聊天、主题、
原有会话与分页功能、CSP/XSS。此验收没有调用真实模型或执行训练。
当前 18880 返回的 JS/CSS 已核对与本次构建逐字节一致；公开摘要仍取决于实际
模型事件，不保证每次回复都有摘要。

Console 使用 React / TypeScript 与 MIT 许可的 `@assistant-ui/react`，通过
`ExternalStoreRuntime` 接入现有 Python Host 的会话、投影和 SSE API。
消息与工具执行仍由原生 Codex app-server 驱动，浏览器只负责展示和调用现有控制接口。

## 页面

- 左侧会话列表、搜索、首次提问生成本机标题、刷新恢复所选会话。
- 新会话欢迎页、建议问题、Enter 发送、Shift+Enter 换行、停止回复。
- 流式 Markdown、GFM 表格、代码高亮、代码与回复复制、自动滚动及返回最新。
- 可折叠工具卡片：真实调用参数、工具回执、成功/失败状态、最近一次作业观察。
- 右侧能力、执行流程、原生事件；事件详情、精确筛选、固定水位分页和实时订阅。
- 浅色/深色主题、响应式手机布局、键盘焦点和减少动态效果偏好。

标题、主题、当前会话 ID 保存在浏览器 localStorage；它们不会成为聊天正文存储。
正文与事件仍只保留在当前 Host 进程，重启换 epoch 后显示历史重置提示。
历史消息按固定水位翻页，查看旧页时暂停正文的实时替换；返回最新后恢复。
发送请求重试复用相同正文、request_id 和 Idempotency-Key。
会话切换采用取消订阅与请求代数检查，旧会话响应不能覆盖当前会话。

## 构建和验证

```bash
cd galatea/agents/codex-app-server/frontend
npm ci
npm run build
npm test
```

构建输出覆盖 `src/codex_agent/console/static/{index.html,app.js,style.css}`。
现有 `transport.js` 继续处理 UTF-8 与 SSE 帧。所有资源从同源加载；没有 CDN、外部字体或内联脚本。
Node 仅为开发和构建依赖，Python wheel 带有构建产物，运行时无需 Node。
`package-lock.json` 固定依赖；构建产物内包含第三方许可证，明细见
[THIRD_PARTY_NOTICES.txt](../frontend/THIRD_PARTY_NOTICES.txt)。`frontend/licenses/` 补充了 npm 包遗漏的声明：
`react-remove-scroll-bar` 来自上游许可文件；`use-composed-ref` 依照包声明的 MIT 许可
附标准条款，并注明上游未提供独立版权文件。

本地浏览器验收使用合成协议，不访问模型和训练：

```bash
/opt/galatea-agent/bin/python tests/console_browser_fixture.py 18881
# 另一个终端
CONSOLE_TEST_URL=http://127.0.0.1:18881 node tests/test_console_browser.cjs
```

需要先安装 Playwright Chromium，或用 `CHROMIUM_EXECUTABLE` 指向已有 Chromium。
可设 `CONSOLE_SCREENSHOTS` 保存欢迎页、聊天、深色和手机截图。
测试覆盖增量/最终消息去重、工具回执、Markdown/XSS、代码复制、丢失回执重试、
会话控制、刷新、epoch 重置、事件筛选、450 条消息翻页、滚动和移动布局。

2026-10-15 验收：前端 5 项、Console 8 项、观察存储 24 项、SSE 传输 2 项测试通过，
上述 Playwright 浏览器流程通过。已更新本机 `127.0.0.1:18880` 的三个静态文件，
发布校验通过，前台 PID `204962` 保持运行。真实网页已验证原生 Codex 完成回复、
`galatea_get_capabilities` 成功回执、工具卡片、Markdown 与刷新恢复；没有浏览器脚本或 CSP 错误。
验收日志、静态文件备份保存在 `/var/lib/galatea-agent/debug-evidence/assistant-ui-20261015/`，
静态变更及继承证据说明保存在 `/var/lib/galatea-agent/release/evidence/assistant-ui-20261015.json`。
能力工具返回的平台全局清单不代表会话授权；页面能力面板以 Host 当前挂载目录为准。

## 当前能力边界

界面采用类似 ChatGPT 的聊天交互，但只开放 Host 实际支持的能力。
模型由服务配置决定，当前页面没有模型切换、文件上传、语音或图片生成接口。
审批、权限与提问请求只能查看，不能在页面答复；Skill 尚未接入。
训练是否可用以 Host 工具目录和执行范围为准，页面重构不会开启训练权限。

只更新静态页面即可由当前前台进程读取，无需重启清空历史。
安装产物纳入 Host 软件摘要，部署时必须保留原证据、记录静态文件变更并重新核对发布摘要，
避免下次启动因摘要不匹配失败。前台启动方式和网络绑定保持原配置。

## 官方组件复用（2026-10-15）

聊天区改为 assistant-ui 官方 `Thread`、`MarkdownText`、`ToolGroup`、
`ToolFallback`、`ThreadList` 和 `ThreadListSidebar` 组件；其依赖的
Radix/shadcn 侧栏、按钮、提示、弹层、Tabs 和菜单也直接复用。
采用官方 Tailwind 灰阶主题，替换原有自定义聊天 CSS。官方组件以源码方式分发，
固定上游提交与本地适配清单见 [frontend/UPSTREAM.md](../frontend/UPSTREAM.md)。

保留现有 Codex 后端适配、SSE、固定水位历史分页、能力与原生事件视图。
新增官方会话重命名（浏览器本地标题）与回复 Markdown 导出。
附件、编辑、重新生成、归档和删除按 Host 实际能力隐藏；关闭会话仍是独立操作。
工具结果未知时显示“已观察到调用（结果未知）”，不推断成功。

构建加入 Tailwind/PostCSS；运行时仍由 Python 提供三个静态文件，无 Node 服务。
官方弹层滚动锁通过构建时兼容处理使用 CSSStyleSheet，保留现有 CSP。
源码来源、修改边界和浏览器要求均记录在组件来源文档中。

本次验证时，18880 端口已由 `start.sh` 的源码开发实例提供（`serve --dev`）；
返回的 `index.html`、`app.js`、`style.css` 与当前构建逐字节一致。
该模式直接读取源码静态文件，构建即生效。本次未修改 `/opt/galatea-agent` 的
已安装发布包或旧 stage-0 记录，先前 PID 与安装包部署记录仅为历史验收。

本次组件复用验证：TypeScript 检查与生产构建通过；前端 5 项、Console 8 项、
SSE 传输 2 项测试通过。扩展后的浏览器验收覆盖官方组件、搜索与重命名持久化、
Markdown 复制与导出、能力开关、手机侧栏、焦点与滚动锁、CSP 和既有聊天流程。
当前 18880 页面另完成真实 Codex 回复、只读能力工具成功回执、工具展开、刷新恢复
与能力面板检查，未触发训练。记录保存在
`/var/lib/galatea-agent/debug-evidence/assistant-ui-components-20261015/`。

## 会话完整性与展示


会话 UI 使用原生 `turn` / `item` 标识和 `agentMessage.phase`。`commentary`、公开思考摘要、
命令、文件修改和工具调用按连续执行段归入可展开的处理过程；`final_answer` 保持独立，
仅最终文本回复提供复制/导出操作。执行中的补充输入独立成行，不以相同 `turn_id` 或
Host 请求 ID 覆盖初始输入。没有 phase 的旧记录只在轮次成功完成后推断最后一条文本为最终回复。

命令展示命令行、工作目录、输出、退出码和原生耗时；文件修改展示 diff；计划通知展示
步骤状态。失败和未收到回执分开处理。只展示原生公开思考摘要，不展示私有推理正文；
历史恢复不把恢复时间误当成思考完成时间。

侧栏按工作目录分组，顶部提供文件与终端入口。Markdown 中的绝对路径通过原生
`fs/readFile` 打开只读预览，支持文本和 PNG/JPEG/WebP/GIF；`:行号` / `#L行号` 从请求路径
剥离并显示在预览标题。远程 Markdown 图片不自动加载，HTML 不执行。

历史分页读取 `snapshot?view=all`，累计消息、步骤和轮次。每次实时刷新均从同一 watermark
重建已加载页，避免混用历史与实时状态；浏览历史不禁用输入框。SQLite 的原始审计批次
保持不变，启动时按当前投影规则重新生成派生视图，因此既有会话也能恢复补充输入和 phase。

## 验证与兼容范围

验证包括 `frontend` 下的 `npm test` / `npm run build`、Python 会话投影测试和浏览器脚本：

- `tests/test_console_browser.cjs`：历史分页、流式摘要、Markdown、复制、主题、移动端。
- `tests/test_chat_interaction.cjs`：模型/计划设置、审批隔离、补充输入和快捷命令。
- `tests/test_native_browser.cjs`：原生审批、MCP 表单、文件、终端及图片输入。
- `tests/test_conversation_parity.cjs`：过程折叠、命令失败、计划、diff、文件预览和刷新回放。

浏览器脚本使用 `console_browser_fixture.py` 的合成服务，不触发模型或训练。设置
`CONSOLE_TEST_URL` 和 `CHROMIUM_EXECUTABLE` 后运行；`CONSOLE_SCREENSHOTS` 可保存截图。

这是基于 Codex 0.154.0 原生协议的 Web 会话实现，不是桌面应用 UI 源码的复制。
已覆盖上述会话流程；桌面窗口面板、交互式终端仿真、富文档/视频/音频/Artifact
渲染、完整子 Agent 导航和异步问题消息仍不等同于桌面功能。文件当前以文本/图片预览，
Markdown 文件不会成为可编辑的富文档。尚未进行与指定桌面版本截图逐像素比对。
