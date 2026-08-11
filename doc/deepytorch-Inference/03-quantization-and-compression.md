# 量化、稀疏与模型压缩

量化既可能降低内存和带宽，也可能调用更快的低精度矩阵内核，但“位宽更低”不自动等于
“延迟更低”。真正结果由硬件指令、矩阵形状、量化粒度、权重布局、反量化融合、batch、
内核覆盖和准确性约束共同决定。

## 1. 先区分精度方案

| 方案 | 权重 | 激活 | 主要收益 | 典型风险 |
| --- | --- | --- | --- | --- |
| FP32 | FP32 | FP32 | 数值基线、覆盖最好 | 内存和计算成本高 |
| TF32 | FP32 存储 | Tensor Core 内部较低尾数精度 | NVIDIA 上加速 FP32 矩阵运算 | 需明确允许的数值变化 |
| FP16 | FP16 | FP16/混合 | GPU 内存和 Tensor Core | overflow/underflow、部分算子需 FP32 |
| BF16 | BF16 | BF16/混合 | 更大指数范围，现代 CPU/GPU 常有支持 | 尾数精度低，旧硬件收益有限 |
| FP8 | FP8/混合 | FP8/混合 | 新一代加速器高吞吐、低内存 | 硬件和软件版本绑定、缩放策略复杂 |
| INT8 W8A8 | INT8 | INT8 | 权重和激活带宽、整数矩阵内核 | 校准敏感、算子间量化转换 |
| INT8 weight-only | INT8 | FP16/BF16 等 | 降权重带宽，较易保持质量 | 小模型/大 batch 未必更快 |
| INT4 weight-only | INT4 | FP16/BF16 等 | LLM 显存与带宽显著下降 | 质量、packing 和 kernel 强绑定 |
| W4A8/W4A4 等 | 更低位 | 更低位 | 特定硬件的更高密度 | 覆盖、校准和质量风险最高 |

FP8、INT4 等名称不足以描述一个可部署格式。至少还要记录：对称/非对称、group size、
per-tensor/per-channel/per-group/per-token、zero point、scale dtype、权重排列、是否含 outlier
处理，以及目标引擎期望的序列化格式。

## 2. PTQ、QAT 与动态量化

### 2.1 Post-Training Quantization（PTQ）

训练完成后进行量化，成本最低，适合作为首选试验。常见形式：

- 动态量化：运行时根据激活计算 scale，传统 CPU Linear/RNN 场景常见；
- 静态量化：用代表性校准数据提前确定激活范围；
- weight-only：只量化权重，LLM INT8/INT4 常用；
- 平滑/outlier 处理：在权重与激活之间重新分配量化难度，例如 SmoothQuant 思路；
- 误差最小化算法：GPTQ、AWQ、HQQ 等产生不同权重与 scale，最终仍需运行时内核配合。

校准集应来自与生产分布一致、没有最终测试标签泄漏的独立校准切片。必须记录数据版本、样本
选择规则、预处理版本、随机种子、摘要和校准工具版本。

### 2.2 Quantization-Aware Training（QAT）

QAT 在训练/微调时模拟量化误差，通常在 PTQ 达不到质量门槛时使用。它会增加训练成本和
超参数，且不自动保证目标引擎支持生成格式。合理顺序是：

1. 用最终引擎支持的 PTQ 配置建立可运行基线；
2. 明确失败层、任务质量差距和目标 dtype；
3. 用训练/验证数据执行 QAT 或低成本微调；
4. 从干净状态导出并在最终运行时做一次正式质量验收；
5. 测试集不用于反复选择量化配置。

## 3. PyTorch 当前主线：torchao

PyTorch 官方已把量化相关开发集中到 torchao。官方迁移说明包括：

- `torch.ao.quantization.quantize`/`quantize_dynamic` 的 eager 流程迁移到 torchao
  `quantize_` API；
- FX graph mode 迁移到 torchao PT2E quantization；
- PT2E 量化实现已经迁入 `pytorch/ao`；
- `torch.ao.quantization` 存量 API 仍可能暂时可用，但不应作为新代码的长期依赖。

torchao 覆盖原生 PyTorch 自定义 dtype、权重/激活量化、QAT、稀疏、float8，并与 vLLM、
SGLang、ExecuTorch 以及部分 `torch.compile` backend 集成。最小的 weight-only 形式类似：

```python
from torchao.quantization import Int4WeightOnlyConfig, quantize_

model.eval()
quantize_(model, Int4WeightOnlyConfig(group_size=32))
```

真实配置必须按目标设备选择 packing format、group size 和 kernel。不要从一个后端保存量化后
Python 对象，再假设另一个后端能无损读取。

## 4. 量化工具生态

| 工具/方法 | 角色 | 当前建议 |
| --- | --- | --- |
| torchao | PyTorch 原生 dtype、PTQ/QAT、稀疏与训练到服务优化 | 新 PyTorch 原生项目优先评估 |
| TensorRT/Model Optimizer | NVIDIA 量化、模型优化和 TensorRT 工具链 | TensorRT/TensorRT-LLM 目标优先评估 |
| ONNX Runtime Quantization/Olive | ONNX 图量化和端到端优化编排 | ORT 目标优先评估 |
| OpenVINO NNCF | OpenVINO 模型压缩、PTQ/QAT | Intel/OpenVINO 目标优先评估 |
| bitsandbytes | Transformers 常用低精度加载与线性层 | 易用但必须实测目标 GPU/kernel/服务引擎 |
| llm-compressor | vLLM 生态的压缩与量化流程 | vLLM 支持矩阵内使用 |
| GPTQModel | GPTQ 生态的活跃迁移目标之一 | 逐模型和引擎核验 |
| HQQ | 无需校准或轻量 weight-only 量化方案 | 以运行时集成和质量实测为准 |
| AutoGPTQ | 旧 GPTQ 工具 | 官方声明不再维护，建议迁移 GPTQModel |
| AutoAWQ | 旧 AWQ 工具 | 官方声明弃用，迁移到 vLLM/llm-compressor 路径 |

一个算法名称可能被多个工具实现，一个引擎也可能只支持其中若干 checkpoint layout。选型表
必须写成“算法 + 工具版本 + 序列化格式 + kernel/backend”，而不是只写“INT4”。

## 5. 面向不同模型的起点

| 模型/硬件 | 第一轮精度候选 | 第二轮候选 |
| --- | --- | --- |
| CUDA CNN/视觉 | FP16 或 BF16 | TensorRT INT8；质量不足再考虑 QAT |
| CPU CNN/Encoder | BF16（硬件支持时）、动态/静态 INT8 | OpenVINO/ORT/torchao PT2E |
| NVIDIA LLM | BF16/FP16 基线、FP8（硬件支持时）、INT4 weight-only | W8A8、W4A8、QAT，按引擎矩阵 |
| AMD LLM | BF16/FP16 基线、引擎明确支持的 FP8/INT8/INT4 | 避免直接复用只为 CUDA 打包的权重 |
| CPU/本地 LLM | GGUF 的 Q8/Q6/Q5/Q4 等候选 | 用真实任务质量和 tokens/s 选，而非只看文件大小 |
| 移动/边缘 | FP16、INT8 | 目标 delegate 支持的 INT4/混合精度 |

## 6. 稀疏化

### 6.1 非结构化稀疏

把任意权重置零可大幅减少非零参数，但密集 GEMM kernel 通常仍会读取和计算零值。没有稀疏
存储和对应 kernel 时，文件变稀疏并不等于推理更快。

### 6.2 结构化和半结构化稀疏

- 通道/头/层剪枝会改变张量形状，更容易被普通密集 kernel 利用，但需要微调和结构适配；
- N:M（例如特定硬件的 2:4）只有在硬件和引擎明确支持时才有加速价值；
- block sparsity 需要 block 大小与 kernel 匹配；
- LLM 的层、注意力头或专家剪枝可能改变模型能力，不能只看困惑度。

验收时同时记录实际非零率、结构规则、kernel 命中和端到端延迟。

## 7. 蒸馏与架构优化

蒸馏、小模型替换、减少层数/隐藏维度、早退、MoE 路由或缩短上下文通常比单纯编译拥有更大
的理论收益，但它们改变模型本身，属于重新训练和重新验证，而不是透明运行时优化。

合理触发条件：

- 所有运行时优化后仍无法达到 SLO/成本；
- 质量指标可以定义且有训练数据；
- 允许新增训练预算和模型治理流程；
- 新模型以独立 MLflow Run、版本和质量门槛管理。

## 8. 量化验收清单

- 与 FP32 或认可的 BF16/FP16 基线使用同一预处理和数据切片；
- 报告任务指标，不只报告 cosine similarity；
- 检查分组指标、长尾输入、极值和安全关键样本；
- 比较首请求、稳态、batch=1 和生产 batch；
- 记录模型文件、运行时内存、峰值显存、吞吐、P50/P95/P99；
- 确认目标节点真的执行低精度 kernel，没有大面积 dequantize 回 FP32；
- 将量化参数、校准摘要、工具/引擎版本和质量报告作为不可变制品；
- 低精度模型使用独立版本和回滚入口，不覆盖原始权重。

