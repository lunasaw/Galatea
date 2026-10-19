# LLM 训练文档中心

本目录只维护跨项目的 LLM 微调治理方法和可复用协议。具体 workload 的配置、命令、Schema、
运行手册和历史材料归属 `train-model/<project>/`，避免平台层形成第二套项目规范。

[`2026-09-10-governed-llm-finetuning-complete-guide.md`](2026-09-10-governed-llm-finetuning-complete-guide.md)
是唯一的从零执行入口：从新机器安装、数据与模型准备、项目合同、Release、Campaign、受治理训练、
MLflow/Artifact 验收到 Trial、Champion、test-once、发布和恢复，都按这一份指南顺序完成。其他文档
不是前置阅读，只在修改具体项目或专题协议时查阅。

文档分为：

1. **总指南**：跨模型、跨微调方法、跨执行后端的治理不变量。
2. **项目主文档**：位于项目自己的 `docs/`，描述阶段顺序、配置映射、运行边界和停止条件。
3. **专题契约**：评测、外部记忆等可复用协议。
4. **历史追溯**：已合并的项目阶段文档不保留副本，通过 Git 历史追溯。

## 唯一执行入口与可选专题

| 类型 | 文档 | 用途 |
| --- | --- | --- |
| 唯一执行入口 | [`2026-09-10-governed-llm-finetuning-complete-guide.md`](2026-09-10-governed-llm-finetuning-complete-guide.md) | 新机器完整流程、治理闭环、证据契约和迁移方法 |
| 可选项目维护 | [`llm-lora-playground/docs/README.md`](../../train-model/llm-lora-playground/docs/README.md) | Toy SFT/LoRA、比较、Artifact 和旧/现行接口边界 |
| 可选项目维护 | [`wechat-persona/docs/README.md`](../../train-model/wechat-persona/docs/README.md) | 私有数据、RAG、角色 LoRA、容量实验和现行 MCP V1 |
| 可选专题 | [`fine-tuning-evaluation-protocol.md`](fine-tuning-evaluation-protocol.md) | Base/Prompt-only/Adapter、盲测和 test-once 的维护契约 |
| 可选专题 | [`memory-grounded-evaluation-protocol.md`](memory-grounded-evaluation-protocol.md) | 外部记忆、检索、证据支持、删除和 owner 隔离 |
| 可选项目维护 | [`wechat-persona/docs/data-preprocessing.md`](../../train-model/wechat-persona/docs/data-preprocessing.md) | 微信项目导入、脱敏、session、候选和数据血缘 |

## 当前权威关系

- 治理规则以 [governed-training-workflow](../../.codex/skills/governed-training-workflow/SKILL.md) 和总指南为准。
- 项目实际接口、配置字段、命令和测试以 `train-model/<project>/` 中的代码与
  `galatea.project.yaml` 为准。
- 每个项目的 `docs/README.md` 负责解释“先做什么、何时停止、产出什么证据”；被其完整吸收的旧
  `design.md`、`runbook.md`、`implementation-plan.md` 和 `acceptance-checklist.md` 不保留工作树副本。
- 若文档之间出现冲突，按以下优先级处理：

  ```text
  项目代码/测试与项目契约
    > governed-training-workflow / evidence contract
    > 2026-09-10 总指南
    > 项目 docs/README.md
    > Git 中的历史设计稿、旧运行手册和旧实施计划
  ```

## 当前状态（2026-09-10）

| 范围 | 状态 | 说明 |
| --- | --- | --- |
| `llm-lora-playground` | implementation complete | 合成 Toy、LoRA、评测、Artifact、Ray 边界和测试已实现 |
| `wechat-persona` 代码/契约 | implementation complete | 数据治理、RAG、LoRA、恢复和原型接口已实现/测试 |
| 真实私有数据正式 SFT | `formal_evidence_blocked` | 仍需 consent、人工审核、formal snapshot、Release 和授权绑定 |
| 生产模型 Promotion | 未执行 | 必须经过 candidate freeze、test-once、人工/安全审查和独立 promotion |

“代码已实现”不等于“训练已授权”；合成、private、experimental、smoke 或 `promotable=false` 都不能
绕过声明的执行后端。

## 项目主文档的固定结构

新增或迁移其他模型时，项目主文档应至少回答：

- 任务、模型/processor revision、微调方法和主指标是什么；
- 数据、授权、split、隐私和撤回门在哪里；
- check/plan、Release、后端 Driver 和 MLflow/Artifact 如何串联；
- smoke、baseline、Trial、Champion、test-once 和 promotion 如何区分；
- 失败、重试、恢复、超时、Artifact 不一致时如何停止；
- 哪些组件可替换，哪些治理字段必须保持；
- 当前状态、阻断理由和下一步放行条件是什么。

## 文档归属规则

| 内容 | 唯一归属 | 不应放置的位置 |
| --- | --- | --- |
| 跨模型治理、证据、晋级和迁移方法 | `doc/train-llm/` | 某个项目的历史目录 |
| 跨项目评测或数据协议 | `doc/train-llm/` | 复制到多个项目后独立修改 |
| 项目命令、配置映射、状态和运行手册 | `train-model/<project>/docs/` | `doc/train-llm/` 的新日期目录 |
| 项目 JSON Schema | `train-model/<project>/schemas/` | 文档目录或平台公共目录 |
| 项目辅助脚本 | `train-model/<project>/scripts/` | `doc/` |
| 已被合并的项目历史设计和实施记录 | Git 历史 | 工作树中的重复副本 |

已合并的历史设计稿、阶段计划和旧运行手册不在工作树保留副本，通过 Git 历史追溯。新增模型项目时，
不要向 `doc/train-llm/` 添加项目专属设计稿。

## 维护规则

修改模型、tokenizer/processor、数据 manifest、split、预处理、评估协议、Artifact schema、资源、
后端或质量门时，先更新项目代码/配置和测试，再同步项目 `docs/README.md`。只有跨项目不变量变化时
才更新总指南；不要只改旧历史文档或 Registry alias 来改变历史 Run 的含义。
