# 测试验收

安装 `python -m pip install -e '.[test]'` 后在项目目录运行：

```sh
python -m unittest discover -s tests -p 'test_*.py'
cd frontend
npm test
npm run build
```

浏览器测试使用独立的模拟 Host。另开一个终端，在项目目录执行 `python tests/console_browser_fixture.py 18881`；保持其运行，在 `frontend` 目录执行：

```sh
npx playwright install chromium
export CONSOLE_TEST_URL=http://127.0.0.1:18881
npm run test:browser
node ../tests/test_native_browser.cjs
node ../tests/test_settings_browser.cjs
node ../tests/test_native_completion_browser.cjs
node ../tests/test_subagents_browser.cjs
node --test src/markdown-math.browser.cjs
node ../tests/test_chat_interaction.cjs
node ../tests/test_activity_scroll_browser.cjs
node ../tests/test_conversation_parity.cjs
node ../tests/test_workbench_browser.cjs
node ../tests/test_sidebar_resize_browser.cjs
node ../tests/test_copy_reply_browser.cjs
node ../tests/test_thread_export_browser.cjs
node ../tests/test_progressive_loading_browser.cjs
node ../tests/test_immediate_send_browser.cjs
node ../tests/test_conversation_panels.cjs
node ../tests/test_review_picker_browser.cjs
node ../tests/test_moderation_notices_browser.cjs
node ../tests/test_composer_capabilities.cjs
node ../tests/test_composer_queue_browser.cjs
node ../tests/test_composer_ime.cjs
node ../tests/test_skill_mentions_browser.cjs
node ../tests/test_slash_commands_browser.cjs
node ../tests/test_project_roots_browser.cjs
node ../tests/test_file_editor_browser.cjs
node ../tests/test_terminal_recovery.cjs
node ../tests/test_voice_browser.cjs
node ../tests/test_webrtc_browser.cjs
node ../tests/test_mcp_app_browser.cjs
node ../tests/test_apps_launch_browser.cjs
```

已有 Chromium 时可设置 `CHROMIUM_EXECUTABLE=/absolute/path/chrome`。测试仅操作模拟会话，不连接运行中的 Agent 服务。

`test_moderation_notices_browser.cjs` 验证连续内容审核元数据不会堆积成顶部提示或历史聊天活动，
真实警告仍可展开与关闭，原始审核事件仍可查询，刷新后旧投影也不会重新出现。

`test_settings_browser.cjs` 验证默认配置的分层读取、用户层版本化保存、重置、冲突保留草稿、
项目覆盖与只读发布、高级字段和隐藏字段保留、文件预览、刷新及手机/深色布局。
`configuration-data.test.ts` 和 `store.test.ts` 验证最小配置补丁、推理档位显示、
项目默认值继承、显式会话覆盖和读取竞争；`test_host_core.py` 验证原生有效设置的持久化。

可用隔离 HOME 验证真实原生配置契约，无模型调用，不改用户配置：

```sh
CODEX_SETTINGS_TEST_BINARY=/absolute/path/codex \
  python -m unittest discover -s tests -p 'test_settings_runtime.py'
```

该用例验证配置层覆盖、版本冲突、项目层写入拒绝、字段删除、新聊天继承和显式会话设置通知。
还验证带点的命名权限和 filesystem 路径、服务商叶字段修改时保留凭据、analytics 设置以及旧 profile 写入拒绝。

同一二进制可验证无模型的聊天资源和高级执行契约：

```sh
CODEX_SETTINGS_TEST_BINARY=/absolute/path/codex \
  python -m unittest discover -s tests -p 'test_native_completion_runtime.py'
```

`test_native_completion.py` 验证聊天 Shell 接纳/不确定响应、独立审查及完成竞争、
readState 正确响应层级与持久化、完整配置 Schema、临时 OAuth 交接脱敏和只读回执恢复。
`test_native_completion_browser.cjs` 验证嵌套权限编辑、后台刷新后固定草稿版本与冲突、
设备码/Gateway 取消和聊天资源。`test_webrtc_browser.cjs` 使用合成 PeerConnection，
验证 offer/answer、高级上下文、静音、原生结束与设备清理；不等同于真实远端媒体验收。

`test_activity_scroll_browser.cjs` 验证展开处理过程后持续追加记录时自动滚动、向上翻阅时保留位置、
返回最新后恢复跟随，以及桌面/手机布局下输入框保持可点击、草稿保留和完成后重新展开。

`test_thread_export_browser.cjs` 验证会话复制子菜单、完整 Markdown 下载与剪贴板回退、深度链接、
450 条历史记录的分页导出、失败分页不下载与重试、草稿保留，以及键盘、深色和手机布局。

`test_review_picker_browser.cjs` 验证四种审查入口、分支和提交搜索、完整 SHA、键盘逐级返回、
inline/detached 提交、中文输入法保护、草稿保留、读取失败与空仓库、丢失回执后的幂等重试，以及深色和手机布局。
`review-targets.test.ts` 在临时 Git 仓库验证本地分支、分离 HEAD、最近 100 条提交与空仓库。

MCP Apps 验收覆盖消息/上下文桥、工具可见性和作用域、确认/拒绝、下载、全屏/画中画/右侧固定、
折叠时保留页面、手机布局、关闭重开。后端 `test_mcp_app_context.py` 验证持久化和原生 start/steer，
`test_mcp_app_runtime.py` 在上述运行时环境变量启用后验证真实模型请求中的非可信上下文。
`test_apps_launch_browser.cjs` 验证未选择聊天时直接打开应用、工具与资源使用同一绑定会话、
重复打开复用会话，以及明确返回 thread-not-found 时自动创建会话并重试；全程不切换当前聊天。
可为构建与 fixture 同时设置 `CONSOLE_BUILD_DIR=/absolute/test-build`，隔离测试产物。

`test_console.py` 覆盖静态 gzip 协商、ETag/304 更新、HEAD、摘要资源长期缓存与路径限制；
`store.test.ts` 覆盖会话列表、聊天记录、模型和项目目录的独立加载、辅助接口失败、
启动恢复与用户切换的竞争，以及启动目录结果复用。
`test_progressive_loading_browser.cjs` 通过挂起请求验证侧栏先于历史显示、聊天先于健康检查和项目目录显示，
并检查状态核对期间禁止发送、后台补齐不清空草稿、快速切换不串会话。
`test_immediate_send_browser.cjs` 挂起创建会话与发送请求，验证消息立即显示、输入框立即清空、
重复发送保护、服务端记录去重、失败消息保留与原请求重试，以及切换聊天不影响其他草稿。
`test_immediate_steer_browser.cjs` 在回答进行时挂起补充请求，验证 Enter 和发送按钮即时回显、
输入框清空、原生 clientUserMessageId 去重、失败重试身份保持，以及跨聊天草稿隔离。
`store.test.ts` 另验证首条消息不等待历史和目录加载、两种回执顺序、创建失败重试与切换时的模型归属。
首次加载性能使用浏览器新建上下文，在相同带宽和延迟下比较 FCP 与 JS 传输字节，
再用同一上下文重开验证版本缓存。拆包后应回归终端、文件预览、公式及 MCP 交互视图。

`test_workbench_browser.cjs` 覆盖任务面板开关、草稿保留、Diff 轮次隔离、计划、目录浏览、
文件预览、底部命令执行与面板状态保留，以及深色和手机布局。
设置 `CONSOLE_SCREENSHOTS=/absolute/output/path` 可保存浏览器验收截图。

`test_composer_queue_browser.cjs` 验证输入框上方的队列条、Enter/Tab 分流、输入法保护、
丢失回执后的幂等重试、保留附件的编辑、排序/删除、刷新恢复、切换会话时保留草稿，
以及深色和手机布局。队列消费沿用原生运行时；浏览器用例通过模拟运行状态验证交互。

`test_sidebar_resize_browser.cjs` 验证会话侧栏拖动、宽度限制、刷新与收拢后保留宽度、
双击重置、键盘调整、取消拖动、窗口缩放和手机抽屉。

`test_copy_reply_browser.cjs` 逐字比对长回答（包括最后一行和 Markdown），并验证流式完成、
刷新恢复、剪贴板 API 缺失或拒绝时的兼容复制、失败提示和重试。

Python 回归覆盖配置拒绝、无扩展 Host、可配置 namespace、Provider 加载与清理、参数/操作 allowlist、回执去重、未知操作恢复、进程恢复、权限请求及 Console SQLite/SSE。

真实原生运行时测试需显式指定包目录（必须包含 bin/codex 与 codex-package.json）：

```sh
CODEX_TEST_RUNTIME_PACKAGE=/absolute/path/runtime \
  python -m unittest discover -s tests -p 'test_*.py'
```

历史协议用例同时校验包文件摘要和执行位；版本相同而文件内容或执行位不一致也会拒绝，不能据此绕过兼容性检查。

原生测试以本地模拟模型和 stdio MCP echo 验证：外置 Skill 发现与加载、Agent 工具调用和结果消费、Shell 审批、用户提问、Dynamic Tools 成功/失败与跨进程恢复。不访问付费模型或训练平台。

`test_capability_runtime.py` 另以真实二进制验证原生项目、多目录、分组、暂停目标、队列管理、文件监听和 PTY。
队列会自动消费已加载的空闲聊天；该用例先卸载测试聊天，再编辑队列，确保不触发模型。
浏览器 fixture 验证每条原生请求的固定版本 Schema；依赖外部服务和平台的验收边界见
[Web 能力对齐](17-web-capability-parity.md)。

独立协议探针也必须显式选择扩展契约与只读工具。以下为已冻结兼容性描述的历史协议样本；`examples/echo` 尚未生成正式兼容性描述，使用上面的源码集成测试验证：

```sh
python scripts/runtime_probe.py --package /absolute/path/runtime \
  --output /absolute/path/probe-output --contracts tests/fixtures/dynamic-tools \
  --probe-tool galatea_get_capabilities --probe-arguments '{"protocol_version":"galatea.tools/v1"}'
```

`tests/fixtures/dynamic-tools` 和 `codex-*` 为历史真实 wire 回归样本；其中业务名称仅属于测试数据。默认运行时无此 catalog。`examples/echo` 为新的独立通用扩展示例。

此范围不等于真实训练业务联动、外部 MCP 认证、Apps、插件或生产部署已验收。接入具体业务后需要另行验证该服务的权限、幂等、超时恢复和业务可视化。

## 2026-10-17：0.163.0 升级验证

实际 0.163.0 二进制与本地模拟模型/MCP：194 项后端用例，189 通过、5 项历史探针跳过；
前端 91 项通过，TypeScript 与构建通过。Chromium 完整会话测试覆盖分叉、指定轮/上一轮回退、
继续对话、归档/取消归档、删除、项目移除和刷新。审批用例显式指定 on-request/read-only。

冻结在 0.154.0 的历史探针改用 CODEX_TEST_LEGACY_RUNTIME_PACKAGE；它验证原始包树和权限摘要，
不能用新版包代替。当前本机旧 npm 包的包树摘要与冻结证据不同，直接启用该历史探针会被拒绝；
本次未放宽这一校验，也未把它算作新版验收通过。历史 wire fixtures 的离线回归保持原样。

本机服务切换后，/health/ready 返回 ready=true，/api/native/protocol 与 /api/capabilities
均报告 codex-cli 0.163.0。17 条持久化会话记录完成受限迁移；12 条可见会话全部为 paginated，
逐条 thread/read 成功。模型设置、Codex Home、Host state 与工作目录保持原路径。
本地备份位于 .local/backups/runtime-0.163.0-20261017-172557/。

## 2026-10-17：原生能力补齐验证

在隔离 fixture 与实际 0.163 二进制上验收，未重启运行中的 18880 实例。
Python 208 项中 203 通过、5 项历史探针跳过；前端 104 项通过，TypeScript 与构建通过。
10 套新增及相关浏览器回归包括原生补齐、完整设置、终端恢复、WebRTC、语音、审查、native、workbench、MCP App 和 integrations。
逐项能力矩阵、配置集/外部提供方限制见[原生能力补齐与验收](20-native-capability-completion.md)。

## 2026-10-17：设置页面样式统一

全部分类沿用“配置”页的设置行、说明、控件对齐、分隔线和折叠入口。
高级能力、账号、聊天资源、Memory 以及 Schema 嵌套表单使用同一布局；
样式限定于设置弹窗，保留聊天工具及终端工作区的独立布局。

前端 106 项测试通过，TypeScript 和构建通过。配置、原生补齐、扩展管理、
终端恢复、native、MCP App 六套浏览器交互验收通过。
另逐页检查 24 个分类（Windows 分类使用模拟平台），桌面和 390px 手机视口均无横向溢出，
并检查高级表单展开、深色模式和键盘操作。静态资源已更新，无需重启 18880。

## 会话设置归位与上下文占用（2026-10-18）

`test_session_settings_browser.cjs` 逐页检查全局设置不再包含会话写入或当前聊天用量，
并覆盖所有迁移后的 `/` 面板、当前 threadId 写入、API Key 不请求 ChatGPT 额度、
已有草稿保留、会话切换、未知容量、原生占用下降后清除提醒、浅色/深色及 390px 手机布局。
语音高级上下文按聊天隔离，`test_webrtc_browser.cjs` 使用 `/voice` 设置后验证启动参数。

本次验证：108 项前端单元测试、TypeScript/构建通过；会话设置、快捷命令、配置保存、
原生能力补齐、扩展管理、Apps 打开、WebRTC、原生交互、会话任务面板浏览器用例通过。
浏览器用例仅连接隔离 fixture；运行中的 18880 实例未重启，静态 JS/CSS 与本地构建摘要一致。

## 配置页脱敏来源兼容（2026-10-18）

真实实例中，通用脱敏将 `origins.cli_auth_credentials_store` 从配置来源对象替换为字符串；
“全部原生设置”读取 `name.type` 时抛出异常，外层错误边界显示“内容加载失败”。
来源展示现已兼容被隐藏或缺失的元数据。后端配置读取保留合法来源元数据与凭据存储方式枚举，
真实密钥及私密指令继续脱敏；前端兼容尚未重启的旧后端响应。

8 项配置/Schema 前端用例、30 项后端观察与原生接口用例、TypeScript/构建及配置浏览器回归通过。
浏览器覆盖被隐藏的来源、保存/恢复默认、版本冲突、配置层切换、只读和移动布局；
18880 只读实测可打开配置并重新读取，无浏览器异常、无原生写入、未重启实例。
全量前端 109 项中 108 通过；现有 `workbench-data.test.ts` 的跨条目差异计数用例失败
（预期新增 2 行，实际 1 行），不属于此次配置修复范围。
修复构建成功并完成实测后，再次构建时工作区出现新的 `turn-file-changes.tsx` 引用，
但对应 `turn-file-changes.css` 尚不存在，导致该次打包失败；没有替换已验证的静态入口。
