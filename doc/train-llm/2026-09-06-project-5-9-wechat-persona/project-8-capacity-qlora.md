# 项目 8：模型扩容与 QLoRA 学习

## 1. 决策问题

项目 8 不是“参数越大越好”的训练竞赛，而是回答：在相同数据、prompt、split、评测协议和角色
安全门下，容量提升是否带来足够的质量收益，是否值得延迟、显存和运维成本。

## 2. 严格实验顺序

1. 用项目 7 冻结的数据和 protocol 在 Qwen3-1.7B 做 BF16 LoRA；只改变 `model_id/revision` 和
   经预算批准的资源配置。
2. 若主指标相对 0.8B 提升不足 5 个百分点，或收益不抵消延迟/显存代价，停止扩容，优先改数据/RAG。
3. 只有确认 0.8B/1.7B 的容量瓶颈后，才选择 4B 级指令模型做 QLoRA；4B 不与前两者混用 tokenizer、
   chat template 或量化设置而不记录版本。

所有比较在 candidate freeze 后才访问同一 test 一次；validation 可用于 checkpoint 和资源方案选择。

## 3. QLoRA 兼容性隔离

建立独立 `qlora-4b.yaml` 和环境锁定，单独验证：

- Transformers 模型架构、PyTorch/CUDA runtime、bitsandbytes、PEFT/TRL 版本；
- 4-bit NF4、double quant、compute dtype、设备映射和显存峰值；
- forward-only 加载、2-step backward fixture、adapter 保存/加载和 Artifact round-trip；
- Blackwell GPU 驱动/编译兼容性、CPU fallback 是否被禁止。

兼容性失败只标记环境阻断，不把它解释成数据或模型质量失败。QLoRA 训练仍沿用固定 Ray Driver、
MLflow 和 checkpoint 恢复边界；不能从本地 shell 绕过。

## 4. 统一指标和成本报告

每个模型报告相同的 `lora_vs_prompt_only_win_rate`、安全门、validation loss/PPL、RAG 事实指标，
以及冷/热首 token 延迟、总延迟 p50/p95、输出 tokens/s、峰值 GPU allocated/reserved memory、
加载时间、checkpoint 大小和每个样本成本。指标报告必须给出硬件、batch、sequence/new-token 限制、
warm-up 和测量人口。

采用门槛：主指标相对当前 Champion 起始基线至少 +5 个百分点（或在实验前声明等价的最小有意义差异），
所有安全/隐私硬门通过，Artifact round-trip 通过，且资源代价在书面预算内。仅模型名称或参数量增加
不构成采用理由。

## 5. 验收和停止

验收产物包括 0.8B/1.7B/4B 的兼容 Run 矩阵、环境 digest、量化配置、统一评测报告、资源对照图表、
test-once 绑定、候选冻结和书面采用/停止决定。任何数据、split、协议不兼容的 Run 只能分组报告，
不能排序为“最优”。

