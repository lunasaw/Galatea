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
node ../tests/test_subagents_browser.cjs
node --test src/markdown-math.browser.cjs
node ../tests/test_chat_interaction.cjs
node ../tests/test_conversation_parity.cjs
node ../tests/test_workbench_browser.cjs
node ../tests/test_sidebar_resize_browser.cjs
node ../tests/test_copy_reply_browser.cjs
node ../tests/test_progressive_loading_browser.cjs
node ../tests/test_conversation_panels.cjs
node ../tests/test_composer_capabilities.cjs
node ../tests/test_composer_queue_browser.cjs
node ../tests/test_composer_ime.cjs
node ../tests/test_skill_mentions_browser.cjs
node ../tests/test_slash_commands_browser.cjs
node ../tests/test_project_roots_browser.cjs
node ../tests/test_file_editor_browser.cjs
node ../tests/test_terminal_recovery.cjs
node ../tests/test_voice_browser.cjs
node ../tests/test_mcp_app_browser.cjs
```

已有 Chromium 时可设置 `CHROMIUM_EXECUTABLE=/absolute/path/chrome`。测试仅操作模拟会话，不连接运行中的 Agent 服务。

MCP Apps 验收覆盖消息/上下文桥、工具可见性和作用域、确认/拒绝、下载、全屏/画中画/右侧固定、
折叠时保留页面、手机布局、关闭重开。后端 `test_mcp_app_context.py` 验证持久化和原生 start/steer，
`test_mcp_app_runtime.py` 在上述运行时环境变量启用后验证真实模型请求中的非可信上下文。
可为构建与 fixture 同时设置 `CONSOLE_BUILD_DIR=/absolute/test-build`，隔离测试产物。

`test_console.py` 覆盖静态 gzip 协商、ETag/304 更新、HEAD、摘要资源长期缓存与路径限制；
`store.test.ts` 覆盖会话列表、聊天记录、模型和项目目录的独立加载、辅助接口失败、
启动恢复与用户切换的竞争，以及启动目录结果复用。
`test_progressive_loading_browser.cjs` 通过挂起请求验证侧栏先于历史显示、聊天先于健康检查和项目目录显示，
并检查状态核对期间禁止发送、后台补齐不清空草稿、快速切换不串会话。
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
