# 07 训练治理与证据

## 1. 通用 Task 不等于训练 Campaign

数据处理任务可以只有检查与清洗两个阶段，不创建 Campaign、Ray Job 或 MLflow 训练 Run。
Task 的动态展示阶段也不能取代治理系统内部的法定状态。

例如 App 中的“训练与分析”可以是一个展示阶段，但 baseline、trial、champion 和 evaluate
各自仍须遵守对应授权、不可变输入、预算和最终评估约束。App 改名、合并、插入阶段
都不能修改这些约束。

## 2. 显式选择一种执行路线

当前仓库有两条独立路线，依据 [Agent 集成说明](../../../doc/agent-galatea.md)：

| 路线 | 实际控制面 | App 集成方式 |
| --- | --- | --- |
| `project_governance` | 项目 YAML + `packages/galatea-governance` + 固定 Ray 入口 | Skill 执行受治理项目入口；适配器读取该路线的权威执行与证据 |
| `governance_mcp` | `services/galatea-mcp` 外部 MCP + Campaign/Plan/Operation | Agent 调用治理工具，Task 绑定权威标识，Worker 查询或执行已授权恢复 |

每个训练执行绑定 `governance_route`、project_id、release/config/data identity、
授权引用、operation 身份及固定入口。一个 Attempt 不能途中切换路线来绕过失败。
两条路线不共享授权状态，也不能用其中一条的“已批准”作为另一条的有效证明。

首个训练集成切片建议选择已预置项目与授权的 `governance_mcp`；
项目 YAML 路线随后按实际项目契约独立验收，不用一段模糊代码兼容两套未核实协议。

## 3. 现有外部治理 MCP 的真实能力

以 [README](../../../services/galatea-mcp/README.md)、
[工具契约](../../../services/galatea-mcp/contracts/tools.json) 和
[service.py](../../../services/galatea-mcp/src/galatea_mcp/service.py) 为准。

| 能力 | 当前事实 | 产品处理 |
| --- | --- | --- |
| galatea_plan_run / galatea_submit_job | 支持已有项目和约束下的计划与提交 | 原样关联实际 plan/operation，不自行生成成功记录 |
| Campaign 查询、operation 观察和停止 | 已有 | 服务端适配器按主体和项目范围调用 |
| Campaign 注册与授权修改 | 可信管理通道，服务使用单写锁 | 首版显示待办；不能让 App MCP 调用离线 writer |
| dynamic_builds / uploads | 当前声明不支持 | 代码生成后停在发布就绪检查，不自动训练新代码 |
| promotion / pause_resume | 当前声明不支持 | 不显示可用的推广/冻结按钮 |
| raw_logs / log_bytes | 当前不暴露原始日志，额度为 0 | 展示治理摘要和证据；日志适配另行实现 |
| 制品读取 | 受限定路径、大小与 digest 校验 | 通过 MLflow Artifact / Tracking API，不访问服务存储目录 |

`galatea_get_operation` / `galatea_list_operations` 是查询已存事实；
`galatea_observe_job` 会刷新状态，并可能派发已接纳的 pending 操作、停止到期作业和更新证据，
不能按名字当成纯只读工具。Worker 要单独获得对应恢复权限，只处理本任务已受理的操作；
取消时先建立治理侧屏障，再使用可能有副作用的观察入口。
没有恢复权限时可显示只读旧事实，但不能宣称具有实时观察和 watchdog 保证。

外部服务采用单 writer 状态，Campaign 注册/修改不能在在线服务持锁时启动第二个写进程。
App 在线批准预算若要实现，必须先新增治理服务自己的授权 API、审计与并发控制，
不能由 Task Service 直接改 Campaign JSON 或绕过 writer.lock。

当前常见执行角色与治理阶段约束为：baseline 对应 baseline，trial 对应 search，
champion 受 candidate_frozen/champion_training 约束，evaluate 受 champion_ready/final_evaluation
约束。映射以服务运行时能力和实际状态为准；Task Stage 的名称不能驱动治理状态跳转。

## 4. 训练前置检查

Agent 提出训练意图后，先完成只读就绪检查：

1. 项目身份、目标 profile、固定执行入口与资源约束明确。
2. Release 不可变，代码、环境、配置和数据清单 digest 可验证。
3. 训练、验证、最终测试 split 的身份和读取规则冻结。
4. 当前路线下的 readiness/plan/execution/authorization 证据齐全且一致。
5. 预算与期限有效，操作幂等键已持久化，允许的角色与资源满足请求。
6. 观察与证据读取能力有效，失败与取消后仍可追踪 operation。

任意项缺失则 waiting_approval、waiting_input 或 blocked，明确原因。
生成新训练代码本身不意味着它已经成为可运行的有效 Release。
如果线上治理只接受预置项目，就必须经现有可信发布流程就绪后再继续。

## 5. 不可绕过的执行边界

仓库声明使用 Ray 的项目，每一次 Training Run，包括 smoke、一个 epoch、试验和私有验证，
均须经过项目的固定受治理 Ray 入口。任何参数更新、fit、真实 checkpoint/adapter/model 输出
或持久训练比较证据都按 Training Run 处理。

不得通过本地 Python、Notebook、通用 `ray job submit`、直接 Driver 或换一个阶段名称绕过。
Task Provider、Host 运行许可与用户“继续”都不构成训练预算或数据权限授权。
项目检查与数据处理可以使用受限本地执行，但必须证明不执行训练、不访问受保护最终测试数据。

## 6. 训练状态与业务状态映射

| 外部事实 | Task 处理 |
| --- | --- |
| 已受理 / queued / running | stage waiting_external，保存权威身份与最后观察时间 |
| Job 成功、证据尚未确认 | stage verifying，不立即成功 |
| Job 成功、Run/Artifact/指标与输入校验通过 | stage completed，可接续分析 |
| 明确失败且没有未知写入 | stage failed；Agent 可诊断，重试需新 Attempt 和有效预算 |
| 查询超时、丢失或 404 | operation unknown，Task blocked / 继续对账 |
| 停止请求已发但未确认 | Task cancelling，保持观察 |
| Operation 已完成但结果迟到 | 按 control_epoch 保存观察，不恢复取消任务 |

MLflow Run 的终态和 Ray Job 的成功不完全等价。需要明确哪个是执行终态、哪个是证据终态，
并检查 parent Run、输入、输出与角色一致性，不能用一个 UI 绿色标识代替全部验证。

## 7. 证据和数据隔离

Task 仅保存证据摘要及 API 引用，不复制 MLflow 数据库或对象存储目录。
Worker 通过 Tracking、Artifact、Model Registry API 读取；不打开 `mlflow.db`，
不要求训练客户端持有长期 MinIO 密钥，不解析任意对象 URI 形成 SSRF。

证据至少包含：项目与任务、数据版本和 split、预处理版本、代码/Release digest、
配置、随机种子、环境、执行资源、operation/job/run、指标定义与方向、Artifact digest。
数据清洗产物也记录输入数据身份和处理规则，以便之后训练能引用明确版本。

模型比较只能在数据、split、预处理、指标和评估协议兼容时进行。
搜索与早停只使用训练和验证数据，最终测试集冻结后只做一次最终评估。
不把 test 指标用于反复重规划调参，不向 App 或模型上下文泄露秘密标签和测试样本。

## 8. 审批分层

| 确认或授权 | 拥有者 | 可以证明什么 |
| --- | --- | --- |
| App 工具确认 | Host + 用户交互收据 | 某主体确认了这次具体 UI 操作 |
| 原生工具审批 | Codex 原生权限层 | 某工具或命令在指定范围可执行 |
| Task 持续运行授权 | Task Service + Host 可信请求 | 允许在目标、时间和动作范围内后台接续 |
| 训练授权、资源预算 | 选定治理系统 | 特定项目/角色/输入/期限内可执行训练 |
| 结果审阅 | 用户 + result digest | 审阅了明确版本结果 |
| 模型推广 | 独立治理/Registry 流程 | 可以修改明确生产别名或发布目标 |

任何一层不能隐式替代其他层。Task Service 可以展示与关联批准状态，
不能自签有效训练授权。后续增加在线审批时，治理服务必须验证审批者角色、
不可变请求 digest、预算、有效期和幂等性，返回可查询的权威收据。

## 9. 治理版本与演进

启动时读取 capabilities，与管理员配置的工具集/版本基线核对。
缺失必要能力 fail closed；可选能力禁用并在 UI 说明，不调用不存在的工具。
依赖版本或授权协议升级需重跑训练边界、未知状态、预算和最终测试隔离验收。
历史 Skill 的 `wait_external` 输出不是当前 Host 已实现的调度机制，必须显式转换为本方案的 wait。
