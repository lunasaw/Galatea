# 项目 5–9：wechat-persona 技术方案

> 状态：代码与契约已实现；真实数据训练/正式证据仍受治理门阻断，尚未表示项目 5–9 已正式验收。
>
> 日期：2026-09-06
>
> 适用范围：经双方明确授权的微信导出数据、关系记忆 RAG、真实角色风格 LoRA、模型容量对照、
> 本地聊天和动画脚本原型。

本目录把路线图中的项目 5–9 细化为一个可落地、可回滚、可审计的第二阶段项目。它承接项目 0–4
的 Ray/Galatea/MLflow 训练边界，但不会把当前的 `baseline_only`、`uncertain` 或
`experimental_only` 工件升级为正式质量证据。

## 文档导航

| 文档 | 内容 |
|---|---|
| [`design.md`](design.md) | 总体架构、状态机、数据流、身份与安全边界 |
| [`implementation-plan.md`](implementation-plan.md) | `wechat-persona` 项目骨架、接口、任务顺序和测试策略 |
| [`runbook.md`](runbook.md) | 从授权检查到 RAG、LoRA、扩容和原型发布的操作顺序 |
| [`acceptance-checklist.md`](acceptance-checklist.md) | 项目 5–9 的逐项验收门和证据位置 |
| [`project-5-data-engineering.md`](project-5-data-engineering.md) | 导入、脱敏、session、人工审核、撤回和 SFT 导出 |
| [`project-6-rag.md`](project-6-rag.md) | 记忆卡、BM25/embedding、召回评测和删除验证 |
| [`project-7-real-role-lora.md`](project-7-real-role-lora.md) | 真实角色 LoRA、五组对照、盲测和隐私/依赖门禁 |
| [`project-8-capacity-qlora.md`](project-8-capacity-qlora.md) | 0.8B→1.7B→4B 的容量实验和 QLoRA 兼容性隔离 |
| [`project-9-local-prototypes.md`](project-9-local-prototypes.md) | 本地聊天 CLI、事件卡和动画脚本生成器 |
| [`schemas/`](schemas/) | 授权、消息、候选、记忆、事件卡和实验 manifest 契约 |

## 当前状态和硬阻断

现有 [`2026-09-05-wechat-dataset-processing-plan.md`](../2026-09-05-wechat-dataset-processing-plan.md)
记录了真实数据的当前状态：授权尚未在流水线核验、5,000 条候选全部为 `uncertain`、正式
`datasets/{train,validation,test}.jsonl` 为空。因此：

- 项目 5 只能继续做导入契约、脱敏、审核工具和删除演练；不能导出正式 SFT。
- 项目 6 可以用合成/公开 fixture 验证检索代码，但不能把未确认的真实记忆建成可服务索引。
- 项目 7 的真实角色 LoRA 保持 `formal_training_eligible=false`，直到授权、审核、隐私和
  数据集门禁全部通过。
- 项目 8 只能先完成配置、环境和 forward-only 兼容性检查；不因“模型更大”自动启动训练。
- 项目 9 只能用合成或已放行的脱敏事件卡；不得接入未审核聊天正文、声音或肖像。

## 统一执行原则

任何会更新参数、遍历真实数据形成训练证据、生成 adapter/checkpoint、创建持久评测 Run 或访问
最终测试集的动作都属于 Training Run。对于声明 `executionBackend: ray` 的项目，唯一允许的路径是：

```text
Dataset Snapshot
  -> consent/role authorization
  -> immutable Release
  -> Galatea readiness and evidence-bound authorization
  -> fixed Ray Driver
  -> Driver-owned MLflow Run
  -> train + validation only
  -> Artifact API round-trip
  -> candidate freeze
  -> one-time test claim
  -> human/safety review
  -> explicit promotion (separate action)
```

本方案把“状态、证据、可撤回性”作为一等产物。任何失败、撤回或协议变更都会创建新的数据/Run
身份，不覆盖已有成功工件。
