# 通用 PyTorch 推理引擎与编译器

本章面向 CNN、检测、分割、语音、推荐、Transformer Encoder 和非自回归模型。LLM 专用
请求调度、KV Cache 和多卡策略见 [大模型推理](04-llm-inference.md)。

## 1. 主流候选对比

| 方案 | 输入/前端 | 主要硬件 | 优势 | 主要约束 | 新项目定位 |
| --- | --- | --- | --- | --- | --- |
| PyTorch eager + 优化库 | `nn.Module` | CPU、CUDA、ROCm、MPS、XPU 等 | 兼容性最好、调试最直接 | Python/调度开销，融合有限 | 必须保留的基线 |
| `torch.compile`/Inductor | `nn.Module` | CPU、CUDA 及已支持后端 | 改动小、融合、原生生态 | 首次编译、graph break、重编译 | 默认首个编译候选 |
| `torch.export` + AOTInductor | `ExportedProgram` | 以官方支持矩阵为准 | AOT 打包、减少生产端 Python 依赖 | 图约束更强、制品与环境绑定 | PyTorch 原生独立部署候选 |
| Torch-TensorRT | compile/export | NVIDIA GPU | PyTorch 前端接 TensorRT，支持 JIT/AOT | NVIDIA 绑定、算子与 shape profile | CUDA 高性能候选 |
| TensorRT | ONNX/API/网络定义 | NVIDIA GPU、Jetson | 成熟优化、低精度、部署工具完整 | 构建复杂、引擎可移植性有限 | NVIDIA 性能上限候选 |
| ONNX Runtime | ONNX | CPU、CUDA、TensorRT、OpenVINO、QNN、Core ML 等 EP | 跨语言、跨平台、Execution Provider 丰富 | 导出和 EP 算子覆盖决定收益 | 通用可移植候选 |
| OpenVINO | PyTorch/ONNX/IR | Intel CPU、GPU、NPU 为主 | Intel 优化、量化、异构设备、工具完善 | 非 Intel 不是主优势；算子需验证 | Intel 首选候选 |
| Apache TVM | 多前端/Relax | CPU、GPU、边缘设备 | 可定制编译、跨硬件研究与产品能力 | 编译工程和调优成本高 | 有编译团队时评估 |
| IREE | PyTorch/ONNX/MLIR | CPU、GPU、Vulkan 等 | AOT、跨平台、适合嵌入式/原生应用 | 生态和算子覆盖需逐项验证 | 特殊部署候选 |
| AITemplate | PyTorch 前端/图转换 | NVIDIA、部分 AMD | 静态图编译和生成高性能代码 | 动态性、模型覆盖和构建成本 | 固定图专项评估 |
| XLA/OpenXLA | PyTorch/XLA | TPU，部分 CPU/GPU | TPU 主路径、整图编译 | lazy/compile 语义、shape 和生态差异 | TPU 必选路径 |

“支持某硬件”不代表所有算子、dtype 或动态 shape 都同等优化。候选进入基准前必须运行目标
模型的算子覆盖、fallback 和精度检查。

## 2. PyTorch 原生路径

### 2.1 eager 是可靠的对照组

eager 基线应包含生产实际会使用的 CUDA/cuDNN、oneDNN、ROCm 或其他厂商库版本，并使用
正确的推理模式、精度和批处理。一个未调优 eager 与一个高度调优引擎的对比无法说明引擎本身
的贡献。

原生路径的主要优势是：

- 自定义 Python 逻辑和新算子可快速接入；
- 与训练权重、调试工具和 PyTorch Profiler 一致；
- 部分优化可以按模块逐步引入；
- 不需要先稳定跨框架的交换格式。

### 2.2 `torch.compile`

建议至少测试默认模式和一个与场景匹配的模式，并记录：

- 首次编译时间和首请求延迟；
- 稳态延迟、吞吐和内存；
- 捕获图数量、graph break 与重编译次数；
- 每个 shape bucket 的缓存命中；
- 进程重启后缓存是否可复用；
- 自定义算子是否回退到 eager。

Inductor 在 NVIDIA GPU 上常使用 Triton 生成内核，在 CPU 上生成本地代码并调用优化库。
某些厂商运行时也提供 `torch.compile` backend，例如 OpenVINO 和 Torch-TensorRT；写了
`torch.compile` 并不代表一定使用默认 Inductor。

### 2.3 `torch.export` 与 AOTInductor

适合希望将捕获和编译移到构建阶段、在 C++ 或更精简环境运行的团队。它要求把控制流、
动态 shape 和自定义算子表达为可导出的形式。需要把导出约束和编译器版本当作制品元数据，
不能把生成文件当成可跨环境的通用模型格式。

## 3. NVIDIA 路径

### 3.1 Torch-TensorRT

Torch-TensorRT 当前官方入口同时支持：

- `torch.compile(..., backend="tensorrt")` 的进程内编译；
- `torch.export` 风格的提前编译、序列化和 Python/C++ 部署。

它适合希望保留 PyTorch 模型入口，同时利用 TensorRT 图优化、融合和低精度能力的团队。
应重点检查分区数量、未支持算子、PyTorch/TensorRT 边界复制，以及每个输入 profile 的覆盖。

### 3.2 原生 TensorRT

TensorRT 更接近 NVIDIA 平台的部署运行时。它可以通过 ONNX 或 API 构建 engine，并用
optimization profile 描述动态输入范围。主要工程注意点：

- engine 通常与 TensorRT/CUDA、GPU 架构和构建配置强相关，应在目标环境构建或严格验证；
- FP16、BF16、FP8、INT8/INT4 的可用性取决于 TensorRT 版本、硬件代际和层实现；
- INT8 校准数据必须有代表性，并记录数据摘要与校准缓存摘要；
- plugin 能补齐算子，但增加 ABI、安全、升级和跨平台成本；
- `trtexec` 适合引擎级基准，不代表含预处理和网络的服务端到端性能。

## 4. ONNX Runtime

ONNX Runtime（ORT）用 Execution Provider（EP）把同一 ONNX 图分配给不同硬件后端。官方
列出的 EP 包括 CPU、CUDA、TensorRT、OpenVINO、DirectML、QNN、NNAPI、Core ML、
XNNPACK、ROCm/MIGraphX、CANN 等；每个 EP 的发布和算子支持状态应单独核验。

优势：

- Python、C/C++、Java、C#、JavaScript 和移动端接口较完整；
- 图优化、Transformer 优化、量化、I/O Binding 和 ORT Model Format 工具成熟；
- 可按优先级组合 EP，并在不支持节点上回退。

风险：

- “运行成功”可能包含大量 CPU fallback，导致频繁设备复制；
- PyTorch 自定义算子、数据相关控制流和动态 shape 可能难以导出；
- 不同 EP 对同一 ONNX opset、dtype 和量化格式支持不同；
- EP 优先级和 provider options 属于性能配置，必须随制品记录。

新代码应使用基于 `torch.export` 的 ONNX 导出器（`dynamo=True`），用 `dynamic_shapes`
表达动态输入，并启用导出报告/验证定位问题。导出后至少检查 ONNX checker、ORT 输出一致性、
节点分配日志和端到端性能。

## 5. OpenVINO

OpenVINO 是 Intel 面向云、AI PC、边缘和物理设备的推理工具链，提供直接模型转换、
PyTorch `torch.compile` backend、ONNX/IR 路径、自动设备选择、异步推理和量化工具。

适合场景：

- Intel Xeon/Core CPU 上需要 oneDNN/图级优化；
- Intel GPU/NPU 或 CPU+GPU 异构部署；
- 希望通过 `benchmark_app`、Model Optimizer、NNCF 等形成完整工具链；
- 生成式与传统模型在同一 Intel 平台部署。

不要再把 Intel Extension for PyTorch（IPEX）当作新项目默认路径。其官方仓库已经归档并
提示不再保证维护、修复或更新；存量 IPEX 应比较迁移到原生 PyTorch/Inductor 或 OpenVINO。

## 6. 通用编译器与专项编译器

### 6.1 Apache TVM

TVM/Relax 提供图级和算子级编译、自动调优和多后端代码生成，适合硬件多样、需要自定义算子
或有编译器团队的场景。代价是前端导入、调度搜索、运行时集成和版本维护都需要专门投入。

### 6.2 IREE

IREE 以 MLIR 为基础，把模型 AOT 编译到精简运行时，适合原生应用、Vulkan/移动/边缘和
需要跨语言封装的部署。应验证 PyTorch 导出链、动态 shape、目标驱动和关键算子性能。

### 6.3 AITemplate

AITemplate 通过图转换和代码生成优化固定或有界 shape 的模型，在支持的 GPU 与模型上可能
取得较好性能。它不是 PyTorch 的透明替换，动态控制流、编译时间、二进制体积和算子覆盖是
主要门槛。

### 6.4 BladeDISC 等长尾方案

BladeDISC、厂商 `torch.compile` backend、面向特定芯片的 MLIR 编译器可能适合已有生产
投入的团队，但活跃度、版本兼容和支持渠道差异大。将它们放入候选前应至少确认近期 release、
目标 PyTorch 版本、公开回归测试、许可证、已知安全问题和退出方案。

## 7. 硬件维度的首批候选

| 硬件 | 原生基线 | 专用候选 | 可移植候选 |
| --- | --- | --- | --- |
| NVIDIA GPU | PyTorch CUDA + Inductor | Torch-TensorRT/TensorRT | ONNX Runtime CUDA/TensorRT EP |
| AMD GPU | PyTorch ROCm + Inductor（按支持矩阵） | ROCm 专用库、MIGraphX | ONNX Runtime/TVM/IREE，逐项验证 |
| Intel CPU/GPU/NPU | PyTorch CPU/XPU + Inductor | OpenVINO | ONNX Runtime OpenVINO/CPU EP |
| Apple Silicon | PyTorch MPS | Core ML | ExecuTorch/ORT Mobile/MLC |
| Google TPU | PyTorch/XLA | XLA/OpenXLA | 通常不以 ONNX 为主路径 |
| AWS Inferentia/Trainium | PyTorch Neuron 前端 | Neuron compiler/runtime | 导出到其他硬件只作为迁移路径 |
| ARM/移动 SoC | PyTorch 开发基线 | ExecuTorch delegate、厂商 SDK | ORT Mobile、IREE、TVM |
| NVIDIA Jetson | PyTorch CUDA | TensorRT、ExecuTorch delegate | ONNX Runtime/Triton 边缘部署 |

## 8. 不应混淆的工具

| 名称 | 实际角色 |
| --- | --- |
| Triton language/compiler | 编写和生成 GPU kernel，常被 Inductor 或 LLM 引擎使用 |
| NVIDIA Triton Inference Server | 模型服务、调度、动态批处理、协议和后端管理 |
| FlashAttention/xFormers/FlashInfer | Attention 或 LLM kernel/算子库，不是完整服务平台 |
| CUDA Graph | 捕获并重放 GPU 工作，降低 launch 开销，不是模型交换格式 |
| ONNX | 模型交换 IR；真正执行的是 ORT、TensorRT、OpenVINO 等后端 |
| `torch.export` | PyTorch AOT 图捕获/序列化基础，不自动保证性能提升 |

