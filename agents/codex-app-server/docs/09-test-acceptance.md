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
node ../tests/test_chat_interaction.cjs
node ../tests/test_conversation_parity.cjs
node ../tests/test_workbench_browser.cjs
```

已有 Chromium 时可设置 `CHROMIUM_EXECUTABLE=/absolute/path/chrome`。测试仅操作模拟会话，不连接运行中的 Agent 服务。

`test_workbench_browser.cjs` 覆盖任务面板开关、草稿保留、Diff 轮次隔离、计划、目录浏览、
文件预览、底部命令执行与面板状态保留，以及深色和手机布局。
设置 `CONSOLE_SCREENSHOTS=/absolute/output/path` 可保存浏览器验收截图。

Python 回归覆盖配置拒绝、无扩展 Host、可配置 namespace、Provider 加载与清理、参数/操作 allowlist、回执去重、未知操作恢复、进程恢复、权限请求及 Console SQLite/SSE。

真实原生运行时测试需显式指定包目录（必须包含 bin/codex 与 codex-package.json）：

```sh
CODEX_TEST_RUNTIME_PACKAGE=/absolute/path/runtime \
  python -m unittest discover -s tests -p 'test_*.py'
```

历史协议用例同时校验包文件摘要和执行位；版本相同而文件内容或执行位不一致也会拒绝，不能据此绕过兼容性检查。

原生测试以本地模拟模型和 stdio MCP echo 验证：外置 Skill 发现与加载、Agent 工具调用和结果消费、Shell 审批、用户提问、Dynamic Tools 成功/失败与跨进程恢复。不访问付费模型或训练平台。

独立协议探针也必须显式选择扩展契约与只读工具。以下为已冻结兼容性描述的历史协议样本；`examples/echo` 尚未生成正式兼容性描述，使用上面的源码集成测试验证：

```sh
python scripts/runtime_probe.py --package /absolute/path/runtime \
  --output /absolute/path/probe-output --contracts tests/fixtures/dynamic-tools \
  --probe-tool galatea_get_capabilities --probe-arguments '{"protocol_version":"galatea.tools/v1"}'
```

`tests/fixtures/dynamic-tools` 和 `codex-*` 为历史真实 wire 回归样本；其中业务名称仅属于测试数据。默认运行时无此 catalog。`examples/echo` 为新的独立通用扩展示例。

此范围不等于真实训练业务联动、外部 MCP 认证、Apps、插件或生产部署已验收。接入具体业务后需要另行验证该服务的权限、幂等、超时恢复和业务可视化。
