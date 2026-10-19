# Codex 原生能力全量复核（2026-10-17）

> 本文保留补齐前的审计快照；其中“当前缺失”描述当时状态。
> 后续实现、逐项能力矩阵与原生限制以[原生能力补齐与验收](20-native-capability-completion.md)为准。

当前 open-codex 已接通大部分原生主流程，但还不能称为“所有原生能力完整支持”。主要差距是设置的字段覆盖、账号认证、会话资源关联与已读同步，以及部分事件和高级参数的产品流程。通用协议工具可调用某个方法，只证明协议可达，不能等同于普通用户能完成对应任务。

这次把历史对齐文档明确排除的账号能力、0.163 新增的 attachment 能力也纳入清单；内部编排、已弃用接口、平台条件和外部服务验收分别标注，不把所有 RPC 都转换成按钮需求。

## 基线与完整检查范围

- 原生基线：实际固定版本 `codex-cli 0.163.0`；SHA-256 `399ff69c431c066e40de318fba45c2b07f45a76b19939d247ee4828fc70c4215`。直接从该二进制生成包含 experimental API 的 JSON Schema，已验证仓库协议包的 client/server/notifications 三部分一致。
- open-codex 基线：`galatea` 提交 `63bc7ed` 加工作区已有修改。源码快照与哈希清单记录到 2026-10-17 17:58（北京时间），包含 176 个文件。本次工作仅增加审计材料，没有修改应用实现。
- 检查期间其他工作已将 18880 实例从 0.154 升级到 0.163；17:30 的只读检查为 ready、171 个对外方法、Linux、配置可编辑。新配置页已经存在，不能继续列为“没有配置页”。
- 完整设置结构来自本地 upstream `codex` 提交 `e89b7c4` 的 `codex-rs/core/config.schema.json`；它是源码设置结构，不冒充二进制导出的完整设置 Schema。运行时开关单独用真实二进制的 `experimentalFeature/list` 核对。
- 官方文档在线访问本次未成功，因此不声称这份报告代表所有线上最新版本或闭源 Desktop 的私有能力；结论对应上述可验证版本和工作区。

| 检查对象 | 全量范围 | 检查方式 |
| --- | ---: | --- |
| 客户端请求 | 172 个，含 initialize | 每个方法单独归类，追踪专用前端/Host 路由与限制 |
| 请求顶层参数 | 545 条，包含互斥参数分支 | 保留原生 Schema、分支与 required；不是 545 个独立产品功能 |
| 服务端反向请求 | 11 个 | 审批、交互、工具、自动响应及明确不支持的回调 |
| 服务端通知 | 85 个 | 区分专用消费、统一流消费、原始观察和缺失提示 |
| 客户端通知 | 1 个 initialized | Host 握手编排 |
| 会话条目 / 用户输入类型 | 19 / 7 种 | 对照投影、聊天白名单、媒体和工具卡片 |
| 顶层设置 | 103 项 | 逐项状态见本文末尾表格 |
| 设置路径节点 | 1,725 条 | 含容器、数组、联合类型、动态键和权限源码补表；不能当作独立开关数 |
| 运行时功能开关 | 175 个 | 全部分页枚举，对照界面切换入口和阶段 |
| 源码 features 配置键 | 185 个 | 单独核对别名、已移除项及结构化参数，不与 175 混算 |

方法归类为：117 个已有功能入口、17 个部分接入、9 个缺少专用产品流程、13 个账号能力未产品化、11 个内部/替代流程/无需独立按钮、5 个原生提供方不可用。这个分组是静态追踪与只读验证的结果，不能计算成端到端覆盖率。

相较仓库保留的 0.154 协议，0.163 新增 14 个方法（Gateway OAuth 三项、Bedrock GovCloud 检查、Memory 状态、rollout 压缩、四个 attachment/owner 方法、按 item 读取、预测、已读状态、验证取消），移除 thread/rollback；thread/revert 原本已经存在。完整增删列表保存在 inventory-summary.json。

## 已有能力，避免重复列缺口

| 能力域 | 当前已有内容 | 仍需注意的边界 |
| --- | --- | --- |
| 会话与项目 | 创建、恢复、历史、搜索/命中、归档/恢复、删除、命名、分叉、回退、项目多根目录、分组和排序 | 高级创建/恢复参数、跨连接状态同步见后文 |
| 执行与输入 | 原生 turn/steer/interrupt、持久队列 CRUD/顺序/立即发送、目标及预算/暂停、计划模式 | 目标全局上限和 Agent 调度配置未编辑 |
| 权限与审批 | 命令/文件/权限请求、用户问题、MCP elicitation、原生粒度选项、Guardian 记录与批准一次重试 | 命名权限定义和严格复核警告未完整产品化 |
| 文件与终端 | fs 读写/目录/复制/删除/监听、文件预览编辑、PTY 输入/resize/停止、后台终端 | 逐次 env/超时/输出限制等缺专用设置；不同执行接口语义不同 |
| 模型与默认配置 | 模型、原生推理档位、tier、摘要、协作模式、默认权限、配置来源/覆盖关系 | 配置不是完整 Schema 编辑器，见设置差距 |
| Skills/MCP/Apps | Skills 启停/目录/引用、MCP 状态/OAuth/工具/资源/事件、原生 Apps 目录/详情、MCP Apps 嵌入交互 | 扩展策略设置不足；真实外部服务按环境验收 |
| 插件和市场 | 搜索、安装/卸载、同步、共享保存/列表/检出/删除、市场添加/升级/移除 | 默认/逐插件策略和独立更新分享对象流程见后文 |
| Memory/Hooks | 会话记忆启停/全局 reset、Hooks 清单/来源/信任状态/执行记录 | 全局记忆策略、状态和 Hooks 设置缺编辑 |
| 语音与多模态 | 图片/音频输入、本地媒体历史、原生实时 websocket、PCM 播放、语音时间线 | WebRTC、设备选择和高级实时参数未补齐 |
| 宿主和运维 | 远程配对/撤销、环境接入、外部配置导入/历史、诊断/手动反馈、Windows 条件入口 | 账号、平台、远端和模型语音没有全量真实服务验收 |

## 设置差距：优先处理的部分

103 个顶层项中，16 项已有专用默认字段编辑器；8 项部分支持；5 项已有查看/相关功能但缺全局设置；57 项缺少专用设置；其余 17 项分别是启动/宿主级、CLI/TUI、兼容/弃用或客户端私有配置。这个分类描述界面覆盖，不表示 57 项全部都应成为普通用户开关，也不表示 16 项所有原生约束已实测。

| 优先级 | 配置域 | 已有 | 尚未补齐 |
| --- | --- | --- | --- |
| P0 | 命名配置集 `profile/profiles` | 配置层和当前工作目录继承 | 创建、编辑、选择配置集；不能把 `permissionProfile/list` 当作 profiles 支持 |
| P0 | 权限 `permissions/default_permissions` | 命名权限列表、选择及默认 ID；workspace-write 四个默认字段 | 命名权限定义/继承、工作区根、filesystem 路径规则、network 域名/代理/Unix socket 等编辑；当前会话高级 sandboxPolicy 也无完整编辑 |
| P0 | 服务商 `model_providers` | 21 个源码字段中的 5 个：name/base_url/env_key/wire_api/requires_openai_auth | auth 命令、AWS SigV4、gateway_oauth、capabilities、headers/env headers、query、catalog、重试/流超时/WebSocket 等；“使用 OpenAI 登录认证”开关也没有配套登录流程 |
| P0 | 隐私与遥测 | 手动 `feedback/upload` | analytics.enabled、feedback.enabled、otel 的 exporter/endpoint/提示词记录等；上传反馈按钮不等同于隐私设置 |
| P0 | 配置与能力描述准确性 | schema/requirements 可读取；新源码已隐藏 deprecated personality | 两个网页搜索控件均缺 `indexed`；服务商能力将未返回的 namespaceTools 显示成“不支持”，应表达未公布/未知 |
| P1 | Agent 与目标 `agents/goals` | 子 Agent 活动、目标及单目标 tokenBudget | enabled、并发上限、深度、默认子模型/推理、interrupt_message、角色文件/描述/昵称；全局 max_goal_token_budget |
| P1 | Memory `memories` | 会话 enabled/disabled、全局 reset | 14 个源码字段：抽取/归并模型、生成/使用、保留/过滤/速率限制、external context、版本等；还未读取 `memory/status` 的 v2Ready/v2ConsolidatedThreads |
| P1 | Hooks `hooks` | 清单和执行记录可看 | 12 类事件及 state 的设置、启停/信任流程；应通过已有配置写入能力实现，不虚构不存在的 Hook CRUD RPC |
| P1 | Apps / 插件 / Skills | 安装、连接、启停或目录已有 | Apps 默认/app/link/tool 级权限、审批与危险操作策略；plugins 默认/逐插件策略、thread disabledPluginIds；skills bundled/include_instructions/max_context_tokens |
| P1 | MCP 与认证策略 | 服务配置 JSON 新建/替换、OAuth 和重载已有 | 读取已有配置后编辑/删除/继承及冲突处理的完整流程；enterprise managed auth、凭据存储、回调地址/端口、启动 grace；JSON 可输入不等于完全不支持字段 |
| P1 | 自动审查 `auto_review` | reviewer 选择、Guardian 卡片 | policy/extra_policy、模板、上下文限制、circuit_break_action；review_model 默认值 |
| P1 | Shell 和工具行为 | inherit/include_only/exclude/set；工具调用流程已有 | ignore_default_excludes、filters、experimental_use_profile、allow_login_shell；tools、tool_suggest、输出限制和后台终端最大超时 |
| P1 | 指令与项目上下文 | developer_instructions、model_instructions_file | compact_prompt、project_doc_max_bytes/fallback_filenames、project_root_markers、include_* 上下文开关、projects trust_level；项目 RPC CRUD 不等于 config projects 信任设置 |
| P2 | 实时语音 `realtime/audio/experimental_realtime_*` | 浏览器 version/voice/model/outputModality 与 websocket | 原生 transport/type/start/end 指令、prompt/backend URL、启动上下文及 handoff 参数；麦克风/扬声器/声道选择须区分浏览器设备和 Codex 主机设备 |
| P2 | 浏览器/计算机使用 | 原生功能开关可列出 | browser_use 默认及逐 origin/历史访问策略、computer_use 默认/逐应用/OS 限制；须按平台和提供方开放 |
| P2 | 宿主、历史与其他 | 有诊断页和配置文件只读预览 | history persistence/max_bytes、ghost_snapshot、压缩其他阈值、cloud/orchestrator 等；sqlite/log/thread store/卸载时延属于宿主或启动设置 |

实验开关列表本身已经接入：175 个中 stable 54、beta 4、underDevelopment 71、deprecated 4、removed 42，removed 在界面禁用。因此不能写“实验功能开关大批未接入”。实际差距在结构化参数，例如 async_compaction、code_mode/code_mode_host、context_management、current_time_reminder、guardianv2、multi_agent_v2、network_proxy、non_prefixed_mcp_tool_names、rollout_budget、sleep_tool、token_budget；apps_mcp_path_override 也不是一个普通 bool。源码另有 tool_registry 的结构化配置。

源码 185 个 features 键和运行时 175 个功能名并不相同。源码包含兼容别名/已移除配置，二进制也有源码表未列出的名；不能把差值解释为漏掉 10 个开关。

## 设置以外尚未补齐的原生流程

### 9 个没有专用流程的方法

| 方法 | 具体差距 |
| --- | --- |
| `thread/attachment/add`、`list`、`remove`、`thread/attachmentOwner/list` | 持久化会话资源关联的增删、列表和反查；消息上传文件已有，两者不能合并统计 |
| `thread/readState/update` | 原生已读/未读与 expectedRevision 冲突处理、跨连接同步 |
| `thread/prediction/request` | 实验性预测请求及更新状态 |
| `memory/status` | 全局 Memory v2 就绪/归并进度可见性 |
| `rollout/compress` | 后台 rollout 存储压缩触发；不同于上下文 compact，RPC 响应也不保证压缩已完成 |
| `thread/shellCommand` | 在会话内运行用户 shell 命令并形成原生会话记录；不等于已有 command/exec 或 process/spawn，需呈现它自身的权限语义 |

### 账号及认证：13 个方法没有产品流程

`account/login/start`（API key、ChatGPT 浏览器/设备码及实验 Bedrock 分支）、`account/login/cancel`、`account/logout`；
`account/bedrock/discover|setup|checkGovCloudRequirements`；
`account/gatewayOAuth/read|login|cancel`；
`account/usage/read`、`account/workspaceMessages/read`、`account/rateLimitResetCredit/consume`、`account/sendAddCreditsNudgeEmail`。

目前只有 account/read 和 rateLimits/read 对应的只读账户/额度页面。历史文档选择“不新增账号管理”是产品范围边界，在本次全量对齐里仍应列为缺口。原生 `chatgptAuthTokens` 参数分支自带内部/不稳定限制，不能因此增加任意令牌导入表单。

### 已有入口但参数或语义不完整

- `thread/start/resume/fork`：普通生命周期已有；临时会话 ephemeral、daybreakEnabled、provider fallback、能力根/运行工作区根、高级指令覆盖、fork prediction/deferGoalContinuation 等缺少专用流程。Host 对绑定会话的 resume path/history 明确拒绝，dynamicTools 由 Host 注册表管理，属于所有权边界。
- `thread/settings/update`：已有当前会话模型、摘要、tier、协作、权限等；缺 disabledPluginIds 和完整高级 sandboxPolicy 编辑器。`turn/settings/update` 的原生字段已有正常路径。
- `turn/start/steer`：普通输入、音频、Skill/mention 和 MCP App 的 additionalContext 已用；outputSchema、toolOutput、运行根、插件禁用、部分 service tier/metadata 等高级编排没有完整专用界面。545 条参数明细包含互斥分支，避免只查方法名漏掉参数。
- `review/start`：四种 target 已有；Host 明确拒绝 detached delivery，只有 inline。另起聊天再做 inline 也不能宣称等同 native detached 语义。
- `thread/realtime/start`：websocket 已有；WebRTC/SDP、初始条目、start/end instructions、client managed handoffs、V3 handoff/channel 等未接入。
- `command/exec`、`process/spawn`：PTY 主流程已接入；逐次 env、超时、输出上限、command 的 permissionProfile 等无专用控件。
- `plugin/share/updateTargets`：现有 share/save + remotePluginId 能更新共享对象；缺的是不依赖本地检出的独立更新流程，不能把“插件共享”整体列为未实现。

`/aside` 的临时侧边会话生命周期未实现，可以基于 ephemeral 设计；`/share` 缺聊天快照分享服务，而当前 app-server 没有对应快照 RPC，应单列产品服务需求。

### 反向请求与事件

11 个反向请求中，7 个审批/交互回调进入 broker（含旧 applyPatchApproval/execCommandApproval），item/tool/call 进入 Host ToolRegistry，currentTime/read 自动响应。外部 `account/chatgptAuthTokens/refresh` 和 `attestation/generate` 返回 -32601，当前缺少外部凭据所有权或平台证明提供方；它们不能用人工 JSON 表单伪装完成。

85 个服务端事件都能进入原始观察链路，但下列 21 类尚无专用消费或用户展示：

| 类别 | 事件及差距 |
| --- | --- |
| 会话资源与已读 | thread/readState/changed、thread/attachment/updated、thread/prediction/updated |
| 内容变化 | item/fileChange/patchUpdated 没有对应增量投影；已有 outputDelta 和最终 fileChange 不等于流式 patch 更新 |
| 审查与风险 | autoApprovalReview/strictReviewRequired、guardianWarning、turn/moderationMetadata、model/safetyBuffering/updated |
| 模型与认证状态 | model/rerouted、model/verification、modelProvider/authRecoveryStarted、modelProvider/authRecoveryCompleted |
| 配置警告 | configWarning、deprecationNotice；不能只留在开发者观察器 |
| 跨连接变化 | project/changed、thread/project/updated、skills/changed |
| 账号变化 | account/updated、account/login/completed、account/gatewayOAuth/changed；account/updated 后还需按原生约定重读账户和 requirements |
| 平台警告 | windows/worldWritableWarning，仅在适用主机处理 |

另有一组事件只有原始观察或通用生命周期处理，完整清单在 server-notifications.csv。例如 thread/status/changed、thread/environment/connected/disconnected、turn/diff/updated、MCP progress 等，需要判断是否补专用状态、进度或 diff 展示；按需读取/轮询不能一概视为完全没实现。`thread/settings/updated` 已在这轮其他工作中补上 Host 状态同步，不再列为缺失。

19 种 ThreadItem 中，普通聊天的白名单过滤了 sleep、enteredReviewMode、exitedReviewMode，原生等待及审查进入/退出状态没有聊天提示。hookPrompt、functionCallOutput 也未作为普通聊天展示，但属于内部上下文/工具输出边界，不能直接暴露私有内容。7 种 UserInput 主要路径已有；image.fileId 分支、image detail 选择及 text_elements 的通用 span 编辑尚未产品化。

## 不应该直接当成缺失按钮的能力

- initialize/initialized、内部订阅与 elicitation 计数、loaded/items 细粒度读取、历史注入、测试方法、导入历史记录等，应按客户端编排需要使用；不要求每个接口一个按钮。已有 session 文件搜索也不必再做单次 fuzzyFileSearch 界面。
- 真实 0.163 的 userVerification/status 返回 providerUnavailable（当前构建或账号不可用），5 个相关方法单列条件能力；不可伪造验证成功。
- Windows sandbox、computer use、远程配对、Apps/OAuth、插件共享和实时模型语音受平台、账号、功能阶段和外部服务条件限制。Linux 不显示 Windows 设置是合理行为。
- personality 和 multiAgentMode 在当前原生版本已弃用；前者源码已按 Schema 隐藏，后者不应补无效开关。源码与已部署静态构建需要分别验收，不能仅凭源码断言运行页面已经更新。
- tui、file_opener、disable_paste_burst 等 CLI/TUI 配置应定义 Web 等价交互；不机械复制终端行为。desktop 是 opaque 配置，无法从开放 Schema 枚举闭源客户端私有键。
- 原生配置接口只写用户层；项目/系统/托管层只读和正式 Release 配置不可变属于已有边界。config reload 不保证把存量聊天的模型/权限全部切换，也不能在线应用所有宿主启动参数。

## 建议补齐顺序与验收标准

1. **先补设置基础与准确性**：扩充基于 Schema/requirements 的配置编辑；优先 profiles、命名权限定义、provider 完整配置及隐私项；修复 indexed/能力未知值的表达。保留现有版本冲突、来源、覆盖、删除继承和脱敏字段保护。
2. **补全高频设置域**：agents/goals、memories + memory/status、Hooks、Apps/插件/Skills/MCP 策略、Shell/自动审查/项目指令。每项明确默认配置、当前会话、当前轮次、浏览器偏好及宿主启动作用域。
3. **补会话一致性与可见性**：持久 attachment、readState、跨连接刷新、配置/认证/Guardian 警告、patch/diff 进度与 sleep/review 状态。
4. **补账号与提供方流程**：登录/取消/退出、Gateway OAuth、Bedrock 及受支持的用量扩展，按原生状态机和凭据所有权实现。
5. **再补条件与实验能力**：detached review、ephemeral/aside、高级 turn 参数、WebRTC/handoff、预测、rollout 存储维护和平台相关能力。

设置验收应覆盖读取有效值及来源、修改用户层、版本冲突、恢复继承、上层覆盖提示、脱敏字段保留、受 requirements 限制、刷新/重启持久性，以及“用于新聊天”和“立即影响当前会话”的区别。实验参数还需证明当前二进制实际接受且条件可用，不能只以表单存在验收。

## 本次验证与限制

完成了精确二进制 Schema 对照；隔离 HOME 的 10 个只读原生查询（config/read、requirements、permission profiles、全部 feature 分页、memory/status、hooks、协作模式、provider capabilities、diagnostics、verification），没有发起 turn；18880 的 ready/协议/观察状态读取；真实浏览器列出 20 个设置分类（另有返回聊天按钮）并只读打开配置、模型与权限、Memory、Hooks、语音、Apps、MCP、插件、Skills 九页，未出现页面脚本错误。

17:57 的最终只读复核确认运行时仍为 0.163.0、171 个方法、ready；交流风格控件已隐藏，两处搜索选项均缺 indexed；浏览器无页面脚本错误。观察器当时短暂积压 9 个事件且无错误，此项不表述成观察链路始终无延迟。

这不等于全部外部服务端到端验收。现有 fixture/测试可用于补齐时回归，本次没有将历史测试通过数量当成所有原生能力已验证。工作区在并行变化，source-manifest 和分时段浏览器记录用于区分源码与部署时间点。

## 全量清单和可复核证据

所有 CSV 均带具体方法/字段/状态，方法与事件清单附字面引用位置；字面引用只是定位线索，方法结论还需结合本报告的 Host 边界。完整二进制 Schema 保留嵌套结构，不仅靠方法名搜索。

- [方法总表](../../../../platform-data/agent-ui-parity/20261017-native-capability-audit/client-methods.csv) / [545 条参数与分支](../../../../platform-data/agent-ui-parity/20261017-native-capability-audit/client-parameters.csv)
- [反向请求](../../../../platform-data/agent-ui-parity/20261017-native-capability-audit/server-requests.csv) / [服务端通知](../../../../platform-data/agent-ui-parity/20261017-native-capability-audit/server-notifications.csv) / [客户端通知](../../../../platform-data/agent-ui-parity/20261017-native-capability-audit/client-notifications.csv)
- [19 种会话条目](../../../../platform-data/agent-ui-parity/20261017-native-capability-audit/thread-item-types.csv) / [7 种用户输入](../../../../platform-data/agent-ui-parity/20261017-native-capability-audit/user-input-types.csv)
- [103 个顶层设置](../../../../platform-data/agent-ui-parity/20261017-native-capability-audit/settings-top-level.csv) / [1,725 条配置路径](../../../../platform-data/agent-ui-parity/20261017-native-capability-audit/settings-fields.csv)
- [175 个运行时开关](../../../../platform-data/agent-ui-parity/20261017-native-capability-audit/runtime-features.csv) / [185 个源码 feature 配置键](../../../../platform-data/agent-ui-parity/20261017-native-capability-audit/source-feature-keys.csv)
- [二进制协议一致性](../../../../platform-data/agent-ui-parity/20261017-native-capability-audit/protocol-verification.json) / [原生只读探测](../../../../platform-data/agent-ui-parity/20261017-native-capability-audit/isolated-native-read-probes.json) / [运行实例](../../../../platform-data/agent-ui-parity/20261017-native-capability-audit/running-instance-snapshot.json) / [浏览器设置页](../../../../platform-data/agent-ui-parity/20261017-native-capability-audit/live-browser-settings.json) / [最终浏览器/实例复核](../../../../platform-data/agent-ui-parity/20261017-native-capability-audit/live-browser-final.json)
- [源码快照清单](../../../../platform-data/agent-ui-parity/20261017-native-capability-audit/source-manifest.json) / [统计](../../../../platform-data/agent-ui-parity/20261017-native-capability-audit/inventory-summary.json) / [可重建清单脚本](../../../../platform-data/agent-ui-parity/20261017-native-capability-audit/build_inventory.py) / [精确原生 Schema 目录](../../../../platform-data/agent-ui-parity/20261017-native-capability-audit/native-0.163.0-schema/)

原始证据保存在 Git 忽略的 platform-data 审计目录。本文和下面的 103 项清单保存在源码文档中；若需跨机器复核，应一并保存审计目录。

主要实现证据：配置编辑 [configuration-settings.tsx](../frontend/src/configuration-settings.tsx)、会话设置 [model-settings.tsx](../frontend/src/model-settings.tsx)、集成 [integrations.tsx](../frontend/src/integrations.tsx)、运行时页 [runtime-settings.tsx](../frontend/src/runtime-settings.tsx)、Host 边界 [native.py](../src/codex_agent/native.py)、会话状态 [host.py](../src/codex_agent/host.py)、内容投影 [projection.py](../src/codex_agent/console/projection.py)、聊天白名单 [messages.ts](../frontend/src/messages.ts)。

## 103 个顶层配置逐项状态

“有字段编辑器”不包含任意嵌套字段；“部分”请结合上文和字段 CSV。没有专用设置不表示原生拒绝通过 config 文件或通用写入接口配置。

| 配置键 | 状态 | 补充说明 |
| --- | --- | --- |
| `agents` | 缺少专用设置 | enabled、并发、深度、默认子 Agent 模型/推理、interrupt message、角色文件/描述/昵称均无设置流程；子 Agent 查看不等同配置 |
| `allow_login_shell` | 缺少专用设置 |  |
| `allow_symlinked_codex_home` | 启动/宿主级配置 | 非普通在线配置，不保证 reload 生效 |
| `analytics` | 缺少专用设置 | 没有 analytics.enabled 隐私开关 |
| `approval_policy` | 有字段编辑器 |  |
| `approvals_reviewer` | 有字段编辑器 |  |
| `apps` | 查看/关联已有，设置不全 | 目录/详情/连接链接已有；_default、app、link、tool 级启停/审批/危险操作配置缺编辑 |
| `apps_mcp_product_sku` | 缺少专用设置 |  |
| `audio` | 查看/关联已有，设置不全 | 无设备/声道选择；原生主机设备与浏览器设备需明确分开实现 |
| `auto_review` | 缺少专用设置 | 已有 reviewer 切换/Guardian 记录；policy、extra_policy、circuit_break 等配置未编辑 |
| `background_terminal_max_timeout` | 缺少专用设置 |  |
| `browser_use` | 缺少专用设置 | 历史访问、默认 origin、逐 origin 策略未编辑 |
| `chatgpt_base_url` | 缺少专用设置 |  |
| `check_for_update_on_startup` | CLI/TUI 或 Web 适配 | 按 Web 交互适配，不照搬终端界面 |
| `cli_auth_credentials_store` | 缺少专用设置 |  |
| `cloud` | 缺少专用设置 |  |
| `compact_prompt` | 缺少专用设置 |  |
| `computer_use` | 缺少专用设置 | 默认/逐应用访问和 OS 特定限制未编辑，受原生平台/提供方约束 |
| `daybreak` | 缺少专用设置 |  |
| `default_permissions` | 有字段编辑器 |  |
| `desktop` | 客户端私有、不透明 | 原生 Schema 为 opaque 桌面客户端配置，无法枚举受支持键；不作为 app-server 已承诺的 Web 功能 |
| `developer_instructions` | 有字段编辑器 |  |
| `disable_paste_burst` | CLI/TUI 或 Web 适配 | 按 Web 交互适配，不照搬终端界面 |
| `experimental_compact_prompt_file` | 兼容/弃用字段 | 兼容旧配置；以原生 Schema 描述为准 |
| `experimental_realtime_start_instructions` | 缺少专用设置 |  |
| `experimental_realtime_webrtc_call_base_url` | 缺少专用设置 |  |
| `experimental_realtime_ws_backend_prompt` | 缺少专用设置 |  |
| `experimental_realtime_ws_base_url` | 缺少专用设置 |  |
| `experimental_realtime_ws_model` | 缺少专用设置 |  |
| `experimental_realtime_ws_startup_context` | 缺少专用设置 |  |
| `experimental_thread_store` | 启动/宿主级配置 | 非普通在线配置，不保证 reload 生效 |
| `experimental_use_unified_exec_tool` | 兼容/弃用字段 | 兼容旧配置；以原生 Schema 描述为准 |
| `features` | 部分支持 | 175 个运行时开关已枚举/可切换；复杂 feature table 配置不覆盖 |
| `feedback` | 缺少专用设置 | 只有手动上传反馈，不等于 feedback.enabled 设置 |
| `file_opener` | CLI/TUI 或 Web 适配 | 按 Web 交互适配，不照搬终端界面 |
| `forced_chatgpt_workspace_id` | 缺少专用设置 |  |
| `forced_login_method` | 缺少专用设置 |  |
| `ghost_snapshot` | 缺少专用设置 |  |
| `goals` | 缺少专用设置 | 已有单会话目标预算；全局 max_goal_token_budget 未编辑 |
| `hide_agent_reasoning` | CLI/TUI 或 Web 适配 | 按 Web 交互适配，不照搬终端界面 |
| `history` | 缺少专用设置 |  |
| `hooks` | 查看/关联已有，设置不全 | 清单/信任状态/执行记录只读；源配置 state/handlers 的编辑、启停、信任流程缺失 |
| `include_apps_instructions` | 缺少专用设置 |  |
| `include_collaboration_mode_instructions` | 缺少专用设置 |  |
| `include_environment_context` | 缺少专用设置 |  |
| `include_environment_context_time` | 缺少专用设置 |  |
| `include_permissions_instructions` | 缺少专用设置 |  |
| `instructions` | 兼容/弃用字段 | 兼容旧配置；以原生 Schema 描述为准 |
| `log_dir` | 启动/宿主级配置 | 非普通在线配置，不保证 reload 生效 |
| `marketplaces` | 部分支持 | 添加/升级/移除已有；源配置表不是完整字段编辑器 |
| `mcp_enterprise_managed_auth` | 缺少专用设置 |  |
| `mcp_oauth_callback_port` | 缺少专用设置 |  |
| `mcp_oauth_callback_url` | 缺少专用设置 |  |
| `mcp_oauth_credentials_store` | 缺少专用设置 |  |
| `mcp_optional_startup_grace_ms` | 缺少专用设置 |  |
| `mcp_servers` | 部分支持 | JSON 配置可达，但无逐服务器结构化编辑/删除/环境/权限/超时完整工作流 |
| `memories` | 查看/关联已有，设置不全 | 仅会话模式/全局 reset；generate/use、模型、过滤/保留等全局字段缺失 |
| `model` | 有字段编辑器 |  |
| `model_auto_compact_token_limit` | 有字段编辑器 |  |
| `model_auto_compact_token_limit_scope` | 缺少专用设置 |  |
| `model_catalog_json` | 缺少专用设置 |  |
| `model_context_window` | 有字段编辑器 |  |
| `model_instructions_file` | 有字段编辑器 |  |
| `model_post_turn_compact_threshold_percent` | 缺少专用设置 |  |
| `model_provider` | 有字段编辑器 |  |
| `model_providers` | 部分支持 | 每 provider 仅 name/base_url/env_key/wire_api/requires_openai_auth；其余认证、AWS、headers、重试、超时、能力无专用表单 |
| `model_reasoning_effort` | 有字段编辑器 |  |
| `model_reasoning_summary` | 有字段编辑器 |  |
| `model_verbosity` | 有字段编辑器 |  |
| `notice` | CLI/TUI 或 Web 适配 | 按 Web 交互适配，不照搬终端界面 |
| `notify` | 缺少专用设置 | 原生命令通知配置无入口；浏览器通知是另一个产品适配任务 |
| `openai_base_url` | 缺少专用设置 |  |
| `orchestrator` | 缺少专用设置 |  |
| `oss_provider` | 缺少专用设置 |  |
| `otel` | 缺少专用设置 | 没有 telemetry exporter/endpoint/log_user_prompt 等设置 |
| `permissions` | 缺少专用设置 | 已有命名权限选择/默认 ID；命名配置定义、继承、workspace/filesystem/network 细粒度编辑未提供。Schema 为 opaque object，另列 Rust 字段补表 |
| `personality` | 兼容/弃用字段 | 原生已弃用，无效果；源码已隐藏 |
| `plan_mode_reasoning_effort` | 有字段编辑器 |  |
| `plugins` | 部分支持 | 目录/安装/同步/共享已有；默认与逐插件配置及 thread disabledPluginIds 缺编辑 |
| `profile` | 缺少专用设置 | 没有命名模型配置 profile 的选择流程；不要与 permissionProfile 混淆 |
| `profiles` | 缺少专用设置 | 没有命名配置集的创建/编辑/切换；不能把 permissionProfile/list 计为支持 profiles |
| `project_doc_fallback_filenames` | 缺少专用设置 |  |
| `project_doc_max_bytes` | 缺少专用设置 |  |
| `project_root_markers` | 缺少专用设置 |  |
| `projects` | 缺少专用设置 | 原生 project RPC CRUD 已有；config projects 的 trust_level 等不是同一能力，缺设置信任流程 |
| `realtime` | 查看/关联已有，设置不全 | 浏览器仅保存 version/voice/model/outputModality 并传 start；原生 transport/type 等未完整映射 |
| `responses_api_metadata` | 缺少专用设置 |  |
| `review_model` | 缺少专用设置 |  |
| `sandbox_mode` | 有字段编辑器 |  |
| `sandbox_workspace_write` | 有字段编辑器 |  |
| `service_tier` | 有字段编辑器 |  |
| `shell_environment_policy` | 部分支持 | inherit/include_only/exclude/set 已有；ignore_default_excludes/filters/experimental_use_profile 未编辑 |
| `show_raw_agent_reasoning` | CLI/TUI 或 Web 适配 | 按 Web 交互适配，不照搬终端界面 |
| `skills` | 部分支持 | 启停和扩展根目录已有；bundled、include_instructions、max_context_tokens 无设置流程 |
| `sqlite_home` | 启动/宿主级配置 | 非普通在线配置，不保证 reload 生效 |
| `suppress_unstable_features_warning` | 缺少专用设置 |  |
| `thread_unload_delay_secs` | 启动/宿主级配置 | 非普通在线配置，不保证 reload 生效 |
| `tool_output_token_limit` | 缺少专用设置 |  |
| `tool_suggest` | 缺少专用设置 |  |
| `tools` | 缺少专用设置 | 工具详细配置未完整编辑，启用功能开关不能替代工具行为设置 |
| `tui` | CLI/TUI 或 Web 适配 | 按 Web 交互适配，不照搬终端界面 |
| `web_search` | 部分支持 | 只有 disabled/cached/live，缺少原生 indexed |
| `windows` | 缺少专用设置 |  |
