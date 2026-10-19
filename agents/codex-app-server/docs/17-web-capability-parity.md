# Web 能力对齐

2026-10-17。基线为实际 `codex-cli 0.163.0` 二进制生成的 experimental JSON Schema，
共 172 个客户端方法（含 Host 管理的 initialize），171 个方法由 Host 对外提供。
用户提供的官方 Desktop 截图是布局和交互依据；方法存在不等于账号、功能开关和平台条件已满足。
开发者工具仅用于诊断，不计为专用功能页面。

2026-10-17 完整补齐后的逐方法/设置清单和验收见
[原生能力补齐与验收](20-native-capability-completion.md)。历史验收记录保留当时部署状态。

## 功能位置与实现

| 位置 | 功能 | 原生能力 / 边界 |
| --- | --- | --- |
| 输入框 ＋ | 文件上传、Apps/插件/Skills 搜索列表、引用标签 | 图片、音频、文本文件；`UserInput.skill` 与 `mention`，引用随下一条消息发送，也支持运行中 steer |
| 输入框 `$` | 当前光标处搜索、选择 Skill；Enter/Tab 确认、Esc 关闭 | `skills/list` 按当前工作目录加载已启用 Skill；选中转为可移除的引用标签，复用 `UserInput.skill` 与 steer |
| 输入框 `/` | 按 Desktop 截图排列的中文命令、图标、描述、禁用原因；中英文筛选、方向键、Enter/Tab、Esc；MCP/分支/模型/推理子菜单 | MCP 从原生状态读取；模型和推理只使用运行时公布的选项；分支可选择原生轮次；归档沿用确认流程；运行中命令不进入 steer |
| `/usage`、`/feedback` | 账户额度、重置时间、余额及当前上下文占用；诊断与反馈表单 | `account/read`、`account/rateLimits/read`；不估算费用、不提供未经运行时验证的账单入口；仅点击上传时发送反馈 |
| `/init`、`/compact` | 创建仓库指南的普通 Agent 任务；原生上下文压缩 | `/init` 沿用 CLI 提示词流程，先检查已有 AGENTS.md，不覆盖；`thread/compact/start`；占用比例使用最近一次上下文 Token / 模型窗口 |
| 输入框模式 | 目标、计划模式互斥；推理强度滑块和模型选择 | `thread/goal/set`；`collaborationMode`；模型公布的 reasoning efforts，不用提示词模拟计划 |
| 输入框上方 | 目标内容、用量、预算、暂停/继续、清除 | `thread/goal/get|set|clear`；原生目标只接收文字 |
| 主输入框与上方队列条 | 主输入框添加；窄条预览、编辑、删除，更多菜单排序/立即发送；无独立扩展面板入口 | 运行中 Enter → `turn/steer`，Tab/队列按钮 → `thread/queue/add`，沿用原生 CLI 默认键位；`thread/queue/*` 管理持久队列，运行时在聊天空闲时自动发送 |
| 输入框上方 | 录音时长、取消、保存附件；实时静音、结束、文字和语音记录 | 原生 `audio` 输入、`thread/realtime/*`、`thread/timeline/list`；PCM 输入输出、WAV 附件 |
| 左侧放大镜 | 浮层聊天搜索、正文命中位置、聊天跳转；搜索文件快捷操作 | `thread/list|search|searchOccurrences`，`fuzzyFileSearch/session*`；文件打开右侧预览 |
| 左侧历史 | 全部、已归档聊天，恢复与归档等操作 | `thread/list|read|resume|archive|unarchive|delete`；分叉、回退沿用原生轮次身份 |
| 左侧项目 | 项目 CRUD、多个根目录及排序、分组、移动聊天 | `project/*`、`thread/metadata/update`、创建时 `projectId`；首个根目录为新聊天默认目录 |
| 左侧非项目分组 | 创建、重命名、删除及移动聊天 | `threadSection/*`、`thread/section/move`；与项目分组并存 |
| 右侧文件 | 目录/文件预览、UTF-8 编辑、复制、删除、监听 | `fs/*`；保留 BOM，二进制禁用文本编辑，保存前核对外部变化，删除显式确认 |
| 手动代码审查 | CLI 风格的四项选择器；基准分支、未提交更改、指定提交、自定义要求；键盘导航、搜索与逐级返回；当前/独立聊天 | `command/exec` 只读查询本地分支和最近 100 条提交；`review/start` inline/detached（后者原生已弃用） |
| 右侧审批 | 命令/文件/权限审批记录、Guardian 状态及批准一次重试 | 原生双向事件关联，`thread/approveGuardianDeniedAction`；响应不确定时保留请求身份 |
| 右侧 Agent | 当前聊天的子 Agent、后台终端查看/停止/清理 | `thread/list.ancestorThreadId`、`thread/backgroundTerminals/*` |
| 底部终端、设置同一分类 | 沙箱命令 / 主机进程标签，PTY、输入、尺寸、停止、多标签 | `command/exec*` / `process/*`；后者按原生语义不经过 Codex sandbox；恢复复用原请求和进程身份 |
| 设置：配置 | 常用默认字段与全部 103 个原生配置域；嵌套对象、命名权限继承/路径/网络、服务商全部字段、扩展策略与隐私；配置来源与文件预览 | `config/read`、`configRequirements/read`、`config/batchWrite`；用户层版本化叶字段写入与冲突草稿；2 个旧 profile 项仅查看/删除 |
| 设置：账户与登录 | ChatGPT/设备码/API Key/Bedrock、Gateway OAuth、取消/退出、额度与工作区消息、显式 credits/提醒邮件 | `account/*`；内部 token 分支不开放；真实账号服务需独立环境验收 |
| 设置：聊天资源、高级原生能力 | attachment/owner、已读版本、预测与聊天 Shell；高级创建/恢复/轮次/结构化输出/环境参数、插件共享更新和 rollout 压缩 | `thread/attachment*`、`thread/readState/update` 等；Host 管理身份和 dynamicTools，保留原生权限及接纳约束 |
| 设置：模型与权限 | 当前轮次 / 后续回复、服务等级、摘要、权限配置、约束、协作模式、实验功能、服务商能力 | `turn/settings/update`、`thread/settings/update`、`permissionProfile/list`、`configRequirements/read` 等；保留未知权限设置，隐藏原生已弃用的风格设置 |
| 设置：Memory、Hooks | 会话 Memory 模式、重置；Hooks 清单、来源和执行记录 | `thread/memoryMode/set`、`memory/reset`、`hooks/list`；不推导不存在的 Hooks CRUD |
| 设置：Apps、插件、Skills | Apps 详情/已安装，插件原生搜索/安装/同步/共享/市场，Skills 扩展根目录 | `app/list|read|installed`、`plugin/*`、`marketplace/*`、`skills/*`；扩展根在 Host 重启后保留 |
| 设置：MCP | 状态、授权、工具表单、资源读取、事件订阅/停止 | `mcpServer*`；嵌入视图使用官方 MCP Apps AppBridge、隔离 iframe、CSP、作用域工具和修改操作确认 |
| 设置：远程、环境、导入 | 开关/配对/客户端撤销，环境添加/状态/新聊天，发现/选择/导入/历史 | `remoteControl/*`、`environment/*`、`externalAgentConfig/*`；导入历史由原生流程记录，不人工伪造成功 |
| 设置：Windows、诊断 | Windows 安装/完成通知（仅 Windows 主机显示），原生诊断和反馈 | Windows 能力由原生主机平台决定；反馈上传必须用户点击 |
| 消息流 | 异步问题、回答关联、记忆引用、生成/查看图片、MCP App、子 Agent、Hook | 保留真实 `agentMessage` 字段；异步回答沿用 CLI 信封，运行中 steer / 空闲 turn start |

设置为独立的左侧分类、右侧内容页面。聊天功能不再混在设置分类中。
项目分组是 Web 展示组织，存储在同一 Host；项目和非项目会话分组由原生存储管理。
连接其他主机或其他 Codex Home 不会自动同步这些数据。
原生会话排序仅适用于指定分组；无分组会话按更新时间读取，其手工排列保留为当前浏览器的显示偏好。

## 明确边界

- 原生配置写入仅支持用户层；项目、系统和托管层展示值与来源，正式 Release 保持只读。版本冲突保留草稿，重新读取后再修改。
- 默认配置供新聊天继承；Host 不再强加固定批准策略和沙箱。原生重载只刷新运行时支持的配置（如 MCP/插件），不会把已有聊天的模型和权限全部切换。已有聊天继续使用会话设置接口。
- 账号与 attachment 已有独立页面；认证、credits 和邮件只在用户显式操作时调用。原生配置集切换在当前 app-server 有上游接口限制，见完整验收文档。
- `/aside` 不在该 CLI 命令实现中；`/share` 缺少 app-server 快照分享服务；入口展示明确禁用原因，不创建持久分支或虚构分享链接。
- `multiAgentMode` 在当前二进制中已弃用/忽略；页面不提供无效开关。只显示模型实际公布的推理强度。
- 外部 ChatGPT token 刷新与 `attestation/generate` 缺少平台提供方，Host 返回不支持，不弹出凭据 JSON 表单。
- 当前原生构建的用户验证接口返回 `providerUnavailable`；设置页和高级协议工具不展示用户验证操作。原生提供方可用后再按运行时能力开放。
- 远程配对、共享插件、Apps 授权、实时模型语音、Windows 安装依赖实际服务或平台。
  已实现界面与原生调用，浏览器模拟验收不能证明对应外部环境可用。
- 麦克风需要 HTTPS 或 localhost 以及浏览器许可。录音使用 WAV；实时对话支持原生 WebSocket/WebRTC，
  Web Host 负责浏览器设备选择、采集和播放。未用付费语音服务做实测。
- 目标及输入队列的自动续跑由原生运行时负责。Host 的显式发起并发限制不替代原生内部调度。
- 文件保存前比较原文防止已观察到的外部修改；原生 `fs/writeFile` 没有原子比较并交换，不能保证消除极短的并发写入窗口。
- 普通界面不为 initialize、测试方法、内部订阅和历史分页等编排接口各造一个操作按钮。
  当前协议没有的 Desktop 功能（跨主机交接、通用富文档编辑等）不伪造实现。

## 验证

源码测试与构建：`npm test`、`npm run build`；Python 测试见 [测试验收](09-test-acceptance.md)。
`test_capability_runtime.py` 使用隔离 HOME 和实际固定版本二进制验证项目多目录、会话分组、
暂停目标、队列编辑、文件读写/复制/监听/删除及 PTY 输入/调整大小/停止，无模型调用。
已有原生运行时用例使用本地模拟 Responses 服务验证审批、工具、恢复与会话生命周期。

浏览器测试使用合成 Host（fixture 对请求做固定协议 Schema 校验），不会修改真实项目或调用模型：

- `test_conversation_panels.cjs`：三类引用的标签及原生消息、审查目标、子 Agent 范围、历史入口。
- `test_project_roots_browser.cjs`：目录添加/排序/删除、刷新、保留未知元数据、新聊天默认目录。
- `test_composer_capabilities.cjs`：目标/计划模式、预算、队列 CRUD/顺序/启动、搜索/后台终端。
- `test_composer_queue_browser.cjs`：内嵌队列条、原生 Enter/Tab 分流、重试幂等、附件保留、切换/刷新和深色/手机布局。
- `test_skill_mentions_browser.cjs`：`$` 搜索、光标补全、键盘/鼠标、引用去重、原生发送/steer、输入法、失败恢复与手机布局。
- `test_slash_commands_browser.cjs`：截图命令顺序、中英文筛选、键盘/输入法、子菜单、当前聊天 MCP 状态、上下文窗口占用、用量/反馈、初始化/压缩/指定轮次分支/归档、运行中拦截及深浅色/手机布局。
- `test_file_editor_browser.cjs`：BOM、外部编辑冲突、草稿、保存、复制、监听清理和删除确认。
- `test_settings_browser.cjs`：配置保存/重置、版本冲突与草稿、来源与覆盖提示、隐藏字段保留、高级字段、文件预览、推理档位偏好、只读实例和手机/深色布局。
- `test_voice_browser.cjs`：合成麦克风 WAV、原生语音事件、播放、静音、结束、历史恢复和设备拒绝。
- `test_terminal_recovery.cjs`：刷新复用进程身份，输出回放、继续输入和停止。
- `test_mcp_app_browser.cjs`：资源/握手/工具结果、作用域与审批、消息/上下文桥、下载、显示模式、固定与折叠、手机布局、关闭重开。完整边界见 [MCP Apps Host](18-mcp-apps.md)。
- 既有 console、native、workbench、integrations 用例覆盖主流程、设置、主题、手机布局和安全边界。

依赖账号或平台的能力必须在相应环境另做实际服务验收；此处不将模拟结果写成外部服务已通过。

2026-10-16 本次验收：Python 174 项通过（包含真实固定版本运行时，无跳过），
前端 60 项通过，TypeScript/打包通过，12 套浏览器交互验收通过。
逐组读取会话另经真实二进制验证，避免仅凭 JSON Schema 判断运行时允许的参数组合。
18880 开发实例已保留状态备份并更新；实际页面加载、“＋”引用列表及模型设置页检查通过。
实测同时修复了 MCP 工具 Schema 中 `role` 属性触发脱敏异常的问题。

2026-10-16 斜杠菜单补充验收：前端 77 项通过，TypeScript/打包通过；
斜杠菜单、Skills 引用、输入法和输入框能力四套浏览器验收通过。
斜杠菜单另验证当前聊天的 MCP 查询参数和实时上下文占用（使用当前窗口 Token，而非累计计费用量）。
修复后实际 MCP 列表读取成功，健康检查与观察链路均为 ready。
该构建的用户验证服务返回 `providerUnavailable`，保留原生错误，不声明已可用。未提交代码。
