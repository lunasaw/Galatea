# 真实回复 SFT 第二轮：完整问答上下文与同目标核验

后续证据盲审、117 条共识草案和 v3 修复见[最新记录](daily-topic-sft-evidence-audit.md)。

## 2026-09-18 的结论

第二轮完成选择器修正、200 个固定目标的重建、机器复审、建议保留草案和前后对照。
回应关联由 85% 增至 88%，上下文完整由 89% 增至 89.5%；尚未通过计划的 98% / 95%
建议门槛。P3 仍需返工，P4 数据快照和 P5 训练尚未开始。

| 同一批 train 目标 | 首轮 | 第二轮 |
| --- | ---: | ---: |
| 目标 / 实际审核数量 | 200 / 200 | 200 / 200 |
| 建议保留 / 排除 / 待定 | 146 / 25 / 29 | 152 / 18 / 30 |
| 关联正确 | 170（85%） | 176（88%） |
| 上下文完整 | 178（89%） | 179（89.5%） |
| 结构隔离 / 替换目标 | 0 / 0 | 0 / 0 |
| 序列最大长度 | 451 | 717 |

关联比例的 Wilson 95% 区间为 82.8%–91.8%，上下文完整为 84.5%–93.0%。这些是机器判断
的区间，未包含审核模型偏差；不是人工标注准确率或保留 precision，也不是模型效果评测。

## 修正与不可变对照

新增配置 [`daily-topic-sft-v2.yaml`](../configs/daily-topic-sft-v2.yaml)，选择策略版本为
`prefix-topic-exchanges-v2`。内部话题状态仍使用 v1 前向规则，没有把规则分类冒充模型语义关联。

- 最近八轮优先；历史 target 回复与其前置 self 轮成组加入，前缀显式引用递归补齐。
- 随后补入当前话题的历史证据，再用剩余预算补充更早的前文；每次以完整依赖组检查长度。
- 无法完整加入的历史组省略；当前消息的必需引用缺失、非过去或超预算则拒绝候选。
- 仍限定同 owner、train split、session、自然日；最多读取前 64 轮、选入 16 轮，目标预留
  固定 256 tokens，总长不超过 1,024。选择器不接收目标正文或目标侧引用。

`--reference-pilot` 校验首轮 manifest、来源、tokenizer、采样、合并和编码协议，逐条比对真实
目标内容，并按原顺序尝试原来的 200 个目标。失败会进入 quarantine，不能换成容易样本。
对照报告以原 200 个目标为分母，隔离项也不能通过缩小分母提高比例。

151 条输入改变，49 条输入完全相同。在改变输入的样本中，关联判断 10 条由错变对、3 条
由对变错；完整性判断 11 条改善、7 条退步。在输入完全相同的 49 条中，关联判断仍有
1 条改善、2 条退步，完整性有 3 条退步。这说明存在审核模型或批次上下文的波动，不能把
全部净变化归因于选择器，更不能据此宣称新方案已验证优于窗口基线。

## 剩余问题与分层

关联或上下文任一项失败共 30 条。11 条已包含当前 session/day 内全部可用前文，其中 7 条
位于会话当天切片开头；另 19 条尚有更早前文未选入，其中 4 条达到 64 轮历史上限。
“还有前文”只表示结构上可继续核对，不代表这些前文一定能解释真实回复。
缺失的语义、媒体、线下交流或跨会话证据不能由模型补造。

主要原因：上下文不足 16、判断不确定 14、未回应上下文 7、第三方信息 5、沟通信号不足 4、
缺失媒体 1、隐私风险 1，其余 152 条机器建议保留。

短回复 102 条，机器建议保留 81 条，关联正确 93 条、上下文完整 94 条。
选中前缀包含多个已知类别的样本有 55 条，其中关联正确 51、上下文完整 50；这只是难度代理指标。
规则可检测的话题回转只有 4 条，显式引用仍为 0 条，无法对这些关键分层作质量结论。
新报告将 `unresolved_category`、`multiple_context_categories`、`context_topic_return` 分开，
不再把 unresolved 数量当作交错话题数量；各项仍不是人工标注的语义真值。

下一步先核对这 30 条的原始前缀证据：区分选择器遗漏和输入本身不足，完善前缀语义关联及
延期规则，补充引用/延迟/话题回转难例，再独立核验保留 precision 与误排。仍需保留完整候选
分母与筛选覆盖率。规则冻结并通过数据门后，才处理 validation、快照准入和受控 smoke。

## 执行与验证

机器审核保持首轮同一 prompt、schema、模型和 API 政策。34 个批次全部完成，4 并发，实际
发出 35 次 HTTP 请求，其中 1 次请求未返回用量；服务已报告输入 215,435、输出 22,844 tokens。
未配置服务单价，不估算金额。预算上限仍为 36 请求、350,000 输入、172,800 输出 tokens。

新增持久化预算账本：发请求前预留；失败、超时、无效响应和中断不退回额度；实际报告量高于
估计时按较高值计入后续准入。账本通过文件锁串行更新，重启与并发不会重新获得完整预算。
输出以每次请求最大输出预留；服务未报告的实际用量保持未知，不计作零。

项目全量 220 项测试通过，验证了依赖递归、超预算原子拒绝、缺失/未来引用、目标无关性、固定目标无替换、
审核波动分离及预算恢复/并发。200 条新版候选在真实 tokenizer 和 Driver 编码入口回读通过，
仅目标 tokens 参与监督；旧 v1 的 200 条候选重建后逐字段完全相同。没有加载模型权重或执行
optimizer step；没有读取 validation 或 test 正文，没有创建 MLflow 训练证据。

## 私有产物

以下路径相对受控根 `/srv/galatea-private/wechat-persona/`，正文与页面未写入源码：

```text
topic-sft/topic-pilot_a6d281885ebb5ee133b6/
topic-reviews/topic-review_bad17ddc18bf87d42117/
topic-reviewed-drafts/topic-reviewed-draft_0b32aa37efbea6198fdd/
topic-comparisons/topic-comparison_83d6ab0051936a41f305.json
```

审核草案包含 `train.draft.jsonl`（152 条）、全 200 条 `review.html`、机器决策和质量报告。
机器来源、`human_review_completed=false`、`formal_training_eligible=false` 均保留。
旧 test 历史暴露问题保持原结论；本轮未为取得最终测试资格重划 split。

| 证据 | 文件 SHA-256 |
| --- | --- |
| 第二轮结构报告 | `ec0fc2890356bc2da81ce9c842699c12bb86c8afeb84efaa8cc4ca66ae7e8112` |
| 第二轮机器报告 | `92cbf344758842667304cd607a06f57c8e6342d33c4a7291a2224bedee0f15fc` |
| 建议保留草案 manifest | `efd949aed20f9bad37d193b6f10576aa71824c18d0c34f6765ed200c7bcb8b1f` |
| 同目标对照报告 | `1dea8f66e6238eccaf5c6df5836bf85875c643c209d239ddfe2c47cc2ca87b7b` |

入口沿用 [`build_topic_reply_candidates.py`](../scripts/build_topic_reply_candidates.py)、
[`prelabel_topic_replies.py`](../scripts/prelabel_topic_replies.py)、
[`export_topic_review.py`](../scripts/export_topic_review.py)。重建时指定 v2 `--config` 及首轮
`--reference-pilot`；机器审核使用新 pilot，其余显式路径参数与首轮一致。
新增 [`compare_topic_pilots.py`](../scripts/compare_topic_pilots.py)，参数为 `--before`、`--after`、
`--before-review`、`--after-review`、`--output-root` 和 `--controlled-root`。
比较前校验完整产物摘要、审核覆盖、原始目标、tokenizer 和审核协议。
