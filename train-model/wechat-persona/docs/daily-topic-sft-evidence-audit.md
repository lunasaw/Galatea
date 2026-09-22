# 真实回复 SFT：证据盲审与第三版上下文修正

后续已完成 v3 的跨模型家族核验，最新状态见
[跨模型家族核验与 58 条草案](daily-topic-sft-cross-review.md)。下文保留本阶段的历史记录。

## 2026-09-18 的结论

完成第二版全部 200 条候选的证据盲审，并对原先的 30 条问题样本另核对完整的同 session/day
过去消息。原来建议保留的 152 条中，117 条再次获得支持，35 条出现分歧。现有证据不能证明
机器保留质量达到 95%，P3 尚未验收，未启动训练。

已导出 117 条共同保留的 v2 草案与逐条回应证据；另外修正了误丢历史开场消息的问题，生成
同一批 200 个目标的 v3 候选。v3 改变 18 条输入，已恢复一个确定性遗漏；新版尚未机器复审，
不继承 v2 的审核结论。117 条草案和 v3 候选是两个有独立身份的产物。

## 固定分母的盲审结果

新协议要求返回回应对象和理解回复所必需的原消息索引。请求隐藏旧状态、旧理由、候选 ID 和
是否属于问题样本；只提供实际选中的前文和原始回复。响应必须引用本条真实过去消息，回应
对象必须为 self，keep 必须关联明确、上下文完整、无硬风险，置信度至少 0.9。

这是同一服务、同一 requested model（`gpt-5.6-sol`）在新协议下的独立调用，未取得不可变
模型 revision。它不等于不同审核者提供的独立真值，也不等于人工 precision。

| 原预审状态 | 固定数量 | 本轮 keep | 本轮 reject | 本轮 uncertain |
| --- | ---: | ---: | ---: | ---: |
| keep | 152 | 117 | 10 | 25 |
| reject | 18 | 5 | 6 | 7 |
| uncertain | 30 | 9 | 2 | 19 |
| 总计 | 200 | 131 | 18 | 51 |

原 keep 的机器复核支持率为 117/152（76.97%，Wilson 95% 区间 69.67%–82.95%）。这是协议间
的支持率，不能直接当作真实准确率。35 条分歧中，16 条上下文不足、7 条仍不确定、5 条回应
关联不足、3 条第三方信息、2 条缺失媒体、2 条控制或辱骂。

原 reject 中的 5 条、原 uncertain 中的 9 条获得新协议支持，保留为可能误排/延期的核验对象，
没有直接转入草案。新草案只取两次原输入审核均 keep 的交集 117 条，覆盖原总体的 58.5%；
不能再以该交集的 117/117 自证筛选质量。

## 30 条问题样本的归因

诊断请求只扩展到目标之前的真实同 session/day 消息，不含未来、跨 split 或事实记忆。
30 条的可用前文均完整读取，没有因预算裁掉消息；最长原始前缀为 209 轮、421 条原消息。
扩展视图单独审核，旧结论仍不发送给模型，且其结果不能直接用于训练数据的上下文选择或准入。

| 机器证据判断 | 数量 | 处理 |
| --- | ---: | --- |
| 完整过去消息仍不能支持回应关联 | 16 | 延期/排除，不能补造问题 |
| 关联可判断但仍缺上下文 | 3 | 保留证据不足状态 |
| 所需证据确实位于未选中的前文 | 2 | 检查能否由纯前缀规则恢复 |
| 原输入在本次核验已被判为足够 | 8 | 记录审核分歧，不归因于扩大上下文 |
| 扩展视图判断改善但没有新增必要证据 | 1 | 保留审核分歧 |

两条有遗漏证据的样本进一步定位为：

1. **历史开场消息被误删。** 原始前缀仅两轮，先是 target 主动发言，再是 self；第二版的
   `exchange_dependencies` 强制每条历史 target 前面必须有 self，因而删除了首轮。
   第三版根据源容器证明当前前缀包含 session/day 起点后，允许保留整段历史 target 开场消息。
   对从长历史中截出的前缀不启用该规则，不伪造缺失的 self 提问。
2. **较远的语义先行信息未被识别。** 所需三条消息位于当前输入前约 25–28 轮，与最后一轮
   self 没有共享双字词项，也未被规则分到同一话题。扩展诊断能识别这些证据，但不能把看过
   答案后选出的 ID 硬塞回输入；仍需只看前缀的语义锚点选择与独立核验。

## 第三版和配对证据

[`daily-topic-sft-v3.yaml`](../configs/daily-topic-sft-v3.yaml) 使用 `prefix-topic-openers-v3`。
来源、50 天采样、200 个目标、seed、64 轮历史上限、16 轮上下文上限、tokenizer、1,024 总长和
256 回复预留保持原协议。`prefix_complete_start` 由源消息容器首 ID 推导，不从答案推导。

v3 对固定目标生成 200 条候选，无替换或结构隔离；相对 v2 改变 18 条输入。两条机器诊断遗漏中
完整恢复 1 条，另一条仍缺三条历史证据。200 条通过实际 tokenizer 与 Driver 编码检查，仅监督
末尾目标，最长序列仍为 717 tokens。v2 的 200 条重新构造后逐字段相同。

117 条 v2 共识草案的输入、目标、原始 candidate digest 完全不变；语义回应证据单独写入
`reply-link-evidence.jsonl`。其中 55 条与原假设相同、51 条缩小到最后一轮 self 中的具体原消息、
9 条增加更早回应锚点、2 条转指更早的 self。后两类仍只引用原先已选中的上下文。
这些是机器语义核验，不冒充聊天源中的真实 `reply_to`，也不用于反向选择输入。

## 执行、预算与验证

配置为 [`topic-evidence-audit-v1.yaml`](../configs/topic-evidence-audit-v1.yaml)。长输入采用紧凑
原消息数组并按 token 预算拆批：原输入 34 批、扩展诊断 6 批，4 并发，40 次请求全部完成。
计划上限为 44 请求、350,000 输入、132,000 输出 tokens。服务报告实际输入 329,741、输出
52,734 tokens；所有请求均返回用量。没有服务单价，不报告虚构金额。

预算账本在请求前持久化预留，进程锁阻止同时重复执行。完成后校验 manifest 与输出摘要并直接
返回已冻结报告；本轮完成后重复执行没有新增请求，草案重复编译也返回原产物。
代码、策略、前文或审核版本改变会生成新身份，不把旧审核静默搬到新版本。

项目全量 236 项测试通过。新增测试覆盖隐藏旧结论、索引与角色校验、未来/跨 split 拒绝、旧分母保留、诊断结果不得转成
训练上下文、配对证据绑定、历史开场恢复与截断边界、重跑零请求，以及结构修复不继承质量标签。
没有模型权重加载、optimizer step、MLflow 训练证据或最终测试访问。

## 私有产物与入口

以下路径相对 `/srv/galatea-private/wechat-persona/`。目录为 700，文件为 600，正文未进入源码：

```text
topic-evidence-audits/topic-evidence-audit_503cfbf4af33fa74d9dd/
  manifest.json, report.json, decisions.json, budget.json, review.html
  consensus.train.draft.jsonl, <request-digest>.json
topic-evidence-drafts/topic-evidence-draft_3ca3dad7598deb73d8e6/
  manifest.json, selection.json, train.draft.jsonl, reply-link-evidence.jsonl
topic-sft/topic-pilot_a6734662ea89717b99c8/
topic-comparisons/topic-context-repair_f3f601d42e7df28d7460.json
```

审核页包含全部 200 条旧/新状态、实际输入、原回复、回应对象与必要证据；30 条问题样本还可展开
完整过去消息，未选入的内容以红色标示。所有产物仍是 machine 来源，
`human_review_completed=false`、`formal_training_eligible=false`。

| 证据 | 文件 SHA-256 |
| --- | --- |
| 证据盲审 manifest | `85e2da418161b96866748b21dac0f1696fbcbbec5964dac0bfb8c02c2c520ebd` |
| 证据盲审报告 | `bf36733794a8905ad148a5bfa44a2fb8ae9656e5825f86a1cfb1e157a582c01b` |
| 117 条草案 manifest | `dbfeac79540ec76f02a718ac26f9acc7ebf674a2f8afcc039abc0da267253299` |
| v3 pilot manifest | `544b9fd8586b2fd26049eacd4ee0e65dc113edfa6c0224d220fdab0ce0d97407` |
| v3 结构修复报告 | `43e997c66e7982bd7818aed33fac40d18e35af0b190f68f5257a2cc4fd518234` |

入口：

- [`audit_topic_evidence.py`](../scripts/audit_topic_evidence.py)：`--plan/--execute`，绑定 `--pilot`、
  `--review`、`--consent`、`--authorization-reference`、`--output-root`、`--controlled-root`；
  API 端点通过 `OPENAI_BASE_URL` 提供，凭据不进入报告。
- [`compile_topic_evidence.py`](../scripts/compile_topic_evidence.py)：指定 `--pilot`、`--review`、
  `--audit`、`--output-root`、`--controlled-root`，生成不授予训练资格的共识草案及配对证据。
- [`build_topic_reply_candidates.py`](../scripts/build_topic_reply_candidates.py)：使用 v3 配置及 v2
  `--reference-pilot`，其余来源、同意、tokenizer 与输出路径参数沿用前两轮。
- [`check_topic_context_repair.py`](../scripts/check_topic_context_repair.py)：指定 `--before`、`--after`、
  `--audit`、`--output-root`、`--controlled-root`，记录遗漏证据是否恢复，不迁移旧质量结论。

下一步应校准 35 条原 keep 分歧和 14 条原非 keep 的新支持项，并独立核验 v3 的改变输入；
不能靠重复询问同一审核模型直到通过。之后才冻结筛选和配对协议，处理 validation 与训练快照准入。
