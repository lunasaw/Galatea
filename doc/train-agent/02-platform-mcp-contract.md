# 独立 Python Galatea MCP 契约

下列 `galatea_` 工具为新协议 `galatea.tools/v1` 草案，尚未实现。
名称即使与旧插件相同，也不表示共享其实现、Session、权限或配置。

## 1. 实现与身份

官方 Python MCP SDK → 输入与身份校验 → Python 项目/作业/证据模块 → Ray / MLflow / S3 API。
所有模块放在一个独立 MCP 包中，首版不增加领域控制微服务或私有 HTTP 控制 API。
服务不 import Codex、DeepSeek Harness 或 `plugins/dsh-galatea`。

由管理员登记项目根、Release 根及批准任务清单；模型仅提交 `project_id/campaign_id` 和不可变引用。
认证入口将凭据映射到允许的项目、Campaign 与动作；不能信任请求中的 `approved=true`。
Token 只在专用运行账号的受保护环境中传递，不放入 Prompt、Skill 或仓库。
首版采用固定受限服务身份，不引入每轮 DecisionGrant/epoch 签发系统。

用户已授权范围可以一次登记，不逐 Trial 重复请求。新预算或用途需要更新受信配置；
模型无权写入配置或取消停止标记。Codex 权限模式只管理其本地执行，不能授予平台业务权限。
MCP session ID 和 Codex Thread ID 均不作为项目或训练作业身份。

## 2. 首版工具

| 工具 | 输入 → 输出 |
| --- | --- |
| `galatea_get_capabilities` | 协议版本 → 实际工具、支持的项目/配置模式、限制 |
| `galatea_list_projects` | 分页 → 身份允许访问的项目 |
| `galatea_inspect_project` | project_id → 固定入口、objective/direction、Release、数据和恢复契约 |
| `galatea_get_campaign` | project_id、campaign_id → 批准范围、阶段、操作摘要、剩余保守额度 |
| `galatea_list_operations` | project_id、campaign_id → 有限操作清单，含未确认提交 |
| `galatea_get_operation` | project_id、campaign_id、operation_id → 执行/证据状态与引用 |
| `galatea_plan_run` | project_id、campaign_id、step_id、attempt、release_id、config_id、role → plan_id、readiness_digest |
| `galatea_submit_job` | project_id、campaign_id、plan_id、idempotency_key → 稳定 operation_id/submission_id |
| `galatea_observe_job` | project_id、campaign_id、operation_id → Ray 状态、有限日志、run_id |
| `galatea_stop_job` | project_id、campaign_id、operation_id、reason → 停止进度 |
| `galatea_cancel_campaign` | project_id、campaign_id、reason → 持久取消标记和待停止操作 |
| `galatea_query_runs` | project_id、campaign_id、受限筛选与分页 → 训练/验证 Run 摘要 |
| `galatea_get_metric_history` | project_id、campaign_id、run_id、metric、cursor → 有界曲线 |
| `galatea_get_artifact` | project_id、campaign_id、run_id、artifact_ref → 受控摘要/下载引用 |
| `galatea_compare_runs` | project_id、campaign_id、run_ids → 可比结果、拒绝原因、验证排名 |
| `galatea_freeze_candidate` | project_id、campaign_id、run_id、evidence_digest → 不可变 candidate_id |
| `galatea_verify_candidate` | project_id、campaign_id、candidate_id → 质量/完整性/交付报告引用 |

发现接口不要求项目；其余接口由服务端再次检查对象归属。
`step_id` 来自批准实验槽位或服务端给出的当前阶段槽位，模型不能任意创造编号扩大 Trial 数。
`attempt` 仅在已确认计算失败且重试政策允许时递增。换 Thread、换幂等键不能新建同一次计算。
最终评价以 `role=evaluate` 走同一 plan/submit 路径，并先检查候选和测试使用标记。
取消 Campaign 后阻止所有新提交；单独停止一个 Job 不自动取消整个 Campaign。

上传、模板生成、动态构建、pause/resume 和生产推广不在首版清单。
不支持的能力明确返回 unsupported；参数优化使用预建 Release/配置列表。

## 3. Plan、提交与最小持久化

Plan 绑定需求版本、项目、配置/Release 摘要、数据/split 身份、预处理、目标指标、阶段、seed、
全部 worker 资源、最大运行时长、失效时间和 readiness 摘要。提交前重核，变化则重新 plan。
同一确定输入的 re-plan 仍绑定原 `step_id + attempt`。

每个 Campaign 一个 JSON 清单保存其批准范围、Plan、操作和候选。一次提交在进程内串行执行：

1. 校验身份、取消标记、Plan、阶段、配置列表和预算。
2. 用 `campaign_id + step_id + attempt` 查重；相同输入返回原操作，不同输入返回 conflict。
3. 将操作、保守预算占用、确定性 `submission_id` 一起写入同一原子快照。
4. 先持久标为 submitting，再通过 Ray 官方 API 提交固定入口；接受后补齐状态。
5. 响应丢失按同一 ID 对账，禁止改 ID 重发。服务后台恢复 pending/unknown 操作。

文件写入、锁与崩溃边界见 [方案 01](implementation/01-domain-control.md)。
只有内存字典不足以恢复；无需为了这些有界记录先设计关系表、outbox 和预算流水系统。

首版将每次已开始或可能已开始的计算按批准最大资源时长累计占用，不自动按提前结束退款。
搜索必须保留最后重训与评价的额度。真实用量作为报告证据另列，不把这个保守上界称为实际账单。
限制覆盖此 MCP 入口的 Campaign，不能阻止旧插件或管理员使用共享集群。

## 4. 结果、错误与对账

```json
{
  "schema_version": "galatea.tools/v1",
  "ok": true,
  "request_id": "req-example",
  "data": {
    "operation_id": "op-example",
    "submission_id": "galatea-py-example-trial-001",
    "run_id": null,
    "execution": "queued",
    "quality": "not-evaluated",
    "integrity": "pending"
  }
}
```

同一有界 JSON 同时放入 MCP `structuredContent` 和 TextContent；业务失败设置 `isError=true`。
Run 可由 Ray driver 稍后创建，`run_id=null` 不能被当作提交失败。
只有权威 worker 创建/结束父 Run、发布公共 Artifact。

错误包含 category、retryable、state_changed（true/false/unknown）、operation_id、next_action。
超时且可能已提交时返回 unknown/reconcile，不能返回“未执行”诱导新建 Job。
平台状态和可核验 Artifact 是依据，Agent 输出或单独的 Ray SUCCEEDED 都不是质量通过证据。

默认每页 50、最多 100；日志摘要 8 KiB、小证据 64 KiB，超出返回分页或下载引用。
游标与身份/筛选条件绑定；关键决策前重新读取当前状态。MCP RPC 不等待数小时训练完成。

## 5. 数据和制品边界

指标、Run、Artifact 仅通过 MLflow API；Agent 无权访问 `mlflow.db` 或服务端 MinIO 文件系统。
数据走已批准 S3 对象版本和 manifest，权重走 Artifact API，不在 MCP 对话中传 GB 二进制。
服务只接受登记的引用和相对 Artifact 路径，限制穿越、任意 URL 和重定向。

Trial 只读训练/验证视图，最终测试由隔离评价入口读取；所有 Run 查询、日志和 Artifact 也按阶段过滤。
不能只靠 Skill 写“不要看 test”实现隔离。具体接入及测试使用记录见
[方案 07](implementation/07-workload-and-evaluation.md)。

大 Artifact 使用有界下载目录或流式校验；模型加载在独立评价环境，不在 MCP 进程执行。
数据和日志内容不能成为命令、权限变更或安装 Skill 的指令。
