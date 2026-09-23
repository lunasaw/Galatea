# 09 测试与验收

实施决策（2026-09-23）：已按用户确认采用双层 schema 合同。原始 `tools.json` 负责 Host 严格
参数校验；`runtime-compatibility.json` 冻结真实模型请求的 schema 并绑定 binary/package/protocol
摘要。下文“模型 schema 一致”指与此兼容性合同逐字段一致，Host 原始约束保持不变。
最新实测见 [实施记录](11-stage0-implementation.md)。

## 1. 单元测试

- 17 个支持工具合同全部生成 dynamic tool schema，名称、namespace、required、枚举和 digest 一致；
  principal 过滤后的 session 子集不能出现未授权工具。
- JSON Schema 拒绝额外字段、错误类型、缺失字段、越界 limit、错误 project/campaign scope。
- Tool Registry 对每个工具返回既有 envelope；敏感字段脱敏；response 超过 256 KiB 时失败关闭。
- `callId` 重放返回同一 receipt；相同 id 不同参数返回 conflict。
- mutation handler 在主服务状态不可用、授权不足和未知副作用时不重试。

## 2. App-server 协议测试

使用复制的真实 binary 和 mock model endpoint，不能只 mock Host 自己的 RPC：

1. `initialize.capabilities.experimentalApi=true`/`thread/start` 成功；未 opt in 时请求必须被拒绝。
   对完整全集和受限 principal 分别捕获实际模型 request，确认新建时有效工具清单精确等于各自批准
   的 Galatea allowlist，且不存在 shell、文件、HTTP、MCP/plugin、协作或模型管理工具。
2. 模型产生 dynamic function call，Host 收到 `item/tool/call`，返回成功 text content，
   app-server 继续第二次模型请求并完成 turn。
3. Host 返回 `success=false`，模型收到错误 envelope 并能正常结束或继续。
4. tool handler 延迟、超时、取消时，Host 返回明确失败/unknown，app-server 不被卡死。
5. `thread/read`、跨进程 `thread/resume` 后，从实际模型 request 确认工具清单和历史 item 正确；
   catalog/runtime/effective-config digest 变化、rollout 缺失或恢复能力未经阶段 0 证明时均被阻止。
6. app-server 异常退出、stdout EOF、坏 JSON-RPC、重复 response id 均 fail closed。

## 3. Console 测试

- `POST /messages` 的同一 `request_id` 重试不产生第二个 turn。
- SSE 从任意 seq 续读，事件顺序不变；持久化日志缺口产生带范围的 `observation_gap`。仅连接写
  失败不能声称已经向客户端交付了 gap。
- 未认证的本机 HTTP 写请求、跨 Origin 写请求、缺少 CSRF token 的 cookie 写请求和越权 session
  SSE 订阅均被拒绝。
- Console 看不到 token、secret、完整 prompt、私有 reasoning、测试样本和原始 Ray 日志。
- Agent Loop 把 tool request/response 合并为一个步骤，流式 delta 合并为一条消息。
- interrupt/close 在 turn 完成前显示 pending，不能删除未核对 session。

## 4. 平台集成测试

- 只读 preflight 不创建 Ray Job、不读取 final test、不修改 MLflow Alias。
- `plan_run` 与 `submit_job` 在 fake Ray/MLflow backend 下保存 plan、idempotency key 和
  operation receipt。
- `submit_job` 后 Host 崩溃，重启只 observe 原 operation，不重复提交。
- 在 intent 写入后、handler 执行中、receipt 写入前分别注入崩溃，重启只通过 operation/业务幂等
  键对账；callId 重放只有在已有确定 receipt 时才返回成功。
- 操作 unknown、后端 404、MLflow artifact digest 错误、Campaign revision 过期均被拒绝。
- compare/freeze/verify 只能对同一 dataset/split/preprocess/metric protocol 的 evidence 操作。
- 后台 reconciler 能推进 pending/unknown operation；启动首次 reconcile 失败、循环退出或 freshness
  超时时 readiness 失败，不静默停滞；Registry 与 reconciler 不能并发破坏单写锁。

## 5. 运行验收记录

每项记录 `expected`、`observed`、`evidence_path`、`runtime_version`、`git_revision` 和
`status`。以下事实不能用本地 fake 代替：

- 真实复制的 Codex binary 能完成 dynamic tool round-trip；
- 真实配置的 MLflow Tracking/Artifact API 可读；
- Ray Job identity、资源边界和 stop/reconcile 语义；
- 正式 Campaign 的数据、split、code、environment 和 quality gate；
- systemd 进程组清理以及配置目录权限；
- package tree manifest、effective Codex config 来源与 digest，以及子进程 `HOME`/`CODEX_HOME`
  隔离。
- 子进程环境中没有 backend secrets；若阶段 0 选择强 secret isolation，还要证明 app-server 身份
  不能读取 Host secret 文件、环境或 credential store，只能获得已批准的 Galatea capability。

## 6. 回退

回退只停止新 Host 实例，保留 `config/codex-home`、Host state、事件和 operation 引用。
不得删除状态目录来“重置”会话，也不得重新提交所有未知 Job。若内化 adapter 有问题，可暂时
恢复既有 `services/galatea-mcp` 独立服务用于只读核对，但新 Agent 不应自动切换到 MCP；切换
必须是显式部署版本变更并重新执行协议/权限验收。
