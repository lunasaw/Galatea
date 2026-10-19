# 日级话题与真实回复候选

> 本文是日级回复候选链的现行说明。旧评审轮次、路由诊断、恢复配置和阶段报告已从
> 工作树移除，必要时从 Git 历史追溯。

## 范围

该链路从已授权、已脱敏且 split 已冻结的消息中，构造 train-only 的真实回复候选。
它只做数据准备：不更新参数、不创建 MLflow Run、不生成 checkpoint，也不授予正式训练资格。

现行实现只保留一套配置：

- 配置：[`configs/daily-topic-sft.yaml`](../configs/daily-topic-sft.yaml)
- 入口：[`scripts/build_topic_reply_candidates.py`](../scripts/build_topic_reply_candidates.py)
- 构建器：[`topic_candidates.py`](../src/wechat_persona/topic_candidates.py)
- 上下文选择：[`topic_context.py`](../src/wechat_persona/topic_context.py)
- 引用校验：[`reply_links.py`](../src/wechat_persona/reply_links.py)
- 输出 Schema：[`topic-reply-candidate.schema.json`](../schemas/topic-reply-candidate.schema.json)

## 不变量

每个候选必须同时满足：

- 仅读 `train` split，不物化 validation/test 正文；
- 上下文和目标属于同一 owner、session、day 和 split；
- 上下文的时间与稳定源顺序都早于目标回复；
- 引用只能指向同一受控范围内的过去消息；
- 上下文按完整 turn 选取，不将不连续片段伪装成连续对话；
- 目标正文不参与上下文选择；
- 使用固定 tokenizer revision、chat template、上限和目标 token 预留；
- 完整序列超长时失败关闭，不在训练时静默截断；
- 语义 digest 覆盖角色、源消息、顺序、文本、选择策略和 token 位置。

当日或 session 切片从 target 消息开始时，只有源数据能证明切片起点完整，
才能把该历史 target turn 放入上下文。否则保守排除，避免为历史回复虚构问题。

## 只读校验和构建

只校验配置：

```bash
python train-model/wechat-persona/scripts/build_topic_reply_candidates.py --check
```

读取受控输入并生成计划，但不写候选：

```bash
python train-model/wechat-persona/scripts/build_topic_reply_candidates.py \
  --plan \
  --source <controlled-source-snapshot> \
  --memory <controlled-memory-snapshot> \
  --consent <controlled-consent.json> \
  --tokenizer-path <immutable-tokenizer-directory> \
  --output-root <new-controlled-output-root> \
  --controlled-root <controlled-root>
```

`--execute` 使用相同参数发布不可覆盖的候选包。生成的数据仍然是
`human_review_completed=false`、`formal_training_eligible=false`；候选构建不等于人工审核、
正式 snapshot 或训练授权。

## 现行状态

代码保留经测试的候选构建和 Driver 编码约束。旧的多轮机器评审最终没有满足回应锚点、
上下文完整性和完整审核覆盖门槛，因此没有被合并为正式训练数据。相关阶段代码与报告
保留在 Git 历史中，不再作为当前入口。

后续若要训练，必须从新候选包开始，完成独立人工审核、冻结 validation 协议、
生成带不可变身份的 formal snapshot，再通过 Galatea 授权的 Ray Driver 执行。
