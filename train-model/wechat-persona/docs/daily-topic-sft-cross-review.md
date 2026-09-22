# 真实回复 SFT：跨模型家族核验

后续的来源分流、裁定工作台和合成判据检查见
[分歧分流与判据检查](daily-topic-sft-adjudication.md)。下文保留本轮原始核验结论。

## 2026-09-18 的结论

236 个审核视图全部完成：Claude 核验 200 条 v3 和 18 条改动前 v2，GPT 重新核验 18 条改动后
v3。原 117 条 GPT 共识仅有 59 条获得 Claude 支持（50.43%，Wilson 95% 区间 41.50%–59.33%）。
按预先固定的双模型证据、共同回应锚点和风险排除规则，导出 58 条机器共识草案。

审核模型之间的分歧很大，现有证据不能证明 P3 数据质量达标。v3 的结构修复成立，但不能据此
宣称语义质量改善。P3 保持未验收，P4–P6 尚未开始；本轮没有训练或最终测试访问。

## 固定总体与分歧结果

| 第三版 200 条的 Claude 判断 | 数量 |
| --- | ---: |
| keep | 70 |
| reject | 10 |
| uncertain | 120 |
| 回应关联正确 | 85/200（42.5%） |
| 上下文完整 | 86/200（43.0%） |

这些是该审核器按固定协议给出的机器判断。与旧 GPT 结果的差距包含审核器口径差异，不能解释为
v3 导致的数据质量下降；182 条输入事实上没有变化。

原 49 条保留分歧全部获得第三方模型家族的判断：

| 原先分歧层 | 固定数量 | Claude keep | Claude reject | Claude uncertain |
| --- | ---: | ---: | ---: | ---: |
| 原预审 keep、GPT 证据非 keep | 35 | 7 | 1 | 27 |
| 原预审非 keep、GPT 证据 keep | 14 | 3 | 1 | 10 |

18 条改动输入的 Claude 配对结果为：6 条 keep→keep、10 条 uncertain→uncertain、1 条
uncertain→keep、1 条 keep→uncertain，保留数均为 7。关联正确判断由 9 变 7、上下文完整由
10 变 7；小样本和服务非确定性仍在，不能用一条改善或净保留数不变自证修复收益。
同一批 v3 输入的 GPT 新审核为 keep 12、uncertain 6。

已恢复历史开场证据的那条样本，GPT 新审核为 keep，Claude 对改动前后均 uncertain，所以
没有进入共识草案。另一个未恢复远处必要前文的样本，两套机器证据均 uncertain。

## 58 条草案

当前输入的两套证据共同 keep 有 63 条，其中 4 条回应锚点不同、1 条有旧硬风险，最终选择 58。
全体 200 条的处理数闭合：58 选入、126 未共同 keep、12 有旧硬风险、4 条回应锚点不一致。
这些类别按既定优先级互斥统计；12 条风险中只有 1 条同时被两套当前证据 keep。

58 条中，51 条显式绑定完全相同的旧输入/原子来源，7 条绑定 GPT 新审核；55 条来自原 117 条
共识，3 条来自其余总体。覆盖率为 58/200（29%），这不是筛选后的准确率。58 条原始 candidate
行与 v3 完全一致，review_status 仍为 uncertain，机器语义依据保存在独立文件，不伪造人工通过。

## 冻结的核验范围

本轮延续 [证据盲审与第三版上下文修正](daily-topic-sft-evidence-audit.md)，不修改候选、目标、
split 或上下文选择器。固定 200 个 train 目标中，49 条在原预审与证据协议之间存在 keep/非 keep
分歧，18 条在 v3 改变了实际输入，两者交集为 6 条、并集为 61 条。

在读取本轮结果前固定以下范围：

- `claude-sonnet-4-6` 盲审全部 200 条 v3 输入。
- 同一 Claude 模型分别盲审改动前的 18 条 v2 输入；同一目标的新旧输入不进入同一个请求。
- `gpt-5.6-sol` 重新核验这 18 条 v3 输入。其余 182 条只有在实际 prompt、原子来源、角色、
  顺序、cutoff、owner、session/day、split 和目标完全相同的情况下，才显式绑定旧 GPT 证据。

请求不包含旧状态、旧理由、候选 ID 或分层信息。两个 requested/returned model 属于不同
家族，但仍通过同一服务，未取得服务端不可变模型 revision 或实现证明；结果是机器交叉支持，
不能称为人工真值或独立测得的 precision。

## 草案选择规则

只选择 Claude 与适用于当前输入的 GPT 证据均 keep、共享至少一条 self 回应锚点的样本。
两者必要证据的并集必须全部在实际输入中。旧预审或旧证据协议出现的 privacy、third_party、
control_or_abuse、unsafe、identity_or_capability 风险继续阻断自动纳入，不能被新多数票覆盖。

草案保留原始 v3 candidate 行，回应证据单独保存；机器推断不写成来源 `reply_to`，也不用于
看过答案后回填上下文。原 117 条共识的支持率固定以 117 为分母；有改动的样本使用独立审核的
旧 v2 视图计算该支持率，避免用修复后的样本替换旧总体。

所有产物保持 `human_review_completed=false`、`formal_training_eligible=false`、
`quality_gate_passed=false`、`training_ready=false`。不读取旧 test，不更新模型参数或模型别名。

## 接口兼容与预算

最初 3 次调用仅发送合成问答。Claude 的 Chat Completions 兼容接口未按 `response_format`
返回完整字段，严格解析拒绝了该结果，真实数据没有进入这 3 次调用。修正后在相同审核规则外
明确提供完整 JSON Schema；允许剥离包住完整 JSON 的 Markdown 代码围栏，仍逐字段严格校验，
不推测或补造缺失状态、理由、布尔值和引用索引。

GPT 的 Responses 协议已有上一轮成功调用证据，本轮检查其 requested/returned model、原证据
摘要和端点绑定。对新 Claude 传输重新执行一个合成探针，通过后才发送真实候选。

原总上限为 44 请求、350,000 输入和 132,000 输出 tokens。前 3 次探针消耗 3 个请求，保守
占用 3,168 输入和 9,000 输出额度；剩余配置为 41 请求、346,832 输入和 123,000 输出，禁用
自动重试。后续计划恰为 1 次合成探针 + 40 个数据批次，4 并发、每批最多 6 条。服务单价没有
配置，因此不报告货币成本。

原探针记录保留在
`/srv/galatea-private/wechat-persona/topic-cross-reviews/topic-cross-review_a83bd535a6d0ad919de1/`。
失败产物不被标为完成审核；剩余额度在配置中扣除，没有因修复实现而重新获得整轮预算。

第一轮数据请求结束时，236 个视图中 206 个通过严格校验，5 个批次未通过；缺少视图没有被计为
reject，也没有发布完整草案。对这 5 批执行单独、有限的格式故障恢复：最多 10 请求、80,000
输入和 30,000 输出 tokens，每批最多额外重试一次。这是原 44 次之后的补充故障恢复预算，
不包含重新询问已经有效的 keep/reject/uncertain。

`--resume-from` 逐批验证原请求内容、model、源绑定、决策与缓存摘要，显式导入 35 个已成功
数据批次和 1 个合成探针，保留原 response digest 与来源工作区。只为 5 个缺失批次调用 API。
恢复输出使用新的身份，并绑定上一轮 identity、report、decisions、budget 和已导入缓存的文件
摘要。恢复后的原始响应保存在私有目录，失败原因只输出校验类别，不输出聊天正文。

恢复实际调用 6 次，5 个缺失批次全部完成，其中 1 批发生一次协议重试。合计为原 44 次加补充
6 次，共 50 次请求；服务报告总输入 222,080、总输出 20,059 tokens，无缺失用量的请求。
最终工作区报告中的 `usage_cumulative` 只统计恢复阶段的 6 次；总数由三个独立预算账本相加。

## 入口与验证

- [`topic-cross-review-v1.yaml`](../configs/topic-cross-review-v1.yaml)：固定模型家族、预算、
  风险延续和机器审核身份。
- [`topic_cross_review.py`](../src/wechat_persona/topic_cross_review.py)：输入/来源等价绑定、
  两种 API 传输、严格证据解析、持久预算、进程锁、缓存及不可变审核包。
- [`cross_review_topic_replies.py`](../scripts/cross_review_topic_replies.py)：`--plan` 为只读，
  `--execute` 执行冻结范围；完成后重复执行回读已核验摘要，不新增调用。
- [`test_topic_cross_review.py`](../tests/test_topic_cross_review.py)：合成契约测试；测试不访问
  实际服务、不加载模型权重、不执行 optimizer step。

参数为 `--before` v2 pilot、`--after` v3 pilot、`--review` v2 原预审、`--audit` 上轮证据审核、
`--consent`、`--output-root`、`--controlled-root` 和 `--authorization-reference`。服务地址通过
`OPENAI_BASE_URL` 提供，凭据从受控 auth 文件读取，不进入源码或报告。

输出包括全量审核页 `review.html`、200 条处理依据 `adjudication.jsonl`、独立响应
`decisions.json`、原样草案 `train.draft.jsonl`、回应证据 `reply-evidence.jsonl`、报告、预算和
manifest。私有正文只在受控目录，目录 700、文件 600；源码文档仅保存聚合结果和摘要。

项目全量 250 项测试通过。对最终 58 条草案另外完成真实 tokenizer 和 Driver 编码回读：
最大 717 tokens，仅末尾目标参与监督，无截断；原子来源、过去边界、train 分区、证据引用、
全部输入/输出摘要和文件权限检查通过。完成后使用相同参数重跑，恢复预算仍为 6 请求，没有
新增调用或改变已冻结报告。

## 私有产物与后续

最终工作区：

```text
/srv/galatea-private/wechat-persona/topic-cross-reviews/topic-cross-review_a1047dda4ea6a82881fb/
```

其 `--resume-from` 为同一根目录下的 `topic-cross-review_6280cba692aeb1a118ba`。原 v2/v3
pilot、旧预审和证据审核均保持原样。最终审核页展示全部 200 条的实际输入、原始回复、两套
意见、回应对象和未纳入原因。

| 产物 | 文件 SHA-256 |
| --- | --- |
| manifest.json | `4e11243cf9766656c4d817a54ab40bd707983dcfca8337a16fef466f7e651bf6` |
| report.json | `5fa5099809e1fdc2ca9ed2a31b4909c376957978eb145dd55e3ae6e81f733f0b` |
| train.draft.jsonl | `6f5333362730d371e777c5a10c7dd87ce360c73e7b9b4e742e2be8571d9be77c` |
| reply-evidence.jsonl | `4f3506d713d7b472d6ffac77b28e939ea26b50dbc4e799908c17c60e3aca73a5` |
| adjudication.jsonl | `79bcde4d4a7e5b52deab219e1a5457db42c919700151ebc36b90601d2351e70f` |

下一步先把“已有输入上的审核口径分歧”与“实际缺少前向证据”分开裁定，明确短回应、主体指代、
同话题续接的保留/延期标准，并处理 12 条旧风险与 4 条锚点分歧。继续实现只看前缀的语义配对
时，需要固定正负例和来源依据，不能从这些目标答案倒推输入。规则冻结后，再独立核验至少
200 条 validation 候选并完成 W7a 快照准入；不再反复询问这些有效机器标签直到得到 keep。
