# 现状与 Python 接入依据

> **阅读范围（2026-10-16）**：第 1 节同步当前组件归属；后续 Python SDK 源码与部署资料是历史依据。
> 独立 Runner 已删除。当前通用 Host 通过配置连接 Skill、外部 MCP 和可选 Provider，
> 不内置 Galatea Service；历史 SDK 配对不能替代当前 Runtime 协议验收。
> 当前边界见 [总体架构](../agent-galatea.md)、[Host 文档](https://github.com/lunasaw/open-codex/blob/b5d9a0d26b14a8d8c0c308b6229bba866ac4d70a/docs/README.md)。

核对日期：2026-09-06。本地 Galatea：`7ef8377`；本地 Codex 源码：`459a79e`。
本文的 SDK 结论来自本地源码，不代表已安装 Python 发布包或远端完成兼容性验收。

## 1. 当前源码归属

2026-10-16：Agent Runtime 统一为 Codex；Host 已移除内置 Galatea Adapter 和业务依赖，
通过原生 MCP 或可选通用 Provider 接入外部能力。当前实现以
[`agents/codex-app-server`](https://github.com/lunasaw/open-codex/blob/b5d9a0d26b14a8d8c0c308b6229bba866ac4d70a/README.md)为准。
本文后面的 Python SDK 源码核对是历史依据，不能替代当前 app-server 协议与版本验收。

| 资产 | 当前归属 |
| --- | --- |
| `agents/codex-app-server/` | 原生 app-server Host、Console、Dynamic Tool Registry 与恢复边界 |
| `services/galatea-mcp/` | Python 平台领域服务、MCP transport、Campaign 与证据约束 |
| `packages/galatea-governance/` | 项目 YAML 固定入口需要的独立 Release、证据与提交校验 |
| `train-model/<project>/` | 项目自身入口、环境、配置、测试和不可变 Release |
| `.codex/skills/`、`packages/galatea-training-skills/` | 仓库工作流和可独立发布的训练方法 |

共享 Ray 不提供全局预算隔离；需要全局配额或不同信任方隔离时，在部署层分配专用资源。
业务状态只能有一个 writer。当前架构见 [Codex 与 Galatea](../agent-galatea.md)。

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
Python SDK 直接接原版 app-server，无需自写 JSON-RPC 协议、修改 Core 或引入其他 Agent Runtime。
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
