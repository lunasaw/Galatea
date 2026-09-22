# P3 validation 缺失判断恢复与预算修正

日期：2026-09-18。在用户新的继续指令下，为[上轮 validation 审核](daily-topic-sft-validation-review.md)
留下的 35 个缺失判断建立独立恢复批次。原 365 个有效判断、200 条候选、v4 规则及旧预算记录
保持原样；这是机器数据预审，不是训练或模型质量评估。

## 1. 冻结范围与入口

新增 [`topic-validation-recovery-v1.yaml`](../configs/topic-validation-recovery-v1.yaml)、
[`topic_validation_recovery.py`](../src/wechat_persona/topic_validation_recovery.py)、
[`topic_validation_budget.py`](../src/wechat_persona/topic_validation_budget.py) 和
[`recover_topic_validation.py`](../scripts/recover_topic_validation.py)。

配置精确绑定父 manifest 文件摘要
`232255196d89a954eaa5d0653a6fa16d64e07bc92aacbad520575bbc08c6d1b5`。
从原始记录重算父批次，确认 GPT 缺 19、Claude 缺 16，按 `(judge, sample_id)` 固定排序；
不按旧置信度、状态或内容优先选择。继承的保留、排除、延期都不得重问。

请求内容、单条批大小、生成参数、精确模型名及 validation 解析器与父批次相同。
新的授权引用必须不同于父批次，并绑定当前 preflight。`--plan` 校验来源和最坏情况预算，
不创建输出或发送请求；`--execute` 在独立工作区执行并封存，不重开父批次。

## 2. 预算修正及其边界

旧预算按 payload 分词加固定余量预留，GPT 上报输入实际约为估算的 2.1983 倍。
新版本保持原 token 估算可追溯，并在派发前按模型应用固定预留规则：

| 项目 | 固定值 |
| --- | --- |
| GPT 单次输入预留 | 原估算 ×3 +512 token |
| Claude 单次输入预留 | 原估算 ×2 +512 token |
| 总输入预算 / 不参与派发的余量 | 700,000 / 20,000 token |
| 输出总预留 / 单次输出上限 | 280,000 / 4,000 token |
| 请求数 / 并发 / 超时 | 最多 70 / 2 / 240 秒 |
| 恢复首轮 / 失败修复 | 35 次 / 每项最多另 1 次 |

在真实执行前，逐请求确认新输入预留覆盖父批次全部 366 份已知上报用量；其中 GPT 182 份、
Claude 184 份。父批次另 1 次用量未知，不声称已核实。
35 次首轮预留 293,347 输入 token；全部各修复一次的最坏预留 586,694，低于 680,000 派发限额。
这些倍数只改变预算分配，不用于调整筛选规则、回复标签或 validation 样本。

预留先于请求持久化，低于预留的上报不释放已占用额度；两个在途请求的预留始终同时计入。
任一输入或输出上报超过单次预留时，如实记录真实值，并在账本内持久熔断；此后禁止新增派发。
连续 429/502 与模型身份漂移熔断仍有效。已有预留却无原始响应的中断请求不自动重发。
若崩溃发生在原始响应保存后、结算前，恢复时先核验并结算该响应，再允许后续派发。

预留基于已有接口用量证据，并非供应商承诺的绝对 token 上界。供应商若异常上报更大用量，
已在途请求仍可能结算；新实现负责保留实际用量并停止后续请求，不能宣称任意异常下费用都绝不越线。
本轮最多一次失败修复，预算或熔断停止后不递归创建恢复批次。

## 3. 当前路由与工程验证

新的 preflight 使用 4 次合成探针、1 次模型目录 GET；GPT/Claude 各精确返回两次，
身份与原合成协议门一致。上报输入/输出 16,892/358 token，预算计入 17,792/16,000。
此用量独立记录，不占用 35 项恢复预算。

路由目录：`topic-validation-reviews/topic-axes-v4-preflight_ffae405b057a76f01c84`，
manifest 文件摘要 `fa793892f5004fa68efdbec29d1e50b47d1f72f93192ffd679ecabdde1d2bc9e`。

恢复执行前 354 项项目测试通过，加入第 6 节诊断后全部 358 项通过。恢复新增 8 项覆盖只读计划、
仅缺失项恢复、继承排除/延期不重问、单次修复上限、
授权与父产物防篡改、模型分别预留、在途额度保留、超预留持久熔断、结算前中断恢复、无响应预留
不重发、精确身份与连续 429 熔断，以及完整/未完成结果的无请求重放。

目录为 700、文件为 600；公开材料只记录聚合值与摘要。旧验证协议及准备包不改写，
独立参考尚未完成，引用 0、交错代理 5 的数量缺口也不因机器恢复完成而消失。

## 4. 本轮实际结果

恢复已封存为 `incomplete`，CLI 返回 2。35 次首轮、4 次失败修复，共 39 次请求新增 31 个有效判断；
继承的 365 个有效判断逐条不变，现为 GPT 196/200、Claude 200/200，合计 396/400。
剩余 4 个 GPT 判断在首轮和唯一一次修复中均返回 HTTP 400；两次 payload 摘要逐条相同，
本地 JSON/UTF-8 检查有效，没有以空正文或本地猜测补齐。
父批次原有 URLError 和空正文两个失败项本次均已恢复。

| 审核器 | 有效判断 | keep | reject | uncertain | 缺失 |
| --- | ---: | ---: | ---: | ---: | ---: |
| GPT | 196 | 113 | 46 | 37 | 4 |
| Claude | 200 | 98 | 33 | 69 | 0 |

200 条候选中，85 条机器共识保留、111 条未共同保留、4 条缺完整双审。
共识由 80 增至 85，不是独立参考核实的正确样本数；没有导出新的 validation 草稿。

| 分层 | 当前分母 | 机器共识保留 | 未共同保留 | 缺完整双审 |
| --- | ---: | ---: | ---: | ---: |
| ordinary | 64 | 24 | 37 | 3 |
| short_reply | 102 | 44 | 57 | 1 |
| explicit_reference | 0 | 0 | 0 | 0 |
| interleaved_proxy | 5 | 2 | 3 | 0 |
| low_confidence（尚未完整） | 190 | 75 | 111 | 4 |
| multiple_context_categories | 71 | 32 | 39 | 0 |
| unresolved_category | 3 | 3 | 0 | 0 |

分层重叠，不能相加。未回收的 4 个判断使低置信度成员集合仍未完整，不将未知量默认为正确。

主恢复上报输入/输出 136,520/6,670 token，预算计入 333,693/156,000；8 次 HTTP 400 无供应商用量，
全部保留预留额度，因此上报值不能当完整账单。本轮没有超总预算、单次预留或模型身份漂移；
停止原因是四项各一次修复后仍失败，不是预算耗尽。它没有证明接口未来不会超出预留。

39 份原始请求记录、判断、分层和用量已独立重算；源码/输入摘要及私有权限核验通过。
禁止 socket 连接后实际 CLI 回放为 0 次网络尝试，结果、退出码和文件摘要不变。
preflight 也完成了禁止网络的重放。

产物目录：`/srv/galatea-private/wechat-persona/topic-validation-reviews/topic-validation-recovery-v1_ced5ccc87ae6a7a81af3`。

| 文件 | 文件 SHA-256 |
| --- | --- |
| `manifest.json` | `cda28b87f736b2cb2a6e5a0fa8f189f7de5190b6c69168cf1a3af422c9d119bb` |
| `report.json` | `e92551b05ccb7791957005cf1d763269bb5f3bc801e6095610c2497af62412da` |
| `decisions.json` | `5e4cd9ae87223408567088571dce0a1c6156dad5b6a9baa969c25b91fb8fcfa6` |
| `cases.json` | `f59096e2da2f74a4c20c90704a998c18f1cae9791941744dd4c442625b79c007` |

## 5. 不可变实现摘要

| 文件 | 文件 SHA-256 |
| --- | --- |
| 恢复配置 | `93fbdb621c659a4f4fed3753edef8da655d6386c5529a8db5926a10b354b0683` |
| 预算实现 | `8b81a254003cd9741486580dd141203483b70954b0127b2b7b748ee2384f48fb` |
| 恢复实现 | `978bd8c3ef3b5e78665a5859059993f8370cc9cbb0224c4df4f02735a74649dd` |

manifest 还绑定入口脚本、原验证实现、协议、配置、授权、数据包及前置产物。
完成机器审核也保持 `human_review_completed=false`、`formal_training_eligible=false`、
`promotable=false`、`training_run=false`、`p3_accepted=false`，不导出可训练 validation 草稿。

## 6. HTTP 400 的一次独立诊断

冻结传输层只保存 HTTP 状态而未保存错误正文，因此无法从这 8 份历史失败记录确定根因。
另加 [`topic-validation-http-diagnostic-v1.yaml`](../configs/topic-validation-http-diagnostic-v1.yaml)、
[`topic_validation_http_diagnostic.py`](../src/wechat_persona/topic_validation_http_diagnostic.py) 和
[`diagnose_topic_validation_http.py`](../scripts/diagnose_topic_validation_http.py)。
该诊断绑定本次恢复 manifest，固定选择按 sample ID 排序的第一个缺失判断，只发送原 payload 一次；
不修改内容、审核判据或模型，不追加审核决定，最多 20,000 输入/4,000 输出 token、120 秒。

响应正文私下保存，普通日志仅允许固定错误码/分类；未知错误字段不原样输出。
HTTP 200 也只作为路由诊断保留，不将偶然成功的第三次响应并入已封存判断。
新增 4 项测试覆盖来源/路由/新授权、错误正文私有保存与日志脱敏、仅一次请求、无响应预留
不重发、防篡改、零新增判断与无请求重放。

实际该相同请求返回 HTTP 200，没有复现之前的 400；因此不能断言是样本永久无效、输入格式问题
或内容策略拒绝。根因仍未确定。未新增判断，审核回收量仍为 396/400。
该次上报输入/输出 6,949/289 token，预算计入 20,000/4,000；禁止网络的回放没有追加请求。
连同 preflight，本轮总计 44 次推理请求和 1 次目录 GET，三段预算分别保存。

诊断目录：`/srv/galatea-private/wechat-persona/topic-validation-reviews/topic-validation-http-diagnostic-v1_d8fde45bd697e4b142fd`。
manifest 文件摘要 `45899111efd020cf1df3c0d56b06893450b81e0a4ae3700fcbd7d0744c7f3500`，
report 文件摘要 `d3ae400e30617f1e6d19fcaa172063b4dcd9e78f47a48179182016f72a2bd40a`。

下一步为剩余 4 项制定带 HTTP 错误正文保留的受限处理方案，并接通独立盲参考的录入和统计。
不能反复重问至获得理想答案，不能用诊断成功替代缺失审核，也不能把 HTTP 错误标为内容拒绝。
独立参考与稀有分层缺口仍在，P3 未验收，P4–P6 未开始。
