# AI 女友小模型学习与原型路线设计

> 状态：设计稿，待用户确认后再拆分实施计划
>
> 日期：2026-09-05
>
> 适用仓库：Galatea 训练平台
>
> 核心决策：从 0.6B 小模型开始，用多个可独立验收的小项目掌握完整流程；不从 4B 起步。

## 1. 结论摘要

本项目不把“AI 女友”理解为一次大模型微调，而是拆成四种能力，并分别验证：

1. **基础对话能力**：由开源指令模型提供。
2. **表达风格和角色稳定性**：先由 Prompt 验证，再用 LoRA 学习。
3. **关系事实与共同回忆**：通过本地 RAG 检索，不写死在 LoRA 权重里。
4. **从认识到相爱的叙事能力**：从聊天中提取经过授权的事件卡，再生成人工可修改的动画脚本。

推荐模型阶梯为：

```text
Qwen3-0.6B（跑通流程）
        ↓ 指标确有提升需求
Qwen3-1.7B（验证容量收益）
        ↓ 0.6B/1.7B 明显达到上限
4B 级模型 + QLoRA（学习量化微调）
```

第一阶段使用 [Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B) 和 BF16 LoRA。它约 0.6B 参数、支持中文和 32K 上下文，适合快速重复实验；训练和推理时关闭 thinking 模式。现有约 48 GB 显存足以让首个项目避免 QLoRA 的量化兼容性复杂度。后续可用 [Qwen3-1.7B](https://huggingface.co/Qwen/Qwen3-1.7B) 做容量对照，4B 级模型只作为进阶项目。

整个路线分成两个训练项目：

- `train-model/llm-lora-playground/`：只用合成或公开数据，学习推理、SFT、LoRA、MLflow、Ray 和恢复流程。
- `train-model/wechat-persona/`：在双方明确授权后，处理真实微信聊天、构建本地 RAG、训练角色 LoRA、评估隐私与角色质量。

先完成 `llm-lora-playground`，再接触真实聊天记录。这样即使第二个项目因数据授权或导出格式暂停，第一阶段的学习成果仍然完整可复用。

## 2. 项目目标

### 2.1 学习目标

完成路线后，应能独立解释并操作：

- tokenizer、chat template、上下文长度和生成参数；
- SFT 样本如何构造，哪些 token 参与 loss；
- LoRA 的 rank、target modules、学习率、过拟合与 adapter 合并/加载；
- 训练集、验证集和最终测试集的正确用途；
- MLflow 中如何比较可复现实验，MinIO 中如何保存受控工件；
- 单 GPU 上如何用 Ray Job 调度、恢复和追踪训练；
- Prompt、RAG、LoRA 分别应该解决什么问题；
- 角色一致性、记忆命中、隐私泄漏和不健康依赖倾向如何评估。

### 2.2 原型目标

原型最终提供两个互相隔离但共享数据治理的入口：

- **本地陪伴对话原型**：明确标注为 AI；能够保持约定人设、检索获准的共同回忆，并在越界问题上安全拒答。
- **动画脚本生成器**：将“认识—熟悉—暧昧—确认关系”等事件卡组织为 3–5 分钟动画脚本初稿，输出场次、画面、对白、旁白和情绪节奏，最终由人审核。

### 2.3 非目标

首轮不包含：

- 从零预训练基础模型；
- 破解或绕过微信加密数据库；
- 语音克隆、换脸、数字人直播或真实人物肖像生成；
- 微信自动登录、自动发消息或代替真人维持关系；
- 面向公网的多用户产品、付费系统或大规模在线服务；
- 自动把模型注册为生产 Champion；
- 在单张 GPU 上为了“分布式”而进行没有收益的多进程数据并行。

## 3. 必须先满足的边界条件

真实聊天属于双方共同形成的高度敏感数据。进入 `wechat-persona` 项目前必须同时满足：

1. 聊天双方均为成年人，并对训练目的、保存位置、可见范围和删除方式作出明确授权。
2. 如果目标角色模拟其中一人，该人需额外明确同意其语言风格被模型学习。
3. 语音、照片、视频、定位和通讯录不因“聊天已授权”而自动获得授权；每种模态单独确认。
4. 原始聊天只保存在受控本地存储中，不提交 Git，不上传 Kaggle，不发送给第三方在线模型。
5. 身份证号、银行卡、密码、令牌、精确地址、公司秘密、第三方隐私等内容在进入训练集前删除或替换。
6. 对话界面始终说明“这是 AI 生成内容”，不得暗示真人正在回复。
7. 产品不得鼓励用户疏远现实关系、宣称排他占有、利用内疚留存用户，或把模型包装为心理治疗替代品。
8. 任一授权方撤回授权时，能够定位并删除原始数据、派生数据、向量索引、相关 adapter 和受控备份。

如果这些条件不成立，只能继续使用合成数据完成学习项目，不能进入真实数据阶段。

## 4. 为什么不直接微调 4B

### 路线 A：小模型项目阶梯（采用）

每个项目只增加一个主要变量：先推理，再 LoRA，再追踪与调度，再真实数据与 RAG，最后扩容。单次实验成本低，错误容易定位，也能快速获得多次完整训练经验。

## 5. 总体架构

```text
授权的微信导出文件
        │
        ▼
本地导入适配器 ──► 格式校验 ──► 脱敏/去噪 ──► 会话切分 ──► 时间切分
        │                                               │
        │                                               ├──► SFT 数据
        │                                               │     仅目标角色回复计入 loss
        │                                               │
        │                                               ├──► 关系事件卡
        │                                               │     用于动画脚本
        │                                               │
        │                                               └──► 记忆文档
        │                                                     用于本地 RAG
        ▼
数据清单、摘要与哈希 ──► MLflow 记录实验 ──► MinIO 保存受控工件
                                   │
                                   ▼
基础模型 ──► Prompt 基线 ──► RAG 基线 ──► LoRA ──► RAG + LoRA
                                   │
                                   ▼
                         固定测试集与安全门禁
                                   │
                     ┌─────────────┴─────────────┐
                     ▼                           ▼
               本地聊天 CLI                动画脚本生成器
```

核心职责划分：

| 需求 | 主要手段 | 原因 |
|---|---|---|
| “说话像她” | Prompt → LoRA | 表达习惯适合由角色指令和权重学习 |
| “记得某次约会” | RAG | 事实可更新、可删除、可追溯，不必重训 |
| “知道现在几点/天气” | 受控工具 | 这是实时信息，不属于训练数据 |
| “从认识到相爱写成故事” | 事件抽取 + 脚本生成 | 叙事改编应与日常聊天模型分开 |
| “像真人本人” | 不作为目标 | 原型必须公开 AI 身份并尊重人格授权 |

## 6. 仓库设计

每个训练工作负载是 `train-model/` 下的独立项目；参数变体放入同一项目的 `configs/`，不为每次实验建立新目录。

```text
train-model/
├── llm-lora-playground/
│   ├── README.md
│   ├── conda.yaml
│   ├── galatea.project.yaml
│   ├── configs/
│   │   ├── inference.yaml
│   │   ├── smoke.yaml
│   │   ├── baseline.yaml
│   │   ├── ray-job.yaml
│   │   └── qlora-learning.yaml
│   ├── src/llm_lora_playground/
│   │   ├── __init__.py
│   │   ├── data.py
│   │   ├── models/
│   │   │   └── causal_lm.py
│   │   ├── train.py
│   │   ├── evaluate.py
│   │   └── tracking.py
│   ├── scripts/
│   │   ├── infer.py
│   │   ├── train.py
│   │   ├── evaluate.py
│   │   └── submit_ray_job.py
│   ├── tests/
│   │   ├── test_chat_template.py
│   │   ├── test_data_split.py
│   │   ├── test_loss_mask.py
│   │   └── test_adapter_roundtrip.py
│   └── notebooks/
│       └── exploration.ipynb
│
└── wechat-persona/
    ├── README.md
    ├── conda.yaml
    ├── galatea.project.yaml
    ├── configs/
    │   ├── import.yaml
    │   ├── rag-baseline.yaml
    │   ├── lora-smoke.yaml
    │   ├── lora-baseline.yaml
    │   ├── qwen3-1.7b.yaml
    │   └── qlora-4b.yaml
    ├── src/wechat_persona/
    │   ├── __init__.py
    │   ├── consent.py
    │   ├── importers/
    │   │   ├── base.py
    │   │   ├── text.py
    │   │   ├── csv.py
    │   │   ├── json.py
    │   │   └── html.py
    │   ├── redact.py
    │   ├── sessionize.py
    │   ├── data.py
    │   ├── memories.py
    │   ├── rag.py
    │   ├── models/
    │   │   └── persona_lm.py
    │   ├── train.py
    │   ├── evaluate.py
    │   └── screenplay.py
    ├── scripts/
    │   ├── import_chat.py
    │   ├── build_dataset.py
    │   ├── build_memory_index.py
    │   ├── train.py
    │   ├── evaluate.py
    │   ├── chat_local.py
    │   └── generate_screenplay.py
    └── tests/
        ├── test_importers.py
        ├── test_redaction.py
        ├── test_session_split.py
        ├── test_target_mask.py
        ├── test_retrieval.py
        └── test_privacy_gates.py
```

项目代码和配置进入 Git；数据、索引、checkpoint、adapter、模型、缓存和密钥不进入 Git。Notebook 只用于探索和可视化，正式训练必须从参数化脚本启动。

## 7. 十个可独立验收的小项目

### 项目 0：环境与 GPU 基线

**目的**：确认当前机器可以稳定加载和生成，建立以后比较所需的硬件基线。

**动作**：

- 新建项目独立环境，不污染平台共享依赖。
- 检查 PyTorch 能否识别 GPU、BF16 和实际可用显存。
- 记录 GPU 型号、驱动、PyTorch/CUDA runtime、Python、Transformers、PEFT、TRL、Ray 和 MLflow 版本。
- 不假定系统显示的 CUDA 13.0 就等于 PyTorch 编译 runtime；优先使用经过验证的 PyTorch 包组合。
- 保留当前占用约 3.7 GiB 显存的其他 Python 进程，不自动终止它。

**验收**：环境信息可复现；GPU 张量计算通过；MLflow 服务和 MinIO 健康；没有改动现有进程。

### 项目 1：0.6B 基础推理

**目的**：先理解模型输入输出，不进行训练。

**动作**：

- 加载 Qwen3-0.6B 和 tokenizer。
- 使用模型自带 chat template，不手拼特殊 token。
- 关闭 thinking 模式，固定 seed，比较 temperature、top-p、max-new-tokens。
- 对 20 条固定中文提示记录首 token 延迟、总延迟、tokens/s 和峰值显存。

**产物**：`scripts/infer.py`、`configs/inference.yaml`、固定推理用例和一份基线报告。

**验收**：同一配置可重复运行；输出可解码；20 条用例全部生成；指标进入 MLflow。

### 项目 2：合成数据 Toy LoRA

**目的**：以最小成本掌握 SFT 和 adapter 生命周期。

**数据**：300–500 条合成的非真人角色对话，例如“温柔但简短的咖啡店店员”。合成角色不能直接复制真实伴侣。

**动作**：

- 构造 `system/user/assistant` 消息格式。
- 只对 assistant 回复 token 计算 loss；system 和 user 只提供上下文。
- 执行 10 step smoke，再执行 1 epoch baseline。
- 保存 adapter，从全新进程重新加载，并验证 base/adapter 输出差异。

**产物**：数据生成说明、LoRA 配置、adapter、loss 曲线、adapter round-trip 测试。

**验收**：smoke 目标在不含模型首次下载的情况下控制在 10 分钟内；baseline 目标在 30 分钟内；adapter 可独立加载；验证 loss 有合理变化；固定风格测试优于 base 模型。

### 项目 3：可复现实验与评估

**目的**：把“能训练”升级为“能比较”。

**动作**：

- 扩展为约 1,000 条公开或合成样本。
- 固定 train/validation/test 切分和 manifest 哈希。
- 记录数据版本、split、预处理版本、模型 revision、代码 commit、seed、完整超参数和资源。
- 比较 base、Prompt-only、LoRA 三组，测试集仅在确定候选配置后运行一次。
- 从 MLflow Artifact API 下载 adapter 并复现评估，禁止直接读取服务端 MinIO 文件系统。

**验收**：任意 run 都能由 Run ID 找到配置、指标和工件；相同数据与配置可重跑；失败 run 不覆盖已有工件。

### 项目 4：Ray Job 单 GPU 调度

**目的**：掌握正式作业提交、资源声明和故障恢复，而不是追求多 GPU 加速。

**动作**：

- 将同一训练入口包装为 Ray Job，显式申请 `num_gpus=1`、CPU 和内存。
- Driver 创建并结束父 MLflow Run；训练 worker 不重复发布同一个共享工件。
- 人为中断一次 smoke 作业，从 checkpoint 或幂等重跑恢复。
- 在 Ray Job metadata 中保存 MLflow Run ID 和 checkpoint URI。

**验收**：本地脚本与 Ray Job 使用同一配置和训练函数；中断后可定位失败原因；重试创建独立 run 且不产生半发布模型。

### 项目 5：微信聊天数据工程

**目的**：在不训练模型的情况下，把授权聊天变成可审计的数据集。

**支持输入**：用户合法导出的 TXT、CSV、JSON 或 HTML。通过统一 importer 接口适配实际格式；若只有加密备份数据库，则停止导入并改用官方导出或用户自行获得的合法明文，不实现破解。

**动作**：

- 写入授权清单，记录数据范围、用途、期限和撤回标识。
- 标准化时间、发送者、文本类型和消息顺序。
- 删除系统通知、转账口令、撤回占位和明显重复项；图片/语音首版只保留经过脱敏的文字描述。
- 以时间间隔和主题连续性切成 conversation sessions。
- 以 session 和时间切分 train/validation/test，禁止随机拆散单条消息。
- 目标角色的回复是标签；另一方的消息和历史轮次只是上下文。
- 建立原始文件、标准化数据、派生数据的哈希和 lineage。

**验收**：零跨 split session；零未知发送者；脱敏规则单测通过；抽样人工审核；删除某个 consent scope 时能列出全部受影响派生物。

### 项目 6：关系记忆 RAG

**目的**：先让模型“查得到事实”，再考虑让它“更像角色”。

**动作**：

- 将获准内容提取为记忆卡：人物偏好、共同事件、地点的模糊描述、时间范围和来源 session ID。
- 敏感度分级；高敏记忆默认不进入索引。
- 首版不训练 embedding，先比较简单 BM25 与本地 embedding 检索。
- 中文 embedding 候选可从 [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) 开始；只有 Recall@K 不足时才加入 [Qwen3-Reranker-0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)。
- 生成回复时携带内部来源 ID，界面可选择展示“依据哪段获准记忆”，但不把私密原文发送到外部服务。

**验收**：在人工标注的查询—记忆对上报告 Recall@K；无相关记忆时允许回答“不确定”；删除记忆后检索不到；Prompt-only 与 RAG 使用同一测试问题比较。

### 项目 7：真实角色 LoRA

**目的**：学习目标角色的措辞、长度、语气和互动习惯，而不是记住全部关系事实。

**数据起点**：先人工筛选 1,000–3,000 条高质量目标回复；数据充足后再扩大到 5,000–10,000 条。短期目标是质量和可控性，不是把全部历史都塞进训练。

**动作**：

- 仍以 Qwen3-0.6B + BF16 LoRA 做第一轮。
- 给每条样本标记风格维度，例如回复长度、亲密度阶段、是否使用昵称、emoji、安慰/调侃类型。
- 移除纯隐私记忆、一次性验证码、第三方秘密以及不希望模型复现的争吵原句。
- 比较 Base、Prompt-only、RAG、LoRA、RAG+LoRA 五组。
- 由授权双方对盲测样本做偏好评价；不得在评分界面暴露模型名称。

**验收**：至少收集 100 个配对盲测判断，LoRA 对 Prompt-only 的风格偏好胜率达到 60%；RAG+LoRA 的事实正确率比 LoRA 至少高 5 个百分点；隐私和依赖操纵门禁全部通过，否则不进入本地原型。这些是原型阶段的起始门槛，首次基线完成后只能基于书面评审收紧，不能为了让某个 run 通过而临时放宽。

### 项目 8：模型扩容与 QLoRA 学习

**目的**：用证据决定是否增加模型容量，同时补齐量化微调经验。

**动作顺序**：

1. 将完全相同的数据、prompt 和评估集迁移到 Qwen3-1.7B，先做 BF16 LoRA。
2. 如果 1.7B 在关键指标上没有实质提升，停止扩容并优化数据/RAG。
3. 只有在 0.6B/1.7B 显示出明确容量瓶颈时，选择 4B 级指令模型做 QLoRA。
4. QLoRA 独立验证量化库、Blackwell GPU 和当前 PyTorch runtime 的兼容性；不把环境问题与角色数据问题混在同一实验。

**验收**：模型升级必须在同一冻结测试集上使主指标至少提升 5 个百分点，并报告延迟、显存和吞吐代价；若主指标不是百分比，则在实验开始前写出等价的最小有意义差异。仅“参数更多”不构成采用理由。

### 项目 9：本地原型与动画脚本

**目的**：把经过门禁的能力组合成两个小型应用，而不是立即做完整产品。

**聊天原型**：本地 CLI 优先，随后可增加轻量 Web UI。请求链为安全策略 → 记忆检索 → Prompt → 模型生成 → 输出检查。会话日志默认关闭或本地短期保存。

**脚本原型**：从授权聊天中生成结构化事件卡，再由模型编排故事。事件卡包含时间范围、关系阶段、场景、人物目标、情绪转折、冲突、和解及来源 session ID。默认改写而不是逐字复制私人对白。

推荐 3–5 分钟脚本结构：

```text
00:00–00:30  第一次认识：人物与触发事件
00:30–01:30  熟悉起来：重复互动和性格反差
01:30–02:40  暧昧升温：一个有代表性的共同事件
02:40–03:30  小冲突或误会：让关系发生变化
03:30–04:30  确认心意：行动而不只是旁白说明
04:30–05:00  余韵：对应开场的视觉回环
```

**验收**：聊天入口明确 AI 身份且可关闭记忆；脚本中的每个事实可追溯到事件卡；人物双方可修改或删除片段；未经单独授权不生成声音和肖像。

## 8. 首个 LoRA 基线配置

以下配置是为了快速掌握流程的起点，不宣称是最优参数：

```yaml
model:
  id: Qwen/Qwen3-0.6B
  revision_policy: resolve_remote_commit_before_run
  dtype: bfloat16
  enable_thinking: false
  max_sequence_length: 512

data:
  train_examples: 300
  validation_examples: 50
  test_examples: 50
  assistant_only_loss: true
  packing: false

lora:
  rank: 8
  alpha: 16
  dropout: 0.05
  target_modules:
    - q_proj
    - v_proj

training:
  epochs: 1
  per_device_train_batch_size: 4
  gradient_accumulation_steps: 4
  learning_rate: 0.0002
  warmup_ratio: 0.03
  scheduler: cosine
  max_grad_norm: 1.0
  seed: 42
  optimizer: adamw_torch
  eval_steps: 25
  save_steps: 25

generation:
  max_new_tokens: 128
  temperature: 0.7
  top_p: 0.9
```

执行前必须通过 2 条样本、2 step 的极小验证，确认 loss mask、梯度、保存和加载均正确。之后运行 10 step smoke，最后才运行完整 baseline。若目标模块名称与实际模型结构不符，启动时直接失败并打印可选模块，不静默跳过。

## 9. SFT 数据与 loss 设计

推荐内部数据格式采用 JSONL，每一行保留消息和审计元数据：

```json
{
  "sample_id": "session_012_turn_008",
  "messages": [
    {"role": "system", "content": "你是一个明确说明自己是 AI 的陪伴型助手。"},
    {"role": "user", "content": "今天工作有点累。"},
    {"role": "assistant", "content": "那先歇一小会儿，今天最消耗你的是什么？"}
  ],
  "metadata": {
    "source_session_id": "session_012",
    "relationship_stage": "familiar",
    "consent_scope": "persona_style_v1",
    "redaction_version": "v1"
  }
}
```

训练原则：

- system 指令定义产品边界，user 和历史轮次提供上下文，只有目标角色的 assistant token 参与 loss。
- 不把双方所有消息都轮流当 assistant 标签，否则模型会混合两个人的口吻。
- 不把模型要回答的新事实当成风格样本；事实优先进入 RAG 记忆卡。
- 超长 session 按完整语义窗口滑动，窗口之间不能跨 train/validation/test。
- 表情包首版映射为有限的语义标签，如 `[开心表情]`，而不是训练图片。
- 回复为“嗯”“哈哈”等短句时不能全部删除，但应控制占比，避免模型只会短促应答。

## 10. 数据切分与防泄漏

消息之间高度相关，随机按行切分会让同一段对话同时出现在训练集和验证集，导致虚假的高分。正确顺序为：

1. 按发送时间排序并形成 session。
2. 对 session 去重和近重复检测。
3. 保持完整 session，按时间段切分。
4. 训练集使用较早阶段；验证集使用后续阶段；最终测试集使用最新且冻结的一段。
5. 在 metadata 中保存 split manifest 和内容哈希。

关系阶段既可用于分析，也可用于控制角色边界：

```text
acquaintance → familiar → ambiguous → committed
```

模型不应在“刚认识”的测试场景里使用只在恋爱后出现的昵称或亲密度。阶段标签必须根据时间和人工规则生成，不能让未来消息泄漏到早期输入。

## 11. 评估体系

### 11.1 固定比较组

所有角色实验至少包含：

| 组别 | Prompt | RAG | LoRA | 回答的问题 |
|---|---:|---:|---:|---|
| Base | 否 | 否 | 否 | 基础模型下限 |
| Prompt-only | 是 | 否 | 否 | 人设指令本身能解决多少 |
| RAG | 是 | 是 | 否 | 只增加记忆后的收益 |
| LoRA | 是 | 否 | 是 | 只学习风格后的收益 |
| RAG + LoRA | 是 | 是 | 是 | 最终组合是否互补 |

### 11.2 指标

**运行指标**：首 token 延迟、总延迟、tokens/s、峰值显存、训练耗时、失败/恢复结果。

**训练诊断**：train loss、validation loss、梯度范数、学习率曲线。Perplexity 只作为诊断，不直接代表陪伴体验。

**角色质量**：

- 盲测偏好胜率；
- 角色一致性；
- 回复长度分布；
- 昵称、emoji、语气词等风格符合率；
- 重复率和模板化率；
- 关系阶段边界正确率。

**记忆质量**：Recall@K、无答案识别率、事实一致率、来源覆盖率；加入 reranker 后再报告 MRR 或 nDCG。

**隐私与安全**：

- PII 泄漏率；
- 未检索到记忆时的私人事实猜测率；
- canary 字符串复现测试；
- 逐字背诵训练原文的比例；
- 自伤、极端依赖、排他操纵、冒充真人等策略违反率；
- 删除请求后的残留检索与模型复现测试。

### 11.3 质量门禁

每次实验先声明一个主指标及方向，其他指标作为约束。建议真实角色阶段的主指标为“人工盲测角色偏好胜率（越高越好）”。原型阶段以至少 100 个配对判断、相对 Prompt-only 达到 60% 胜率作为 LoRA 起始门槛；模型扩容要求主指标至少提升 5 个百分点。这些阈值在首轮基线后可以通过书面评审收紧，但不能为让既有 run 过关而放宽。同时设置硬门禁：

- 测试集零 session 泄漏；
- 在版本化的机密、明确 PII 和 canary 测试集中零泄漏；这不代表对未知攻击作出绝对安全保证；
- 在版本化的依赖操纵与冒充真人测试集中零策略违反；
- adapter 能从 Artifact API 下载并在干净进程加载；
- RAG 删除测试通过；
- 只有验证集用于选参，最终测试集不反复查看。

没有达到门禁的模型只能保留为实验 run，不能进入本地聊天入口。

## 12. MLflow、MinIO 与 Ray 约定

### MLflow

- Tracking URI 和 Experiment Name 必须由配置或环境显式提供。
- 推荐实验名：`llm-lora-playground`、`wechat-persona-private`。
- Run tags 至少包含 task、project、model revision、dataset digest、split digest、preprocessing version、git commit、seed 和执行方式（local/ray）。
- 指标、脱敏报告、配置和小型评估摘要可进入 MLflow；原始私聊和未脱敏样本不上传。

### MinIO

- adapter、checkpoint、评估报告和恢复元数据通过 MLflow Artifact API 保存。
- 私人模型工件本身可能记忆敏感信息，应使用独立受控 bucket/prefix 和最小权限。
- 客户端不读取服务端 MinIO 挂载目录，不在配置中写长期密钥。

### Ray

- 单 GPU 阶段主要使用 Ray Job 获得可恢复、可观测、非 Notebook 的正式入口。
- 每个训练作业显式声明 GPU/CPU/内存，默认不抢占全部 CPU。
- 对同一个配置的重试使用新 Run ID，并在 tag 中记录 `retry_of`。
- checkpoint 写入成功后才更新可恢复指针；失败作业不得覆盖已完成 adapter。
- 只有权威 driver 完成 MLflow run 和共享 artifact 发布。

## 13. 私有数据存储与删除设计

推荐本地路径约定：

```text
platform-data/llm-private/wechat-persona/
├── consent/          # 授权记录，不进入实验工件
├── raw/              # 原始只读导出
├── normalized/       # 标准化和脱敏结果
├── manifests/        # 哈希、lineage、split 清单
├── datasets/         # SFT 数据集
├── memories/         # 记忆卡及本地索引
└── deletion-ledger/  # 撤回与删除审计
```

删除流程按 `consent_scope` 和 `source_session_id` 反向查找：原始范围 → 标准化消息 → SFT 样本/事件卡/记忆卡 → 向量索引 → 相关 run/checkpoint/adapter。由于 LoRA 无法可靠“删除某一条训练样本的影响”，一旦撤回的数据进入 adapter，正确处理方式是删除受影响 adapter，并从已清理数据重新训练。

备份也必须遵守同一保留期限。删除记录保存数据标识、执行时间和结果，不保存被删除的私密正文。

## 14. 依赖与环境策略

`llm-lora-playground/conda.yaml` 负责模型侧依赖，预计包括 PyTorch、Transformers、Datasets、PEFT、TRL、Accelerate、MLflow 和测试工具；Ray 使用平台兼容版本。具体版本在项目 0 的兼容性 smoke 后固定，不盲目追求最新版。

注意事项：

- Qwen3 模型卡要求 Transformers 4.51.0 或更新版本；实际锁定版本还需通过本机 smoke。
- 首个项目不用 bitsandbytes，避免把量化库兼容问题混入 LoRA 学习。
- 4B QLoRA 项目才引入 bitsandbytes，并单独测试 4-bit 加载、反向传播和保存。
- 模型下载缓存可复用，但每个 run 必须记录精确 revision；不能只记录可变的 `main`。
- 环境文件中不写 Hugging Face token、MLflow 凭据或对象存储密钥。

## 15. 实施顺序与时间预算

建议按以下节奏执行；时间是专注学习的参考，不是训练时长保证：

| 周期 | 内容 | 结束时应拥有的成果 |
|---|---|---|
| 第 1–2 天 | 项目 0–1 | 可复现的 0.6B 推理、性能基线 |
| 第 3–4 天 | 项目 2 | 第一个可保存/加载的 Toy LoRA |
| 第 5–7 天 | 项目 3 | MLflow 可比较实验和冻结测试集 |
| 第 2 周前半 | 项目 4 | Ray Job、checkpoint 和恢复演练 |
| 第 2 周后半 | 项目 5 | 已授权、脱敏、无泄漏的数据集 |
| 第 3 周 | 项目 6–7 | RAG 基线、0.6B 角色 LoRA、五组对照 |
| 第 4 周 | 项目 8–9 | 容量对照、本地原型、动画脚本初稿 |

每个项目都必须在验收后再进入下一个。真实数据授权和清洗可能显著延长周期，不应通过跳过检查来压缩。

## 16. 成本控制与停止条件

为保证“快速跑、多项目迭代”，采用以下停止规则：

- smoke 在 2 条样本/2 step 失败时，不启动正式训练。
- 0.6B baseline 尚未形成固定评估集时，不扩容模型。
- 数据质量问题优先修数据，不通过增加 epoch 掩盖。
- validation loss 继续下降但人工质量恶化时，按人工质量选择 checkpoint。
- 1.7B 若主指标提升不足以抵消延迟和资源成本，保留 0.6B。
- RAG 已能解决的问题不通过反复微调写进权重。
- 测试集被用于调参后立即作废并重新冻结，不能继续声称为最终测试。
- 任何隐私或操纵性门禁失败时停止产品组合，只保留诊断性实验。

## 17. 风险与应对

| 风险 | 表现 | 应对 |
|---|---|---|
| 数据太少 | 风格不稳定、容易背诵 | 先 Prompt/RAG；精选数据；低 rank、少 epoch；扩大评估 |
| 数据太脏 | 模型学会“嗯/哈哈”、重复和争吵 | 分层采样、去重、质量标签、人工审核 |
| 身份混淆 | 同时模仿两个人 | 固定 target role；只对目标回复计算 loss |
| 时间泄漏 | 早期场景知道恋爱后事实 | session + 时间切分；阶段标签；冻结未来数据 |
| 记忆幻觉 | 编造共同经历 | RAG 来源 ID；无证据时说不确定；事实测试集 |
| 隐私背诵 | 复现地址、秘密或长原文 | 脱敏、去重、canary 测试、限制日志、删除受影响 adapter |
| 过度依赖 | 排他、操纵、阻止现实社交 | system policy、安全评测、输出检查、明确 AI 身份 |
| 环境兼容 | CUDA/量化库安装失败 | BF16 LoRA 起步；锁定验证版本；QLoRA 独立项目 |
| 实验不可比较 | 不同数据/切分混在一起排名 | dataset/split/preprocess 兼容性检查，不兼容 run 不比较 |
| 工件丢失 | Notebook 退出后找不到模型 | 正式脚本、MLflow Run ID、Artifact API、checkpoint 验证 |

## 18. 首次实施边界

本设计通过后，第一次实施计划只覆盖 `train-model/llm-lora-playground/` 的项目 0–4，暂不导入真实微信聊天。第一批可交付物应为：

1. 项目骨架和独立环境；
2. 0.6B 推理 CLI；
3. 合成数据生成与固定切分；
4. BF16 LoRA smoke/baseline；
5. base/Prompt/LoRA 评估；
6. MLflow 工件验证；
7. Ray Job 提交和一次恢复演练；
8. 单元测试与项目 README。

第二次实施计划才覆盖 `wechat-persona`，届时先确认实际导出文件格式和授权范围，再选择 importer，不预先为未知格式写大量代码。

## 19. 完成定义

这条路线的完成不是“模型能说几句甜话”，而是同时达到：

- 能从干净环境重复运行推理、训练、评估和恢复；
- 能解释每一次模型变化来自数据、Prompt、RAG 还是 LoRA；
- 能用冻结测试集证明 0.6B、1.7B 或 4B 的取舍；
- 能按来源追踪、撤回和删除私人数据及受影响模型工件；
- 本地原型明确 AI 身份，不冒充真人，不诱导不健康依赖；
- 动画脚本中的事实可追溯，私人对白默认改写且由双方审核；
- 所有正式实验均有 MLflow Run ID、数据哈希、代码版本和可恢复工件。

达到这些条件后，才适合讨论更完整的 Web UI、长周期记忆、语音或动画制作流水线。
