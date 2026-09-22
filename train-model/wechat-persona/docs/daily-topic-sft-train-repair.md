# P3 train 返工：原子锚点、连续上下文与引用来源

核对日期：2026-09-19。该轮属于离线数据准备与组件验证，外部请求为 0。
旧 validation 验收保持封存；本页不将开发样本重建结果记作新的验证成绩。

## 已落地的修复

旧候选在没有显式引用时，把最后一整个 self 轮次记作 `responds_to_ids`。
同一轮可以包含多个问题，这个字段实际只是相邻轮次假设。
旧机器共识导出保持原候选不变，因而下游仍然拿到该假设。

新版 `topic-reviewed-candidate-v2` 把原字段移到 `reply_link_hypothesis`，
将两个模型共同支持的 self 原子消息写入有效 `reply_link`，必要上下文取两个审核的并集。
证据绑定 parent candidate、两个 decision、review manifest 的摘要；
`source_reply_to_claimed=false`，机器语义判断不会变成源文件引用或人工审核。
原输入、目标正文、消息血缘、cutoff 和监督 token 边界全部保持一致。

已从首轮、批量恢复、逐条恢复的 72 / 16 / 19 个原始响应记录重建 400 个判断。
同一批 200 个目标仍选出 99 条草稿，87 条未达双模型共识、12 条旧硬风险、2 条锚点分歧保持排除。

| 99 条草稿的锚点变化 | 数量 |
| --- | ---: |
| 缩小到轮次内的具体消息 | 46 |
| 转向更早的 self 消息 | 8 |
| 部分重叠但集合改变 | 1 |
| 与原假设一致 | 44 |

55 条有效锚点发生变化。该计数说明旧导出与已有审核的绑定问题得到修复，
不表示 99 条已经获得独立准确率验收。

## 连续上下文对照及退化隔离

新增 `prefix-contiguous-exchanges-v4`，只看目标回复之前的前缀：
从最近的 self 轮次向前扩展，历史回复必须连同提问保留，引用依赖传递闭合，
两端之间的轮次全部保留。某个完整交换超预算即停止，避免跳过它再拼接更早片段。
仍采用原 tokenizer revision、1,024 总长度、256 目标预留、最多 16 个上下文轮次，
不截断目标，不跨日、跨 session 或 split，不用目标文字或审核标签选择输入。
连续是指授权文本视图中的连续轮次，不能恢复已被过滤的媒体/引用消息或外部谈话。

原 200 个 train 目标全部重建，未替换目标，构建隔离数为 0；其中 86 条输入变化，
累计增加 32 个上下文消息位置、移除 206 个消息位置。67 条包含完整可用前缀，
133 条在较早完整交换超过预算时停止。全部新候选都是 `topic-reply-candidate-v2`，
有效锚点未决、`review_status=uncertain`，禁止搬用旧 keep 标签。

已逐条与旧审核证据做结构对照：旧回应锚点遗漏 0 条，必要上下文遗漏 1 条。
这 1 条原先属于 selected，故不能声称连续方案全面改善。单独的复审队列编译器
重算必要证据差集，阻断这 1 条与 12 条旧硬风险，冻结 **187 条待复审候选**。
200 条完整分母与所有排除原因都保留。草稿的原子锚点修复与新上下文实验是两份产物，
不会把新上下文替换进已审核的 99 条草稿。

## 原始引用缺失的实际原因

元数据扫描按字节跳过所有正文，仅解码 ID、类型、引用键和规范化记录位置。
原始 297,500 条消息中有 **7,074 条 quote**，全部携带引用；
当前 consent 的消息类型只有 `text`、媒体类型为空，这些 quote 均在授权类型范围之外。
已保留 train 157,207 条全部来自原始 text，原始引用字段与规范化 `reply_to` 都为空。
因此不能把该现象归因为已保留文本在导入时丢掉了引用。

另查明原始 `quoteServerId` 使用 server ID 命名空间：7,071 条能匹配 `serverId`，
3 条在本次导出中无匹配；直接匹配作为规范化消息 ID 的 `id`，匹配数为 0。
未来若纳入 quote，需要先使其内容用途和类型进入有效 consent，随后实施显式的
server ID → message ID 解析、唯一性、过去性和 split/session 校验，不能只取消类型过滤。
本轮未修改 consent、未导入 quote 正文、未解码最终测试集正文。

## 不可变产物

均位于受控根目录 `/srv/galatea-private/wechat-persona/`，目录 700、文件 600。
以下只公布目录名和摘要，不公开聊天正文或消息标识。

| 产物 | 目录 | manifest 文件 SHA-256 |
| --- | --- | --- |
| 修复包 | `topic-train-repairs/topic-train-repair_514ec4ba79eaf7207572` | `ece03689c850b5354364f2c14d41b1e99daa8c576f6b0ce0c88d2c1a6d9db316` |
| 复审队列 | `topic-repair-review-queues/topic-repair-review-queue_62987c93b63c680f325c` | `93018c7e86ba537fa37161b1f5ca889bc51ea03d428dc455af8beac57882ec7f` |

修复包包含 `train.atomic.draft.jsonl`、`context.candidates.jsonl`、`selection.json`、
`context-comparison.json`、`quote-provenance.json`、`policy.json` 与聚合报告。
复审队列包含 `review.queue.jsonl`、全体 200 条的 `dispositions.json` 和聚合报告。
发布使用 staging 和原子 no-replace；重放重新核验来源、原始响应、tokenizer 和所有输出。
既有文件被改动时失败，不覆盖已发表证据。

## 入口和验证

新增入口：

- [修复包编译](../scripts/repair_topic_train_drafts.py)：`--plan` / `--execute`；
  显式传入 pilot、review、raw、source、consent、tokenizer-path、output-root、controlled-root、base-url。
  base-url 仅用于核对旧审核网关身份，不发请求。
- [复审队列编译](../scripts/prepare_topic_repair_review.py)：`--plan` / `--execute`；
  显式传入 repair、output-root、controlled-root。
- [冻结修复策略](../configs/topic-train-repair-v1.yaml)。
- [20 项定向回归测试](../tests/test_topic_train_repair.py)。

定向测试涵盖原子锚点、证据篡改、禁止人工/正式资格升级、因果性、完整交换预算、
目标内容不能改变上下文、旧标签不可转移、引用命名空间、正文不解码、全分母排除、
不可变重放和私有权限。配置目录契约已登记新策略。

项目全量 **398 项测试通过**；实际修复 CLI 在 socket 连接被禁止的条件下完整重放通过，
重新扫描来源、重建判断、编码候选后得到相同 manifest 文件摘要。
复审队列重复发布及 Markdown 相对链接检查通过，`git diff --check` 无错误。

## 后续门槛

P3 仍处于返工后的待验证状态。下一阶段是对 187 条新输入开展绑定新候选摘要的机器审核，
对修正后的有效原子锚点做独立核验，之后再冻结新的验证批次。
2026-09-20 已执行该复审及传输恢复，累计 196/374 个有效判断；模型路由身份漂移后封存停止，
未导出新草稿。最新状态和预算见[新上下文复审与恢复](daily-topic-sft-repair-review.md)。
旧 validation 不能回改用于证明新方案达标；引用/交错稀有层仍不能在当前数据范围内宣称覆盖。
新格式也尚未接入正式训练 Driver 的准入协议。P4–P6 不放行，无训练、MLflow Run、
checkpoint、最终测试或模型发布。
