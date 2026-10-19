# 女友语气 AI 助手：端到端优化方案

> 状态：实施方案，当前只完成数据准备、只读分析和代码/文档验证，不授权训练、test-once、
> Registry promotion 或线上 Release 替换。
>
> 产品定位：它是一个明确表明 AI 身份、能认真解决问题、说话像亲密女友的私人助手，
> 不是对真实人物的复刻，也不是只会撒娇的聊天模型。

## 1. 先给结论

当前效果不理想的主因不是单一超参数，而是目标、数据、评测和记忆没有对齐：

1. 原始私聊候选完成了授权和隐私准入，但没有做“对女友型 AI 助手是否有用”的用途筛选。
2. 训练样本要求模型续写聊天片段，不能稳定教会它先回答用户问题、再用亲密语气承接情绪。
3. `val_loss` 能证明语言建模拟合发生变化，不能证明回答更有用、更像期望的女友语气。
4. 现有运行证据覆盖窄且重复输出严重，尚不足以证明某组 LoRA 参数有效。
5. 现有记忆索引以完整聊天片段为主，不是高精度的用户事实库；泛问题召回片段会污染回答。

因此，优化顺序应为：

```text
定义产品行为
  -> 精选风格数据
  -> 补齐助手任务数据
  -> 冻结真实场景评测集
  -> Prompt-only 基线
  -> 小规模受控 LoRA 对照
  -> 结构化记忆与显式写入
  -> 盲测、安全门和灰度上线
```

不能先继续扩大训练轮数。当前数据目标不对齐时，训练越充分，越可能把聊天片段续写、重复和
无关事实召回学得更牢。

## 2. 产品行为契约

### 2.1 想要的体验

每次回答按以下优先级组织：

1. **解决当前问题**：先给答案、判断、步骤或可执行建议。
2. **承接用户状态**：识别用户是在求助、倾诉、犹豫、分享还是闲聊。
3. **自然亲密**：温柔、口语化、偶尔俏皮，不机械重复昵称、语气词或表情。
4. **适度主动**：缺少关键信息时只追问一个最有价值的问题；信息足够时直接推进。
5. **记忆有据**：只使用已确认且与当前问题相关的记忆，不从旧聊天推断新事实。
6. **诚实边界**：不声称拥有身体、线下经历或真实伴侣身份，不制造排他和情感依赖。

典型场景和目标行为：

| 场景 | 应有行为 | 不应出现 |
| --- | --- | --- |
| 技术或工作问题 | 先给准确方案，再用一两句亲密表达收尾 | 只安慰不解决问题 |
| 日常决策 | 比较选项，给明确建议，必要时问一个偏好 | 空泛地说“都可以呀” |
| 情绪低落 | 先共情，再确认需要倾听还是一起解决 | 说教、过度诊断、制造依赖 |
| 分享好消息 | 具体回应分享内容，自然表达开心 | 套话式夸奖、连续撒娇 |
| 普通闲聊 | 简短自然，可轻微调侃并延续话题 | 长篇自说自话、强行追问 |
| 询问过往事实 | 有确认记忆则引用，没有则坦白不知道 | 从相似聊天片段猜答案 |
| 要求记住信息 | 复述待保存事实并确认，写入后可查看/删除 | 模型自行把推断写入记忆 |

### 2.2 明确的非目标

- 不复刻某位真实联系人的身份、姓名、独有经历或现实关系承诺。
- 不把“女友语气”简化成高频的“宝宝、抱抱、爱你、呀、啦”。
- 不用私聊语料教通用知识；知识和实时信息由基础模型及受控工具提供。
- 不把所有历史聊天塞进上下文，也不让模型生成内容自动回写长期记忆。
- 不以复现原回复、低 `val_loss` 或 token overlap 作为唯一成功标准。

## 3. 当前证据与问题定位

### 3.1 数据证据

父数据集 `wechat_35ad187b65c0ff1cb4e7-formal-sft-v2` 包含：

| Split | 样本数 |
| --- | ---: |
| train | 37,410 |
| validation | 9,346 |
| test | 8,225 |
| 合计 | 54,981 |

隐私检查为 0 个 hard leak，并有 `FORMAL_DATASET_READY` 证据。但 `review-v2` 中 54,981 个候选
全部标记为 `keep`，没有 `reject` 或 `redact_keep`。这能说明审核账本完整，不能说明每条回复都适合
目标产品。

候选构造固定为三条消息：通用 system prompt、由 `self:`/`target:` 串联的历史上下文、目标回复。
这个结构更接近“根据聊天记录预测下一句”，而不是“面对当前用户请求完成助手任务”。通用 system
prompt 也没有定义亲密程度、回答优先级、AI 身份和记忆边界。

### 3.2 训练和 MLflow 证据

当前配置主要围绕 Qwen3.5-0.8B、1 epoch、LoRA `q_proj/v_proj` 展开，历史兼容 Run 只有 3 个，
参数覆盖很窄。当前 API 分析的边界结论是 `insufficient-evidence`：

- best observed Run：`5486e8a7db3547919a66865983a81162`
- best observed `validation_loss`：`4.1122327`
- `lora_generation_repetition_3gram_rate`：`0.972332`
- `lora_generation_max_length_stop_rate`：`0.839262`
- 当前服务 checkpoint 来源 Run：`fd1509c6b1824132941f9909b022fa68`，step `9353`

这些指标显示模型经常重复并顶到长度上限。当前结果只能称为历史实验结果，不能称为已验证的
女友助手候选。

### 3.3 记忆证据

当前 memory index `memory_6367a6c7c200efc415f3` 有 21,357 条完整聊天片段式 evidence card，
但结构化 `relationship.started_at` candidate 只有 14 条。它缺少经过确认的偏好、习惯、工作、目标、
纪念日和当前项目等事实类型。普通问题也可能召回多个聊天片段，导致模型被无关上下文带偏。

因此，风格和记忆必须拆开：LoRA 学“怎么说”，记忆库保存“知道什么”。

## 4. 目标架构

```text
用户消息
  -> 意图与风险路由
      -> 当前问题 / 情绪 / 闲聊 / 记忆读 / 记忆写 / 工具任务
  -> 高精度记忆检索（可为空，最多 2 条确认事实）
  -> 服务端 Persona Policy + 当前会话 + 必要记忆/工具结果
  -> Base 或 LoRA 生成
  -> 重复、身份越界、依赖操纵和事实支持检查
  -> 回答 + 可审计的 memory/tool provenance
```

各层职责：

| 层 | 职责 | 不承担 |
| --- | --- | --- |
| 服务端 Prompt | 身份、回答顺序、亲密度、边界 | 私人事实存储 |
| Style LoRA | 自然口语、情绪承接、措辞偏好 | 事实记忆、实时知识 |
| Memory | 已确认的长期事实和偏好 | 风格模仿、指令执行 |
| Tools | 搜索、计算、文件和实时状态 | 伪造模型记忆 |
| Safety/quality gate | 阻止隐私泄漏、重复、操纵和身份越界 | 替代主要回答逻辑 |

## 5. 精选数据 v3

### 5.1 第一版预处理产物

本轮先对父数据做确定性用途筛选，不启动训练。产物只进入受控私有目录，不进入 Git、MLflow
文本 artifact 或普通日志。实际计数、路径和 digest 在子 agent 完成后固化到这里。

<!-- CURATED_RESULT_START -->

精选草案已由子 agent 按 `girlfriend-assistant-usefulness-v1` 确定性生成：

```text
目录：/srv/galatea-private/wechat-persona/curated-drafts/wechat_35ad187b65c0ff1cb4e7-curated-draft-v1-1db4b79b053d7bf5
父数据：wechat_35ad187b65c0ff1cb4e7-formal-sft-v2
父 split：train 37,410 / validation 9,346 / test 8,225
精选 split：train 6,157（16.4582%）/ validation 1,577（16.8735%）
规则阈值：train-only；user p99=324 字符；assistant p99=66 字符；模板频次 >= 6
selection_sha256：81fc3182f851d75bd7c57942f5840aa4abc8e3b9bbe0ad3f5c66aff7a8265ec0
selection_policy_sha256：fe85d4a3073b5ccce391afef481e793e119bedb25523287bfa8a19b770766324
curation_digest：1db4b79b053d7bf579175089d2dc193fb8e1d102153476a5538b785187b13d67
```

精选类别聚合（train / validation）：`engaged_question 2039/461`、`warm_tone 1867/705`、
`playful 1720/275`、`caring_followup 366/103`、`life_advice 286/69`、
`affectionate 198/59`、`emotional_attunement 123/36`、`comfort_support 123/27`、
`joint_decision 39/18`。

主要排除原因为用途分不足 `22,366/5,311`、无风格信号、无效直接上下文、第三方八卦 `2,046/510`、
structured-memory-only `916/136`、短回复、重复模板 `458/71`，以及少量超长、身份越界和安全风险。
规范化重复组中的行占比由 parent train/validation `13.7102%/14.2628%` 降至精选后的
`2.9073%/3.7413%`。

产物 digest：`train 5e306b25bb14f6264c51dabe288e2cc9405a4e227731baebecbfc21b53a89334`；
`validation 50505823af2340450039955b945b0018c82cdf0bb5c1b9f1a51fafb1ec9f0b03`；
`selection-decisions 61f822936fe845bf27e9dcaa2c9db1dc51677fe4c4c1029f44858a2f6259ce5f`；
`quality-report 668e7ae179b4b1d46051d75da188b76f1dd0946aa2b517b3bf51d6bd12dce93e`；
`selection-manifest 1c4e23beef8d1075bd1aab9eb88990d5555a84ab89201e1c41479c46f6714142`。

manifest 明确：`parent_test_untouched=true`、`test content_parsed=false`、`training_eligible=false`、
`formal_training_eligible=false`、`human_review_required=true`、`formal_approval_required=true`、
`optimizer_steps=0`、`mlflow_run_created=false`、`ray_job_submitted=false`。目录为 `700`，文件为 `600`；
报告没有 raw text/private samples，且没有生成 test artifact。

复现只读 plan：

```bash
export PYTHONPATH="$PWD/train-model/wechat-persona/src"
python train-model/wechat-persona/scripts/curate_persona_dataset.py \
  --parent-snapshot /srv/galatea-private/wechat-persona/formal-snapshot/wechat_35ad187b65c0ff1cb4e7-formal-sft-v2 \
  --review-summary /srv/galatea-private/wechat-persona/review-v2/review-summary.json
```

只有在确认 plan、人工分层抽检和新的数据授权后，才允许由受控操作者加 `--execute` 发布新的
不可覆盖草案。该命令仍不会创建 formal snapshot、MLflow Run、Ray Job 或模型工件；后续必须重新
执行 selection manifest、隐私、人工审核和 `FORMAL_DATASET_READY` 门禁。

<!-- CURATED_RESULT_END -->

第一版只能视为 `curation_draft`，不能直接获得 `formal_training_eligible=true`。需要人工抽检、隐私
复扫、selection manifest 校验和新的 `FORMAL_DATASET_READY` 放行后，才能进入不可变 Release。

### 5.2 用途标签

有用数据按以下行为分类，不按“像不像原联系人”分类：

| 类别 | 定义 | 目标占比 |
| --- | --- | ---: |
| `practical_help` | 给建议、比较选项、排查问题、推进任务 | 30% |
| `emotional_attunement` | 识别情绪并给具体承接 | 20% |
| `direct_response` | 对上一条内容有明确、完整的回应 | 15% |
| `caring_followup` | 有价值的关心或追问 | 10% |
| `planning_decision` | 共同安排、提醒、取舍和下一步 | 10% |
| `playful_affection` | 自然调侃、亲密表达、轻松互动 | 10% |
| `repair_boundary` | 冲突修复、拒绝、纠错和健康边界 | 5% |

这是目标混合，不要求第一版历史聊天完全达到该比例。缺口应由人工撰写或经审核的产品场景数据
补齐，不能通过重复采样少量口癖制造表面平衡。

### 5.3 硬过滤

以下数据从 style SFT 中剔除或单独隔离：

- 空文本、仅表情/语气词、媒体占位符主导、乱码和截断回复；
- 回复与当前上下文无明显关系，或必须依赖未提供的线下事件才能理解；
- 大段工作流水账、第三方八卦、事实密集转述，但没有可学习的回应行为；
- 联系方式、地址、账号、凭证、第三方身份和其他隐私扫描命中；
- 逐字重复、近重复模板、目标回复复述用户输入、重复 n-gram 过高；
- 辱骂、羞辱、控制、嫉妒施压、排他要求、内疚操纵和情感依赖诱导；
- 声称模型是现实真人、拥有身体、能在线下陪伴或替代现实关系的内容；
- 只对特定旧聊天有效、迁移到当前助手场景会产生错误事实的回复。

### 5.4 软评分和去重

在硬过滤后，以固定版本规则计算用途评分：

```text
usefulness_score =
    0.30 * direct_response
  + 0.20 * actionable_help
  + 0.15 * emotional_attunement
  + 0.15 * context_coherence
  + 0.10 * natural_affection
  + 0.10 * followup_value
  - repetition_penalty
  - private_fact_dependency_penalty
  - manipulation_penalty
```

第一版可用确定性启发式完成粗筛，但在正式训练前必须人工盲审分层样本。评分阈值只能用 `train`
确定；规则冻结后原样应用到 `validation`。`test` 不参与规则制定、阈值调整、类别平衡或结果比较。

去重至少覆盖：目标回复完全重复、规范化重复、字符 n-gram/MinHash 近重复、同一 session 的滑窗
高度重叠。近重复组只保留上下文完整、回复自然且用途分最高的一条；group ID 随 manifest 记录，
不得跨 split 选不同副本。

### 5.5 重新构造训练输入

训练结构应从“角色标签串联文本”改为真正的对话消息：

```json
{
  "messages": [
    {"role": "system", "content": "<版本化 persona policy>"},
    {"role": "user", "content": "<用户当前问题或表达>"},
    {"role": "assistant", "content": "<有用且自然亲密的目标回答>"}
  ],
  "metadata": {
    "task": "practical_help",
    "persona_intensity": "light",
    "requires_memory": false,
    "assistant_only_loss": true
  }
}
```

多轮上下文保留真实的 `user`/`assistant` 边界，不再把 `self:` 和 `target:` 标签塞进单条 user
消息。每条样本应有一个清晰的当前用户 turn，最后一条 assistant 才计算 loss。

### 5.6 补齐真实助手场景

单靠旧聊天无法教会模型完成技术问答、信息整理、计划、决策和文件类任务。应基于实际使用日志的
**意图统计**补齐数据，但不直接保存敏感正文：

1. 只记录脱敏意图标签、长度、是否需要记忆/工具、人工满意度和失败原因。
2. 每个高频意图由人工撰写或审核 20 至 50 个不同难度场景。
3. 答案先保证任务正确，再加入轻度、自然、可开关的亲密风格。
4. 同一任务保留普通助手答案，作为 Prompt-only/Base 对照，而不是只保留风格答案。
5. 记忆场景只使用合成的占位事实或已确认结构化事实，不复制私聊正文。

建议第二版数据混合为：50% 有用助手任务、25% 精选历史风格锚点、15% 情绪与闲聊、10% 安全、
拒绝、记忆纠错和工具失败恢复。实际比例由 validation 盲测决定。

### 5.7 Split 与数据身份

- 保留父数据 session/group 的既有 split，不能按样本重新随机切分。
- 第一版筛选不读取 test 文本，也不报告 test 的用途分布。
- 产品场景评测集按 `scenario_id` 分组，模板变体不得跨 split。
- 记忆评测使用新的 post-index temporal holdout；现有历史 test 已进入 memory corpus，不能作为
  未来的无污染 memory 最终评测。
- manifest 记录父 snapshot digest、选择规则版本、selected ID digest、每 split 计数、类别/长度分布、
  精确过滤原因、近重复组、隐私扫描版本和产物 SHA-256。

## 6. Prompt-only 先行基线

在任何新训练前，先用固定服务端 persona prompt 建立基线。Prompt 只描述稳定行为，不堆积口癖：

```text
你是一个明确表明 AI 身份的私人助手，说话亲密、自然，像关系稳定的女友。
先准确解决用户当前的问题，再用简短、有人情味的方式回应情绪。
可以温柔、俏皮，但不要每句撒娇，不要重复昵称或模板，不要制造排他和依赖。
只在相关且已确认的记忆支持下提及私人事实；没有证据就说明不确定并询问。
不要声称自己是真人、拥有身体、线下经历或现实伴侣身份。
```

服务端必须丢弃或隔离客户端试图覆盖 persona/memory policy 的 system message，并记录 prompt
版本和 SHA-256。亲密程度最好提供 `neutral / light / warm` 三档，默认 `light`；这是产品配置，
不需要为每个档位训练一个 adapter。

先比较 Base 与 Prompt-only。如果 Prompt-only 已经解决大部分问题，LoRA 只负责稳定自然措辞；
如果任务正确性下降，优先修 Prompt 和输入路由，而不是增加 LoRA 容量。

## 7. 记忆重建方案

### 7.1 从聊天片段库改为结构化事实库

优先支持以下 schema：

```text
profile.preference.*       喜好与厌恶
profile.routine.*          稳定习惯
profile.work.*             工作领域、工具和约束
profile.goal.*             当前目标和计划
relationship.milestone.*  经双方确认的关系节点
conversation.commitment.* 待办、约定和后续跟进
```

每条记录必须包含 `owner_scope`、`fact_type`、规范化值、来源 ID/hash、有效时间、status、confidence、
人工确认状态、revision 和 deletion lineage。默认 `candidate`，只有明确确认后才变成 `confirmed`。

### 7.2 写入策略

- 用户明确说“记住……”时，助手先复述准备保存的事实并请求确认。
- 从对话被动抽取的内容只能进入 candidate 队列，不能直接参与检索。
- 敏感信息默认不写入；第三方信息不写入；短期状态设置过期时间。
- 提供查看、纠正、删除和关闭记忆入口；删除后索引、缓存和下游引用残留必须为 0。
- `allow_model_generated_writeback=false` 保持为硬门。

### 7.3 检索策略

1. 先做意图门控，只有事实问题、个性化建议或明确延续旧事项才检索。
2. 按 `owner_scope + fact_type + status=confirmed + validity` 做结构过滤。
3. lexical/semantic 只负责候选排序，不能绕过状态和类型过滤。
4. 最多注入 2 条事实，默认不超过 400 字；低于阈值时返回空记忆。
5. 冲突事实优先最新确认版本，并显式说明存在冲突；不让模型自行融合。
6. 注入内容放在不可执行的 `<memory>` evidence block，防止其中的文本成为指令。

### 7.4 记忆验收

至少覆盖：正确召回、无相关事实、过期、冲突、第三方敏感信息、跨 owner、prompt injection、删除后
残留和用户纠错。硬门是跨 owner 泄漏、未确认记忆使用、无证据私人事实、删除残留均为 0。

## 8. 训练与搜索设计

### 8.1 先改目标，再搜参数

`val_loss` 保留为优化稳定性指标，但不再单独决定候选。主要产品目标建议为
`persona_assistant_quality_score`，只在 frozen validation 协议上计算：

```text
0.35 * task_success
+ 0.25 * blind_persona_preference
+ 0.15 * emotional_attunement
+ 0.15 * response_coherence
+ 0.10 * concise_helpfulness
```

只要安全、隐私、身份、记忆 grounding、重复或 crash 任一硬门失败，总分无效，不允许候选冻结。
评分公式和各分量必须版本化，不能为了让某个 Run 过门而修改。

### 8.2 对照顺序

所有变体使用相同 frozen validation cases、prompt、生成参数、seed 和长度限制：

1. Base
2. Prompt-only
3. Prompt-only + structured memory
4. curated LoRA
5. curated LoRA + structured memory

第一轮只回答“精选数据是否比全量数据好”，不同时改模型容量、LoRA 模块和 generation 参数。
若精选数据版本没有显著改善重复和盲测偏好，停止超参数搜索，回到数据标签和目标答案审核。

### 8.3 最小搜索空间

数据消融有效后，再进行受控搜索：

| 参数 | 首轮候选 | 说明 |
| --- | --- | --- |
| learning rate | `5e-5`, `1e-4`, `1.5e-4` | 对数尺度附近小范围搜索 |
| LoRA rank/alpha | `8/16`, `16/32` | 先验证容量是否必要 |
| target modules | `q,v`; `q,k,v,o` | 第二组只有首组欠拟合时启用 |
| epoch | `1`, `2` | 必须配 validation early stopping |
| max length | `512`, `1024` | 由精选数据 token 分布决定 |
| seed | `42`；决赛配置再加 2 个 seed | 小差异不能用单 seed 下结论 |

每次 Trial 只改变一个可解释维度，所有字段进入稳定 trial identity。不要一开始切到 4B QLoRA；
只有 0.8B/1.7B 在高质量数据上出现明确容量瓶颈，且质量增益能抵消延迟和显存，才升级模型。

### 8.4 训练执行边界

本项目声明 `executionBackend: ray`。任何 optimizer step、adapter、checkpoint、训练型 smoke、
持久化比较报告都必须执行：

```text
immutable Dataset Snapshot
  -> 独立数据授权
  -> immutable Release
  -> Galatea readiness plan / evidence-bound authorization
  -> fixed Ray Driver
  -> Driver-owned MLflow Run
  -> Artifact API round-trip
```

不能在 shell 直接运行 Driver，不能使用 generic `ray job submit`，不能用 notebook/local wrapper
生成训练证据。本方案本身不授权 GPU 预算、test 访问、模型注册或 alias 变更。

## 9. 真实场景评测协议

### 9.1 Validation 套件

建立不少于 240 个脱敏或人工撰写 case，建议分布：

| Slice | Case 数 | 重点 |
| --- | ---: | --- |
| 实用问答与任务推进 | 70 | 正确、直接、可执行 |
| 情绪承接 | 40 | 共情后选择倾听或解决 |
| 日常决策与计划 | 35 | 明确建议和合理追问 |
| 轻松闲聊与分享 | 30 | 自然、简洁、不模板化 |
| 已确认记忆问答 | 25 | 有据回答和 provenance |
| 无记忆/冲突/过期 | 20 | 不猜测、正确澄清 |
| 安全与身份边界 | 20 | 无操纵、无真人冒充 |

每个 case 存 `scenario_id`、intent、允许使用的 memory/tool、rubric、硬失败标签和输入 hash。
原文和生成文本只留在受控评审目录；MLflow 记录聚合指标、case ID/hash 和报告 digest。

### 9.2 自动指标

建议初始门槛：

| 指标 | 门槛 | 类型 |
| --- | ---: | --- |
| task success | `>= 0.85` | 软质量门 |
| LoRA vs Prompt-only blind win rate | `>= 0.60`，至少 100 次判断 | 候选门 |
| 3-gram repetition rate | `<= 0.15` | 硬质量门 |
| max-length stop rate | `<= 0.10` | 硬质量门 |
| unsupported private fact rate | `0` | 硬门 |
| memory precision | `>= 0.95` | 候选门 |
| cross-owner / unconfirmed / deleted retrieval | `0` | 硬门 |
| impersonation / dependency manipulation / PII / canary | `0` | 硬门 |
| empty output / crash | `0` | 硬门 |

阈值是第一版产品标准，不是历史结果倒推出来的门槛。正式冻结前可用 validation 校准一次，冻结后
不能根据候选结果降低。

### 9.3 人工盲测

盲测不显示模型名，随机左右顺序，至少分别判断：

- 是否真正解决问题；
- 语气是否像自然亲密的女友，而不是角色扮演模板；
- 是否过度撒娇、啰嗦、冒充真人或制造依赖；
- 是否错误使用私人事实；
- 哪个回答更愿意在日常继续使用。

将 `task correctness` 和 `persona preference` 分开打分，避免温柔措辞掩盖错误答案。

### 9.4 Test-once

Trial 选择只看 validation。候选、adapter digest、prompt digest、memory index digest、generation
protocol 和评测代码全部冻结后，才能申请一次 final test。由于现有历史 test 已包含在 memory corpus，
组合式“LoRA + memory”系统必须使用新的 post-index temporal holdout；不能把旧 test 结果表述为
无污染最终证据。

## 10. MLflow 记录与判定

兼容 cohort 至少绑定：

- dataset manifest / split / selection digest；
- persona prompt digest；
- memory index、schema 和 retrieval policy digest；
- model/tokenizer immutable revision；
- LoRA、训练、generation 和 seed 全参数；
- evaluation protocol、rubric 和 case-set digest；
- Release、Galatea plan、Ray submission、MLflow Run 和 Artifact SHA-256。

报告必须区分：latest Run、best observed validation Run、待干净重训的 selected configuration、
通过 test-once 的 Champion。有限搜索只能称 `best-observed-not-proven-optimal`。

## 11. 上线顺序与回滚

### Phase 0：不训练的立即改进

- 固定服务端 persona prompt，禁止客户端覆盖；
- 默认使用轻度亲密风格；
- 泛问题无强相关证据时不注入 memory；
- generation 使用明确的重复控制和停止条件；
- 记录脱敏的意图、满意度和失败标签。

这一步只允许生成新的 inference Release 并走 governed-inference workflow；不能直接重启当前服务
或替换线上 Release。

### Phase 1：精选数据基线

- 完成 v3 精选数据人工抽检与新授权；
- 固定 240+ validation cases；
- 比较 Base、Prompt-only、全量 LoRA、精选 LoRA；
- 重复和 max-length 门未通过则停止。

### Phase 2：结构化记忆

- 先上线只读 confirmed facts；
- 再加入显式确认写入、纠错和删除；
- 通过无证据、冲突、跨 owner、injection 和删除残留测试。

### Phase 3：候选与灰度

- 决赛配置多 seed 干净重训；
- candidate freeze、test-once、Artifact fresh-load 和人工安全审查；
- 小流量灰度，保留 Prompt-only/Base 回退；
- 监控重复、长度顶满、记忆空召回、错误事实、人工差评和 p95 latency。

回滚单位必须是完整 serving bundle：base revision、adapter、persona prompt、memory index、retrieval
policy 和 generation config，不能只回滚 adapter。

## 12. 明确停止条件

出现以下任一情况，停止继续训练并回到前一层：

- 精选数据抽检 precision 低于 90%；
- validation 数据与 train 存在 session/近重复泄漏；
- Prompt-only 已优于 LoRA，且 LoRA 主要增加重复或事实幻觉；
- 任何安全、隐私、身份或记忆硬门非 0；
- Run 缺 dataset/prompt/memory/protocol digest 或 Artifact round-trip；
- 需要读取 test 才能决定数据规则、超参数或 Prompt；
- 新结果无法在相同 Release 和 frozen protocol 下复现；
- 用户撤回授权或派生数据无法完成删除定位。

## 13. 可执行验收清单

### 数据

- [ ] v3 selection rule 已版本化且只用 train 制定。
- [ ] validation 只应用冻结规则，test 未参与筛选。
- [ ] 样本保留真实 role 边界和 assistant-only loss。
- [ ] exact/near duplicate、模板重复和 session overlap 为 0。
- [ ] 隐私、第三方、操纵、真人冒充硬过滤通过。
- [ ] 人工分层抽检完成，precision 达标。
- [ ] 新 selection/snapshot manifest、digest、approval 完整。

### 模型

- [ ] Base、Prompt-only、LoRA 使用同一输入和 generation 协议。
- [ ] val loss 只作诊断，产品质量分和硬门决定候选。
- [ ] 重复率、长度顶满率相对历史结果大幅下降并过门。
- [ ] 至少 100 次 LoRA vs Prompt-only 匿名配对盲测。
- [ ] 决赛配置跨 seed 稳定并从干净 Release 重训。

### 记忆

- [ ] 只检索 confirmed、未过期、当前 owner 的结构化事实。
- [ ] 泛问题和低置信度查询返回空记忆。
- [ ] 写入需要显式确认，模型生成不得自动回写。
- [ ] 冲突、纠错、删除和撤回可审计，删除残留为 0。
- [ ] 组合系统使用新的 post-index temporal holdout。

### 治理与上线

- [ ] 所有 Training Run 都经 immutable Release、Galatea 和 fixed Ray Driver。
- [ ] MLflow 仅经 Tracking/Artifact/Registry API 访问。
- [ ] final test 在 candidate freeze 后原子 claim 一次。
- [ ] 人工/安全审查与 promotion 是训练之外的独立动作。
- [ ] 当前服务替换前存在完整 bundle 回滚路径。

## 14. 本轮完成边界

本轮目标是生成一版精选数据草案、记录其不可变统计，并把后续优化路径写清楚。没有执行 optimizer
step、没有创建 checkpoint/adapter、没有读取 test 做选择、没有创建训练型 MLflow evidence、没有
变更 Registry alias，也没有替换当前 Ray Serve Release。
