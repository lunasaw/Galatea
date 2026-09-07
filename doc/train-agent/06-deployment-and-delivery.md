# 独立部署与实施路线

## 1. 源码和发布归属

更新：2026-09-07。首版源码已落地为分别发布的目录；本地验证见
[验证记录](09-local-validation.md)，实际安装、恢复和验收命令见
[端到端手册](08-e2e-integration-runbook.md)。真实部署保持待验收。

| 单元 | 源码位置 | 发布依赖 |
| --- | --- | --- |
| Python Galatea MCP | Galatea 的 services/galatea-mcp | Python、官方 MCP SDK、Ray/MLflow/S3 客户端 |
| Python Runner + Codex adapter | agents/training-agent-runtime，独立 wheel | Python、另行验收的 openai-codex、MCP 客户端、发布的 JSON Schema |
| Skill 包 | packages/galatea-training-skills，独立归档 | SKILL.md、自包含参考、版本清单 |
| Workload | Galatea 的 train-model/<project-name> | 该项目自己的环境、入口、配置和测试 |
| 既有 DeepSeek plugin | Galatea 的 plugins/dsh-galatea | 保持当前内容、依赖、接口与能力 |

没有 training-control 微服务、共享 TS contracts 包、数据库迁移目录或旧插件切换任务。
MCP 内部普通 Python 模块即可完成项目、作业、证据和少量状态管理。
协议以 JSON Schema 随 MCP 发布，Runner/Skill 消费已发布制品，不 import 平台内部 Python 模块。

## 2. 部署拓扑

远端现有 Ray、MLflow、MinIO 继续使用各自服务和持久化。
新增 MCP 独立账号/环境/本地状态卷；另一个账号或主机运行 Runner、Codex、Skill 和会话卷。
新增 Python 依赖安装在各组件专用环境，不改造现有平台或 workload 环境来承载 Agent。

| 单元 | 网络与状态 |
| --- | --- |
| MCP | 访问官方后端 API；入口使用受控 HTTPS；写少量任务 JSON 与测试使用标记 |
| Codex worker | 访问模型服务和受限 MCP；写自己的工作区和原版会话目录 |
| Runner | 用 MCP 查询状态，调用 SDK；维护本地 JSON、锁和有限日志 |
| 训练/评价入口 | 运行批准 Release；按角色得到数据和 Artifact 权限，隔离 holdout |

首版仅一个活跃 Campaign 和串行作业。与旧插件共享资源时，独立提交前缀、任务来源和证据来源；
MCP 只计自己入口的授权预算。若需要全局配额或不同信任方隔离，在部署层分配专用资源。
既有插件不中断、不改为新 MCP 客户端、不关闭它的写能力。

## 3. 实施顺序

| 阶段 | 工作 | 退出条件 |
| --- | --- | --- |
| P0 Python 接口验证 | 锁定 openai-codex/配套 Runtime、MCP SDK、Python；fake MCP + Skill + SDK smoke | 真实发布组合支持 start/run/resume/output_schema |
| P1 Python MCP | 独立注册、核心工具、API 适配、文件提交记录和幂等 | 普通 MCP 客户端可检查/提交/观察；无 Codex/Harness 依赖 |
| P2 Runner 与 Skill | 文件状态、进程监督、等待/恢复、有限决策 | 训练等待不调用模型；崩溃后找到原 Job |
| P3 一个 workload 闭环 | 固定配置、验证选优、干净重训、隔离最终评价、Artifact 交付 | 有明确预算的最小真实闭环通过 |
| P4 新数据与 LoRA | 上传、不可变配置/构建、模型目录、业务评价 | 按开放的能力分别验收，不把 Toy smoke 当产品完成 |
| P5 规模扩展（按需） | 并发、多实例、事件推送和配额 | 先提出明确规模目标再选择数据库/队列和迁移设计 |

如果配置内嵌 Release，自动改变参数前先实现新的不可变 Release；首版可先用预建列表。
八份 [实施工作包](implementation/README.md)拆解职责，不代表需要部署八个服务。

## 4. 部署检查

1. 通过只读 API 重新核对目标主机服务、版本、数据、Release 和资源；历史快照不当当前状态。
2. 创建专用账号、Python 环境和受保护本地卷，固定依赖锁/二进制与 Skill 摘要。
3. 登记新 MCP 的项目、受限身份和已批准 Campaign；不导入旧插件 Session 或伪造历史授权。
4. 先验证 stdio/local fake，再验证实际 HTTPS 身份、initialize、tools/list、只读调用。
5. 独立 Agent 环境安装 Skill 与原版 Codex，验证环境隔离、工作区、会话持久化和恢复。
6. 故障测试使用临时目录和 fake 后端；实际训练使用专门批准的小预算，不干扰既有 Job。

新增 unit 模板使用固定账号、工作目录、EnvironmentFile 和私有可写目录；
主机级 CPU/内存限制需部署者按实际服务预算配置，模板未默认设置这些限额。
MCP/Runner 不通过 Requires 把 Ray、MLflow 或 MinIO 的生命周期绑定到 Agent。
worker 单独执行组由 supervisor 管理；重启前先清理孤儿 app-server，再恢复任务。
MCP/Runner unit 模板已创建，尚未安装。目标 Linux 上按仓库规则执行 `systemd-analyze verify`。

联网先直连，失败才按本机配置使用代理；不要把开发机 loopback 代理地址复制为远端配置。
真实主机、私有端点、凭据不写入公共样例。

## 5. 验收与回退

关键验收：无 Codex 环境 MCP 正常；无 Galatea checkout 的 Agent 可使用发布 Skill/MCP；
插件目录内容不变；单写锁生效；提交丢响应后不重复 Job；等待无模型调用；
未知状态不退款重发；失败/中断不当成功；最终测试不用于调参；Artifact 可下载加载；未授权不推广。

分别备份 MCP JSON 状态、测试使用标记、Runner JSON 和 Codex 自有会话卷。
MLflow/MinIO 沿用既有一致备份流程，客户端恢复验证通过 API。
回退先停止新增 Turn/提交，保留已有操作 ID、观察和超时检查，再切回兼容版本。
文件 Schema 升级保留旧快照并显式转换，不能清空状态重建所有 Job。
旧 DeepSeek 路线始终独立，不作为新任务自动重发或回滚入口。
