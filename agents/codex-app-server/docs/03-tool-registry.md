# 03 Tool Registry

实施决策（2026-09-23）：已按用户确认采用双层 schema 合同。原始 `tools.json` 负责 Host 严格
参数校验；`runtime-compatibility.json` 冻结真实模型请求的 schema 并绑定 binary/package/protocol
摘要。下文“模型 schema 一致”指与此兼容性合同逐字段一致，Host 原始约束保持不变。
最新实测见 [实施记录](11-stage0-implementation.md)。

## 1. 内化原则

`services/galatea-mcp` 原本把工具暴露为 MCP `list_tools`/`call_tool`。新应用保留工具名、
输入 schema、业务错误 envelope、状态与证据语义，只替换适配层：

```text
旧：MCP Client → MCP Server → Service.call(principal, name, args)
新：Codex DynamicToolCall → ToolRegistry.call(session, name, args)
```

`ToolRegistry` 不调用 MCP SDK，也不启动 MCP server。它应复用 `Service.call` 前面的
schema、principal、campaign scope、幂等和 operation 逻辑；若现有 `Service` 构造依赖 MCP
transport，先抽出纯 Python application port，再由 MCP server 和 Host 共用，不能复制一份
逻辑导致两套治理规则。

## 2. 17 个支持工具清单

支持全集来源：`services/galatea-mcp/contracts/tools.json`。名称和 schema 在 v1 保持不变；每个
session 的实际 catalog 是该全集与服务端 principal action allowlist 的交集，不能因此在文档或验收
中把“支持 17 个”表述成“每个模型请求必然看到 17 个”。

| 组 | 工具 |
| --- | --- |
| 能力与项目只读 | `galatea_get_capabilities`, `galatea_list_projects`, `galatea_inspect_project` |
| Campaign/操作只读 | `galatea_get_campaign`, `galatea_list_operations`, `galatea_get_operation`, `galatea_observe_job` |
| 运行规划与提交 | `galatea_plan_run`, `galatea_submit_job` |
| 停止与取消 | `galatea_stop_job`, `galatea_cancel_campaign` |
| MLflow 只读证据 | `galatea_query_runs`, `galatea_get_metric_history`, `galatea_get_artifact`, `galatea_compare_runs` |
| 候选证据 | `galatea_freeze_candidate`, `galatea_verify_candidate` |

具体 required 字段、正则、枚举、分页和数组上限不在 Host 重新手写，启动时从版本化的
`contracts/tools.json` 生成 dynamicTools；构建测试断言生成结果与源合同 digest 一致。
`tools.json` 当前只承载输入 schema，不含 Codex 所需的 function `description` 和 MCP 的
read-only/idempotent annotations；这些字段必须来自同一版本化 catalog metadata，并与工具名、
schema digest 一起冻结，不能在 Host 中按名称临时拼接或允许模型覆盖。metadata 必须为每个支持工具
提供唯一 description、read-only/idempotent 标记及公开性策略；缺项即不能生成 catalog。

## 3. Handler 接口

```python
class ToolHandler(Protocol):
    name: str
    input_schema: dict[str, object]
    read_only: bool
    idempotent: bool

    def call(self, context: ToolContext, arguments: dict[str, object]) -> ToolResult:
        ...
```

`ToolContext` 由 Host 创建，至少包含：

- `session_id`, `thread_id`, `turn_id`, `call_id`；
- 固定 `principal_id`、允许的 project/campaign scope 和 action allowlist；
- 当前 catalog/config revision；
- tool call deadline、取消事件、审计 logger；
- 受限的 Ray/MLflow/Object backend facade。

Handler 不接收浏览器传入的 principal、凭据、project allowlist 或预算。所有身份来自配置和
冻结的 Campaign；所有后端客户端由启动组装创建。

## 4. 调用流程

```text
DynamicToolCall
  → 结构校验（JSON-RPC / session / namespace / catalog）
  → 输入 schema 校验（additionalProperties=false 等规则）
  → scope 与 action 校验
  → 幂等查找（callId + canonical args；业务副作用另查业务幂等键）
  → intent 写前记录
  → handler 执行（deadline + cancellation）
  → envelope 校验与脱敏
  → receipt 原子落盘
  → DynamicToolCallResponse
```

副作用工具 `plan_run`、`submit_job`、`stop_job`、`cancel_campaign`、`freeze_candidate`、
`verify_candidate` 必须沿用现有 `MUTATIONS` 和 `Service` 的 fail-closed 语义。特别是：

- `submit_job` 的 `idempotency_key` 是业务提交幂等键，不被 `callId` 替代；callId 只去重同一协议
  调用的已知回执；
- 未知提交结果进入 reconcile，不自动再次提交；
- `freeze_candidate` 和 `verify_candidate` 仍要求 evidence/candidate 所有权和 digest；
- test-once、quality gate、artifact digest 和 production alias 限制不因内化而放宽。

## 5. 结果大小与脱敏

- 单次 tool response 上限沿用 MCP 包的 256 KiB envelope 限制；超限返回
  `response-too-large` 和 `reduce-page-size-and-reconcile`。
- 原始 Ray 日志、测试样本、测试标签、凭据、S3 签名、MLflow token 和完整 Artifact 内容
  不进入模型或 Console；`get_artifact` 只返回允许的受限证据投影。
- `request_id`、`operation_id`、`run_id`、`candidate_id` 和 `artifact_ref` 视为不透明引用，
  不从路径或 URL 猜测对象。
- 失败 envelope 说明 `retryable`、`state_changed`、`operation_id`、`next_action`；模型
  不得把 `unknown` 当作失败后可安全重试。

## 6. 与现有 MCP 包的迁移

建议将 `services/galatea-mcp/src/galatea_mcp/service.py` 及其 domain/backend 依赖整理为
不感知 transport 的 application package，再提供两个薄适配器：

1. 现有 MCP server adapter，保持当前服务和测试兼容；
2. `codex-app-server` ToolRegistry adapter，传入 Host `ToolContext`。

迁移完成前不删除 MCP server；新 Agent 默认不启动它。两套 adapter 必须共享同一套
contract/service tests，禁止只测试一边。

内化 adapter 还必须迁移 `server.py` 目前承担而 `Service.call` 不承担的边界：统一
`galatea.tools/v1` envelope 与 `request_id`、`DomainError`/未知异常归约、256 KiB 上限、
principal action 过滤，以及 `watchdog` 对 `Service.reconcile_all` 的周期调用。不得为了去掉 MCP
transport 而丢掉这些行为。reconcile 调度器必须与 Registry 共用单写锁，具有启动成功检查、最近成功
时间和停机标记；没有活跃 reconciler 或 freshness 超时，Host 必须撤销 readiness。
