# P3 新上下文复审、传输恢复与草稿编译

执行日期：2026-09-20。范围是冻结的 187 条 train 新候选、两个模型的 374 个判断。
原 200 个目标仍是完整分母，12 条旧硬风险和 1 条必要上下文退化保持隔离。
本轮是数据准备和机器审核，不运行训练，不创建 MLflow Run，不读取最终测试正文。

## 协议和恢复边界

审核器原生验证 `topic-reply-candidate-v2`，复用已校准的 v4 分轴判据及不确定性证据。
每次请求只含一个候选；返回模型必须精确匹配 `gpt-5.6-sol` 或 `claude-sonnet-4-6`。
判断绑定新候选、消息索引、实际请求和 prompt 摘要。有效 reject/uncertain 不会因结果不理想重问。
旧 keep 不转移到新输入，机器一致不等于人工真值或独立精度。

第一次合成 preflight 通过后，真实审核在 28 次请求后因连续 502 熔断：
22 个有效判断、5 次 HTTP 502、1 次超时，352 个判断缺失。已封存该包，未导出草稿。
缺失中有 6 个曾尝试但失败，346 个尚未请求；有效判断均保留原始证据。

新的合成 preflight 再次通过后，恢复入口仅补缺失判断，并将并发从 4 降为 2。
已尝试失败的判断只剩一次机会；未尝试的判断最多首次请求加一次格式/传输修复。
每个判断跨父子包合计最多两次，预算跨父子包累计，失败请求的预留不退还。
原始响应先私有落盘，再记录 usage；中断后有预留但没有响应的请求不会自动重发。
模型身份漂移、超预留、连续传输失败均持久熔断。

| 预算 | 原始上限 | 恢复时剩余 |
| --- | ---: | ---: |
| 请求数 | 748 | 720 |
| 输入 token 预留 | 6,500,000 | 6,277,393 |
| 输出 token 预留 | 2,992,000 | 2,880,000 |
| 单次输出上限 | 4,000 | 4,000 |
| 恢复包额外 repair 请求上限 | — | 346 |

审核前、传输恢复前两个 preflight 各含 4 次合成判断和 1 次模型目录请求，计入各自探针账本；
真实候选审核预算与合成探针分开报告，不把探针 usage 混成私有数据审核 usage。
恢复计划最坏输入预留 5,735,477，低于剩余额度减 20,000 headroom。

## 实际结果：模型身份不稳定，审核未完成

恢复包新增 178 次请求，获得 174 个有效判断；另有 2 次响应未完整结束、
1 次分轴证据无效和 1 次模型身份不匹配。后者立即触发持久熔断：
请求 `gpt-5.6-sol`，HTTP 200 的完成响应却声明 `model=gpt-6-sol`。
没有把该返回解释成原模型，也没有扩大允许模型集合。

| 状态 | 数量 |
| --- | ---: |
| 继承有效判断 | 22 |
| 累计有效判断 | 196 / 374 |
| Claude 有效判断 | 184 / 187 |
| GPT 有效判断 | 12 / 187 |
| 缺失判断 | 178：4 个失败、174 个未派发 |
| 已完成双模型共识 keep | 10 / 200 |
| 已完成但非共同保留 | 2 / 200 |
| 尚缺审核的候选 | 175 / 200 |
| 预先隔离 | 13 / 200 |

Claude keep/reject/uncertain 为 110/21/53；GPT 为 10/1/1。
未完成的总体不能用于比较方案质量，10 条部分共识没有导出为新草稿。
`machine_review_completed=false`、`draft_exported=false`，P3 仍未验收。

父子包合计请求 **206 次**，已报告输入/输出 token 为 **590,886 / 22,629**；
6 次父包传输失败没有 provider usage，不能把上述已报告数当成完整账单。
累计保留输入/输出预留为 **1,419,735 / 824,000**，未超过原上限。
身份不匹配响应也计入实际 usage 和预留，没有退款式重置预算。

停止后另跑一次合成诊断，4 个判断通过，又返回原模型组合。
这只能证明当次探针可用，不能消除真实请求已经发生身份漂移的事实。
三次探针累计 12 次合成判断、3 次目录请求，已报告输入/输出为 50,676 / 893 token，
与真实候选审核分开记账。需先稳定原模型路由，或明确切换模型后新建协议并重新校准；
不能仅用一次探针成功解除身份熔断，也不能继承旧协议的模型精度结论。

## 第二次身份恢复与 GPT-6 路由诊断（2026-09-21）

在用户继续指令下，先用三个彼此独立的合成 preflight 验证原模型组合，12 个判断均精确返回
`gpt-5.6-sol` 和 `claude-sonnet-4-6`。随后新建 v2 恢复包，只继承前述 196 个有效判断，
不增加原 748 次请求、6,500,000 输入预留和 2,992,000 输出预留的累计上限。

v2 新发出 68 次请求后再次发生模型身份漂移并持久熔断。累计结果为 262/374 个有效判断；
112 个缺失中包含 1 个 `invalid_case_axes`、1 个 `model_identity_mismatch` 和 110 个未派发。
完整 200 条分母中已有机器共识 keep 36、非共同保留 40、缺审核 111、预先隔离 13。
按父链累计尝试次数，缺失判断中 108 个为 0 次、3 个为 1 次、1 个已达 2 次。
最后一项 Claude 首次因长度截断，第二次把 target 消息索引当作 self 回复锚点而失效；
不能猜测替换索引，也不能在旧协议的两次上限外发起第三次请求。
累计请求 274；输入/输出实际上报 1,078,549/45,016，保留计费预留为
2,081,220/1,096,000，仍未超过原总预算。v2 manifest SHA-256 为
`dcf20fb3a019d10ba0e3e4f6effe4c02fc1f8858767859eb53b20927e6ba20be`。

由于原路由在两批真实审核中均发生漂移，已实现独立的 GPT-6 协议：新 method、新 policy digest、
精确 `model=gpt-6-sol` 校验、三个连续探针、重新合成校准，以及不继承旧判断的 374 条从零审核。
该路径没有进入校准：正式第一个 preflight 在请求前发现 `/v1/models` 未列出 `gpt-6-sol`，
目录只列出 `gpt-5.6-sol` 等模型，因而请求账本仍为 0。未封存的诊断工作区为
`topic-axes-reviews-gpt6/topic-axes-gpt6-preflight_2e48612abc55d7cfed79`；其 identity/catalog
SHA-256 分别为 `8514d0a6893f1dd01215f86ca074b23637581d7cec7f6126e4a4879ab1c70ee4` 和
`55c618688a47d185b57a96ef5c2906119b40cacbf46a26a3311e91291f0b76cd`。该目录没有 manifest，
不得作为通过证据。

额外合成可达性诊断不进入审核证据：`gpt-6-sol` 经 Responses 和 Chat Completions 均返回
502，随后一次 Responses 返回 503；`codex-auto-review` 返回 `gpt-5.6-luna`；
`gpt-5.6-sol` 当次仍精确返回自身。三次 GPT-6 可达性尝试均为传输错误后停止重试。
没有把任何别名响应冒充 GPT-6，没有校准、私有审核、草稿编译、训练或发布。

同时发现 v2 执行 identity 记录了两个项目相对输入路径，而原 `replay_complete()` 固定用绝对路径
重建，导致从其他工作目录重放时误报 lineage 变化。新增只读兼容验证器，按项目根解析记录路径，
重新校验 manifest、父链、源摘要、原始响应、累计预算、decisions、cases 和 report；真实 v2 包已
离线复算为相同的 262 个有效判断、112 个缺失和 274 次累计请求，未修改封存包。
新增测试后项目全量 **420 项测试通过**；真实 v2 包在禁止网络调用时完成同值重放，
草稿编译器因不完整审核继续失败关闭且没有创建输出目录。

### GPT-6 路由再次尝试（2026-09-21）

按新的授权引用和全新 output root 重跑正式第一个 preflight，避免复用上一轮目录缓存。
网关新抓取的 `/v1/models` 快照仍未列出 `gpt-6-sol`，因此再次在任何推理请求前失败关闭；
没有创建 `budget.json`，正式审核请求数为 0。未封存工作区为
`topic-axes-reviews-gpt6-retry-2/topic-axes-gpt6-preflight_d633401829678174bd97`，identity SHA-256
为 `9b8b3a4c323e73851954d6e0cabe707512072c9367099083daebb55e7e339859`；目录快照 SHA-256
仍为 `55c618688a47d185b57a96ef5c2906119b40cacbf46a26a3311e91291f0b76cd`。

一次额外的有界合成直连返回 HTTP 502、`upstream_error`、无返回模型和 usage；随后检查公开别名
`gpt-5.6-sol` 返回 HTTP 200，但模型身份仍精确为 `gpt-5.6-sol`。认证文件仅提供当前 API key，
项目也没有另一个已授权端点。该轮没有 GPT-6 判断、校准或私有样本请求，不能解除门禁。

### 固定原模型的后续探针（2026-09-22）

用户明确要求继续使用 `gpt-5.6-sol`。使用新的统一授权引用，在三个互相独立的工作区顺序
执行 v4 合成 preflight；每轮四个判断均通过，GPT 返回模型精确为 `gpt-5.6-sol`，Claude
精确为 `claude-sonnet-4-6`。三轮 manifest 文件 SHA-256 依次为
`3d539aad91898c416827eb27d5cbd4641e860e0d3c455009c653b84793f51f3c`、
`6939cd9a9d33b28e097326ca96269fec452359d40ff032c843f721803b13538a` 和
`8fd307efd9da5c32fab3498ee5291f5cb49a0fcd9de90855ccd9cf7d7acc1090`。
这些是合成路由证据，不能保证之后每个真实响应均不漂移；后续仍须逐响应精确校验。

本轮未发送新的私有审核请求。v2 包仍有 112 个缺失判断：108 个未尝试、3 个曾尝试一次、
1 个 Claude 判断已尝试两次且两次无效。原协议每判断最多两次，因此即便其余 111 个全部
恢复，也不能仅凭原协议编译 374/374 的完整草稿。不能把用户指定原模型解释为授权第三次
请求；单项额外机会或人工裁定需要明确的新规则和授权，旧包始终保持封存。

## 不可变证据

私有根目录为 `/srv/galatea-private/wechat-persona/`。以下均为 manifest 文件 SHA-256。

| 产物 | 目录 | SHA-256 |
| --- | --- | --- |
| 原审核封存 | `topic-repair-reviews/topic-repair-review_43e778abf9608d76db3f` | `721703560b90045bb9bb6fc95719e56cc6665a02f0a814302fec809da45c9e64` |
| 恢复封存 | `topic-repair-recoveries/topic-repair-recovery_3e4fc766a07fc2961744` | `f9566fd1df7a64c96aaf41579a1cdc0a3752f8ba271411916e08bd62b956e363` |
| 初始探针 | `topic-axes-reviews/topic-axes-v4-preflight_764b35d28caccf226371` | `fe97d12fe45bc973c3fef99068897a04b2cd7ec8ad0e103f3795085e3dcf8c17` |
| 恢复前探针 | `topic-axes-reviews/topic-axes-v4-preflight_13646acb2566764a7710` | `7ea17d8b80101a08528624481a9e969273cff7f804cc7eecf6096dfa65f28c33` |
| 身份诊断探针 | `topic-axes-reviews/topic-axes-v4-preflight_bb190564aaf0fb3d7b51` | `35badfba7746bedeb13ab82b8f6e80e1cd8294569d809f49d906c7822c62db4b` |

## 编译和回放

编译器重新校验 queue、repair、consent、原始响应、父子调用账本和全部实现摘要。
只有完整的 374 个有效判断、无熔断、两模型 keep 且有共同 self 原子锚点时才导出对应行。
必要上下文取两模型并集；新有效锚点与旧相邻轮次 hypothesis 分开保存。
复审时看到的输入、原始目标和监督 token 均保持一致，不在编译时再次裁剪或选版本。

输出为不可变 `train.draft.jsonl`、全体 200 条的 `selection.json` 和聚合报告。
`human_review_completed=false`、`formal_training_eligible=false`、`promotable=false`、
`independent_quality_validation_completed=false` 始终保留。
重复执行重建并验证输出；不重新调用已成功判断，不覆盖父包或草稿。

## 入口和验证

- [审核入口](../scripts/review_topic_repairs.py)与[冻结审核配置](../configs/topic-repair-review-v1.yaml)。
- [传输恢复入口](../scripts/recover_topic_repairs.py)与[冻结恢复配置](../configs/topic-repair-recovery-v1.yaml)。
- [草稿编译入口](../scripts/compile_topic_repair_review.py)。
- [审核协议与中断测试](../tests/test_topic_repair_review.py)。
- [累计预算、缺失恢复和草稿绑定测试](../tests/test_topic_repair_recovery.py)。
- [第二次身份恢复入口](../scripts/recover_topic_repairs_v2.py)与[冻结 v2 配置](../configs/topic-repair-recovery-v2.yaml)。
- [GPT-6 探针/校准入口](../scripts/run_topic_axes_review_gpt6.py)与[冻结 GPT-6 axes 配置](../configs/topic-axes-review-v4-gpt6.yaml)。
- [GPT-6 从零复审入口](../scripts/review_topic_repairs_gpt6.py)与[冻结复审配置](../configs/topic-repair-review-gpt6-v1.yaml)。
- [GPT-6 路由、零继承和漂移测试](../tests/test_topic_repair_review_gpt6.py)。
- [v2 项目相对路径重放测试](../tests/test_topic_repair_recovery_v2_compat.py)。

三个入口都要求显式 `--plan` 或 `--execute`。审核需绑定 queue、repair、consent、preflight、
模型端点及授权引用；恢复另绑定封存父包和新的合成 preflight；编译只读本地证据，无外部调用。
受控目录权限为 700，文件为 600。源码及普通日志不存聊天内容或私有消息标识。
运行前设置 `umask 077`，并预先确认 output-root 为 700；冻结审核器的
`mkdir(parents=True)` 只显式设置叶目录，不能保证自动创建的父目录也为 700。
本轮已将两个新建审核/恢复 output-root 收紧到 700，未改变任何证据文件内容或摘要。

14 项审核/恢复定向测试、5 项配置检查通过；最终项目全量 **412 项测试通过**。
测试覆盖有效判断不重问、错误模型、超额 usage、中断预留、累计预算、禁止第三次尝试、
新探针和授权绑定、继承判断不变、不可变重放、证据篡改和不完整审核禁止导出。
新增恢复过程中模型漂移的回归用例，验证持久停止、离线重放和禁止导出。
实际恢复 CLI 在 socket 连接被禁止时完整重放通过，复算相同 manifest；
实际编译 CLI 的 `--plan` 拒绝不完整审核，新草稿输出目录未创建。
68 个文档相对链接、父子包 manifest 摘要、230 项目录/文件权限检查及 `git diff --check` 通过。

## 剩余验收条件

本轮属于 train 开发集返工。需要独立核验修正后的锚点，再冻结新 validation，
不能回改原验证集的 57% 锚点支持率、75.5% 上下文支持率来宣称通过。

当前 consent 仅允许 `message_types=[text]`、`media_types=[]`。
7,074 条 quote 在类型范围之外，显式引用层仍为 0；要覆盖计划要求的至少 20 条引用难例，
需有效授权纳入 quote 文本，或提供已获授权的独立引用样本。
届时还须实现 serverId 到规范化 message ID 的唯一性、过去性和 split/session 校验，
不能仅取消类型过滤。旧 train/validation/test 切分与封存证据不得被新导入覆盖。

P3 尚未验收，P4–P6 不放行。机器复审完成不会自动授权全量快照、GPU 训练、
最终测试或模型发布。背景与父包见[train 返工记录](daily-topic-sft-train-repair.md)。
