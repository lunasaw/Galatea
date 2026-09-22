# P3 独立 validation 准备记录

日期：2026-09-18。已冻结验证协议、200 条 validation 候选、分层成员和无机器判定的盲审包。
本记录对应的数据准备轮次，外部模型请求为 0；封存当时机器审核、独立参考核验和 P3 验收均未完成。
后续双模型执行及独立近重复诊断见[validation 审核执行记录](daily-topic-sft-validation-review.md)，
其产物单独封存，不回写本轮准备报告。

## 1. 实现和契约

新增 [`topic-validation-v1.yaml`](../configs/topic-validation-v1.yaml)、
[`topic_validation.py`](../src/wechat_persona/topic_validation.py) 和
[`prepare_topic_validation.py`](../scripts/prepare_topic_validation.py)。
冻结的判据、分母、分层定义、停止条件和证据限制见
[validation 核验协议 v1](daily-topic-sft-validation-protocol.md)。

旧候选 Schema 和证据视图只支持 train，保持源码与历史 hash 不变。新入口沿用 v3 的
上下文选择、原始回复构造、tokenizer、隐私及引用检查，使用明确的 validation 版本适配。
`topic-reply-validation-candidate-v1` 的 Schema 随产物封存，拒绝 train/test；不把 validation
样本伪装成 train 来调用旧审核器。两个 transport 的新请求格式均用同内容合成样本对照验证，
与冻结 v4 的 prompt、Schema、消息顺序和生成参数一致。

`--plan` 在内存中构造并核对候选、统计和预算，不创建输出目录。`--execute` 只发布数据包，
不执行推理或训练。目录 700、文件 600；来源及代码变动阻断，已存在产物只允许不可变重放。
按受控工作流保留所有机器/人工/训练资格边界，实现和测试均归属本项目。

## 2. 实际抽样结果

从现有 validation 的 182 个自然日中，按 seed 53 冻结 50 天；解码其中 10,079 条消息，
按 137 个 `(session, day)` 切片处理。另有 4 条未进入冻结 session 血缘的原导入记录，只扫描 ID
后跳过正文；没有将它们追加到 validation。

共识别 2,316 个候选目标尝试，按固定 hash 顺序处理首 200 个，全部通过确定性结构检查。
2,116 个目标未选中，221 个缺少相邻 self 的 target 轮次单独计为结构跳过，没有假装已审核。
最终 200 条分布在 42 天、71 个 session；与开发 200 条没有 source message/session 交集，
完整输入输出窗口的精确重复为 0。近重复检查尚未完成，不能把精确重复检查扩展为全面泄漏结论。

| 固定分层 | 样本数 | 准备状态 |
| --- | ---: | --- |
| 普通对话 | 64 | 数量达到 20，待质量核验 |
| 短回复（≤8 字符） | 102 | 数量达到 20，待质量核验 |
| 显式引用 | 0 | 证据不足 |
| 交错话题代理 | 5 | 证据不足 |
| 低置信度 | 尚未计算 | 待机器响应，不能用于反向选样 |
| 多上下文类别 | 71 | 诊断层 |
| unresolved 类别 | 3 | 诊断层 |

分层可重叠，不能相加。交错代理是已保留前文的 topic ID 返回，不是人工确认的交错话题。
稀有层不足不会触发重抽样、降门槛或自动 P3 验收。后续如需要专门困难集，应另冻结独立批次和
统计口径，不把补充样本并回当前 200 条后声称原验证门已通过。

200 条的扩展前文都通过自动结构/隐私检查；其中 52 条最多 64 轮的前文不是 session-day 的
完整起点，盲审证据保留这一限制。缺失的更早信息、媒体或线下情境不能被补造。
`review.html` 只提供模型输入、原回复和可展开前文，不展示机器判定，也不记录浏览即“完成审核”。

历史 validation 曾参加事实抽取（37,433 条消息），这批数据只能用于回复筛选规则的独立样本
核验，不能称为从未暴露的保留集。现有 test 的历史暴露状态不变，本轮未解析 test 正文。

## 3. 冻结产物

受控目录：
`/srv/galatea-private/wechat-persona/topic-validation/topic-validation_b99e0765430f82a7e0ea`。
私有正文仅保存在该目录，公开文档记录计数和摘要。

| 文件 | 文件 SHA-256 |
| --- | --- |
| `manifest.json` | `afe3e682a9da34007921991c3f34ad228d398612d9a74c4dfee7bb66558be335` |
| `report.json` | `a219c6dd45bf50646d75277650ca880cf3dde21cec27a44e47b9fb29394b492a` |
| `validation.candidates.jsonl` | `17d37171e89a66bf2b78749fb2290d6fe59f0a14cd3631fef34c59455b43359c` |
| `selection.json` | `8f96e13a4b73e8750537982f740fe13babe45d7efe3a45b29c70cb7fb7552dc2` |
| `evidence.jsonl` | `fd7f3ff400a71962e43a9c19ee5b6d51f3435f5e157a0a27915cc4c4b1b03b81` |
| `protocol.md` | `6d83545bf52d4012693b6b4a93d1d4c1eff3b978367948a38b2ba2f741ecbfbb` |
| `candidate-schema.json` | `27d8e8b0ccdc9d05090a5ed256f8922292030cf618fe304d7cdeca7f4e798c6f` |

manifest 另绑定开发 v3、已完成 v4 审核、source/split/consent、实际代码、配置、协议文件和
tokenizer 的文件摘要。旧 train 开发和 99 条机器草稿的 manifest 均保持原样。

## 4. 验证与后续

332 项项目测试通过，包括禁止解码 train/test/未选 validation 正文、缺失血缘阻断、split
适配隔离、原回复/token 不变、两种请求格式对等、开发集重叠拒绝、重复隔离、稳定排序、预算和
完整分母、未来/跨 split/目标重叠证据拒绝、页面转义、只读计划、原子发布、防篡改及无请求重放。

实际 200/200 条重新验证 Schema、语义 digest、原始回复、token 统计和 assistant-only 标签。
最长序列 515 token、最长目标 74 token，均在 1,024/256 限额内，无截断。
选中 ID、分层成员、来源 hash、旧 manifest 和权限复查通过。

下一阶段的双模型预审首轮预计 400 次单条请求，输入估算 GPT 615,986、Claude 616,386，
合计 1,232,372 token；预留预算最多 440 次、输入 1,800,000、输出 1,760,000 token。
这是将来调用的计划，不是本轮用量或账单。本轮外部请求始终为 0，尚未消耗该推理预算。

下一步先实现并验证 validation 专用的响应解析、身份/预算和仅失败项恢复入口，再对这批冻结
200 条做双模型预审，并完成独立盲参考与近重复检查。现有 train-only 解析器不能直接复用执行。
引用和交错难例不足仍是单独的证据缺口。P3 未验收，P4–P6 未开始，正式训练资格和人审完成
均为 false；尚未训练或改变模型别名。

上述“下一步”是准备包封存时的状态。validation 专用响应解析与有界执行入口现已实现，
另已完成两个冻结样本包范围内的近重复诊断；后续请求用量、实际回收量和剩余缺口以
[validation 审核执行记录](daily-topic-sft-validation-review.md)为准。本准备包的零请求记录保持不变。
