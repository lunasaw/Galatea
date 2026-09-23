# Galatea Codex App Server Agent

状态：Host、Registry、HTTP/SSE Console、官方后端接线和部署门禁已实现；
已确认采用双层 schema 合同，真实 runtime 兼容性测试通过。生产 release 接纳与现场验收仍独立执行。
最新生产证据与限制见 [12-production-readiness.md](12-production-readiness.md)，
兼容性实施记录见 [11-stage0-implementation.md](11-stage0-implementation.md)。

本目录定义一版直接使用 Codex `app-server` 的 Galatea Agent 应用。它把
`services/galatea-mcp` 的 17 个训练治理工具内化为宿主进程的 Tool Registry，使用
Codex app-server 的原生 `dynamicTools` 和 `item/tool/call` 协议；不启动 MCP 服务、不连接
MCP Transport、不改造 Codex agent loop，也不依赖 CubeFS。

## 可行性结论

结论：**原生集成可行；正式运行必须通过 release 门禁。** 以下为设计时的来源核对，当前实施结果见实施记录：

- `services/galatea-mcp/contracts/tools.json` 确实包含 17 个工具，现有 `Service.call` 已是可复用的
  transport-neutral 业务入口；
- 本地 Codex 源码中的 namespace/function schema、`item/tool/call` 参数和
  `DynamicToolCallResponse` 与本文主链路一致；
- 实际安装的 `codex-cli 0.153.4` 在临时 `CODEX_HOME` 中完成了
  `initialize → initialized → thread/start(dynamicTools)` smoke；
- 未声明 `initialize.capabilities.experimentalApi=true` 时，runtime 明确拒绝请求并返回
  `thread/start.dynamicTools requires experimentalApi capability`。

发布必须持续满足以下六项条件（实现已加入相应校验；现场证据不能由单元测试替代）：

1. 目标发布 runtime 必须锁定版本、二进制/资源/schema 摘要并重跑真实 dynamic tool round-trip；
   当前安装版是 `0.153.4`，不能套用旧样例中的 `0.154.0`。
2. 必须从实际模型请求证明有效工具面只含当前 principal 批准的 Galatea 动态工具。17 个工具是
   Registry 的支持全集，session 可见清单可以是其子集；dynamicTools 是附加项，不会自动移除 Codex
   内建 shell、文件、网络、MCP、plugin 或协作工具。若目标 runtime 无法在不改 Core 的前提下得到
   精确 allowlist，本方案失败关闭。
3. 内化适配器必须承接现有 MCP server 的 envelope/256 KiB 限制、异常归约和
   `Service.reconcile_all` 后台对账；只调用 `Service.call` 不构成完整迁移。
4. HTTP/SSE 实现依赖、同源鉴权、CSRF/Origin 防护及断电恢复需在阶段 0 冻结。生产不能把
   loopback、Python 标准库样例或本地 fake 当成并发、背压和持久化验收。
5. 必须冻结 Codex 的所有有效配置来源和恢复语义。仅设置 `CODEX_HOME` 不能排除系统配置、工作目录
   与受信项目配置；`thread/resume` 后的实际工具面也必须在目标 runtime 上重新取证。
6. 必须冻结 Host 与 app-server 的凭据威胁模型。过滤子进程环境变量不是同一 Unix UID 下的强秘密
   隔离；若要求 runtime 无法读取后端凭据，部署必须采用独立低权限身份或受控 credential broker。

## 已确认的范围

- 参考行为来自 `/data/ai/chenzhangyue/code/service-manager-agent`，但不复制它的业务代码、
  任务管理器、Redis Console、CubeFS 状态布局或 `agents/training-agent-runtime`。
- Codex 源码与 app-server 协议依据来自 `/data/ai/chenzhangyue/code/codex`。
- Codex runtime 采用可审计的复制/打包方式，发布时锁定二进制、资源目录和版本摘要。
- 17 个工具的业务语义、schema、权限、幂等、证据和 Ray/MLflow/Artifact 后端复用
  `services/galatea-mcp` 的代码与 contracts；调用入口改为进程内 Tool Registry。每个 session 只发布
  principal action allowlist 与这 17 个工具的交集。
- `CODEX_HOME` 固定为应用配置目录下的 `codex-home/`，同时给子进程设置专用 `HOME` 并审计全部有效
  配置层；不依赖宿主用户默认目录，不使用 CubeFS。
- Console 与 Agent 宿主同进程，通过本机 HTTP 和 SSE 提供会话、原生事件、工具调用和结果观察。
- 首版不增加数据库、消息队列、Redis、MCP 网关、第二个 Agent runtime 或 Python SDK Runner。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [01-architecture.md](01-architecture.md) | 组件边界、进程模型和数据流 |
| [02-app-server-protocol.md](02-app-server-protocol.md) | 原生 JSON-RPC、dynamicTools 和工具回执 |
| [03-tool-registry.md](03-tool-registry.md) | 17 个工具内化、契约和后端适配 |
| [04-agent-loop-state.md](04-agent-loop-state.md) | 不改 loop 的宿主调度、会话和恢复 |
| [05-console.md](05-console.md) | Console API、SSE、Agent Loop 展示和脱敏 |
| [06-config-deployment.md](06-config-deployment.md) | 配置目录、Codex runtime、凭据和启动 |
| [07-security-boundaries.md](07-security-boundaries.md) | 权限、资源、失败关闭和禁止项 |
| [08-implementation-plan.md](08-implementation-plan.md) | 分阶段实现顺序与文件布局 |
| [09-test-acceptance.md](09-test-acceptance.md) | 测试矩阵、验收证据和回退 |
| [10-source-evidence.md](10-source-evidence.md) | 本地源码、runtime smoke 和设计依据 |
| [11-stage0-implementation.md](11-stage0-implementation.md) | TDD 实施记录、真实 runtime 证据和未通过门禁 |
| [12-production-readiness.md](12-production-readiness.md) | 来源验证、依赖锁、生产候选交付物及切换/回滚步骤 |

## 总体链路

```text
浏览器 Console
   │ HTTP / SSE（同一 Agent 进程）
   ▼
Agent Host
   ├─ 会话、幂等、状态、事件日志、Console API
   ├─ Dynamic Tool Registry（17 个支持工具，按 principal 过滤发布）
   ├─ Galatea domain/service/backends（Ray / MLflow / Artifact API）
   └─ stdio JSON-RPC
        ▼
     codex app-server（复制的 Codex runtime）
        ├─ 原生 Thread / Turn / Skill / approval / sandbox
        ├─ 原生 agent loop
        └─ item/tool/call → Agent Host → DynamicToolCallResponse
```

“内化工具”只改变工具的承载位置和调用边界：模型仍通过 app-server 的动态工具描述发现
工具，仍由 app-server 决定何时调用，宿主只负责执行和回传结果。宿主不能把自然语言当成
工具授权，也不能绕过 Galatea 原有的 Campaign、Evidence、预算和质量门禁。

## 非目标

- 不修改 `/data/ai/chenzhangyue/code/codex` 的 agent loop、Responses 归约、Thread/Turn
  生命周期或 app-server 协议。
- 不把 17 个工具改写为 HTTP API 或 MCP server。
- 不实现 `service-manager-agent` 的商家业务工具、Redis Stream、task-manager、记忆服务或
  CubeFS 迁移。
- 不把 Console 作为训练控制面；浏览器没有直接调用 Ray、MLflow 或 Artifact 的权限。
- 不自动执行模型训练、推广 Alias 或扩大 Campaign 授权；这些仍由 Galatea 现有规则决定。

## 双层 schema 合同

用户已确认采用兼容性测试。原始 `tools.json` 作为 Host 唯一参数校验合同；runtime 对模型发布的
schema 单独冻结在 `runtime-compatibility.json`。full/subset、新建、续轮和跨进程恢复的真实请求
必须与已审查 wire schema 逐字段相等。边界、正则、数组长度/唯一性等 runtime 未保留的约束仍由
Host 在 handler 前严格执行，升级 runtime 必须重新接纳，禁止自动更新 fixture 掩盖漂移。

## 已知待验收项

Codex app-server 当前将 dynamic tool API 标记为 experimental。实现前必须针对实际复制的
runtime 运行协议 smoke，确认 `thread/start`、`item/tool/call`、`turn/completed`、
`thread/read` 和 `thread/resume` 的字段与源码一致，并分别捕获新建与恢复后的真实模型请求；不能只
根据文档、源码测试或模型输出猜测。阶段 0 的所有退出条件见
[08-implementation-plan.md](08-implementation-plan.md)。

本机官方 Ray、MLflow Tracking/Artifact 目录与 S3 train/validation 元数据只读预检已通过。
最终生产模型配置、有效工具面和服务切换仍需接纳；本地协议测试和平台只读检查均不代表训练成功。
