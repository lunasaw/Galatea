# 真实回复 SFT 首轮试点记录

后续同目标上下文修正与复审见[第二轮记录](daily-topic-sft-v2.md)；本文保留首轮证据。

## 2026-09-18 的结论

已跑通「冻结数据身份 → 只读 train 消息 → 前向话题与上下文 → 原始真实回复 →
结构检查 → 外部机器质量预审 → 建议保留子集与审核页面」。本轮生成 200 条候选，机器建议
保留 146 条、排除 25 条、待定 29 条。当前产物是 train-only 数据草案，尚未启动训练。

机器审计估计的回应关联为 170/200（85.0%，Wilson 95% 区间 79.4%–89.3%），上下文完整为
178/200（89.0%，区间 83.9%–92.6%），均低于计划建议的 98% / 95%。这些区间仅描述本批机器
判断的抽样比例，不涵盖审核模型偏差；不能视为人工标注准确率，也不能从机器 keep 自证保留 precision。
因此这轮应返工上下文与配对策略，不能据此判定 P3 数据质量门通过或进入 P5 训练。

## 数据与执行范围

| 项目 | 结果 |
| --- | --- |
| 来源 | `wechat_35ad187b65c0ff1cb4e7`，沿用原 session split |
| 处理分区 | 仅 train；validation 未用于调阈值；test 正文未解析进候选或外部请求 |
| 选择 | seed 42，从 train 的 1,342 个自然日选择 50 天 |
| 消息 / session-day slice | 5,858 / 148 |
| 有前置 self 的目标回复 | 1,342；首轮取 200，其余 1,142 未处理 |
| 其他 target turn | 119 个没有紧邻前置 self，单独计数，不拼造问题 |
| 模型 / tokenizer | `Qwen/Qwen3.5-0.8B` / `2fc06364715b967f1860aea9cf38778875588b17` |
| 长度策略 | 上限 1,024，固定目标预留 256，完整轮次裁剪 |
| 实际序列 / 目标长度 | 45–451 / 3–51 tokens |
| 结构硬检查 | 非过去上下文、跨 split 上下文、目标混入输入、隐私硬命中、canary、静默截断均为 0 |
| 对照 | 同一批目标的 topic / window8 / causal；168 条 topic 输入与 window8 不同 |
| 机器预审 | 34 个批次、4 并发，35 次实际请求，全部完成 |
| API 报告用量 | 输入 184,802，输出 24,456 tokens |

前向话题采用可复现规则状态机，尚未实现外部模型的逐前缀语义标注。
category 不等于 topic ID；同类别仍需前缀引用或词项连续性才能接续。上下文选择器不接收目标正文，
目标 `reply_to` 只做离线核对；只有前缀本身已选中对应证据时，显式延迟回应才能进入候选。
跨 session 和跨日上下文关闭。该简单方案有意保留不确定性，后续需补强语义关联和指代完整性。

新候选使用独立 `topic-reply-candidate-v1`，不向旧 candidate v2 偷加字段。
`candidate_sha256` 绑定角色、内容、owner/split、来源、顺序、截止点、策略和 token 统计。
Driver 的 tokenizer 已针对该版本增加 digest、模板前缀、长度及监督边界检查，超长即拒绝；
真实 200 条已在模型项目环境完成编码回读与 assistant-only mask 检查，无模型加载或 optimizer step。
项目全量 205 项测试通过；配置检查、文档链接、保留子集重复导出和文件权限验证通过。
这项编码兼容不等于训练准入已完成：当前 Driver 的新审核方法准入、validation 分区和 Release 绑定仍待实现。

## 旧 test 暴露审计

只用源 ID、session split、日级证据映射和调用清单交叉核对，确认历史记忆处理中包含：

| 原分区 | 消息 | 会话 | 抽取块 |
| --- | ---: | ---: | ---: |
| train | 157,207 | 3,517 | 4,381 |
| validation | 37,433 | 439 | 662 |
| test | 31,988 | 441 | 571 |

旧 test 标为 `exposed_to_memory_processing`；后续最终泛化结论需要新的未暴露时间段或独立保留集。
本次不移动旧 test 样本进 train，不用它修正候选。消息文件是混合分区的 JSONL：先扫描 ID/时间/顺序，
再仅对所选 train 行执行完整 JSON 解码；没有从 test 行构造正文对象或向模型发送它们。

## 质量结果与不足

主要原因计数：可用回复 146、上下文不足 15、判断不确定 13、未回应上下文 10、控制或辱骂 5、
沟通信号不足 4、第三方信息 3、隐私风险 2、缺失媒体 1、不安全 1。

短回复分层有 102 条，建议保留 76 条，机器认为关联正确 88 条、上下文完整 91 条。
本批显式引用为 0 条，分类 unresolved 为 2 条，不能对引用和交错话题难例作质量结论；
报告中的 `mixed_topics` 是 unresolved 分类计数，尚不构成完整交错话题检测。
下一轮需主动补齐引用、延迟回应、交错话题等难例，分层达到足够样本量后重新核验。

当前报告使用机器审计，不包含人工逐条确认。建议保留 146 条单独导出供继续核验，原始 200 条及全部
决策均保留。未将新机器审核方法改名为旧 `gpt-prelabel-v1`，也未继承其训练资格。

## 私有产物与摘要

所有正文、页面和审核事件都在受控根目录，目录 700、文件 600：

```text
/srv/galatea-private/wechat-persona/
├── topic-sft/topic-pilot_108f7c241cea5360209d/
│   ├── manifest.json
│   ├── source-audit.json
│   ├── policy.json
│   ├── selection.json
│   ├── daily-bundles.jsonl
│   ├── candidates.jsonl
│   ├── comparison-arms.jsonl
│   ├── quarantine.jsonl
│   ├── report.json
│   └── review.html
├── topic-reviews/topic-review_36eb23713600d68b238a/
│   ├── identity.json
│   ├── <request-digest>.json
│   ├── decisions.json
│   └── report.json
└── topic-reviewed-drafts/topic-reviewed-draft_13326fc26d08c330a9d5/
    ├── manifest.json
    ├── train.draft.jsonl
    ├── review-decisions.json
    ├── quality-report.json
    └── review.html
```

| 证据 | 文件 SHA-256 |
| --- | --- |
| 结构报告 | `cf5557211404d5a763f60146136e24f3995956b2e05a0af0e7a59160d2655682` |
| 来源 / 暴露审计 | `e5ed9558816eba20015061e6ef49f2c09dfe4129a53f3516460099c704988d49` |
| 机器质量报告 | `ff87539d8da5c063c68771d0ffdac9f72183c678db146d42706c9efd7b719664` |
| 保留子集 manifest | `76617484d3859de0f94eb3796c2cb76fc6d735714f6e850630815ebf74796294` |

模型 API 没有不可变 revision，保存 requested/returned model、输入/输出 digest 和用量。
外部调用遵循本任务的继续实施授权及既有处理环境，批次绑定新的 `topic-reply-machine-audit-v1`。
只读估计为 78,792 输入 tokens；实际服务报告较高但仍在配置预算内。未配置 provider 单价，
不报告虚构金额。重试最多一次，失败保留缓存；缓存键包含候选、prompt、schema、模型、政策和端点摘要。

## 入口与下一轮

配置：[`daily-topic-sft-v1.yaml`](../configs/daily-topic-sft-v1.yaml)。

1. [`build_topic_reply_candidates.py`](../scripts/build_topic_reply_candidates.py) 提供 `--check`、
   `--plan`、`--execute`；后两者显式绑定 `--source`、`--memory`、`--consent`、
   `--tokenizer-path`、`--output-root` 和 `--controlled-root`。只读计划不创建数据目录。
2. [`prelabel_topic_replies.py`](../scripts/prelabel_topic_replies.py) 提供 `--plan/--execute`，
   指定 `--pilot`、`--consent`、`--output-root`、`--controlled-root`、
   `--authorization-reference`，端点通过 `OPENAI_BASE_URL` 或 `--base-url` 提供。
   凭据只从受控 `--auth-file` 读取，不写入配置、报告或日志。
3. [`export_topic_review.py`](../scripts/export_topic_review.py) 指定 `--pilot`、`--review`、
   `--output-root`、`--controlled-root`，生成不可覆盖的 train 草案和含机器状态的只读审核页。

下一轮先修正上下文充分性和回复配对，补齐难例并按计划分层核验；数据规则冻结后再单独处理 validation。
随后实现审核方法准入和新快照编译，形成 Release/预算/执行绑定后才安排受控 10-step smoke。
不以批次机器保留比例替代全流程质量验收。
