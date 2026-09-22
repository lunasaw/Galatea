# 真实回复 SFT：v3 判据澄清与路由核验

## 2026-09-18 的结果

新版 `topic-reply-axes-v3` 已实现并完成 24 条新合成用例的实际检查。GPT 与 Claude 均有效回收
24/24 条，本轮所有审核响应的模型身份均匹配已绑定的路由。两者的关联、上下文完整性和允许
锚点集合都匹配 24/24，负例误保留均为 0；最终状态分别匹配 24/24、21/24。

预先固定的状态及核心三项匹配门为各至少 22/24，因此整体检查仍未通过。Claude 的三条差异
全部来自自报置信度低于保留门 0.9，没有临时降门或改参考答案。真实 200 条新版审核仍被
阻断，原 58 条机器参考、142 条待裁定及 12 条历史硬风险保持原样。P3 未验收，P4–P6 未开始。

## 本轮改动

[`topic_review_protocol_v3.py`](../src/wechat_persona/topic_review_protocol_v3.py) 澄清四个边界：

- 完成确认、感谢、问候、明确选择本身就是有效沟通，不能以信息量少或没有新增事实判低信号。
- 识别“同意”动作不等于知道“同意什么”；未提供的线下安排仍造成必要上下文缺失。
- 回应关联与安全风险分别判断，有控制或危险建议的回复也可能明确回应前文。
- confidence 表示对分轴判断的确定程度，不是回复质量或主观训练价值；不要求模型为过门报高分。

v2 源码、失败产物和阈值全部保留。v3 复用 v2 的 Schema、来源校验、状态计算与统计函数，
不改写历史响应。新决策的 `method` 仍标示共享的 `topic-reply-axes-v2` 分轴契约，另外强制绑定
`review_protocol=topic-reply-axes-v3`、新 prompt SHA 和请求摘要；缺少这些字段的旧决策不能
被直接纳入 v3。`protocol_calibration_passed` 不在单条决策中被静默改成 true。

[`topic_axes_review_v3.py`](../src/wechat_persona/topic_axes_review_v3.py) 增加执行前的目录/路由检查：
两条小型合成探针由每个审核器各调用一次，必须全部通过格式和模型前缀检查，每个审核器的
两次返回身份还必须一致。后续校准和真实审核要求与该完整返回身份精确匹配。身份不符会
打开中断开关，停止尚未发出的请求；已经在途的请求仍保留证据。不会自动改模型或重试身份
漂移，只有缺失/格式问题可以进入有预算的修复队列。真实审核未完整回收时不导出部分草案。

新用例为 [`topic-axes-validation-v3.json`](../configs/fixtures/topic-axes-validation-v3.json)，包含
15 个预期 keep、3 个 uncertain、6 个 reject；与此前 18+24 个用例没有相同的完整输入/回复对。
包含已知/未知指代、已说明/未说明线下安排的对照。门槛仍是每方有效完成 24 条、状态和
状态/关联/完整性同时匹配各至少 22 条、负例误保留为 0。自报置信度门仍为 0.9。

## 网关排查和路由恢复

第一次只读目录查询列出 `gpt-5.6`、`gpt-5.6-sol` 和 `claude-sonnet-4-6`，未列出上轮返回的
`gpt-6-sol`。目录存在不保证对应推理路由可用：

| 阶段 | 调用与结果 | 处置 |
| --- | --- | --- |
| 标准名 preflight | 4 次；Claude 2 次有效，`gpt-5.6` 2 次 HTTP 错误 | 失败证据保留，未发送新校准用例 |
| 单次 HTTP 诊断 | 重放 1 个失败探针，HTTP 502、`upstream_error` | 原错误正文私有保存，仅输出状态和错误类别 |
| sol 路由 preflight | 4 次；`gpt-5.6-sol` 和 Claude 各 2 次有效且身份一致 | 绑定本轮后续响应的完整返回身份 |
| v3 合成校准 | 8 次，48/48 条有效，身份/格式失败 0 | 语义门仍失败，没有进入真实审核 |

标准名配置保留在 [`topic-axes-review-v3.yaml`](../configs/topic-axes-review-v3.yaml)。恢复配置
[`topic-axes-review-v3-sol.yaml`](../configs/topic-axes-review-v3-sol.yaml) 单独记录旧 preflight
manifest 摘要，只调整请求路由，保留 v3 判据、用例和全部资格门。恢复路由不是绕过对历史
错误身份的检查，本轮也没有证明网关已永久修复；`route_stability_proven=false`，且没有
后端模型修订证明。未修改网关服务、账号或访问权限。

HTTP 诊断使用 [`diagnose_topic_review_route.py`](../scripts/diagnose_topic_review_route.py)，只接受
来源/协议匹配、含 HTTP 失败的 preflight；上限固定 1 次，重新发送既有失败的合成探针。
实现 [`topic_route_diagnostic.py`](../src/wechat_persona/topic_route_diagnostic.py) 在私有目录留存
HTTP 响应原字节，终端不回显错误消息、凭据、URL 或模型正文。重复执行只回读已完成证据。

## 校准结果与剩余分歧

| 审核器 | 有效回收 | 状态匹配 | 核心三项同时匹配 | 允许锚点集合匹配 | 负例误保留 |
| --- | ---: | ---: | ---: | ---: | ---: |
| GPT | 24/24 | 24/24 | 24/24 | 24/24 | 0/9 |
| Claude | 24/24 | 21/24 | 21/24 | 24/24 | 0/9 |

Claude 的三个差异如下，仅使用合成用例 ID 和分类描述：

| 用例 | 沟通类型 | 其余轴 | 自报 confidence | 规则结果 / 预期 |
| --- | --- | --- | ---: | --- |
| `axes-fresh-v3-03` | 简短感谢 | 关联明确、上下文充分、useful、clear | 0.88 | uncertain / keep |
| `axes-fresh-v3-13` | 有明确先行词的借用请求 | 关联明确、上下文充分、useful、clear | 0.72 | uncertain / keep |
| `axes-fresh-v3-24` | 尊重第三方隐私的建议 | 关联明确、上下文充分、useful、clear | 0.88 | uncertain / keep |

没有把高于或低于某个自报分数当作真实准确率，也没有事后把 0.9 改为 0.88 或 0.72。
`status=complete` 只代表全部响应通过契约校验；`protocol_gate_passed=false` 明确表示质量门
未通过。v2/v3 使用不同的新用例，不能将分数之差直接解释为可比较的真实效果提升。
本轮用例在分析后已成为开发诊断材料，不能反复重问并当作独立验收。

## 执行、预算和验证

新入口为 [`run_topic_axes_review_v3.py`](../scripts/run_topic_axes_review_v3.py)。三个 scope
分别为 `preflight`、`calibration`、`private_review`，均有只读 `--plan` 与显式 `--execute`。
校准要求 `--preflight`；真实审核还要求 `--calibration` 和原 200 条的全部来源/consent 参数。
网关和凭据仍由外部配置提供。使用 sol 配置的计划示例：

```bash
/data/conda/envs/attend-ray-py312/bin/python \
  train-model/wechat-persona/scripts/run_topic_axes_review_v3.py \
  --scope preflight \
  --config train-model/wechat-persona/configs/topic-axes-review-v3-sol.yaml \
  --output-root /srv/galatea-private/wechat-persona/topic-axes-reviews \
  --controlled-root /srv/galatea-private/wechat-persona \
  --authorization-reference <authorized-task-reference> \
  --plan
```

每次 preflight 的上限为 4 请求、24,000 输入/12,000 输出 token；额外 HTTP 诊断上限为
1 请求、12,000 输入/3,000 输出 token；校准为 8 请求、60,000 输入/24,000 输出 token。
保留 2 并发和 120 秒请求超时。真实审核仍为 68 首轮请求及最多 4 次格式恢复，但本轮没有执行。

本轮合计 17 次推理请求，另有 3 次只读模型目录 GET。服务回报输入 47,124、输出 4,783 token；
3 个 HTTP 失败没有 token 回报，不能视为已知零费用。预算按预留/回报较大值扣账，累计输入
56,801、输出 51,000 token，未超出各自冻结预算。校准本身为 8 次、输入 31,186、输出 4,349。

全量 298 项项目测试通过。新增覆盖前置身份门、精确模型身份变化的停止行为、新旧协议隔离、
不可变重放、完整回收但误保留时仍阻断、历史风险继承、草案原样保留、不导出部分草案、
缓存和预算绑定、HTTP 原字节留存与脱敏报告。真实审核入口已实际返回 blocked，未加载/发送
私有候选，也未创建 v3 真实审核目录。v2 及两个 v3 preflight 的零新增调用重放已验证。

## 受控产物

以下目录均位于 `/srv/galatea-private/wechat-persona/topic-axes-reviews/`：

| 目录 | manifest 文件 SHA-256 |
| --- | --- |
| `topic-axes-v3-preflight_4a12306dd5920a1b7bab` | `e25424aeb10ce4d208a30b2d905a021ef2ab9afccf482f6febb3adecda2f8226` |
| `topic-route-diagnostic_9afdaf8b44f926c8693f` | `784a933a0e1a897e88f5620fc94dd62f54df78d3343797a82fffeb80c2795c6f` |
| `topic-axes-v3-preflight_e89992e2c4fd18e55f49` | `0daaf5a7989253dd517b0492cbced6beb81f3fba48a23dd32be791298cf83ca3` |
| `topic-axes-v3-calibration_a6c8abb5b3250ccb34f4` | `ea3826abeb8e8ababc65a3d17d18cd3d0a291a4cb0656f0728a2ecb140427631` |

新用例 SHA 为 `1c603b9d050105574a2663454eaab3a9b24f3c51747258615e9d24773931f693`；
校准 report SHA 为 `0fbbc33fa02163ed1c73bace5cef8936f5988d8a7573d6c3d571b5de5e94ae1d`。
原始请求结果仅在私有 700 目录、600 文件保存，不进入源码，也不是训练或 MLflow 证据。

下一步应单独验证未校准自报置信度是否适合作为跨审核器统一硬门，以及如何用独立证据刻画
不确定性。任何调整都应在开发材料上明确依据、形成新版本并冻结新检查，不能将本次 21/24
追认成通过或要求模型直接报更高分。原真实候选的人工/正式训练资格仍为 false。
