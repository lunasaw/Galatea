# 07 安全边界

## 1. 权限分层

| 层 | 决定者 | 约束 |
| --- | --- | --- |
| Codex approval/sandbox | app-server + thread config | 控制模型可用的原生能力 |
| Dynamic Tool catalog | Host config | 从 17 个支持工具中只公布当前 principal 允许的子集 |
| Galatea principal | Host config | project/campaign/action scope |
| Campaign/Evidence | Galatea service | 预算、数据身份、质量门禁、候选证据 |
| HTTP Console | Host | 只提供本机输入/观察，不授予后端权限 |

模型输出、Skill 文本和 Console 请求都不能改变后两层。`experimental_only`、
`promotable=false`、缺失批准或未通过质量门禁必须继续由 Galatea service 拒绝。

## 2. 工具策略

- 首版 Registry 支持全集包含 17 个函数；每个 session 的 dynamic catalog 只能是该全集的
  principal-filtered 子集。由于 dynamicTools 不会自动移除 Codex 内建能力，必须同时关闭 shell、
  文件、任意 HTTP、MCP/plugin、协作和模型管理工具，并以新建与 resume 后实际模型请求中的有效
  tool list 为验收证据。
- 工具名称、schema 和 namespace 使用 allowlist；未知工具立即 `success=false`。
- 所有输入以 JSON Schema 严格校验；拒绝额外字段、错误类型、超长分页和越权引用。
- read-only 工具也必须检查 project/campaign scope；不能因为没有副作用就绕过身份。
- mutation 工具执行前重新读当前 Campaign/operation 事实，不能使用旧 turn 中的授权快照
  代替当前状态。

## 3. 数据保护

- 事件和 Console 采用最小公开字段；`public_detail` 是单向投影，不允许浏览器传回投影来
  重建后端调用。
- HTTP 写接口和 SSE 订阅必须通过受审查的本机身份验证；loopback 仅限制网络来源，不是授权。
  Cookie 模式必须做 Origin/Referer 与 CSRF 校验；bearer/capability 模式禁止通过 URL query 传递
  token。健康端点只公开存活/就绪布尔状态和非敏感原因。
- 子进程环境白名单不等于 OS 进程隔离。同一 UID 下只承诺“凭据不被直接继承或进入模型上下文”；
  若威胁模型要求 app-server 无法读取 Host backend secrets，必须使用独立身份/容器或 credential
  broker，并以权限测试证明，不能只检查 `env` 输出。
- Artifact 使用 API 读取并校验 digest；不读取服务端 MLflow DB，不扫描服务端对象存储文件系统。
- test data 只由受治理 evaluate/verify path 访问；不会因为 Console 或 Agent Loop 展示而泄漏。
- 日志中的 tool arguments 需要按 schema 字段脱敏；`reason`、`config_id`、`artifact_ref`
  等字段也按项目敏感性配置。

## 4. 失败关闭

以下情况均停止新 turn 或 mutation，并保留可诊断状态：

- runtime/version/schema/catalog digest 不匹配；
- `CODEX_HOME` 或 Host state 损坏；
- effective Codex config 来源或 digest 不能证明来自受控目录；
- app-server 进程组无法确认已退出；
- Tool call 结果或 backend operation 状态 unknown；
- principal、Campaign、Evidence 或 artifact 归属不明确；
- 后端不可达且可能已产生副作用；
- SSE/Console 写失败（不影响业务执行，但标记 observation gap）。
- reconciler 未运行、首次 reconcile 失败或 freshness 超时；

## 5. 明确禁止

- 禁止把 MCP endpoint、MCP token 或 MCP SDK 作为隐式 fallback；
- 禁止在工具失败后由模型重复提交未知副作用；
- 禁止把 `turn/completed` 当成 Ray Job 或 MLflow 训练成功；
- 禁止在 `CODEX_HOME` 外写 Codex session；
- 禁止让浏览器提交 project/campaign scope 覆盖服务端配置；
- 禁止自动修改 production model alias。
- 禁止只凭 Host 配置中的 `allow_shell_tools=false` 或 prompt 声称内建工具已关闭；必须由
  runtime 配置和协议 smoke 共同证明，做不到就阻止启动。
- 禁止把 SSE 客户端看到的事件当作权威状态；权威状态必须来自已 fsync 的 Host journal 和
  Galatea operation 事实。
