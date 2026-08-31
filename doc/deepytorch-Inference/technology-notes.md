# 技术方案与实施边界速查

本页是 [推理加速选型与验证规范](README.md) 的技术附录，只用于解释候选边界和实施注意事项。
候选是否采用以及是否通过发布，以主规范中的硬门槛和实测结果为准。

## 1. 从瓶颈选择优化层

端到端时间包括排队、解析、预处理、主机到设备复制、模型执行、设备到主机复制、后处理和
序列化。先用 profiler 和服务 trace 确认主导项：

| 主导瓶颈 | 常见证据 | 优先动作 |
| --- | --- | --- |
| Python/调度 | 小算子多、GPU 利用率低、launch 间隙明显 | 合批、融合、`torch.compile`、CUDA Graph |
| 计算 | GEMM/卷积占比高，计算单元接近满载 | BF16/FP16/FP8/INT8、优化 kernel、专用引擎 |
| 内存/显存带宽 | batch 小、权重读取多、算术强度低 | 权重量化、融合、减少中间张量、提高有效 batch |
| H2D/D2H | memcpy 占比高、设备时间线有空洞 | pinned memory、异步复制、预取、I/O Binding、设备侧前后处理 |
| 输入管线 | CPU 满而 GPU 空闲 | 并行 decode/tokenize、缓存、批量预处理、避免重复转换 |
| 排队/调度 | 模型快但 P99 高 | 背压、并发上限、动态批处理、长短请求隔离 |
| KV/显存容量 | OOM、换出、并发无法增加 | 分页 KV、上下文限制、KV/权重量化、前缀复用、并行策略 |
| 冷启动/编译 | 首请求或扩容显著变慢 | 离线构建、全 profile 预热、兼容缓存、最小热副本 |

推荐顺序：训练产物 -> B0 eager 单次推理基线 -> B1 eager 调优对照组 -> 输入/I/O ->
batch/线程/精度 -> `torch.compile` -> 一个专用引擎与一个可移植候选 -> 量化 -> 服务调度与
伸缩。每次只改变一个主要因素，并同时报告相对 B0 的总收益、相对同精度 B1 的引擎增量收益。

## 2. 图捕获、编译和服务的边界

```text
Python nn.Module
  |-- torch.compile ----------> 进程内捕获、JIT 编译和缓存
  |-- torch.export -----------> 受约束、可序列化的 PyTorch AOT 图
  |       |-- AOTInductor ----> 预编译 PyTorch 制品
  |       `-- Torch-TensorRT --> TensorRT 或混合图制品
  `-- torch.onnx.export ------> ONNX -> ORT / TensorRT / OpenVINO / 其他后端

推理制品 -> Ray Serve / Triton / KServe / 引擎自带 server -> 路由、排队、批处理和伸缩
```

- `torch.export` 和 ONNX 是图/交换层，本身不保证加速。
- `torch.compile` 可能使用 Inductor 或厂商 backend；写了 compile 不代表一定使用 Inductor。
- Triton language/compiler 是 GPU kernel 编译工具；NVIDIA Triton Inference Server 是模型服务。
- FlashAttention、xFormers、FlashInfer 是 kernel/算子库，不是完整服务平台。
- Ray Serve、Triton Server 和 KServe 管理服务，不会自动让 `model(inputs)` 变快。

### 动态输入策略

| 策略 | 适用条件 | 主要代价 |
| --- | --- | --- |
| 固定 shape | 固定图像或固定批任务 | padding 或多制品 |
| shape/长度 bucket | 生产分布存在少量主要区间 | 需要路由和多份缓存/profile |
| 有界动态 | 输入范围稳定且后端支持良好 | 优化空间变小、约束复杂 |
| 完全动态 | 低流量或确实无法分桶 | 重编译、fallback 或通用 kernel 性能 |

TensorRT profile、ONNX dynamic shapes、Inductor guard 和 LLM 最大上下文的表达不同。必须用同一
生产分布分别配置，而不是机械复用参数。

## 3. 引擎能力边界

| 方案 | 优势 | 主要风险或成本 |
| --- | --- | --- |
| PyTorch eager | 兼容性和调试最好，必须保留 | Python/launch 开销、融合有限 |
| `torch.compile`/Inductor | 改动小、原生融合、适合首个候选 | graph break、guard、首次编译、动态 shape 重编译 |
| `torch.export` + AOTInductor | 构建前移、可面向精简/C++ 环境 | 图约束更强，制品与软件/硬件环境绑定 |
| Torch-TensorRT | 保留 PyTorch 前端并使用 TensorRT | NVIDIA 绑定、分区边界、shape profile 和 fallback |
| TensorRT | NVIDIA 上成熟的图优化、低精度和工具 | 构建/plugin 复杂，engine 可移植性有限 |
| ONNX Runtime | 跨语言，Execution Provider 多 | 导出覆盖、EP 节点分配和跨设备复制决定收益 |
| OpenVINO | Intel CPU/GPU/NPU 工具链完整 | 非 Intel 不是主要优势，仍需检查算子和量化覆盖 |
| TVM/IREE/AITemplate | 可定制、AOT 或特殊平台能力 | 需要编译器工程投入，动态性和维护成本较高 |
| XLA/厂商编译器 | TPU/ASIC 的主要路径 | shape、运行语义、硬件和制品绑定 |

`torch.compile` 应记录图数量、graph break、重编译、缓存命中和首请求。TensorRT/Torch-TensorRT
应记录 optimization profile、分区、plugin 和 engine 构建环境。ONNX Runtime 应记录 opset、
Execution Provider 顺序/options、节点分配和 I/O Binding；运行成功但大量 CPU fallback 不合格。

## 4. LLM 专项注意事项

普通静态图推理无法覆盖 LLM 的完整问题。引擎还需管理 continuous batching、KV Cache、流式
输出、请求抢占、prefix cache、多卡并行、LoRA 和结构化输出。

| 候选 | 更适合 | 重点验证 |
| --- | --- | --- |
| vLLM | 广泛模型支持和通用在线/离线 GPU 服务 | 目标模型/硬件、KV、调度、量化、API 契约 |
| SGLang | 结构化生成、共享前缀、Agent/RAG 工作流 | prefix 命中、运行时能力、调度和正确性 |
| TensorRT-LLM | NVIDIA 性能上限、多卡和低精度投入 | engine 构建、模型覆盖、版本绑定、插件和重建成本 |
| llama.cpp | CPU/桌面/边缘和 GGUF 生态 | 量化质量、设备 offload、上下文和格式转换 |
| MLC LLM | Web/移动/多后端编译部署 | 编译链、目标设备覆盖、包体和运行时约束 |

并行策略按瓶颈选择：单卡放不下时先考虑 tensor parallel；吞吐扩展优先数据并行副本；超长模型
或架构限制才考虑 pipeline parallel；prefill/decode 分离只在规模和流量能覆盖额外路由、网络和
运维成本时引入。并行越复杂，故障域、通信和可观测性成本越高。

KV 容量估算必须包含层数、KV heads、head dimension、dtype、token 数、并发、block 碎片和
运行时开销。只按权重大小判断“能放几路请求”会严重高估容量。

## 5. 端侧和专用硬件

- ExecuTorch 适合 PyTorch 模型向移动、嵌入式和 delegate 交付；必须测试目标设备和 delegate。
- Core ML 适合 Apple-only；PyTorch MPS 更适合开发验证，不等同于最终移动制品。
- ORT Mobile 适合已有 ONNX 资产和跨移动平台；需裁剪算子并验证 EP fallback。
- Qualcomm、Jetson、Ascend、Neuron、TPU 等使用对应厂商编译器时，保存目标芯片、固件、
  编译参数和支持矩阵，源 PyTorch/MLflow Model 仍是可重建来源。
- 端侧正式基准使用低、中、高档真机，报告安装包、峰值内存、冷/热启动、持续性能、温升、
  电量和 delegate 失败时的行为；模拟器结果不能作为发布结论。

## 6. 制品与平台集成

建议职责分工：MLflow 管理血缘、质量和 Registry；MinIO 由 MLflow Artifact API 间接承载制品；
Ray Job 构建和验证；Ray Serve 承载在线服务，Ray Data/Job 承载离线任务；项目适配器加载实际
引擎。公共平台代码不得假设具体任务、metric、模型族或单一硬件厂商。

每个部署制品附带不可变 manifest，至少包含：

```yaml
schema_version: 1
source:
  mlflow_run_id: <run-id>
  model_uri: runs:/<run-id>/model
  model_digest: <sha256>
  code_revision: <git-commit>
model:
  task: <task-name>
  input_schema_digest: <sha256>
  preprocessing_digest: <sha256>
  precision: <bf16|fp16|int8|int4|...>
engine:
  name: <inductor|tensorrt|onnxruntime|openvino|vllm|...>
  version: <exact-version>
  build_options_digest: <sha256>
  target_hardware: <model-and-architecture>
environment:
  image_digest: <container-image-digest>
  compatibility_manifest: <artifact-path-or-digest>
validation:
  quality_report_uri: <mlflow-artifact-uri>
  benchmark_report_uri: <mlflow-artifact-uri>
  tested_input_profile: <artifact-path-or-digest>
```

模型、plugin、pickle、Python backend 都可能执行代码。生产只加载 allowlist 来源且校验 digest/
签名的制品；管理面和推理面分权；限制输入、batch、并发、上下文和超时；Ray Dashboard、
Triton 管理端点、MLflow 与 MinIO 不直接暴露到不可信网络。

## 7. 常见症状速查

| 现象 | 首查 | 常见根因 |
| --- | --- | --- |
| P99 突升 | queue、batch、输入长度、重编译、资源利用率 | 突发、长短请求混跑、cache miss、资源争用 |
| GPU 利用率低 | CPU/preprocess、H2D、launch timeline | 输入管线、batch 太小、Python/同步开销 |
| OOM | 权重、KV、workspace、副本、reserved/碎片 | 容量模型错误、取消泄漏、并发或 profile 变化 |
| 质量回退 | model/tokenizer/digest、精度、前后处理 | 制品错配、量化、算子/fallback 变化 |
| 节点特有错误 | GPU 架构、驱动、固件、engine target | 节点漂移、二进制不可移植 |
| 首请求超时 | 下载、编译、全 profile 预热和缓存 | 构建未前移、缓存丢失、ready 过早 |
| 流式请求不释放 | 客户端断开、取消传播、生成上限 | 队列、计算或 KV 未清理 |

故障缓解使用限流、隔离、摘除节点或回滚到已审核完整版本。不得在现场覆盖 MLflow artifact 或
直接修改生产 alias；诊断数据写入独立 Run 并脱敏。

## 8. 维护与迁移

截至本页核验日期，下列项目不用于新项目默认选型：

| 存量路径 | 状态 | 优先迁移方向 |
| --- | --- | --- |
| TorchServe | 有限维护/无计划安全补丁，仓库归档 | Ray Serve、Triton、KServe/BentoML 或引擎 server |
| Hugging Face TGI | maintenance mode/仓库归档 | vLLM、SGLang；本地场景 llama.cpp 等 |
| Intel Extension for PyTorch | 仓库归档且不保证修复 | 原生 PyTorch/Inductor、OpenVINO |
| FasterTransformer | 后续开发转向 TensorRT-LLM | TensorRT-LLM、vLLM 或 SGLang |
| AutoGPTQ/AutoAWQ | unmaintained/deprecated | GPTQModel、llm-compressor 或引擎官方工具 |
| 新建 TorchScript/旧 ONNX exporter | PyTorch 主线已转移 | `torch.export`、AOTInductor、export-based ONNX exporter |

存量系统不应因状态变化立即停机。先限制安全暴露，固定当前契约和基线，建立并行候选，完成
质量、API、性能和回滚验证后再灰度迁移。维护状态证据见 [官方资料](references.md)。
