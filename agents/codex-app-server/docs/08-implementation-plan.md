# 08 实施计划

实施决策（2026-09-23）：已按用户确认采用双层 schema 合同。原始 `tools.json` 负责 Host 严格
参数校验；`runtime-compatibility.json` 冻结真实模型请求的 schema 并绑定 binary/package/protocol
摘要。下文“模型 schema 一致”指与此兼容性合同逐字段一致，Host 原始约束保持不变。
最新实测见 [实施记录](11-stage0-implementation.md)。

本计划把实现拆成可独立验收的阶段；每阶段都先有 fake/forward-only 测试，真实 Ray/MLflow
训练和最终测试只在用户明确授权后执行。

## 阶段 0：冻结来源和合同

- 锁定 Codex source commit、runtime target/version 和复制方式。
- 用 `initialize.capabilities.experimentalApi=true` 验证 experimental API；增加未 opt in 必须失败的
  负向 smoke。
- 生成 app-server v2 JSON schema fixture，确认 `thread/start.dynamicTools` 和
  `item/tool/call` 的字段。
- 建立 17 个支持工具的 catalog metadata，按一个受限 principal 生成完整全集和严格子集两种 fixture；
  捕获 mock model 的新建与 resume 后真实 request，断言有效工具面精确等于各自批准的 Galatea
  allowlist，且不存在 shell、文件、HTTP、MCP/plugin、协作或模型管理工具；不能仅断言 17 个动态
  工具“也在其中”。
- 复制 `services/galatea-mcp/contracts/tools.json`，计算 contract digest。
- 为 function description、read-only/idempotent annotations 建立版本化 catalog metadata，禁止
  由 Host 按工具名临时拼接。
- 冻结 Host HTTP/SSE 依赖选择及许可证；明确标准库实现能否满足并发、背压、断线和停机语义。
- 冻结本机 HTTP 写接口和 SSE 的身份认证、Origin/CSRF、session 授权、body/连接上限与审计字段；
  loopback 只能作为网络边界，不能作为唯一授权机制。
- 冻结 Codex 配置来源：`CODEX_HOME`、受控 `HOME`、system/user/cwd/repo 层的允许范围和
  effective config digest 计算方式。
- 冻结 Host/app-server secret 威胁模型；若要求强隔离，选定独立 UID/容器或 credential broker，
  并把权限、文件 ownership、`/proc` 可见性和 systemd 影响纳入验收。
- 清理所有 MCP transport 入口的设计引用，保留原 MCP 包兼容性测试。

阶段 0 通过条件：目标 package/version/source revision/schema/catalog/config digest 全部冻结；
experimental opt-in 的正负 smoke 通过；新建和 resume 后有效工具面精确匹配 allowlist；intent/receipt
崩溃窗口、reconciler freshness、HTTP 鉴权/CSRF 和 package tree 摘要均有自动化证据。任一项缺失、
未知或只能依赖 prompt/Host 声明时，阶段 0 失败，禁止进入部署实现。

交付：`manifest.json`、runtime smoke 记录、catalog/config digest、阶段 0 通过/失败记录。

## 阶段 1：抽出 transport-neutral Galatea service

- 将 `Service.call` 前的 registry、principal、schema、state 和 backends 整理为不依赖
  `mcp.server` 的 application API。
- 保持 17 个工具名、输入 schema、envelope、MUTATIONS 和错误码不变。
- 为 `ToolContext` 增加 session/thread/turn/call、deadline、cancel 和审计字段。
- MCP server adapter 继续通过旧入口运行，确保现有测试不回归。
- 抽出并共用 envelope/异常/响应大小边界，Host 启动等价的 `Service.reconcile_all` 后台循环。

交付：shared service tests、MCP compatibility tests、ToolRegistry unit tests。

## 阶段 2：Codex app-server Host

- 创建 `agents/codex-app-server/src/codex_agent/`：`host.py`、`rpc.py`、`catalog.py`、
  `registry.py`、`state.py`、`events.py`、`config.py`、`process.py`。
- 启动 app-server stdio，完成 initialize、thread/start、turn/start、resume、interrupt。
- 从合同和 metadata 生成 17 个支持工具的 namespace，并按 principal 生成 session 子集；
  不把“17 个”硬编码成每次请求的可见清单。
- 接收 `item/tool/call` server request，执行 Registry，并回传 DynamicToolCallResponse。
- 保存 thread/turn/call journal，处理进程退出和 unknown 状态。

交付：不连接真实模型的 protocol harness、dynamic tool round-trip、resume/interrupt tests。

## 阶段 3：Console

- 创建 `console/server.py`、静态页面和最小 HTTP/SSE adapter。
- 实现 session/message/interrupt/close/events/loop API 和 request id 幂等。
- 添加原生事件聚合、工具调用参数/结果脱敏、SSE seq 续读和持久化日志驱动的 gap 检测；
  不把连接写失败伪装成已持久化的 `observation_gap`。
- 不复制 Redis、Tunnel、task-manager 或 CubeFS。

交付：浏览器端 smoke、SSE 断线回放、重复提交、事件脱敏和工具 loop 展示测试。

## 阶段 4：平台后端接线

- 使用现有 Galatea registry/state/backend 配置，先做 `check` 和只读工具。
- 接入 `plan_run`/`submit_job`/stop/cancel 的 fake backends，验证幂等和 unknown。
- 接入真实 MLflow/Ray/Object API 的 preflight；不自动提交训练。
- 在最小明确授权下运行一条受治理 smoke，证据中记录 Run ID、Job ID、Artifact digest。

## 阶段 5：部署与验收

- 复制 runtime 到 config/runtime，执行版本和 digest manifest。
- 安装 loopback systemd unit，验证 `systemd-analyze verify`、liveness/readiness 和
  app-server process-group cleanup。
- 完成故障注入：tool call timeout、submit 后崩溃、state 损坏、runtime mismatch、
  catalog mismatch、effective config mismatch、旧进程存活、backend unknown、reconciler 停止和
  receipt 保存窗口崩溃。
- 只读验证通过后再决定是否接入正式 Campaign；生产 Alias 仍保持人工审查。

## 建议文件布局

```text
agents/codex-app-server/
├── README.md
├── pyproject.toml
├── src/codex_agent/
├── console/static/
├── scripts/
├── tests/
├── config/                 # 开发模板；生产可映射到独立绝对目录
└── docs/                   # 本方案
```

`config/` 中不提交真实 secret、数据、checkpoint、模型或执行事件；开发 fixture 与生产运行
目录分开。
