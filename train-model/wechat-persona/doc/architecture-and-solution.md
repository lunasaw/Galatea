# wechat-persona 架构与实施方案

> 梳理日期：2026-09-07。代码基线：`86a87b1f6db4063f8491a957ab58a4fde0c910e1`。
> 依据是本项目源码、配置、测试及已有设计文档；未读取私有聊天、模型权重或线上 MLflow 数据。

## 1. 项目定位与当前结论

`wechat-persona` 将获授权的聊天导出加工为三类资产：用于学习表达风格的 SFT 数据、
可更新和删除的关系记忆、用于文本脚本的事件卡。项目 5–9 是同一个 workload 的五个阶段，
复用数据身份、评估协议和治理规则，不是五个独立服务。

核心设计是**关系事实放在外部记忆，表达风格由 LoRA 适配，应用通过质量与人工审核后启用**。
“只学习风格”是设计目标；当前样本构造主要做脱敏和上下文裁剪，尚不能据此证明私人事实不会进入权重。

当前交付属于**数据与治理基础模块 + 受限原型**。导入、审核、SFT 导出、记忆检索、删除、
评估辅助函数已有代码；真实训练执行器、正式评估编排、聊天生成与检索接线尚未形成完整闭环。
README 的 `code-and-contract complete` 应在这一范围内理解，不代表已有可用的人格模型。

| 阶段 | 职责 | 当前可核实的能力 | 尚需接通或验证 |
| --- | --- | --- | --- |
| 项目 5：数据工程 | 导入、脱敏、会话、审核、数据快照 | 本地函数及显式写入 CLI | 审核结果回写、完整授权链和真实数据验收 |
| 项目 6：记忆 RAG | 可追溯、按 owner 隔离的外部事实 | BM25、向量接口、有效期/冲突过滤、删除验证 | 数据到记忆卡转换；索引 CLI 执行；真实 embedding 质量 |
| 项目 7：风格 LoRA | 在统一架构内训练风格 adapter | 配置、就绪检查、运行绑定校验、直接训练阻断 | 固定 Ray Driver 内的训练、checkpoint、MLflow 父 Run |
| 项目 8：容量与 QLoRA | 用冻结协议决定是否扩容 | 配置差异检查、QLoRA 预检、扩容决策函数 | 真实资源/质量证据与兼容性验证 |
| 项目 9：本地原型 | 聊天及事件脚本 | 候选准入、AI 标识、回复兜底、脚本结构 | 模型加载、RAG 注入、脚本文本生成、完整启停控制 |

## 2. 图与阅读方式

- [项目架构图](system-architecture.html)：展示模块职责、主路径和待接入关系。
- [训练与验收方案图](governed-workflow.html)：展示正式执行所需的目标闭环。
- [验证与交付记录](verification.md)：记录图的校验、浏览器检查、测试结果及文件摘要。

图中“已实现”表示存在可调用模块；“契约已实现”表示接口或门禁存在；“待接入”表示当前
入口尚未完成相关行为。箭头同时包括目标集成关系，不表示已有持续运行的服务或调用链。
源码证据见本文第 10 节及 [架构节点来源](evidence/architecture-sources.json)。

## 3. 模块分层与依赖

| 层 | 主要模块 | 对外输入/输出 | 边界 |
| --- | --- | --- | --- |
| 操作入口 | `scripts/*.py` | CLI 参数、YAML、计划和结果 JSON | 当前主要是 CLI；`review_app.py` 不是 Web 应用 |
| 授权与数据 | `consent`、`importers`、`normalize`、`redact`、`sessionize`、`pipeline` | 聊天导出 → 脱敏消息、session、候选、清单 | 本地数据工程，显式执行才写入 |
| 人工审核与快照 | `review`、`datasets` | 审核候选 → SFT split 与 manifest | 仅保留审核通过样本，检查会话隔离与泄漏 |
| 关系记忆 | `memories`、`rag` | MemoryCard → 索引 → RetrievedMemory | owner、有效期、敏感度及事实版本过滤 |
| 训练与容量 | `training`、`capacity` | 配置、Galatea 运行绑定 → 就绪/阻断结果 | Ray 声明已存在，训练逻辑尚待接入 |
| 评估与物料 | `evaluation`、`safety`、`artifacts` | 冻结协议、输出/指标、Artifact → 验收结果 | 五组比较、候选冻结、test-once、摘要校验 |
| 应用 | `runtime`、`screenplay` | 候选身份/用户消息/事件卡 → 文本原型 | 本地文本使用；生成模型尚未接入 |
| 横向治理 | `governance`、`deletion` | 状态、血缘、撤回范围 → 状态与回执 | 需要上层编排接入全生命周期 |

平台依赖的职责是：Galatea 管理项目、Release 和执行授权；Ray 承载正式训练和持久评估；
MLflow 通过 Tracking/Artifact API 管理 Run 与物料；MinIO 是平台的物料存储后端。
这些是集成方案和仓库平台契约，本文不声称已验证任何服务的在线部署。
JupyterLab 是平台交互入口，本项目当前没有专属 notebook 或独立 Web 服务。

## 4. 数据处理方案

### 4.1 导入到审核候选

`import_chat.py --execute` 调用 `run_import_pipeline()`：核验 processing/text 授权，
读取允许目录内的导出文件，根据显式 speaker 映射区分 `self` / `target`，完成标准化和脱敏，
再进行会话切分与候选构造。TXT、CSV、JSON、HTML 支持是针对代码约定的格式适配，
不能推断为兼容任意第三方导出格式。

当前会话规则与数据格式：

| 项目 | 当前规则 |
| --- | --- |
| session 边界 | 不活跃间隔超过 120 分钟，或会话时长超过 720 分钟 |
| 同角色短消息合并 | 间隔不超过 120 秒，并排除代码列出的系统/支付/通话类型 |
| split | 按 session 时间顺序切为 80% / 10% / 10%，比例计数取整 |
| SFT 目标 | `target` 的回复作为 assistant；最多取之前 8 个 turn 作为上下文 |
| 因果约束 | 仅取目标回复之前的上下文，不引入未来 turn |
| 初始审核状态 | `uncertain`，不能直接进入正式 SFT |
| 数据身份 | 数据 ID 由来源摘要、授权摘要及处理版本共同推导 |

小数据量可能因取整产生空 validation 或 test，正式导出会拒绝空 split。
不能为通过门禁而随意重排已有评估人口。近重复组隔离是对已提供的
`near_duplicate_group` 字段做校验，当前管道没有自动发现近重复组的完整实现。

导入输出为版本目录中的：

```text
redacted/messages.jsonl
sessions/sessions.jsonl
review/candidates.jsonl
manifests/source_manifest.json
manifests/split_manifest.json
manifests/lineage.jsonl
reports/privacy_report.json
```

### 4.2 审核到正式快照

审核支持 `keep`、`redact_keep`、`reject`、`uncertain`。保留样本必须记录 reviewer 和时间；
`redact_keep` 需要修改内容和原因；拒绝需要原因。审核日志不写聊天正文，
但 `review_reason` 为自由文本，使用方仍需避免把敏感正文填入原因字段。

`build_dataset.py --execute --check-approved` 读取审核后的 JSONL 并导出
`train.jsonl`、`validation.jsonl`、`test.jsonl` 和 `manifest.json`。
导出检查审核状态、人工证据、assistant 回复、PII、跨 split session 和近重复组。
样本带 `assistant_only_loss: true` 元数据；真正的 loss mask 仍需训练数据整理器实现。

当前有两个需要补齐的连接点：`review_app.py` 只追加 ID/摘要事件并打印回执，
没有把审核后的样本物化为导出命令所需的 JSONL；正式导出自身也没有重新验证授权账本及
完整源 manifest。建议增加基于候选 ID 的审核结果合并器和可验证的快照清单，
将授权摘要、内容摘要、审核记录、split 摘要绑定后再开放正式导出。

## 5. 关系记忆与删除方案

### 5.1 RAG 的事实层

`MemoryCard` 包含 `memory_id`、`owner_scope`、内容、源消息/session、有效时间、置信度、
状态、敏感度、`fact_key`、审核和血缘信息。默认只检索 confirmed、未过期、非 high 敏感度的卡片。
同一个 `fact_key` 按确定的版本顺序选择最新事实，防止旧事实与新事实同时作为有效证据。

实现提供 BM25 与 embedding 两种索引，当前索引是本地文件，并没有独立向量数据库服务。
`_embed()` 可以接收模型的 `encode()` 或可调用对象；未提供真实模型时使用确定性哈希向量，
该模式适合组件验证，不能作为语义 embedding 效果的证据。检索要求模型版本和索引声明一致。

`build_grounded_messages()` 可将检索正文和记忆 ID 放入受约束的上下文。
`chat_local.py` 当前尚未调用它，也未调用 `retrieve()`。
`build_memory_index.py` 当前仅计划并明确返回 `will_write_index: false`。

建议集成路径：审核资产 → 人工确认的结构化记忆卡 → 按版本构建索引 → 检索评估/删除验证 →
候选准入后加载索引 → 在每次请求中按 owner 检索 → 构造有来源的上下文 → 生成与输出检查。
输入应先完成用途与权限核验；不能把 `owner_scope` 字符串检查等同于用户身份认证。

### 5.2 撤回与失效

RAG 层支持按 memory、session 和 consent scope 删除，重写索引并验证目标已不可检索。
`deletion.py` 按源消息、session 或 consent scope 规划关联对象；若多个对象共享文件，
删除计划扩展到共享路径，当前执行粒度可能是整个文件。
远程 Artifact 的失效依赖调用方提供 `invalidate_artifact` 回调。

建议以统一血缘账本贯穿数据、记忆、事件卡、Run、adapter、checkpoint 和备份。
撤回时先禁用候选与索引，再完成删除、物料失效和验证。只删 RAG 条目不能证明训练权重已经遗忘；
使用相关数据训练的候选必须失效，后续是否重训由新的授权与评估流程决定。
当前 `pipeline.py` 的血缘主要覆盖 session 和候选，尚不等同于完整下游撤回闭环。

## 6. 训练、评估与容量方案

### 6.1 唯一正式执行边界

`galatea.project.yaml` 声明 `executionBackend: ray`，训练入口为
`scripts/submit_train.py --run`。当前入口收集 Galatea/Ray 环境绑定后调用
`run_training()`；该函数即使通过就绪和身份检查，也返回 blocked，要求固定 Driver 拥有模型更新。
`scripts/train.py` 是明确阻断本地训练的兼容入口；`evaluate.py --run` 同样阻断直接持久评估。
**不能通过填写占位配置、伪造环境变量或普通 `ray job submit` 获得正式执行资格。**

接入后的闭环应为：不可变数据和 split → Release → Galatea readiness 与证据绑定授权 →
固定 Ray Driver → Driver 创建父 MLflow Run → 统一数据加载/训练/验证 → Artifact 发布和回读 →
候选冻结 → 一次性最终测试 → 质量与人工审核 → 本地启用。Smoke、Trial、容量对比和 Champion
共用实现，变化仅由配置和角色表达。

需记录数据/模型/tokenizer 版本、代码与环境摘要、完整超参、seed、资源预算、Release、
readiness、execution identity、Ray Job/Submission ID、MLflow Run ID 与 attempt ID。
当前 `verify_artifact_roundtrip()` 验证 API 下载文件和 SHA-256；“新进程实际加载 adapter”
仍需在 Driver 的验收流程中完成，单独哈希通过不足以证明可加载。

### 6.2 五组冻结评估

| 变体 | 提示词策略 | 外部记忆 | 风格 adapter | 回答的问题 |
| --- | --- | --- | --- | --- |
| `base` | 基础 | 无 | 无 | 基础模型表现 |
| `prompt-only` | 冻结的优化提示词 | 无 | 无 | 提示词带来的收益 |
| `rag` | 同一优化策略 | 有 | 无 | 事实检索带来的收益 |
| `lora` | 同一优化策略 | 无 | 有 | 风格适配带来的收益 |
| `rag+lora` | 同一优化策略 | 有 | 有 | 组合后的质量与安全性 |

各组必须共享冻结样本、split 和 generation 口径；prompt 差异是协议中的显式变量，
不能在观察结果后临时调整。`validate_variant_matrix()` 检查五组身份和摘要一致，
`evaluate_variant_matrix()` 汇总相同 case ID 下的非空率和延迟；它本身不生成五组回答或完成盲评。

| 验收项 | 当前约定 | 实施注意 |
| --- | --- | --- |
| 主指标 | `lora_vs_prompt_only_win_rate`，方向 max | 项目注册门槛 ≥ 0.60 |
| 盲评样本量 | `blind_preference_report()` 要求至少 100 条才 `gate_passed` | 平局和不可接受均计入分母；项目 YAML 未单独声明样本量门 |
| 隐私与行为 | PII、canary、不安全行为计数为 0 | 额外检查冒充真人、依赖操纵等 |
| RAG | Recall@K、MRR、支持率、无证据时的不确定率 | 验证 owner 隔离、过期过滤和删除后不可检索 |
| Artifact | API 回读、摘要一致 | 正式闭环还需真实加载验证 |
| 最终测试 | 冻结候选后声明 test-once | test 不用于选模型、调 prompt 或调阈值 |

当前 `validate_safety_gates()` 对缺失的部分指标使用 0 默认值；`safety.py` 的报告字段与项目
quality gate 字段也不完全同名。正式评估应提供显式字段映射、样本数与必填校验，
避免把“没有测量”解释成“没有失败”。

`claim_test_once()` 通过文件锁和原子替换防止同一 `freeze_id` 重复声明，
但本地声明函数本身没有绑定 Galatea Submission，也不能证明实际 test 已执行。
Driver 必须验证角色、候选、split、协议和提交身份，再执行并持久化最终测试证据。
模型 Registry alias 更新始终是单独的显式审核动作。

### 6.3 已有模型配置与扩容策略

下表为仓库配置内容，不代表已下载、已验证兼容或已获训练授权。

| 配置 | 模型声明 | 训练设定摘要 | 资源声明 |
| --- | --- | --- | --- |
| `persona-lora-smoke.yaml` | `Qwen/Qwen3.5-0.8B` BF16 | 10 steps、batch 1、lr 1e-4、seed 42 | 4 CPU、16 GB 内存、1 GPU |
| `persona-lora-baseline.yaml` | 同上 | 1 epoch、`max_steps: null`；实际 role 为 `trial` | 同上 |
| `qwen3-1.7b-lora.yaml` | `Qwen/Qwen3-1.7B` BF16 | Trial、1 epoch、max_steps 10 | 4 CPU、24 GB 内存、1 GPU |
| `qlora-4b.yaml` | `Qwen/Qwen3-4B` | 4-bit NF4、double quant、BF16 compute | 4 CPU、32 GB 内存、1 GPU |

LoRA 配置均为 rank 8、alpha 16、dropout 0.05，目标模块为 `q_proj` / `v_proj`。
所有训练配置当前为 fixture/零摘要、`required`/`immutable` 占位及未通过的治理标记。
资源中的 `memory_gb` 是声明的内存预算，不能直接当作实测 GPU 显存需求。

容量比较应先验证瓶颈，再在冻结数据和评估协议下更换模型；
`capacity_decision()` 的默认要求是质量增加至少 0.05，且资源、安全和物料验证通过。
`compare_model_configs()` 限制只有模型、资源、task、run 等允许部分可变。
当前 0.8B baseline 和 1.7B 配置的 `max_steps` 不一致，不能直接称为只改变容量的公平对照。
4B QLoRA 还需环境版本、GPU、前向、至少两步受治理反向和 adapter round-trip 证据；
项目环境文件当前未声明 bitsandbytes，真实接入前需补足环境锁定和兼容性预检。

## 7. 本地应用方案

聊天目标请求链为：核验已接受且未撤回的候选 → 固定模型/adapter/协议身份 → 校验 owner 权限 →
按开关选择记忆检索 → 构造上下文 → 本地生成 → 隐私/行为检查 → 带“AI 生成内容”标识返回。
当前 `local-chat.yaml` 默认 `enabled: false`，CLI 不加载模型、adapter 或索引，
`safe_chat_response()` 未传 generator 时返回无可用模型的提示。

事件脚本由人工确认的事件卡驱动，保留关系阶段、目标、情绪转折、来源和事实摘要，
生成时间码与分场结构。当前台词/旁白是占位文本，不能描述为已接通 LLM 的脚本创作系统。
`screenplay.yaml` 虽声明 `runtime.enabled: false`，生成脚本未调用聊天的候选准入函数；
应在应用集成中明确脚本入口自己的授权与启用规则。
本项目设计不接入自动发消息、语音克隆或换脸功能。

## 8. 存储、配置和状态机

源代码、测试、配置、schema 与本文档进入 Git。真实聊天、身份映射、数据快照、索引、
生成文本、模型和删除账本放在仓库忽略的 `platform-data/llm-private/wechat-persona/` 受控区域。
部分现有 CLI 允许自选输出路径，README 中的存储约束并非所有写入函数均强制执行，
接入时应统一私有根目录与路径校验。

训练物料经 MLflow Tracking/Artifact API 管理，客户端不读取 `mlflow.db` 或 MinIO 服务端目录。
MLflow URI 由 `MLFLOW_TRACKING_URI` 提供，实验名由项目契约指定为 `wechat-persona`。
`conda.yaml` 是项目环境声明；版本列出不等同于已完成实际安装或框架兼容性验证。

`governance.py` 定义的完整状态顺序为：

```text
DISCOVERED → CONSENT_VERIFIED → IMPORTED → NORMALIZED_REDACTED
→ SESSIONIZED_SPLIT_FROZEN → REVIEWED → FORMAL_DATASET_READY
→ RAG_INDEX_VALIDATED → LORA_TRIAL_VALIDATED → CANDIDATE_FROZEN
→ TEST_ONCE_COMPLETED → HUMAN_SAFETY_APPROVED → LOCAL_PROTOTYPE_ENABLED
```

状态只能相邻推进；`BLOCKED` 和 `WITHDRAWN` 在该辅助函数中均为终态，没有自动恢复转换。
它检查状态顺序，不自行验证每次转换的全部业务证据，也尚未串联所有 CLI。
索引计划提到 `MEMORY_ONLY_SNAPSHOT_CONFIRMED`，但它不在此状态枚举中，
需要在上层编排明确只建记忆与正式 SFT 两条路径的关系。

## 9. 建议实施顺序与验收产物

以下为依据代码缺口整理的实施建议；本次只产出文档，不触发训练或模型推广。

| 顺序 | 工作范围 | 完成条件 |
| --- | --- | --- |
| 1. 数据闭环 | 审核回写、授权/manifest 绑定、私有输出根、全阶段血缘 | 脱敏候选可审核导出；同一源可追溯；撤回能影响完整下游 |
| 2. RAG 闭环 | 候选到 MemoryCard 转换、显式索引执行、真实 encoder 接入 | 按 owner 的检索评估、时效/冲突过滤、删除后不可检索均有证据 |
| 3. 受治理 Driver | 固定 Release 入口、数据整理/loss mask、训练/恢复、MLflow | 在授权的小预算 Smoke 中完成真实更新、物料回读与新进程加载 |
| 4. 统一评估 | 五组真实生成、盲评、指标字段/缺失值校验、test 绑定 | 相同冻结输入可比较；至少 100 条盲评；最终测试不可重复消费 |
| 5. 原型接线 | 模型/adapter/索引加载、上下文注入、统一准入、撤回失效 | 可关闭记忆；无证据时表达不确定；候选失效即停止服务 |
| 6. 容量实验 | 先对齐预算/协议，再评估 1.7B，满足前提后评估 4B QLoRA | 质量增益与时间、延迟、显存等实测成本共同支持决策 |

在第 3 步之前，适合继续做本地只读检查、合成组件测试、前向 fixture 与文档核验。
真实 optimizer 更新、持久训练/比较证据和最终测试必须使用声明的受治理路径，
并具备对应数据授权、Release、资源预算和执行授权。

## 10. 源码与原始设计依据

相对路径从本 `doc` 目录计算。行号用于定位本次基线，后续修改后以函数名为准。

| 结论 | 源文件 / 定位 |
| --- | --- |
| 项目后端、目标、quality gates | [galatea.project.yaml](../galatea.project.yaml) |
| 导入与产物 | [pipeline.py](../src/wechat_persona/pipeline.py)，`run_import_pipeline`，33 行 |
| 会话与 split | [sessionize.py](../src/wechat_persona/sessionize.py)，`sessionize` / `deterministic_split` |
| 审核结果与导出 | [review.py](../src/wechat_persona/review.py)、[datasets.py](../src/wechat_persona/datasets.py) |
| CLI 审核目前只写事件 | [review_app.py](../scripts/review_app.py)，`main` |
| 记忆结构与构造 | [memories.py](../src/wechat_persona/memories.py)，`MemoryCard`，53 行；`build_memory_cards`，214 行 |
| 索引、检索、上下文 | [rag.py](../src/wechat_persona/rag.py)，`_embed`，391 行；`retrieve`，465 行；`build_grounded_messages`，745 行 |
| 索引 CLI 只读计划 | [build_memory_index.py](../scripts/build_memory_index.py) |
| 固定训练边界仍阻断 | [training.py](../src/wechat_persona/training.py)，`run_training`，110 行；[submit_train.py](../scripts/submit_train.py) |
| 五组评估、冻结与一次性声明 | [evaluation.py](../src/wechat_persona/evaluation.py)，46 / 104 / 133 行；[evaluate.py](../scripts/evaluate.py) |
| Artifact API 与文件摘要 | [artifacts.py](../src/wechat_persona/artifacts.py)，`verify_artifact_roundtrip` |
| 运行时准入与安全回复 | [runtime.py](../src/wechat_persona/runtime.py)，151 / 186 行；[chat_local.py](../scripts/chat_local.py) |
| 状态、删除和失效回调 | [governance.py](../src/wechat_persona/governance.py)、[deletion.py](../src/wechat_persona/deletion.py) |
| 扩容与量化 | [capacity.py](../src/wechat_persona/capacity.py)、[配置目录](../configs)、[conda.yaml](../conda.yaml) |
| 事件脚本目前为结构化模板 | [screenplay.py](../src/wechat_persona/screenplay.py)、[generate_screenplay.py](../scripts/generate_screenplay.py) |
| 基础契约验证 | [项目测试目录](../tests) |

原始需求与设计保留在仓库级文档中：

- [项目 5–9 总体设计](../../../doc/train-llm/2026-09-06-project-5-9-wechat-persona/design.md)
- [原始实施计划](../../../doc/train-llm/2026-09-06-project-5-9-wechat-persona/implementation-plan.md)
- [项目运行手册](../../../doc/train-llm/2026-09-06-project-5-9-wechat-persona/runbook.md)
- [验收清单](../../../doc/train-llm/2026-09-06-project-5-9-wechat-persona/acceptance-checklist.md)

本文按源码澄清现状，并未修改原始设计或承诺其所有能力已完成。
