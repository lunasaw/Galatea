# 前端实现

React 与 assistant-ui 负责通用对话、工具展开、消息与交互。Codex adapter 将 Host 会话、原生消息、工具和审批投影为界面状态，不实现独立 Agent Loop。

工具结果按原始内容展示；不识别业务 job 字段或固化训练步骤。Console 协议与本地存储前缀统一为 codex-agent，旧页面本地偏好不自动迁移。

`frontend/build.mjs` 将构建结果写入 Python 包的 console/static。修改前端后应完成 npm test、npm run build 和浏览器回归。第三方依赖与许可记录继续位于 frontend 目录。

## 首次加载与静态资源

构建采用 ESM 拆包，终端、设置、文件阅读、Markdown/公式与 MCP App 在实际使用时加载，
首屏依赖通过 `modulepreload` 并行下载。代码高亮仅注册支持的语言，完整许可单独保存在
`THIRD_PARTY_NOTICES.txt`。模型默认值与项目、历史恢复并行读取，未迁移项目时复用首次目录结果。

Host 对文本静态资源按 `Accept-Encoding` 使用 gzip；带内容摘要的 JS/CSS 缓存一年，
HTML 和固定文件名通过 ETag 校验更新。API 和会话流继续使用原有缓存策略。
构建保留已有摘要文件，以支持更新前已经打开的标签页继续读取旧版模块。

运行中的旧 Host 不支持拆包资源路由。首次升级应在当前轮次结束后重启 Host，并同步发布
完整静态目录；可在 `frontend` 使用 `CONSOLE_BUILD_DIR=/absolute/staging/path npm run build`
先生成独立构建，交给 `ConsoleApp(..., static_dir=...)` 的测试实例验收。

## 会话完整性与展示

会话 UI 使用原生 `turn` / `item` 标识和 `agentMessage.phase`。`commentary`、公开思考摘要、
命令、文件修改和工具调用按连续执行段归入可展开的处理过程；`final_answer` 保持独立，
仅最终文本回复提供复制/导出操作。执行中的补充输入独立成行，不以相同 `turn_id` 或
Host 请求 ID 覆盖初始输入。没有 phase 的旧记录只在轮次成功完成后推断最后一条文本为最终回复。

命令展示命令行、工作目录、输出、退出码和原生耗时；文件修改展示 diff；计划通知展示
步骤状态。失败和未收到回执分开处理。只展示原生公开思考摘要，不展示私有推理正文；
历史恢复不把恢复时间误当成思考完成时间。

“子 Agent”面板按当前主会话的 `ancestorThreadId` 读取后代列表，显示任务名、状态与任务摘要。
点击列表或主聊天的子 Agent 活动，在右侧读取该 Agent 的消息、命令输出、工具结果与公开摘要，
保留主聊天和输入草稿。详情使用 `thread/read(includeTurns=false)` 和
`thread/turns/list(itemsView=full)`，每 1.5 秒更新在途轮次，支持向前分页；不触发 resume。
请求失败保留已经显示的内容，切换 Agent 后丢弃旧请求的迟到结果。

主聊天和子 Agent 消息共用 KaTeX 渲染，支持 `$…$`、`$$…$$`、`\(...\)` 和 `\[...\]`。
代码与金额保持原样，长公式可横向滚动。字体随构建本地打包，通过 `/math-fonts/` 读取，
排版使用 CSSOM 样式，保持现有 CSP 且不开放可信 HTML。
部署本次变更需要重启 Host 载入字体路由；现有轮次结束后再重启。

侧栏保留项目分组 → 项目 → 聊天，并单独显示原生非项目会话分组，顶部提供文件与终端入口。对话 Markdown、结构化读取记录、文件树和
diff 中的文件入口统一通过原生 `fs/readFile` 打开右侧只读阅读面板；桌面不弹出模态窗口，
手机和平板使用全屏面板。支持多文件标签去重、切换、关闭、面板宽度调整、重读和失败重试。
切换文件保留已读内容、阅读位置与源码/预览模式；切换会话清除文件标签，迟到的读取结果
不会覆盖另一文件。打开阅读面板不重建对话和输入框。

链接支持绝对路径、相对于会话工作目录的路径、编码后的中文/空格路径；`:行号[:列号]`、
`#L行号` / `#L起始-L结束` 在源码中定位并高亮。Markdown 文件提供预览/源码切换，文件内部
的相对链接以所在目录解析；代码提供语法着色、行号和换行，图片支持 PNG/JPEG/WebP/GIF。
阅读面板保持只读；同一右侧“文件”分类另有文本编辑、复制、删除和变化监听。
Markdown HTML/MDX 不执行，远程图片不自动加载，网页链接在新标签打开。

原生 `commandActions` 中的 `read` / `search` / `listFiles` 显示为紧凑的读取、搜索、浏览记录，
读取记录的文件名可直接点击，点击不展开命令详情。未知或混合命令继续显示原始命令，
不通过分析 shell 文本臆造读取成功。命令、输出和真实完成状态仍可展开查看。

历史分页读取 `snapshot?view=all`，累计消息、步骤和轮次。每次实时刷新均从同一 watermark
重建已加载页，避免混用历史与实时状态；浏览历史不禁用输入框。SQLite 的原始审计批次
保持不变，启动时按当前投影规则重新生成派生视图，因此既有会话也能恢复补充输入和 phase。

## 验证与兼容范围

验证包括 `frontend` 下的 `npm test` / `npm run build`、Python 会话投影测试和浏览器脚本：

- `tests/test_console_browser.cjs`：历史分页、流式摘要、Markdown、复制、主题、移动端。
- `tests/test_chat_interaction.cjs`：模型/计划设置、审批隔离、补充输入和快捷命令。
- `tests/test_native_browser.cjs`：原生审批、MCP 表单、文件、终端及图片输入。
- `tests/test_subagents_browser.cjs`：子 Agent 详情、命令更新、主聊天和草稿保留、历史分页、
  请求失败恢复、快速切换、公式、CSP、本地字体及移动端。
- `frontend/src/markdown-math.browser.cjs`：独立验证公式分式布局、字体加载与 CSP。
- `tests/test_conversation_parity.cjs`：过程折叠、命令失败、计划、diff、文件预览和刷新回放。
- `tests/test_workbench_browser.cjs`：右侧面板、轮次 diff、目录、终端和草稿保留。
- `tests/test_desktop_conversation.cjs`：工具文件链接、多文件标签、行号定位、Markdown、复制、
  调整宽度、中文/相对路径、失败重试、读取竞态、会话隔离、深色及手机/平板阅读。

浏览器脚本使用 `console_browser_fixture.py` 的合成服务，不触发模型或训练。设置
`CONSOLE_TEST_URL` 和 `CHROMIUM_EXECUTABLE` 后运行；`CONSOLE_SCREENSHOTS` 可保存截图。

这是基于 Codex 0.154.0 原生协议的 Web 会话实现，不是桌面应用 UI 源码的复制。
本轮按用户提供的官方 Desktop 截图实现布局、输入菜单、目标/计划模式与推理滑块，
增加交互终端、原生语音、子 Agent 导航、异步问题和 MCP App 视图。
完整能力表与平台限制见 [Web 能力对齐](17-web-capability-parity.md)。
可编辑富文档、视频和通用 Artifact 编辑器不属于固定协议范围；未承诺与桌面逐像素一致。
