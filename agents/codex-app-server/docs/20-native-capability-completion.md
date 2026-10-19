# 原生能力补齐与验收（2026-10-17）

本次将[补齐前审计](19-native-capability-audit.md)识别的设置、账号、聊天资源、
高级参数和事件消费缺口接入正式界面及 Host。基线为仓库固定的 `codex-cli 0.163.0`，
不是对线上最新版本或闭源 Desktop 私有服务的支持声明。

协议包由实际固定二进制导出，包含 experimental API；完整配置 Schema 来自本地 upstream
`e89b7c4`，保留来源、摘要以及权限 flatten 类型的源码补表。
当前原生版本明确拒绝的能力和外部提供方条件见下文，不能把协议可达当作服务已验收。

## 全量范围与可复核清单

| 对象 | 数量 | 本次状态与证据 |
| --- | ---: | --- |
| 客户端方法 | 172，含 initialize | [逐方法能力矩阵](native-capabilities/methods.csv)；171 个由 Host 提供，内部编排、替代流程和提供方不可用项分别标明 |
| 请求顶层参数 | 545 条，含互斥分支 | [逐参数清单](native-capabilities/parameters.csv)及[固定协议包](../src/codex_agent/protocol/0.163.0.json)；高级表单保留原生 required、联合类型与嵌套字段，Host 身份和工具注册字段保持由 Host 管理 |
| 原生设置 | 103 个顶层域 | [逐项设置矩阵](native-capabilities/settings.csv)；101 项提供用户层编辑，2 项旧 profile 只读及删除 |
| 反向请求 | 11 | [处理矩阵](native-capabilities/server-requests.csv)；审批、用户问题、elicitation、工具和时间回调，以及两个明确不支持的平台回调 |
| 服务端通知 | 85 | [通知矩阵](native-capabilities/notifications.csv)；全部进入观察链路，功能事件另由工作区、投影、状态刷新或通知中心消费；内部事件不逐项创建界面动作 |
| 会话条目 / 用户输入 | 19 / 7 种 | 原生协议、媒体、工具和消息白名单；私有推理与内部 Hook 提示不会作为公开回答显示 |
| 运行时功能开关 | 175 | 原有 `experimentalFeature/list` 分页枚举与 enablement 页面；结构化 features 配置另由完整设置表单管理 |

CSV 中的源码位置是实现索引，不是端到端覆盖率。无需为历史分页、内部订阅、
测试或替代接口逐个创建操作按钮。完整协议、配置结构和 CSV 均随源码保留，
不依赖被 Git 忽略的历史审计目录。

重建清单：

```sh
python scripts/export_config_schema.py --source /absolute/path/to/codex
python scripts/export_capability_matrix.py
```

升级时必须同步实际二进制协议与 upstream 配置版本，并重新执行验收。

## 已补齐的产品流程

| 入口 | 补齐内容 |
| --- | --- |
| 设置 → 配置 → 全部原生设置 | 103 域搜索与分类，嵌套对象、数组、联合类型、动态键；命名权限继承、filesystem/network、工作区信任；服务商全部字段、Agent/目标/Memory、Hooks、Apps/工具/插件/Skills/MCP、隐私与遥测、浏览器/电脑操作、语音及宿主配置 |
| 设置 → 账户与登录 | ChatGPT 浏览器/设备码、API Key、Bedrock 登录与发现、GovCloud 检查；取消/退出；Gateway OAuth 登录状态、跳转与取消；原生额度、工作区消息、显式 credits 操作和额度提醒邮件 |
| 聊天 `/resources` | 持久 attachment 增删查与 owner 查询/分页、打开关联聊天；带 expectedRevision 的已读/未读同步、预测请求；原生聊天 Shell 命令及权限确认 |
| 聊天 `/advanced` | start/fork/resume、聊天/轮次设置、start/steer 完整原生参数；结构化输出、环境、上下文、metadata、多模态细节与禁用插件 |
| 设置 → 高级原生能力 | 插件共享对象更新；rollout 异步存储压缩 |
| 审查选择器 | 保留四种 CLI 审查目标，新增 inline/detached 位置；独立审查聊天绑定到 Host 并跟踪运行状态 |
| 底部终端 → 高级执行设置 | 原生 env、超时、输出上限、TTY、sandboxPolicy 等；无 TTY 时不发送尺寸变更；高级环境变量不写入浏览器持久化 |
| 设置 → 语音；聊天 `/voice` 与语音按钮 | WebSocket/WebRTC、浏览器输入/输出设备、原生高级启动参数；SDP 协商、媒体通道、静音、结束和设备清理 |
| 通知与消息流 | 配置/弃用/Guardian/严格审查、模型路由/验证/认证恢复、导入进度等提示；已读/项目/附件/预测/环境、MCP 授权和额度状态同步；MCP 工具进度、文件补丁更新、轮次 Diff、sleep 和审查条目投影 |

`turn/moderationMetadata` 是诊断元数据，沿用 CLI 行为保留在原始事件中，不创建顶部提示或聊天警告。
前端同时过滤旧 Host 已存入历史的同类警告投影。

配置只提交修改的叶字段，保留未触及的隐藏凭据。带点、引号、反斜线的动态键按原生
keyPath 语法引用；数组替换若包含隐藏凭据，要求重新输入，避免写回脱敏占位符。
草稿首次修改时固定用户层内容与版本，后台刷新不能悄悄把旧草稿升级到新版本；
原生版本冲突保留草稿，用户明确放弃后才重新读取基线。

Host 对聊天 Shell 和 detached review 同样执行并发接纳与不确定响应恢复，处理
完成事件先于响应、读取历史时完成等竞争。原生 readState 从 `thread/read.readState`
和 `thread/list.readStates` 读取，不误当作 Thread 对象字段。

高级终端刷新通过只读 Host 回执和事件历史恢复原进程，不能因浏览器没有保存 env
而用不完整参数重新启动。Gateway 跳转链接保存在有期限的内存交接中，完成/取消后清除；
观察记录持续脱敏，接口禁止缓存。登录、credits、提醒邮件均由用户显式操作触发。

## 当前原生边界

- **配置集存在上游接口缺口。** 0.163 配置 Schema 仍包含 `profile/profiles`，
  但原生写入接口拒绝创建和修改，仅允许删除旧值。CLI 已转为
  `--profile <name>` 与 `<name>.config.toml`；本版本 `codex app-server` 启动命令不接受
  `--profile`。页面展示这个限制，不用旧配置表或测试环境变量伪造配置集切换。
- **提供方条件保留。** 当前构建的 5 个 userVerification 方法返回 `providerUnavailable`；
  `account/chatgptAuthTokens/refresh` 与 `attestation/generate` 缺少平台提供方，返回
  `-32601`。内部 `chatgptAuthTokens` 登录分支不作为普通用户的凭据表单。
- **真实外部服务尚需对应环境。** ChatGPT/Bedrock/Gateway 认证、credits/提醒邮件、
  远程配对、共享插件、Apps OAuth、实时模型语音和 Windows 能力已按原生接口接入；
  合成浏览器和本地协议验收不能证明这些远端服务或特定操作系统可用。
- **保存不等于全部即时生效。** 仅编辑可写用户层；系统、项目、托管层只读，
  组织约束由原生再次校验。宿主启动项需重启；TUI 配置影响 CLI；默认配置主要供新聊天继承。
  `desktop` 是原生不透明 JSON，编辑其值不代表实现闭源客户端行为。
- **遵守原生生命周期。** `dynamicTools` 由 ToolRegistry 注入；已绑定聊天不通过
  resume 的 path/history 替换身份；`multiAgentMode` 等已弃用字段不伪装成有效开关。
  detached review 是原生仍接受的弃用模式。rollout 压缩回执只表示已调度。
- `/aside` 不在该 CLI 命令实现中；`/share` 所需聊天快照服务不由此 app-server 协议提供。
  现有入口保留明确不可用说明，不创建虚构链接。

## 本次验证

- Python 全量：208 项，203 通过，5 个冻结在 0.154 的历史探针跳过。
  开启了实际 0.163 包与设置测试二进制，包含本地模拟模型/MCP 和隔离 HOME；未访问付费模型。
- 真实 0.163 新增验收：命名权限动态路径与继承、服务商隐藏字段保留、版本冲突、
  旧 profile 写入拒绝；聊天 Shell、attachment/owner CRUD、已读版本冲突、执行 env/输出上限/无 TTY。
- 前端单元测试 104 项通过，TypeScript 与静态构建通过。
- Chromium 10 套验收：原生补齐、设置、WebRTC、语音、终端恢复、审查选择器、native、workbench、MCP App 和 integrations。
  覆盖嵌套设置冲突、设备码/Gateway 取消、已读/附件、终端高级参数与刷新恢复、
  inline/detached 提交、设备清理及主要页面/布局。WebRTC 使用合成 PeerConnection，未宣称远端媒体通过。
  MCP App 用例在展开活动和调整视口后使用键盘激活，避免跨 iframe 点击与界面滚动竞争；
  同时覆盖桌面鼠标切换显示模式、作用域工具、消息、下载、保留表单和关闭重开。
- 历史 0.154 包与冻结摘要不符，显式启用时被兼容性校验拒绝；未修改或放宽该检查。

具体命令与环境变量见[测试验收](09-test-acceptance.md)。本次实现使用隔离 fixture，
没有重启或切换运行中的 18880 实例，也没有将源码验收写成线上部署完成。

设置与会话作用范围的逐项检查见 [控制台说明](05-console.md#设置与会话操作的作用范围2026-10-18)。
