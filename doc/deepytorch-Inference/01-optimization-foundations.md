# 优化原理与工作流

## 1. 先判断时间花在哪里

推理请求的端到端时间通常包含排队、解析、预处理、主机到设备复制、模型执行、设备到主机
复制、后处理和序列化。只分析模型前向可能优化了错误的部分。

| 瓶颈 | 常见证据 | 优先动作 |
| --- | --- | --- |
| Python/调度开销 | 小模型、GPU 利用率低、算子很多且很短 | 批处理、算子融合、`torch.compile`、CUDA Graph |
| 计算受限 | 大 GEMM/卷积占比高、计算单元接近满载 | 低精度、优化内核、更合适的 Tensor Core/向量指令 |
| 显存带宽受限 | 大量权重读取、batch 小、算术强度低 | 权重量化、融合、减少中间张量、提高批大小 |
| 主机/设备传输 | memcpy 占比高、设备有明显空洞 | 固定页内存、异步复制、预取、设备侧前后处理、I/O Binding |
| 输入管线 | CPU 满载而 GPU 空闲 | 并行解码、缓存、批量预处理、避免重复格式转换 |
| 排队/调度 | 模型执行快但 P99 高 | 并发上限、背压、动态批处理、隔离长短请求 |
| 内存/KV Cache | OOM、换出、并发上不去 | 量化、限制上下文、分页 KV、前缀复用、合理并行策略 |
| 冷启动/编译 | 首次请求远慢于稳态 | 离线构建、预热、持久缓存、常驻副本 |

建议先用 PyTorch Profiler 或设备工具获得时间线，再决定是否进入图编译。GPU 异步执行时必须
在计时边界同步，否则测到的是 kernel launch 时间而不是执行时间。

## 2. 所有项目都应先做的基线

### 2.1 正确的推理状态

```python
model.eval()

with torch.inference_mode():
    output = model(inputs)
```

`model.eval()` 改变 Dropout、BatchNorm 等模块行为；`torch.inference_mode()` 关闭 autograd
相关开销。两者作用不同，不能互相替代。某些后处理仍需梯度时只能对安全区域使用
`inference_mode()`。

### 2.2 精度和设备上下文

- CUDA 上先比较 FP32、BF16 和 FP16；具体选择取决于硬件、模型稳定性和算子覆盖。
- `torch.autocast` 比手工把所有张量强转为半精度更容易控制算子精度，但仍需准确性回归。
- CPU 上 BF16/INT8 是否受益取决于 ISA、矩阵形状和后端内核，不能套用 GPU 经验。
- 设置 `torch.set_float32_matmul_precision()` 会改变允许的内部精度，需要作为模型配置记录。
- 对卷积模型可比较 channels-last；转换模型和输入后必须检查算子覆盖和额外 layout copy。

### 2.3 批处理、形状和输入

- 离线任务优先增大批量直到吞吐不再改善或内存达到安全水位。
- 在线任务以 SLO 为约束调动态批处理，不以最大吞吐替代 P99。
- 对长度或分辨率变化大的输入做 bucket，通常比完全动态形状更容易编译和复用内核。
- 预分配缓冲区、复用 tokenizer/decoder、避免每次请求创建模型或把权重来回搬运。
- CPU 设置物理核、NUMA、线程数和亲和性；避免 Ray 副本数乘以每副本线程数造成过度订阅。

## 3. PyTorch 2 编译栈的角色

| 组件 | 角色 | 是否直接加速 |
| --- | --- | --- |
| TorchDynamo | 从 Python 执行中捕获可编译图，建立 guard | 间接 |
| AOTAutograd | 训练时处理前后向图；纯推理也参与部分编译流程 | 间接 |
| TorchInductor | PyTorch 默认编译后端，生成 CPU/GPU 代码并融合 | 是 |
| Triton language/compiler | Inductor 和自定义 GPU 内核可使用的编程与编译工具 | 是 |
| `torch.export` | 生成约束明确、可序列化的 AOT 图 | 本身不是 |
| AOTInductor | 把 export 图预编译并打包供 Python/C++ 等环境部署 | 是 |

这里的 Triton 编译器与 NVIDIA Triton Inference Server 是两个不同项目：前者生成内核，后者
管理和服务模型。

### 3.1 `torch.compile` 适合什么

`torch.compile(model)` 保留熟悉的 PyTorch 调用方式，适合作为低迁移成本的首个编译候选。
它可以融合算子、减少 Python/kernel launch 开销并选择优化代码。主要成本包括首次编译、
不同 shape/dtype/device 触发重编译、graph break，以及少数模型的编译失败或数值差异。

常用模式应以当前 PyTorch 文档为准：

- 默认模式：平衡编译时间和稳态性能。
- `reduce-overhead`：主要针对小批量 CUDA 场景降低 Python 开销，可能使用更多内存并依赖
  CUDA Graph 适用性。
- `max-autotune`：用更高编译/调优成本换候选内核搜索，不适合每次启动都重新编译。
- `fullgraph=True`：要求整图捕获，适合发现 graph break，不应在未诊断时盲目作为生产开关。

### 3.2 graph break、guard 和重编译

以下模式容易破坏收益或触发新图：

- 依赖 Tensor 值的 Python 分支、循环或 `.item()`。
- 不可追踪的第三方 Python/C++ 扩展。
- 每次请求变化的 Python 对象结构。
- 无边界的动态 shape、不同 dtype 或不同设备。
- 推理函数中改变全局状态、随机行为或模型结构。

应通过 PyTorch 日志和 `torch._dynamo.explain` 一类诊断能力识别原因，优先修改局部代码或做
shape bucket。不要只把缓存上限调大来掩盖持续重编译。

## 4. 捕获、导出和编译不是一回事

```text
Python nn.Module
  |-- torch.compile ----------> 进程内 JIT 编译和缓存
  |-- torch.export -----------> ExportedProgram（受约束的 ATen 图）
  |       |-- AOTInductor ----> 预编译 PyTorch 制品
  |       `-- Torch-TensorRT --> TensorRT/混合图制品
  `-- torch.onnx.export ------> ONNX -> ORT/TensorRT/OpenVINO/其他后端
```

转换成功只说明示例输入路径可捕获，不说明：

- 所有生产 shape 都满足约束；
- 所有算子都由目标后端执行而没有高成本 fallback；
- 序列化制品能跨 GPU 架构、驱动或运行时版本复用；
- 输出精度和原始模型等价；
- 端到端性能一定改善。

PyTorch 当前 ONNX 主线是基于 `torch.export` 的导出器，`dynamo=True` 已是推荐和默认路径；
动态输入应优先使用 `dynamic_shapes`。存量 TorchScript 模型可以继续维护，但新项目应以
`torch.export`、`torch.compile` 或 ONNX 新导出器为迁移目标。

## 5. 动态形状策略

| 策略 | 优点 | 代价 | 适用场景 |
| --- | --- | --- | --- |
| 固定 shape | 编译和内核最容易优化 | padding 浪费、模型数量增加 | 固定图像、固定批处理 |
| shape bucket | 性能与灵活性的折中 | 需要路由和多个缓存/引擎 profile | NLP 长度、图像分辨率分档 |
| 有界动态 shape | 减少制品数量 | 优化空间变小，约束复杂 | 范围稳定且后端支持良好 |
| 完全动态 | 接口最灵活 | 重编译、fallback 或较慢通用 kernel | 低流量或难以分桶的输入 |

TensorRT optimization profile、ONNX dynamic axes/shapes、Inductor guard 和 LLM 最大上下文长度
表达方式不同，必须把生产分布转换成每个后端自己的有界配置。

## 6. 优化的推荐顺序

1. 固定正确性基线、输入分布、硬件和软件版本。
2. 修正 `eval`/`inference_mode`，消除明显 I/O 和预处理瓶颈。
3. 调 batch、并发、线程、内存格式和 FP16/BF16。
4. 比较 `torch.compile`，记录冷启动和稳态结果。
5. 按硬件加入一个专用引擎和一个可移植运行时候选。
6. 显存或带宽仍是瓶颈时做量化；只有在引擎内核支持下才判断性能收益。
7. 在线服务再调动态批处理、缓存、背压、副本和伸缩。
8. 以任务质量、P99、吞吐、内存、冷启动、稳定性和工程成本共同验收。

不要一次打开所有开关。每次变更只改变一个主要因素，并保留可回滚的基线制品。

## 7. 性能收益为何不能相乘

某项低精度优化声称 1.5 倍、编译器声称 1.4 倍，不代表组合后是 2.1 倍。两者可能同时消除
同一个带宽瓶颈，也可能引入 layout 转换、量化/反量化或 fallback。组合实验需要重新做完整
准确性和端到端性能测试。

