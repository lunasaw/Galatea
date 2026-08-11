# PyTorch 推理加速方案全景与落地指南

本文档集面向需要把 PyTorch 模型从实验环境交付到批处理、在线服务、LLM 服务、边缘设备
或专用加速器的团队。调研与链接核验日期为 **2026-08-11**。目录名沿用任务指定的
`deepytorch-Inference`。

这里的“加速方案”不是一个单一产品，而是可以叠加的多层体系：模型优化、低精度、图捕获、
编译器、推理运行时、专用内核、请求调度和服务编排分别解决不同瓶颈。任何公开性能数字都不
能代替在目标模型、目标输入分布和目标硬件上的可复现实测。

## 1. 结论先行

1. 新项目应先建立 `model.eval()`、`torch.inference_mode()`、合理批处理、混合精度和
   可复现基准，再比较引擎；否则测到的通常是输入管线、同步方式或批大小差异。
2. 希望保留 PyTorch 开发体验时，首个候选通常是原生 `torch.compile`；需要独立部署时再
   评估 `torch.export` + AOTInductor、ONNX Runtime、Torch-TensorRT 或 OpenVINO。
3. NVIDIA 上固定模型追求更高性能上限，可比较 Torch-TensorRT/TensorRT；跨硬件或已有
   ONNX 生态时优先验证 ONNX Runtime；Intel CPU/GPU/NPU 场景优先验证 OpenVINO。
4. LLM 不能只按普通静态图模型处理。新建通用 LLM 服务优先实测 vLLM 与 SGLang；NVIDIA
   平台追求极致且能接受引擎构建与更强绑定时加入 TensorRT-LLM。llama.cpp 和 MLC LLM
   适合接受格式转换的本地、CPU、Web 或边缘场景。
5. Ray Serve、NVIDIA Triton Inference Server、KServe 和 BentoML 属于服务或编排层，
   可以承载一个推理引擎，但本身不等于算子加速。本仓库已有 Ray，在线服务可优先用 Ray
   Serve 组织副本、路由和伸缩，底层仍选择最合适的引擎。
6. TorchServe、Hugging Face TGI、Intel Extension for PyTorch、AutoGPTQ、AutoAWQ 已进入
   归档、有限维护或停止维护状态；FasterTransformer 的后续开发已转向 TensorRT-LLM。
   它们只应作为存量兼容或迁移对象，不作为默认的新项目技术选型。

## 2. 分层全景

```text
训练产物 / MLflow Model
        |
        v
模型与数值层      蒸馏 | 剪枝 | 稀疏 | FP16/BF16/FP8 | INT8/INT4 | QAT
        |
        v
PyTorch 执行层    eager | inference_mode | autocast | SDPA | torch.compile
        |
        v
捕获与交换层      torch.export | AOTInductor | ONNX | 厂商中间表示
        |
        v
引擎与内核层      Inductor | TensorRT | ORT EP | OpenVINO | XLA | vLLM/SGLang
        |
        v
请求与服务层      动态/连续批处理 | 缓存 | 路由 | Ray Serve | Triton | KServe
        |
        v
运行与治理层      可观测性 | 回滚 | MLflow 血缘 | 制品校验 | 容量与安全
```

上层和下层并非一一对应。例如 Ray Serve 可以承载 `torch.compile` 模型、ONNX Runtime 会话
或 vLLM；Triton Server 可以加载 TensorRT、ONNX Runtime、OpenVINO、Python、vLLM 或
TensorRT-LLM 后端。量化格式也不是天然通用，权重布局和内核支持必须与最终引擎一起验证。

## 3. 文档导航

| 文档 | 解决的问题 |
| --- | --- |
| [01-优化原理与工作流](01-optimization-foundations.md) | 如何识别瓶颈，哪些优化可叠加，图捕获和动态形状意味着什么 |
| [02-通用模型引擎](02-general-inference-engines.md) | CNN、Transformer Encoder、检测、分割等模型该比较哪些运行时和编译器 |
| [03-量化、稀疏与模型压缩](03-quantization-and-compression.md) | FP16/BF16/FP8/INT8/INT4、PTQ、QAT、剪枝和蒸馏如何选 |
| [04-大模型推理](04-llm-inference.md) | vLLM、SGLang、TensorRT-LLM 等 LLM 引擎的能力边界和选型 |
| [05-边缘与专用硬件](05-edge-and-accelerators.md) | ExecuTorch、Core ML、ORT Mobile、云端 ASIC 和国产加速器如何落地 |
| [06-服务与平台集成](06-serving-and-platform.md) | Ray Serve、Triton、KServe 的职责，以及与 MLflow/MinIO/Ray 的集成 |
| [07-选型方法](07-selection-guide.md) | 用兼容性门槛和加权评分把候选收敛到 2 至 3 个 |
| [08-基准与验收](08-benchmark-and-validation.md) | 如何公平测量延迟、吞吐、显存、精度、LLM TTFT/ITL 和成本 |
| [09-实施手册](09-implementation-playbook.md) | 从原生 PyTorch 到编译、导出、服务和发布的可执行路径 |
| [10-风险与迁移](10-risks-and-migrations.md) | 已停止维护项目、常见失败模式、升级和回滚策略 |
| [参考资料](references.md) | 官方文档、维护公告与术语来源 |

## 4. 当前调研基线

下列版本仅说明本次核验时官方站点展示的文档基线，不构成相互兼容的版本组合：

| 组件 | 核验时官方文档基线 | 备注 |
| --- | --- | --- |
| PyTorch | 2.13 | `torch.compile`、`torch.export` 和新 ONNX 导出器为主线 |
| torchao | 0.17 | PyTorch 量化与低精度开发集中迁入 torchao |
| ExecuTorch | 1.3 | 移动端、嵌入式和边缘部署 |
| OpenVINO | 2026.3 | Intel 平台和跨设备部署工具链 |
| DeepSpeed | 0.19.4 | 本次只评估 Inference API，不等同于完整服务框架 |
| Ray | 2.56 | 本仓库服务编排候选；升级时以仓库实际固定版本为准 |

vLLM、SGLang、TensorRT-LLM、ONNX Runtime 和 Torch-TensorRT 更新较快，正式选型必须固定
容器、Python 包、驱动、固件和模型提交版本，不能只记录“latest”。

## 5. 五分钟候选集

| 场景 | 首批候选 | 何时增加其他候选 |
| --- | --- | --- |
| 通用 CUDA 在线模型 | eager 优化、`torch.compile`、Torch-TensorRT | 需要跨语言部署时加入 ONNX Runtime/TensorRT |
| 通用 CPU 在线或批处理 | eager 优化、`torch.compile`、ONNX Runtime | Intel 为主时加入 OpenVINO；高可移植性时比较 IREE/TVM |
| Intel CPU/GPU/NPU | `torch.compile`、OpenVINO | 已有 ONNX 资产时加入 ORT OpenVINO EP |
| NVIDIA LLM 服务 | vLLM、SGLang、TensorRT-LLM | 强工作流编排时在外层加 Ray Serve 或 NVIDIA Dynamo |
| AMD LLM 服务 | vLLM、SGLang（ROCm） | 逐模型核对 ROCm 和内核支持；保留原生 Transformers 基线 |
| CPU/桌面端 LLM | llama.cpp、MLC LLM | 必须保留 PyTorch 语义时比较 ONNX Runtime GenAI |
| Android/iOS/嵌入式 | ExecuTorch、ORT Mobile | Apple 单平台加入 Core ML；特定 SoC 使用对应 delegate |
| TPU/Inferentia/Ascend 等 | 厂商 PyTorch 前端和编译器 | 迁移成本过高时重新评估硬件或采用 ONNX 中间层 |

## 6. 使用本资料的方式

1. 先填写 [选型输入表](07-selection-guide.md#2-先写清楚输入)，明确模型、输入分布、硬件、
   SLO、成本和部署边界。
2. 通过兼容性门槛排除无法覆盖算子、动态控制流、精度或硬件的方案。
3. 只保留 2 至 3 个候选，按 [统一基准协议](08-benchmark-and-validation.md) 实测。
4. 把引擎构建参数、数据摘要、代码提交、环境摘要、准确性报告和性能报告作为一个不可变
   制品集合，通过 MLflow Tracking/Artifact API 记录。
5. 影子流量或小比例灰度通过后再扩容；模型 Registry alias 的变更仍需要显式审批。

## 7. 本资料不做的承诺

- 不给出脱离模型、输入和硬件的“最快引擎”排名。
- 不把厂商博客的峰值倍数当作本项目容量结论。
- 不假设量化一定更快；没有对应低精度内核时，转换开销可能抵消收益。
- 不把转换成功等同于数值正确，也不把单请求延迟等同于生产吞吐。
- 不授权启动昂贵训练、全量校准、GPU 压测或生产别名切换。

