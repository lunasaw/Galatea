# Galatea Agent 架构设计索引

> 状态：设计草案。本文档集描述下一步要在 `agent/` 下落地的 Agent Runtime、阶段契约、工具治理和 Claude Agent SDK 使用规范。除非明确标记为“当前已有”，不要把这些设计视为已经部署的常驻服务。

Galatea 当前平台已经具备 JupyterLab、Ray、MLflow、MinIO、项目化训练目录和仓库级 Codex Skill。新的 Agent 架构目标是在不破坏这些平台边界的前提下，把“数据、训练、推理”三个阶段包装成可审计、可恢复、可人工接管的自主工作流。

## 文档结构

| 文档 | 内容 |
| --- | --- |
| [`current-agent-architecture.md`](current-agent-architecture.md) | 当前 Agent 目标架构、组件分层、阶段自治边界和执行拓扑。 |
| [`python-agent-architecture.md`](python-agent-architecture.md) | Galatea Python Agent 实现蓝图，包含 runtime、工具服务器、Ray/MLflow adapter 和 workflow 模式。 |
| [`claude-sdk-development-guidelines.md`](claude-sdk-development-guidelines.md) | 结合 Claude Agent SDK 源码与 README 的开发规范、权限、hooks、sessions、structured output。 |
| [`stage-contracts-and-tools.md`](stage-contracts-and-tools.md) | 数据、训练、推理三个阶段的输入输出契约、工具清单、Ray/MLflow/MinIO 集成规则。 |
| [`sdk-architecture-comparison.md`](sdk-architecture-comparison.md) | Claude Code、Claude Agent SDK 与 Galatea Python 实现之间的概念映射。 |
| [`implementation-roadmap.md`](implementation-roadmap.md) | 从只读 POC 到生产治理闭环的渐进落地路线。 |
| [`claude-code-architecture.md`](claude-code-architecture.md) | 已有 Claude Code 架构源码梳理，作为底层 agent loop 背景材料。 |

## 合并说明

本目录已把早期的 Python Agent 草案和 SDK 对比草案收敛为统一文档集：

- `python-agent-architecture.md` 保留实现蓝图，但去掉默认开放 `Bash`、`acceptEdits`、自动注册模型等不符合平台治理的示例。
- `sdk-architecture-comparison.md` 保留 Claude Code 到 Python SDK 的概念映射，但把实现权威收敛到本索引列出的阶段契约和 SDK 开发规范。
- `current-agent-architecture.md` 与 `stage-contracts-and-tools.md` 是三阶段 Agent 设计的主文档。

## 一句话决策

- Claude Agent SDK 负责“决策、工具调用、代码维护、会话和权限控制”。
- Ray 负责“大数据处理、分布式训练、批量推理和可恢复执行”。
- MLflow 负责“实验、指标、Artifact、模型注册和治理证据”。
- MinIO 负责“不可变数据、runtime release、checkpoint、模型和报告持久化”。
- Agent 不直接读写 `mlflow.db`，不直接拿 MinIO 长期密钥，不用裸 Bash 做平台动作。

## 当前仓库约束

设计必须遵守仓库根 `AGENTS.md` 和平台 README 的现有约束：

- `train-model/<project-name>/` 承载项目代码、配置、环境和项目测试。
- Notebook 只用于探索、展示和短 smoke，不承载正式长任务。
- 正式训练通过 Ray Job、Ray Train 或参数化脚本执行。
- 数据身份、split、预处理、代码、环境、资源和超参数必须可追踪。
- MLflow 访问必须通过 Tracking、Artifact 和 Registry API。
- Registry alias 变更需要显式 review 或 promotion action。
- `platform-data/` 是运行状态，不是源码或客户端集成接口。

## 推荐落地形态

```text
agent/
├── doc/                          # 本文档集
└── src/galatea_agent/            # 后续实现位置
    ├── runtime/                  # ClaudeSDKClient 封装、session、message collection
    ├── agents/                   # DataAgent / TrainingAgent / InferenceAgent prompts
    ├── tools/                    # in-process SDK MCP tools
    ├── schemas/                  # Pydantic/JSON Schema stage contracts
    ├── policies/                 # permissions, approval, budgets, quality gates
    └── hooks.py                  # PreToolUse/PostToolUse/Stop 等确定性治理
```

首版不建议直接做“完全自主训练到上线”。更稳的顺序是：

1. 只读分析和计划生成。
2. 数据阶段小预算 Ray Data job。
3. 训练阶段 `--check-config` 和 `--plan`。
4. 1 epoch smoke train。
5. champion 候选评估，但 promotion 只生成审批请求。
