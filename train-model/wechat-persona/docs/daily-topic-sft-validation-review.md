# P3 validation 双模型审核执行记录

日期：2026-09-18。对已冻结的 200 条 validation 候选执行机器数据预审。
这是回复筛选规则的数据审核，不是模型质量评估或 Training Run；不生成正式训练集、模型或别名。
原协议与抽样见 [validation 核验协议](daily-topic-sft-validation-protocol.md)和
[样本准备记录](daily-topic-sft-validation.md)。
本文保留首次执行的未完成状态；后续缺失项恢复使用独立预算与产物，见
[validation 恢复与预算修正](daily-topic-sft-validation-recovery.md)。

## 1. 固定执行范围

新入口 [`review_topic_validation.py`](../scripts/review_topic_validation.py) 使用
[`topic-validation-review-v1.yaml`](../configs/topic-validation-review-v1.yaml)，精确绑定样本包
manifest `afe3e682a9da34007921991c3f34ad228d398612d9a74c4dfee7bb66558be335`。
`--plan` 校验来源、授权、历史协议门和当前路由后计算预算；`--execute` 才发送请求。

冻结 200 条全部参加审核，不使用开发批次的 58/142 分组、12 条硬风险或既有判定作为验证答案。
旧 99 条 train 草稿保持原样。若当前判断发现风险，则在本批次单独记录；不会将开发样本的
风险数量机械套用到不同的 validation 样本。

新增 [`topic_validation_protocol.py`](../src/wechat_persona/topic_validation_protocol.py)
明确接受 validation Schema 和原子来源，保留 v4 的四轴、不确定性证据、精确引文和保留规则。
候选始终是 validation，不临时改成 train。旧 v2/v3/v4 的源码、配置和原失败记录不变。
对全部 24 个既有开发用合成 fixture，新的两种请求格式和解析结果与原 v4 对照一致；
此检查证明适配行为一致，不算新的独立语义验证。

每条消息仍只发模型实际输入和目标原回复，不发送扩展参考、分层标签、旧判定或参考答案。
数据准备时保存的静态盲审包不添加机器标签。

## 2. 路由及预算

在新授权引用下先完成当前路由 preflight：4 次合成探针、1 次模型目录 GET，
`gpt-5.6-sol`、`claude-sonnet-4-6` 各精确返回两次，与已通过合成门的身份相同。
网关模型名仍不等于独立后端版本证明。

路由目录：`/srv/galatea-private/wechat-persona/topic-validation-reviews/topic-axes-v4-preflight_e4fd926868b6b0c7eb21`。
manifest 文件 SHA-256：`40a900c01a4cdfe0e8eabe39fbb3d931b5d7b83ee00acaef82b956d06cb5c6fe`。
路由上报输入/输出 16,890/295 token，预算计入 17,790/16,000；没有失败或未知用量。
该路由预算单独记录，不藏进 validation 的 440 次上限。

实际审核使用准备阶段已冻结的预算：两并发、逐条请求、400 次首轮，最多另 40 次失败修复，
每个 `(judge, sample_id)` 最多修复一次。修复按 `(judge, sample_id)` 排序，预算耗尽即封存
未完成状态，不递归补预算、不替换样本。有效 keep/reject/uncertain 均不重问。

首轮请求体估算输入 1,232,372 token；输入上限 1,800,000，输出预留上限 1,760,000，
单次输出 4,000，超时 240 秒。用量分别记录供应商上报、实际预留和预算计入量；不将预留输出
算成实际生成 token 或价格。

每次先持久化预算预留，再发送、保存原始响应、解析并记录用量。缓存绑定整个执行身份、
候选、请求 payload、prompt、Schema、配置、代码和精确模型名；中断后已有预留但没有响应的
请求不自动重发。原始响应已经显示身份漂移，即使在熔断状态落盘前崩溃，恢复时也禁止新派发。
连续三次 429/502 开启持久熔断；已在途的最多两请求可以完成，后续不继续派发。

## 3. 结果解释与工程验证

双模型均 keep 且有共同原子 self 锚点才记为 `machine_consensus_keep`。
其他处置为缺审核、两模型未共同保留、锚点不同。完整保留 200 条分母、各模型保留/排除/延期
及理由计数，按冻结分层分别统计。低置信度层只据有效响应中任一原始 confidence <0.9 加入；
若仍有缺失响应，该层明确标为未完整。

没有独立参考就不计算真实 precision，不把机器共识率当精度，不改变 P3 验收结果。
引用 0、交错代理 5 的既有数量缺口不会因审核完成而消失；独立盲参考仍未完成。
两个冻结样本包范围内的近重复诊断见第 5 节，不扩大为全量训练集的结论。
validation 候选已用于历史事实抽取的暴露限制继续保留，本轮不解析 test 正文。

执行前 340 项项目测试通过，后续加入修复上限及近重复测试后 346 项通过。审核新增 9 项测试覆盖
24 用例解析对等、角色/原子锚点/引文/未决轴
校验、train/test 拒绝、只读计划、有效排除和延期不重问、缓存重放、防篡改、精确身份漂移、
新授权路由前提、中断不重发、连续 429 熔断与预算耗尽不派发；48 个失败判断的模拟场景中
严格只修复前 40 个，剩余 8 个保留缺口，重放不追加请求。
入口在封存前从全部原始响应和预留流水重算最终判断；重放再检查原始响应、统计与报告一致。

所有产物在受控目录中保存，目录 700、文件 600；仅公开聚合值和文件摘要。
`human_review_completed=false`、`formal_training_eligible=false`、`promotable=false`、
`training_run=false`、`p3_accepted=false`，不导出可训练 validation 草稿。

## 4. 本轮实际结果

本轮在输入预算停止后封存为 `incomplete`，CLI 返回 2；这是已记录的未完成状态，不是进程崩溃。
实际发送 367 次首轮请求，失败修复发送 0 次，回收 365/400 个有效判断。没有模型身份漂移，
也没有触发连续传输失败熔断。

### 4.1 完整分母与剩余缺口

| 审核器 | 有效 / 应有判断 | keep | reject | uncertain | 缺失 |
| --- | ---: | ---: | ---: | ---: | ---: |
| GPT | 181 / 200 | 106 | 43 | 32 | 19 |
| Claude | 184 / 200 | 89 | 32 | 63 | 16 |

200 条候选中，181 条有完整双审：80 条机器共识保留、101 条未共同保留；另 19 条缺完整双审。
80 条是诊断共识，不是独立参考核实的正确样本，也没有导出新的 validation 草稿。

35 个缺失判断分为：33 个尚未发送（GPT 17、Claude 16），以及已发送但失败的 2 个 GPT 判断。
两个原始失败分别是 `URLError` 和 `invalid axes JSON`；后者响应标记为 completed、上报用量，
但顶层审核文本为空且 output 无内容块，无法本地解析补齐。
报告末态 `response_failure_counts.budget_exhausted=35` 包含因预算不足无法修复的这两个失败；
不能据此将 35 项全称为接口失败，或遗漏原始的两个错误。

GPT 的理由计数：可用 106、上下文无关 39、关联不确定 24、缺所指 6、缺媒体 2、隐私 2、
不安全 2。Claude：可用 89、上下文无关 32、关联不确定 54、缺所指 7、缺媒体 1、未定 1。
有效排除、延期和风险结论均完整保留，不因恢复缺失而重问。

| 分层 | 当前分母 | 机器共识保留 | 未共同保留 | 缺完整双审 |
| --- | ---: | ---: | ---: | ---: |
| ordinary | 64 | 22 | 35 | 7 |
| short_reply | 102 | 43 | 51 | 8 |
| explicit_reference | 0 | 0 | 0 | 0 |
| interleaved_proxy | 5 | 1 | 3 | 1 |
| low_confidence（尚未完整） | 174 | 70 | 101 | 3 |
| multiple_context_categories | 71 | 29 | 34 | 8 |
| unresolved_category | 3 | 3 | 0 | 0 |

分层重叠，不能相加。174 条低置信度成员仅由已返回的有效响应确定，缺失响应使成员集合仍可能
增加；`low_confidence_membership_complete=false`。其中 70 条同时为机器共识保留，符合冻结 v4
将分数用于诊断、以分轴状态及证据决定处置的规则。引用 0、交错代理 5 的数量缺口仍未解决。

### 4.2 实际用量与预算缺陷

| 用量口径 | 输入 token | 输出 token |
| --- | ---: | ---: |
| 主审核供应商已上报 | 1,721,515 | 78,948 |
| 主审核原始预留 | 1,129,603 | 1,468,000 |
| 主审核预算计入 | 1,801,560 | 1,468,000 |

1 次 `URLError` 无供应商用量，按预留计入预算；上报合计不是完整账单。计入值逐请求采用预留和
上报的较大值，输出预留不表示实际生成量。连同第 2 节独立 preflight，本轮共发送 371 次推理
请求，另 1 次模型目录 GET；没有调用训练入口。

GPT 的 182 次有上报请求，本地输入预估合计 559,952，实际上报 1,230,960，约为估算的 2.1983 倍；
Claude 对应为 566,598 / 490,555，比例为 0.8658。当前按 payload 分词并加固定余量的估算，
没有覆盖该 GPT 路由的上报口径，因此输入预算先于 400 次首轮完成而耗尽。

最终预算计入输入比 1,800,000 上限多 **1,560 token**。派发前按估算预留、响应后按上报结算，
两并发在途请求的结算仍会增加计入值；当前预算门不能保证计入输入绝不越线。
这是实际发现的预算缺陷，不能把本轮写成“完全守住输入上限”。供应商已上报输入低于该上限，
但一次未知用量及预算口径差异使这里也不能换算为准确费用。

下一步先在新版本修正按模型的输入预留与在途结算边界，再为这 35 个缺失判断单独冻结恢复方案。
本轮不改上限、不自动追加调用，也不改旧代码或有效判断来重启当前工作区。

### 4.3 封存及离线回放

产物目录：`/srv/galatea-private/wechat-persona/topic-validation-reviews/topic-validation-review_50804531424029506a43`。

| 文件 | 文件 SHA-256 |
| --- | --- |
| `manifest.json` | `232255196d89a954eaa5d0653a6fa16d64e07bc92aacbad520575bbc08c6d1b5` |
| `report.json` | `15792c67f3afc07cd90521590c4727a9c1edfd33231969272a12a5eeff933b12` |
| `decisions.json` | `73757f54f016366399e7b746876b51c3c989cab053a9f167bab3c4aece6ffcaa` |
| `cases.json` | `d327f1dc5e3531ce7f9b28a8c431712ff70f90edb0b63d01fa16e6514ce46d41` |
| 审核配置（项目内） | `d0cfc4e46a046d16c1293c8eed88a0412997f05b3229dde169a55ca30bb55a98` |

367 份原始请求记录（含失败）与预算预留逐一重算，365 个判断、200 条分流结果、用量及分层统计
与封存报告一致。
来源文件和冻结代码摘要、私有权限均验证通过。额外禁止所有外部 socket 连接后，实际 CLI
重放得到相同报告、相同未完成退出码，网络尝试为 0；manifest、报告、决定和分流文件摘要不变。
当前路由 preflight 与近重复诊断也已分别回放验证，没有追加外部请求。

本轮仍缺 35 个机器判断、200 条独立盲参考和稀有层证据，P3 未验收，P4–P6 未开始。
旧 99 条 train 草稿及盲审包保持原样，没有真实 precision、正式训练资格或人审完成声明。

## 5. 同期近重复诊断

新增独立的 [`topic-validation-duplicates-v1.yaml`](../configs/topic-validation-duplicates-v1.yaml)
和 [`audit_topic_validation_duplicates.py`](../scripts/audit_topic_validation_duplicates.py)。
规则在查看匹配结果前固定，既不读取机器判断也不改动原候选。保留 NFKC、casefold 和合并空白后
的模型输入与目标回复，排除所有样本共有的 system prompt；保留说话人标记和顺序。

上下文和回复分别计算字符 5-gram 集合的 Jaccard 相似度，两者都 ≥0.9 才标为疑似重复；
任一比较文本少于 20 字符时，该部分要求规范化后完全相同。
这是一条明确的诊断规则，未证明可以发现所有语义近义改写。共用短回复但上下文不同，不自动标重。

实际检查开发 200×验证 200 的 40,000 对和验证集内部 19,900 对，两个范围均无命中。
不删除、替换或重新划分任何样本，也不据此宣称未来全量训练集已完成去重。
审核运行时的冻结报告仍保留 `near_duplicate_audit_completed=false`；本次后续诊断有独立
manifest，不回写原状态。P3 的独立参考和稀有分层缺口仍存在。

产物目录：`/srv/galatea-private/wechat-persona/topic-validation-duplicates/topic-validation-duplicates_50c0ea252dbc51c070fb`。

| 文件 | 文件 SHA-256 |
| --- | --- |
| `manifest.json` | `b64fbc72febd233e3b8b54c4c737b849815eba9195f2fab7ebec9b615a0d9a65` |
| `report.json` | `f24f480d9b25ab84adb0f63b17ce64976f9f7d612d6feeb7f46a430a718a3ce8` |
| `pairs.jsonl`（空） | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| 配置（项目内） | `588dae2f71dcb6d05b3d1636c7ca9854c86979eea165ebeaca9c006ae0c15606` |

5 项新增测试验证规范化重复、长文本近重复、公共 system 和不同上下文短回复不误报、
split/样本上限、计划不落盘、私有发布与不可变重放、篡改拒绝。实际审计重放无新增请求，源数据不变。
