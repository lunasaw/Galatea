# Native Codex 网页工作台

2026-10-15：在现有聊天 Console 上加入原生 Codex 0.154.0 工作能力。
本页描述当前源码开发模式；13/14 文档中的只读、无交互、内存历史描述属于此前版本。

## 操作入口

2026-10-16：界面调整为 Codex / ChatGPT 的对话布局，无网页登录、账号菜单或 OAuth 登录按钮。
沿用服务端已有模型连接配置，打开网页即可对话。

左侧提供新建、搜索、恢复会话和 Skills；底部是工作目录、设置和主题。
主区保留对话和会话菜单，输入框内直接选择模型、推理强度及计划/执行模式。
工作目录、权限、MCP、Apps、插件、文件和终端从设置打开；执行流程、原生事件和
协议调试收进设置分类，不再占据聊天页顶部。
会话菜单提供分叉、压缩、代码审查、上下文回退和归档。回退上下文不会撤销磁盘上的代码修改。
运行中使用同一输入框补充指令（`turn/steer`），旁边保留停止按钮。
图片按钮将图片内容交给原生 `turn/start.input`。

审批、权限申请、用户提问和 MCP 表单放在所属会话的消息流内。
其他会话及未关联子 Agent 的请求通过右上角待回应提示打开，并显示来源。
支持单次/会话授权、运行时提供的规则授权选项、拒绝、取消、问题选项和自由回答。
页面不会自动批准操作；重复回应必须内容相同，过期请求不能提交到新进程。
来自子 Agent 的请求保留原生线程标识，不关联到不相干的聊天。

斜杠入口：`/new`、`/clear`（新聊天）、`/model`、`/permissions`、`/plan`、
`/skills`、`/mcp`、`/apps`、`/plugins`、`/status`、`/resume`、`/help`、
`/compact`、`/fork`、`/review`、`/undo`。Ctrl/Cmd+Shift+O 新建会话。
输入 `/` 弹出命令菜单，↑/↓ 选择、Enter 执行、Esc 收起；`/plan` 直接切换模式。
这些命令调用网页操作和原生协议，不模拟 CLI 终端键盘事件。

## 协议覆盖与边界

`src/codex_agent/protocol/0.154.0.json` 来自本机固定二进制的
`codex app-server generate-json-schema --experimental`，包含 159 个客户端请求类型。
初始化由 Host 管理；其余 158 个方法由 Host 协议桥接。
「设置 → 开发者工具」提供参数模板、定义和运行结果；网页按产品要求移除了账号及 OAuth 登录方法。
这覆盖目标/队列、审查、模型、配置、项目、文件系统、终端进程、账号、插件、MCP、
实时音频与远程控制等协议域。常用功能有专门表单；其余方法通过参数编辑器使用。

所有请求按实际版本的 JSON Schema 校验，原生错误会返回页面。协议中存在一个方法，
不代表账号、平台或功能开关已使其可用。Apps/OAuth、远程插件、实时音频、验证/证明等
依赖外部服务的功能没有用真实账号逐项验收；网页没有实现完整的音频通话 UI 或设备证明提供方。
外部认证令牌刷新和 attestation 请求提供高级回应/明确失败入口，不伪造凭据或证明。
因此这是完整协议可达与常用编码交互接入，不宣称与 CLI 每项终端交互完全等价。

Host 维护线程/轮次/业务工具边界，原生模型和执行逻辑继续由 app-server 负责。
新建线程不再发送 `environments: []`，因为空环境列表会移除本地 Shell/文件执行工具。
默认线程为 `workspace-write` / `on-request`；完全访问和不审批需要用户在页面明确选择。
Galatea Dynamic Tools 仍按原有 principal、项目、操作和 journal 授权，不因网页权限变化扩大。

原生通用方法可读写 Host 用户的文件、启动进程或修改账户设置。这是本地/可信内网的
单身份客户端，所有访问者共享 Host 身份；不能作为无认证公网服务。
正式 release 的配置编辑继续受不可变配置约束，开发模式允许通过设置修改并记录新绑定。
生产配置或工具面发生变化仍需重新接纳 release。

## 持久化

源码启动首次生成的配置使用 `console.storage.backend=sqlite` 和 `retention=persistent`。
数据库位于 `state_dir/console.sqlite3`，保存经过脱敏的事件和投影变更。
重启保持 epoch/游标并恢复消息、工具记录；分页令牌仍需在新进程重新获取。
旧的 `memory/process_lifetime` 配置继续支持。
原生会话恢复会从 Codex rollout 重建当前聊天投影；历史事件不被覆盖。

升级已有内存实例时，先在无活动轮次时执行：

```bash
.local/conda/bin/python scripts/migrate_console_history.py \
  --url http://127.0.0.1:18880 --output .local/state/console.sqlite3
```

再修改 `.local/agent.json` 的 storage 并重启。脚本拒绝覆盖已有数据库。
已有配置不在每次 `start.sh` 时自动重写，用户修改会保留。

## 验证

- Python 回归覆盖 Host 状态、幂等、跨线程请求、schema 校验、拒绝/取消、图片和 Skill 输入、历史顺序与 SQLite 重启恢复。
- 真实 Codex + 本地模拟 Responses 服务验证 Shell 审批、实际命令执行、用户提问、动态业务工具和跨进程恢复；不调用付费模型或训练。
- Playwright 验证审批按钮、问题选项、MCP 表单、设置、终端、文件、协议清单、斜杠入口、图片发送和手机布局；旧聊天验收继续通过。

新增测试：`tests/test_native_workspace.py`、`tests/test_native_runtime.py`、
`tests/test_native_browser.cjs`、`tests/test_chat_interaction.cjs`。浏览器使用 `tests/console_browser_fixture.py` 的模拟数据。

### 对话界面验收（2026-10-16）

- 15 项前端单测覆盖投影、重试、模型/计划参数绑定、设置失败回滚，以及切换会话刷新活动轮次。
- 三套 Playwright 验收覆盖原有聊天、原生交互、新布局、无登录入口、命令菜单、跨会话审批隔离、
  同输入框补充指令、刷新保留模型/模式、深浅主题及 390px 手机布局。
- `test_native_workspace` / `test_console` 14 项后端测试通过；`test_native_runtime` 使用真实二进制和
  本地模拟模型服务，验证审批、Shell 执行和用户提问，不调用外部模型。
- 静态资源由现有服务逐次读取，构建后刷新即可生效，无需重启或迁移会话。
