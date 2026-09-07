# 现状与 Python 接入依据

核对日期：2026-09-06。本地 Galatea：`7ef8377`；本地 Codex 源码：`459a79e`。
本文的 SDK 结论来自本地源码，不代表已安装 Python 发布包或远端完成兼容性验收。

## 1. DeepSeek 插件与新方案独立

现有 [dsh-galatea](../../plugins/dsh-galatea/README.md) 是 DeepSeek Harness/Cordis 插件。
[注册代码](../../plugins/dsh-galatea/src/index.ts) 注入 tools、approval、permissionPresets、
sessionProjections、systemPrompt；[工具包装](../../plugins/dsh-galatea/src/tools/index.ts)
和 [项目选择](../../plugins/dsh-galatea/src/session-selection.ts) 使用 Harness Agent/Session。
这些是旧工作流的实现，不是新 Python 架构需要消除的代码缺陷。

| 资产 | 新方案如何对待 |
| --- | --- |
| `plugins/dsh-galatea/src/**/*.ts` | 保持不动；只在理解旧业务规则时参考，不抽取、不运行、不作为依赖 |
| `plugins/dsh-galatea/tests/` 与 harness-tests | 继续验证旧能力，不把它们通过当作新 MCP 的证据 |
| [旧 Agent 文档](../agent-galatea.md) | 独立旧路线，保持有效 |
| `train-model/<project>/` | 经契约检查后使用项目本来的 Python 入口和不可变 Release |
| 仓库 MLflow / Ray 方法资料 | 参考方法和数据契约；新包自行发布所需参考资料 |

新 MCP 的注册表与状态目录独立于旧插件。两条路线的 Run 使用不同来源标识和提交 ID 前缀；
默认使用独立新 Campaign。若共用 Ray 集群，各自预算只约束各自入口，不能声称覆盖对方的作业。
需要全局资源隔离时在部署层划分资源或集群，不以改动旧插件为前置条件。

## 2. Python Codex SDK 已有实际源码

[Python SDK README](../../../codex/sdk/python/README.md) 与
[API reference](../../../codex/sdk/python/docs/api-reference.md) 定义了 `openai_codex`：

| 所需能力 | 真实接口 | 本地依据 |
| --- | --- | --- |
| 启动原版 Runtime | `Codex(CodexConfig(...))` / `AsyncCodex` | [api.py](../../../codex/sdk/python/src/openai_codex/api.py) |
| stdio app-server | SDK 启动 `codex app-server --listen stdio://` | [client.py](../../../codex/sdk/python/src/openai_codex/client.py) |
| 新建/恢复会话 | `thread_start` / `thread_resume(thread_id)` | api.py |
| 一次决策、结构化输出 | `Thread.run(..., output_schema=...)` | api.py |
| 进度/中断 | `Thread.turn()` → `TurnHandle.stream/run/interrupt` | api.py |
| 最终状态与用量 | `TurnResult.status/final_response/usage` | [_run.py](../../../codex/sdk/python/src/openai_codex/_run.py) |
| 显式 Skill 输入 | `SkillInput(name, path)` | [_inputs.py](../../../codex/sdk/python/src/openai_codex/_inputs.py) |
| 权限模式 | `ApprovalMode.deny_all` / `auto_review`，`Sandbox` | [_approval_mode.py](../../../codex/sdk/python/src/openai_codex/_approval_mode.py) |

文档中原来的 TS `startThread/resumeThread/runStreamed` 主线已替换。
Python SDK 直接接原版 app-server，无需自写 JSON-RPC 协议、修改 Core 或内嵌 DeepSeek Harness。
SDK 文档声明安装 `openai-codex` 会匹配 `openai-codex-cli-bin`；实际部署仍须锁定并验证发布版组合，
不能只凭本地 checkout 宣称任意已发布版本都具有相同接口。

两个容易误用的细节：Python `CodexConfig.env` 合并 `os.environ`，不是环境白名单；
Python Turn 通知采用 `turn/completed` 等 app-server 名称，不是 TS exec JSONL 的 `turn.completed`。

## 3. 平台后端采用方式

| 平台 | 首版实现方式 | 边界 |
| --- | --- | --- |
| Ray | Python 官方 `JobSubmissionClient` / 必要的 Jobs、State API | 提交固定 Release，查状态和停止；不引入第二套调度器 |
| MLflow | 官方 `MlflowClient` 与 Artifact API | Run、metric history、模型/产物证据；不直读数据库 |
| MinIO | S3 兼容 API 与 Python 客户端 | 已登记数据和对象引用；模型产物继续走 MLflow Artifact 代理 |
| MCP | 官方 Python MCP SDK | 标准 transport、tools 与结构化结果；Python 内部模块完成校验 |

参考入口：[Ray Jobs SDK](https://docs.ray.io/en/latest/cluster/running-applications/job-submission/doc/ray.job_submission.JobSubmissionClient.html)、
[MLflow Python API](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.client.html)、
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)。
这些是后续版本验收的资料入口，不是本次重新联网验证的能力快照。

MLflow/MinIO 官方 MCP 可在能力适合时作为独立诊断工具验证；它们是否覆盖所需工具不影响主线。
首版不要求安装它们，也不透传任意管理工具绕过 Galatea 的项目范围与提交约束。

## 4. Workload 与远端证据范围

`ray-cats-and-dogs`、`ray-handwritten-digits`、`ray-kaggle-house-prices` 可作为成熟契约的接入候选。
[llm-lora-playground](../../train-model/llm-lora-playground/README.md) 具有项目实验代码，
其 [manifest](../../train-model/llm-lora-playground/galatea.project.yaml) 仍需按目标治理合同检查。
不把 Toy LoRA、低 loss 或脚本存在当作真实业务验收完成。

目录旧稿记录过 gc50 的 Ray 2.53.0、MLflow 3.14.0 及服务健康快照。
本次没有重新连接远端，这些历史记录不再作为当前可部署结论。
部署前通过服务 API 重新核对版本、身份、资源、数据与 Artifact 恢复，不读取平台运行数据库。
