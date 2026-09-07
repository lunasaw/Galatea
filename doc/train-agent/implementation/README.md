# Python 解耦架构实施工作包

更新：2026-09-07。以下是原实施工作包，当前实现状态见
[开发记录](09-development-record.md)及[本地验证](../09-local-validation.md)；未部署服务或运行真实训练。
[总览](../README.md)确定架构，本目录细化实现；路径沿用是为了保持链接，不表示沿用旧 TS 方案。

## 1. 共同约束

- 新主线：Python Galatea MCP + 原版 Codex + 独立 Skill 包 + Python Runner。
- `Galatea/plugins/` 所有源码、配置、测试、依赖和现有功能保持不动，无迁移步骤。
- 不修改 Codex Core，不新建自研 Agent，不依赖 DeepSeek Harness。
- 首版不新增数据库、ORM、SQL migrations、队列或 training-control 服务。
- 单 MCP 写进程、单 Runner、一个活跃 Campaign、串行受信 Job，本地持久卷与进程锁。
- 通过官方 Ray、MLflow、S3 API 使用平台；不直读 MLflow 数据库或 MinIO 服务端目录。
- 新服务使用各自 Python 环境；workload 依赖和测试留在对应 train-model 项目。
- 预算内继续执行，正式训练/模型调用沿用明确授权；生产 Alias 单独处理。

## 2. 发布单元

| 单元 | 拟定根目录 | 依赖边界 |
| --- | --- | --- |
| Python MCP | Galatea/services/galatea-mcp | Python MCP SDK、官方平台客户端 |
| Runner + adapter | 独立 training-agent-runtime | openai-codex、MCP client、发布 Schema |
| Skill 包 | 独立 galatea-training-skills | SKILL.md、自包含 references、验证场景 |
| Workload | Galatea/train-model/<project> | 项目环境、入口和不可变 Release |

MCP 包的公共契约置于 `contracts/*.schema.json`，随制品发布；不用另建共享 TS 类型包。
Runner 和 Skill 下载/安装固定版本契约，不 import Galatea 内部源码。
MCP 包内划分 projects、jobs、evidence、state 等模块即可，不再拆控制微服务。

## 3. 工作包与顺序

| 工作包 | 输出 | 依赖 |
| --- | --- | --- |
| [01 最小状态与提交约束](01-domain-control.md) | 文件清单、单写锁、Plan/操作、保守预算 | 共同契约 |
| [02 Python MCP 与后端](02-mcp-and-backends.md) | MCP 服务与官方 API 适配 | 01；协议骨架可用 fake |
| [03 Python Codex adapter](03-codex-adapter.md) | 原版 SDK 启动、恢复、结果和取消 | Python SDK 源码与 fake MCP |
| [04 独立 Skill 包](04-skill-bundle.md) | 方法/分析技能、自包含发布包 | 工具契约；03 验证真实发现 |
| [05 Python Runner](05-runner.md) | 等待、对账、文件调度、worker 监督 | 01–04 |
| [06 数据和 Release](06-data-and-releases.md) | 既有引用验证；后续上传/变体/构建 | 02，动态构建按需开放 |
| [07 Workload 与交付](07-workload-and-evaluation.md) | 单项目训练/评价闭环 | 01/02/06 的既有数据切片 |
| [08 部署验收](08-deployment-and-acceptance.md) | 锁定版本、独立环境、故障和回退证据 | 对应已开放能力 |

最短开发路径：03 用 fake 验证 Python 接口；01/02 跑通无模型提交；04/05 接回决策；
07/08 验收一个固定 workload。06 的全新数据入口和真实 LoRA 是扩展。
八个包是评审边界，不要求一次实现所有能力或部署八个服务。

## 4. 共同对象与命名

| 对象 | 最低字段 | 保存者 |
| --- | --- | --- |
| Campaign | id、request_revision、approved_scope、stage、budget、cancelled | MCP 单任务 JSON |
| Plan | id、step_id、attempt、输入摘要、resources、deadline、expiry | 同一 Campaign JSON |
| Operation | id、plan_id、step_id/attempt、submission_id、cluster_id、run_id、状态、证据引用 | 同一 Campaign JSON；Ray/MLflow 是外部事实 |
| Candidate | candidate_id、Run/配置/Release/验证证据摘要 | 同一 Campaign JSON |
| Evaluation marker | holdout_identity、campaign/candidate/operation 身份 | 受保护独占创建文件，跨 Campaign 保留 |
| Runner state | thread_id、turn_id、decision_seq、状态、引用、检查时间、用量快照 | Runner 自有 JSON |
| Turn proposal | campaign/revision/decision_seq、action、operation/evidence/report 引用 | 模型输出，经 Runner 校验 |

外部字段使用 snake_case。资源内部转换成整数 CPU/加速器秒和字节；
摘要固定为带 schema_version 的规范化 JSON SHA-256，提供固定测试向量，拒绝 NaN/非规范输入。
保留历史项目 evidence 的原算法，新增协议摘要不冒充旧 evidenceDigest。

每份工作包按接口和失败场景写窄测试，再实现最小模块，运行明确测试并评审。
测试计划不等于本次测试结果；后续实现可以用 executing-plans 在同一任务逐项执行。
