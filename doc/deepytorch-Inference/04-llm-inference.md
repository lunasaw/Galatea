# 大模型推理加速

自回归 LLM 的一次请求包含 prefill 和逐 token decode。prefill 更像大矩阵计算，decode 常受
权重/KV Cache 带宽、kernel launch 和并发调度限制。因此普通模型的“固定 batch 前向耗时”
不足以评价 LLM 服务。

## 1. LLM 引擎要解决的核心问题

| 能力 | 解决的问题 | 验证重点 |
| --- | --- | --- |
| 连续批处理 | 请求长度不同，完成后立即插入新请求 | 高并发吞吐与单请求尾延迟 |
| 分页 KV Cache | 减少连续大块分配和内存碎片 | 可用 block、抢占/换出、OOM 行为 |
| 前缀缓存 | 复用系统提示或共享前缀的 KV | 冷/热命中分开测，租户隔离 |
| chunked prefill | 防止超长 prefill 长时间阻塞 decode | TTFT 与 ITL 的权衡 |
| speculative decoding | 用 draft/多 token 预测减少目标模型步数 | 接受率、额外显存、真实输出分布 |
| 张量/流水线/专家并行 | 单卡放不下或需要扩展吞吐 | 通信拓扑、跨节点带宽、故障域 |
| 量化 kernel | 降权重/KV/激活成本 | 模型质量、格式和硬件支持 |
| structured decoding | JSON/正则/grammar 输出 | 约束编译开销和 token 延迟 |
| Multi-LoRA | 一个基础模型服务多个适配器 | 切换成本、缓存、隔离和版本治理 |
| 解耦 prefill/decode | 分别扩缩容两类计算 | 传输 KV 的成本、路由和复杂度 |

## 2. 主流 LLM 引擎对比

| 方案 | 主要定位 | 硬件/部署倾向 | 优势 | 约束 | 新项目建议 |
| --- | --- | --- | --- | --- | --- |
| Transformers + PyTorch | 正确性与定制基线 | PyTorch 支持设备 | 模型覆盖快、易调试、可用 SDPA/compile | 调度与多租户能力有限 | 必须保留基线 |
| vLLM | 高吞吐通用 LLM/VLM 服务与离线推理 | NVIDIA、AMD 及其他受支持平台 | 连续批处理、分页 KV、OpenAI 风格 API、生态广 | 版本更新快，模型/量化/硬件矩阵需锁定 | 默认候选之一 |
| SGLang | LLM/VLM 服务与复杂生成工作负载 | GPU/受支持加速器 | 前缀复用、结构化生成、调度和分布式能力强 | 同样需要逐版本验证模型与后端 | 默认候选之一 |
| TensorRT-LLM | NVIDIA LLM 优化和部署 | NVIDIA GPU | 深度利用 TensorRT、低精度、多卡与专用 kernel | NVIDIA 强绑定、构建和升级成本高 | NVIDIA 性能上限候选 |
| NVIDIA Dynamo | 分布式生成式 AI 推理编排 | NVIDIA，承载 TRT-LLM/vLLM/SGLang 等 | 路由、KV/请求管理、解耦与多节点编排 | 是上层系统，不替代底层引擎 | 大规模 NVIDIA 集群评估 |
| DeepSpeed Inference | PyTorch Transformer 推理库 | 以 CUDA/多 GPU 为主 | kernel injection、TP、CUDA Graph、INT8 | 不是完整通用服务；现代模型覆盖需核验 | 已用 DeepSpeed 或定制场景 |
| LMDeploy | LLM/VLM 服务和离线推理 | TurboMind/PyTorch 后端 | OpenAI 风格服务、量化和多模型生态 | 社区/模型/硬件覆盖需逐版本核验 | 国内生态候选 |
| ONNX Runtime GenAI | 生成式模型运行时 | 多种 ORT EP、桌面/边缘 | 跨语言和设备、与 ORT 生态结合 | 导出/模型构建与特性覆盖需核验 | 跨平台产品候选 |
| llama.cpp | 本地、CPU、边缘 LLM | CPU、Metal、CUDA 等后端 | GGUF 生态、量化、本地部署简单 | 不运行原始 PyTorch 模型，服务扩展能力不同 | 本地/CPU 首选候选 |
| MLC LLM | 跨平台编译式 LLM | GPU、移动、WebGPU 等 | AOT 和多平台运行时 | 编译和模型支持有额外工程成本 | Web/移动/跨端候选 |
| TGI | Hugging Face 历史 LLM 服务 | GPU | 存量成熟功能 | 已归档/维护模式，官方推荐转向 vLLM/SGLang 等 | 仅存量维护/迁移 |
| FasterTransformer | NVIDIA 历史 Transformer kernel | NVIDIA GPU | 既有定制 kernel | 官方声明后续开发转向 TensorRT-LLM | 迁移对象 |

OpenAI 风格 API 只表示接口形状相似，不保证参数、流式事件、错误码、token 计费、工具调用、
结构化输出和多模态语义完全兼容。客户端迁移必须有契约测试。

## 3. vLLM 与 SGLang 如何比较

两者都在快速演进，不能用静态功能表永久定胜负。建议用同一个模型版本和流量回放比较：

- 模型、VLM、embedding、reranker、reward model 的支持情况；
- NVIDIA/AMD/其他加速器上的具体 kernel 与量化格式；
- TTFT、ITL/TPOT、E2E latency、output tokens/s 和 goodput；
- 长短请求混合、公平性、取消请求和背压；
- prefix cache 命中、cache eviction 和多租户隔离；
- speculative decoding 在真实 prompt/输出分布上的接受率；
- tensor/pipeline/data/expert parallel 与跨节点稳定性；
- structured output、工具调用、Multi-LoRA、多模态的契约正确性；
- 升级、观测、故障恢复和社区/商业支持。

选型不是永久承诺。保留标准化请求契约、引擎适配层和同一套回放基准，可以降低后续切换成本。

## 4. TensorRT-LLM 何时值得投入

适合以下条件同时成立的场景：

- 生产硬件明确是 NVIDIA，且 GPU 型号/驱动/CUDA/TensorRT 能标准化；
- 模型架构和输入上界较稳定；
- 性能或单位 token 成本值得承担 build、profile、插件和升级成本；
- 团队能维护 engine 构建流水线、精度验证和目标 GPU 回归；
- 需要 NVIDIA 工具链中的低精度、多 GPU 或 Triton/Dynamo 集成。

不要把 TensorRT-LLM engine 当作通用权重文件。引擎、timing cache、量化权重和插件应记录
目标 GPU 架构与完整软件栈，并可从原始模型和配置重新构建。

## 5. KV Cache 容量

标准注意力的 KV Cache 规模近似与以下量成正比：

```text
并发序列数 x 已缓存 token 数 x 层数 x KV heads x head dimension
              x 2（K 和 V）x 每元素字节数
```

GQA/MQA 会减少 KV heads；KV Cache 量化会减少每元素字节数。除此之外还要预留权重、激活、
通信 buffer、CUDA Graph、workspace 和内存碎片。因此引擎的 `gpu_memory_utilization` 一类参数
不是越大越好，应通过压力测试留出故障和流量尖峰余量。

容量规划必须按输入 token 与输出 token 的联合分布，而不是只用“最大上下文”。极少数超长
请求可用独立队列、较低并发或专用副本隔离。

## 6. 并行策略

| 策略 | 适用目标 | 主要代价 |
| --- | --- | --- |
| 单卡多副本/Data Parallel | 模型单卡可放下，提高总吞吐和隔离 | 每卡复制权重，单请求能力不增加 |
| Tensor Parallel | 单层矩阵跨卡，模型单卡放不下或需低单请求延迟 | 每层 collective，对 NVLink/网络敏感 |
| Pipeline Parallel | 层跨设备/节点 | pipeline bubble、调度和 KV 管理复杂 |
| Expert Parallel | MoE 专家分布 | all-to-all、负载不均和网络要求高 |
| Context/Sequence Parallel | 超长上下文 | 通信与实现复杂，模型支持有限 |
| Prefill/Decode 分离 | 两阶段独立扩缩容 | KV 传输、路由和运维复杂 |

先选择能放下模型的最简单策略。若单卡可放下，多个独立副本通常比不必要的 TP 更容易获得
高集群吞吐和故障隔离。跨节点 TP 前必须测实际 RDMA/IB/RoCE 带宽和拓扑。

## 7. LLM 量化选择

1. 先用 BF16/FP16 建立质量和性能基线。
2. 显存/带宽受限时比较引擎原生支持的 FP8、INT8 或 INT4，而不是先生成某种 checkpoint。
3. 确认是 weight-only、W8A8、W4A8、KV Cache 量化还是混合方案。
4. 用任务质量、长上下文、工具调用、代码/数学和多语言数据做回归。
5. 记录 tokenizer、chat template、rope/scaling、量化 group、校准和 kernel 版本。

GPTQ/AWQ 是算法家族，不是统一的二进制 ABI。旧 AutoGPTQ/AutoAWQ 已停止维护；新流程按
目标引擎选择 torchao、llm-compressor、GPTQModel、NVIDIA Model Optimizer 或引擎官方工具。

## 8. 请求调度与 SLO

### 8.1 交互式对话

重点是 TTFT、ITL 和 P99。应限制每请求最大 token、并发和排队时间，启用流式返回，并将超长
prefill 与普通对话隔离。只追求总 tokens/s 可能导致用户等待更久。

### 8.2 离线批处理

重点是总完成时间、GPU 利用率和单位 token 成本。可以增加 batch、容忍更长排队、按长度排序
或分桶，但要保留输入和输出对应关系并使重试幂等。

### 8.3 共享前缀/RAG/Agent

前缀缓存收益由实际命中决定。动态 RAG 上下文、不同 system prompt 或租户隔离会降低复用。
要分别报告冷缓存、热缓存和稳态混合命中结果，不把完全相同 prompt 的实验冒充生产收益。

### 8.4 Embedding 与 reranking

它们通常是一次前向的 encoder 工作负载，不一定需要自回归调度。应同时比较普通
`torch.compile`/ORT/TensorRT/OpenVINO 与 LLM 服务引擎的专用模式。

## 9. LLM 生产验收

- 固定模型 revision、tokenizer、chat template 和生成参数；
- 同时记录输入、输出和缓存 token 数；
- 分开 prefill、decode、排队、网络和客户端反压；
- 用并发流量测 TTFT/ITL/P99/goodput，不只跑单个 prompt；
- 验证请求取消、客户端断开、超时、OOM 和节点失败；
- 约束式输出和工具调用做 schema/契约测试；
- 对多租户缓存、日志、prompt 和生成内容实施访问控制与脱敏；
- engine 构建和量化不覆盖源模型，通过 MLflow Artifact API 保存验证报告；
- 新版本先影子/灰度，Registry alias 变更必须显式审核。

