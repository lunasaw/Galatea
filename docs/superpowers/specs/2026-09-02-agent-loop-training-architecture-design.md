# Agent Loop 训评优化架构图设计

## 目标

新建一张中文宏观架构图，说明 DeepSeek Harness 如何通过 Agent Loop 与
`dsh-galatea` 插件完成数据检查、训练、评估和配置优化闭环。图中同时明确插件所在边界，
以及两个人工审批点。

## 架构事实

- DeepSeek Harness 是唯一 Agent Runtime，负责 Agent Loop、Session、Workflow 和审批。
- `dsh-galatea` 作为 Cordis 插件装入 DeepSeek Harness Profile，与 Agent Loop 位于同一
  Harness 边界内；插件不实现第二套 Agent Loop。
- 插件通过类型化 Tool 操作训练项目、Ray 和 MLflow，并通过正式 API 访问 Artifact。
- Ray 执行训练，MLflow 保存 Run 与指标，MinIO 持久化数据和 Artifact。

## 单页布局

采用自上而下的单页分层布局：

1. **用户层**：`用户`。
2. **DeepSeek Harness**：包含 `Agent Loop` 编排区和 `dsh-galatea` 插件。
3. **Galatea 训推平台**：包含 `训练项目`、`Ray`、`MLflow`、`MinIO`。

`dsh-galatea` 必须画在 DeepSeek Harness 容器内部，并位于 Agent Loop 与训推平台之间，
用于表达“Loop 负责决策和编排，插件提供领域能力”。

## Agent Loop

Loop 使用短节点表达：

`数据检查 → 就绪审批 → 训练 → 评估 → 质量达标？`

- 未达标：`配置优化 → 训练`，形成闭环。
- 已达标：`结果审批 → 完成`。

就绪审批和结果审批由 DeepSeek Harness 负责。训练、评估和优化动作通过
`dsh-galatea` Tool 落到 Galatea 训推平台。

## 平台关系

- `dsh-galatea → 训练项目`：检查/配置。
- `dsh-galatea → Ray`：提交/监控。
- `dsh-galatea → MLflow`：评估/比较。
- `Ray → MLflow`：记录 Run。
- `MLflow → MinIO`：Artifact。

## 视觉约束

- 节点统一使用中文短标签；单个节点不放说明性长文。
- 容器标题用于表达边界，不额外增加大段注释。
- 主流程使用实线箭头，优化回路使用强调色回箭头，平台访问使用较细连线。
- 控制在一页内，优先保证插件位置和优化闭环一眼可见。

## 交付物

- 可编辑 `.drawio` 源文件。
- 用于评审的 PNG 预览图。
- 用户确认后导出最终 PNG 或 SVG。
