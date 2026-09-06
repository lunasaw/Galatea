# 项目 7：真实角色 LoRA

## 1. 目标和准入

目标是学习目标角色的措辞、长度、emoji、昵称使用和互动习惯，不学习全部私人事实、不复述秘密、
不制造真人在线的错觉。初始规模为人工筛选 1,000–3,000 条高质量目标回复；扩展到 5,000–10,000
条必须有新的数据 digest、review 记录和书面授权。

真实训练的准入条件：项目 5 `FORMAL_DATASET_READY`、双方角色风格授权、人工审核结案、PII/秘密/
canary/第三方门禁通过、train/validation/test 非空且 split 冻结。当前仓库的私有 Trial 仍保持
`formal_training_eligible=false`，不能借本文档变成正式候选。

## 2. 数据设计

每条样本包含脱敏 system/user/history/target assistant 和风格元数据：`relationship_stage`、
`reply_length_bucket`、`nickname_used`、`emoji_class`、`intent`（comfort/tease/ask/refuse 等）、
`source_session_id` 摘要。只对目标 assistant token 计算 loss；事实记忆移入项目 6。

排除一次性验证码、支付、第三方秘密、精确位置、不希望复现的争吵原句、完整长段逐字对白和高风险
依赖操纵样本。短句不全部删除，但按长度分层采样，防止模型退化为“嗯/哈哈”。

## 3. 训练架构

配置变体统一放在 `train-model/wechat-persona/configs/`：`persona-lora-smoke.yaml`、
`persona-lora-baseline.yaml`、后续 Trial/Champion 配置。训练代码复用项目 2–4 的 assistant-only
mask、LoRA、checkpoint、Ray Driver、MLflow 和 Artifact round-trip；仅替换 dataset/consent/role/预算。

推荐首轮：Qwen3.5-0.8B、BF16、rank 8/alpha 16/dropout 0.05、q/v modules、1 epoch 起步。先执行 2 条/2
step preflight，再 10-step governed smoke，最后 validation-only 的 baseline/Trial。目标模块不匹配、
环境无法识别 `qwen3_5` 或 checkpoint round-trip 失败时立即阻断。

## 4. 五组对照和冻结规则

在同一冻结 validation/test 输入、chat template、prompt、generation、seed、长度限制下生成：

`Base`、`Prompt-only`、`RAG`、`LoRA`、`RAG+LoRA`。Prompt-only 的优化只能使用独立 development 集，
正式比较前冻结 prompt digest。RAG 结果必须来自同一已冻结检索快照。

候选选择只读 train/validation：先过隐私/安全硬门，再看 validation loss、自动指标和盲测。冻结记录
包含 adapter artifact digest、protocol/split/config digest、候选 Run ID、生成配置和 test 尚未访问声明。
之后用一次性 `test_evaluation_id` 运行最终 test；任何变更都作废旧 test。

## 5. 质量、安全和人工评测

开放式主指标为至少 100 个配对盲测的 `LoRA_vs_PromptOnly_win_rate`，起始门槛 ≥ 0.60，报告平局、
不可接受率、bootstrap 95% CI 和审核者一致性。辅助指标包括 validation loss/PPL、embedding similarity、
长度分布、重复率、格式成功率、延迟、吞吐和显存。

硬门：PII/canary 泄漏 0、不安全行为 0、冒充真人/排他依赖/内疚操纵 0、无证据私人事实猜测达到
冻结阈值、空输出/崩溃在阈值内、adapter 新进程加载通过。`RAG+LoRA` 的事实正确率应比 `LoRA` 至少
高 5 个百分点；否则优先修 RAG/数据，不重复加 epoch。

盲测界面隐藏 variant、Run ID、loss 和“预期优胜”提示，只显示必要脱敏上下文。未达到门槛的 Run
只能标记 `experimental_only`，不能接入项目 9。

## 6. 回滚和撤回

所有 adapter 按 dataset/consent/protocol digest 命名并只读保存；不覆盖成功工件。任何撤回、canary
泄漏或安全门失败都会撤销候选资格、停止原型入口，并删除受影响 adapter/checkpoint 后从清理数据
重新训练。Registry alias 变更是单独的人工 promotion action，训练脚本不得执行。

## 7. 验收产物

`train_manifest.json`、数据/配置/split/模型 digest、MLflow Run、validation quality report、五组
对照摘要、盲测原始标注（受控）、安全 challenge report、Artifact round-trip receipt、candidate freeze
record、test-once report 和人工 review record。缺任一关键产物时状态不是 accepted。

