# 真实回复 SFT：新版分轴协议执行与独立用例检查

> 本文保留 v2 的失败证据。后续 v3 已回收全部 48 条新用例判断，状态匹配为 24/24、21/24，
> 仍未过固定门槛；见 [v3 判据澄清与路由核验](daily-topic-sft-axes-v3.md)。

## 2026-09-18 的结论

已把 `topic-reply-axes-v2` 接入可执行入口，完成 24 个新合成用例、两套审核器、8 次请求的
一次性检查。预先固定的门槛未通过，因此没有对原 200 条真实候选发起新版审核，没有新增
SFT 草案。原 58 条机器共识参考组、142 条待裁定组和 12 条历史硬风险保持原样。

当前仍为 P3，P4–P6 未开始。这次结果属于数据预审协议诊断，不是训练或模型比较证据；
`human_review_completed=false`、`formal_training_eligible=false`、`promotable=false`。
没有访问旧 test，没有参数更新、MLflow Run 或模型别名变更。

## 冻结的检查协议

配置为 [`topic-axes-review-v2.yaml`](../configs/topic-axes-review-v2.yaml)，新用例为
[`topic-axes-validation-v2.json`](../configs/fixtures/topic-axes-validation-v2.json)。此前的 18 个用例
已作为开发材料，本次没有复用其中任何完整的「前文—回复」对，也没有在看到结果后改写参考答案。
这些参考由实现者编写，不是对私有聊天的人工真值，也不能用于估算真实保留 precision。

两套审核器分别必须满足：

- 有效完成全部 24 条，缺项仍计入 24 的固定分母；
- 最终状态匹配至少 22/24，状态、回应关联、上下文完整性三项同时匹配至少 22/24；
- 参考为 reject 或 uncertain 的负例误保留为 0。

原子回应锚点另行报告。部分用例允许多个明确有效的锚点集合，不强求任意指定的唯一集合。
共同提示词明确 `self` 是对方、`target` 是拟学习的人；请求隐藏样本 ID、规则、预期答案及
历史私有审核意见。两种传输使用同一判据与 Schema，网关兼容 Schema 去掉的数值范围和
去重约束仍在本地严格执行。模型只返回分轴字段，规则计算最终 keep/reject/uncertain。

预先固定 2 并发、每批最多 6 条、8 次请求、40,000 输入 token、每次最多 3,000 输出 token、
总计最多 24,000 输出 token、120 秒请求超时；合成检查无自动重试。估计输入 15,682 token。

## 实际结果与失败归因

| 审核器 | 有效回收 | 状态匹配 | 三项同时匹配 | 允许锚点集合匹配 | 负例误保留 |
| --- | ---: | ---: | ---: | ---: | ---: |
| GPT | 17/24 | 16/24 | 16/24 | 17/24 | 1 |
| Claude | 24/24 | 16/24 | 15/24 | 23/24 | 0 |

GPT 的 7 条缺失由两类问题组成：

1. 一批请求指定 `gpt-5.6-sol`，响应自报 `gpt-6-sol`，不符合冻结的 `gpt-5.6` 前缀。
   该批 6 条全部拒收，没有改为接受另一模型身份。响应字符串只能证明自报身份不一致，
   不能证明网关实际调用了什么后端；两家审核器都没有提供后端模型修订证明。
2. `axes-fresh-16` 同时返回 `relation=undetermined` 和非空 `responds_to_indices`，违反
   “无法确定关联就不能声明确定的回应对象”约束。只拒收这一条，同批其余 5 条有效结果保留。

有效结果中的问题仍足以独立阻断检查，即使补齐上述 7 条，也不能据此宣称通过：

- GPT 在 `axes-fresh-13` 把涉及未提供的电话事件的承诺回复判为上下文充分、keep。
  回应动作可以辨认，不等于承诺的具体事件已经明确；这是本轮 1 条负例误保留。
- Claude 把短接受、感谢和确认改期三例（02、03、23）标为 `low_signal`。其中两例又因
  置信度低于 0.9 计算为 uncertain，一例为 reject；它们与冻结的有效沟通参考不符。
- Claude 对 06、10、21、22、24 的关联、上下文和风险判断均满足条件，但置信度只有
  0.82–0.88，最终状态为 uncertain。这是自报置信度门导致的保留分歧，不能把阈值临时调低。
- Claude 在控制性回复例 18 上正确标出风险并 reject，但把回应关系判为 undetermined，
  因而状态正确、三项同时匹配失败。

上述条目均为合成材料；没有在终端、源码或本页展示真实聊天。已经检查过的这 24 条现在属于
开发诊断材料。下一版若修正判据，需要新的预先冻结用例，不能继续用这批用例报独立验证成绩。

实际消费 8 次请求，服务报告输入 26,167、输出 4,824 token。预算采用预留与服务回报的较大值：
预算扣账累计输入 27,708、输出 24,000 token；没有超出冻结上限。没有重问任何有效判断。

## 入口、恢复与阻断

实现为 [`topic_axes_review.py`](../src/wechat_persona/topic_axes_review.py)，脚本为
[`run_topic_axes_review.py`](../scripts/run_topic_axes_review.py)。只读计划示例：

```bash
/data/conda/envs/attend-ray-py312/bin/python \
  train-model/wechat-persona/scripts/run_topic_axes_review.py \
  --scope calibration \
  --output-root /srv/galatea-private/wechat-persona/topic-axes-reviews \
  --controlled-root /srv/galatea-private/wechat-persona \
  --authorization-reference <authorized-task-reference> \
  --plan
```

网关从 `OPENAI_BASE_URL` 读取，凭据默认从受控认证文件读取，也可显式指定 `--auth-file`。
显式 `--execute` 才会创建工作区或请求模型。以上命令不包含私有端点或密钥。

`--scope private_review` 还必须绑定 `--calibration`、`--before`、`--after`、`--review`、
`--audit`、`--cross`、`--consent` 和原 cross policy。其顺序是：验证校准 manifest、相同源码/
判据/Schema/模型/网关/用例身份并重算门槛 → 核对原 200 条来源和 consent → 才允许请求。
本轮实际调用该执行入口已返回 blocked，阻断发生在私有输入加载和请求之前。

真实审核分支的实现保留原 58/142 分组，并继承全部 12 条旧风险；即使两套新判断都 keep，
没有共享原子回应锚点或存在旧硬风险，也不会进入新草案。候选输入和原始回复不被改写。
该分支仅通过 mocked 合成组件测试，尚未在真实 200 条上执行。其计划上限为 68 次首轮调用，
另外最多 4 次只重试缺失或格式错误条目的调用，总上限 72 次；不重问有效 reject/uncertain。

请求在发出前持久化预算预留。原始响应以 Base64 原字节形式私有保存后才解析；拒绝模型身份
不符、未完成响应、非法枚举/索引、重复字段、非有限数和互相矛盾的分轴证据。中断且未收到
响应的已预留请求不会在相同身份重跑时静默再发。完成或失败的终态写不可变 manifest；
相同身份重跑只校验摘要并返回既有结果，失败不自动开始另一轮。

## 产物与验证

本轮目录：

```text
/srv/galatea-private/wechat-persona/topic-axes-reviews/topic-axes-calibration_a1491e9d7e13881a14ca/
  identity.json, manifest.json, report.json, cases.json, decisions.json, budget.json
  <request-digest>.json
```

`status=incomplete` 表示只有 41/48 条结果通过响应契约校验；8/8 个 HTTP 请求都已结束。
`protocol_gate_passed=false` 同时保留语义门失败，不把这一状态解释成单纯等网络恢复即可继续。
原始响应含审核模型输出，仅在受控目录保存，目录 700、文件 600。

| 产物 | 文件 SHA-256 |
| --- | --- |
| 新用例 | `e691c2c84060d9d9845a558cfe52afd7fd4baea0080226d570fdae2e41274e69` |
| manifest | `7f05dd8cd2aa1b4fb47e9cedb8026426b2b844abb3e546f9fea71e6542625d06` |
| report | `c355744915466da1c79411b87ac2a98d308ecfce0db51d8411294698aa5c614c` |
| cases | `61030af18484599c577bb3eb6416dde6156330762e27fb7da295599291617f1f` |
| decisions | `870235911b967dd03d67749ed7c50f6f171736a93590299cebe85e03f6b9dd48` |

项目全量 286 项测试通过，其中新增 11 项覆盖盲审请求、两种传输、逐条格式失败隔离、模型/
完成状态/索引检查、固定分母、负例误保留阻断、旧风险和锚点门、零调用重放、失败校准阻断
私有加载、原始坏 JSON 留存、只修复缺失项及草案原样保留。实际 manifest 和文件权限已回读，
重放后请求数仍为 8；没有创建真实审核工作区。

## 下一步

先定位网关请求模型与返回身份不一致的原因，再澄清三个协议问题：短确认/感谢的有效沟通
价值、已知回应动作与未知外部事件的区别、关系确定性与硬风险的独立判断。置信度分布需要
单独记录和解释，不能靠要求模型报高分或事后降低 0.9 门槛解决。新版本应保留本轮失败证据，
先冻结新用例和预算再检查；通过后才运行原 200 条真实候选，并继续独立 validation 的准备。
