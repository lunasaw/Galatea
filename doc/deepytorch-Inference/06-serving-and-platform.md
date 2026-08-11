# 模型服务与平台集成

推理引擎负责执行模型，服务层负责协议、排队、批处理、路由、副本、伸缩、健康检查和观测。
二者可以组合，但性能问题必须能定位到各层。

## 1. 服务方案对比

| 方案 | 主要角色 | 引擎支持倾向 | 优势 | 约束 |
| --- | --- | --- | --- | --- |
| Ray Serve | Python/模型组合、分布式副本和伸缩 | 任意可嵌入 Python 的引擎，含 LLM 集成 | 与 Ray 资源调度、Actor、集群一致，适合复杂 DAG | 需自行管理底层引擎参数和平台安全 |
| NVIDIA Triton Inference Server | 多后端高性能模型服务器 | TensorRT、ORT、OpenVINO、Python、PyTorch、vLLM、TRT-LLM 等 | 动态/序列批处理、并发实例、HTTP/gRPC、性能工具 | 配置和模型仓库治理复杂；不是 Kubernetes 编排器 |
| KServe | Kubernetes 推理控制面和标准协议 | Triton、MLServer、自定义 runtime 等 | Kubernetes CRD、路由、伸缩、canary 和多 runtime | 依赖 K8s/Service Mesh；性能由实际 runtime 决定 |
| BentoML | 模型打包、API 服务和部署工作流 | Python 生态和多种引擎 | 开发交付体验、容器化和服务抽象 | 集群与极致性能仍取决于后端和部署环境 |
| FastAPI/gRPC 自建 | 轻量协议层 | 任意嵌入式引擎 | 控制直接、依赖少 | 批处理、伸缩、健康、指标和滚动升级需自建 |
| NVIDIA Dynamo | 分布式生成式 AI 推理编排 | vLLM/SGLang/TRT-LLM 等 | 面向大规模 LLM 路由、KV 和 prefill/decode 架构 | NVIDIA 生态倾向；不是普通模型的通用默认 |
| TorchServe | PyTorch 历史模型服务器 | PyTorch、部分 LLM 后端 | 存量接口和模型归档格式 | 已停止主动维护且无计划安全补丁，不用于新建 |

## 2. 本仓库建议的职责分工

```text
MLflow Tracking / Registry
    | 记录源 Run、模型版本、质量门槛和候选 alias
    v
MLflow Artifact API -> MinIO
    | 下载源模型、导出物、engine、报告和 manifest
    v
Ray Job（构建/验证）
    | torch.export / ONNX / TensorRT build / 量化 / smoke / benchmark
    v
不可变推理制品 + manifest + MLflow Run ID
    |
    v
Ray Serve（在线）或 Ray Data/Job（离线）
    | 资源声明、副本、路由、背压、伸缩、预处理/后处理组合
    v
底层引擎：Inductor / TensorRT / ORT / OpenVINO / vLLM / SGLang ...
```

平台代码保持框架中立：Ray Serve 部署读取一个显式 engine 配置，适配器在项目代码内加载具体
运行时。不要把 Cats vs Dogs 的模型类型、固定 metric 或单一 NVIDIA 假设写入公共服务层。

## 3. Ray Serve

Ray Serve 适合本仓库，因为 Ray 已承担分布式工作负载。其价值主要是：

- 用 deployment/replica 对模型进程和 CPU/GPU 资源建模；
- HTTP/gRPC 入口、DeploymentHandle 和多 deployment 组合；
- 副本级并发上限、排队、背压、健康检查和优雅关闭；
- 基于 ongoing requests 等信号的副本自动伸缩；
- request batching 和模型 multiplexing；
- 与 Ray 集群调度、placement group、Dashboard/指标集成；
- 在支持版本中使用 LLM serving 集成承载 vLLM 等引擎。

Ray Serve 不会自动让一个慢的 `model(inputs)` 变快。`@serve.batch` 是服务级请求合批；vLLM
的 continuous batching 是 token 级调度，两者不能无脑叠加，否则可能增加等待或形成双重
队列。

### 3.1 版本注意

本仓库现有 [Ray API 文档](../ray-api.md) 固定到 Ray 2.53.0，而本次调研时官方站点已展示
2.56 文档。Serve API 更新较快，任何样例必须在仓库实际固定版本上验证；尤其是 autoscaling
字段、并发字段、LLM API 和部署 CLI，不能从最新版网页直接复制到旧集群。

### 3.2 副本资源

每个副本应显式声明：

- `num_cpus`、`num_gpus`、memory 和自定义 accelerator resource；
- 多 GPU 引擎需要的 placement group bundles 和策略；
- CPU 引擎的线程数，防止“副本数 x 每副本线程”超出物理核；
- GPU fraction 仅在引擎确实支持安全共享时使用；
- 模型加载和编译时间应纳入 health/启动超时。

### 3.3 伸缩信号

通用模型可以用正在处理的请求数作为起点；LLM 更适合结合排队 token、KV Cache 水位、TTFT
SLO 和请求长度。副本启动需要数分钟时，纯反应式 autoscaling 往往太迟，应设最小热副本、
预扩容或调度预测。缩容必须等待流式请求完成或安全转移。

## 4. NVIDIA Triton Inference Server

Triton Server 提供多模型/多后端加载、并发模型实例、动态批处理、sequence batching、模型
ensemble、HTTP/gRPC、KServe 协议、指标、trace 和模型管理。它还提供：

- Performance Analyzer：产生请求并测客户端/服务端性能；
- Model Analyzer：搜索模型配置和实例组合；
- GenAI-Perf：面向 LLM/VLM/embedding/ranking 的生成式指标；
- 多种 backend：TensorRT、ONNX Runtime、OpenVINO、Python、vLLM、TensorRT-LLM 等。

注意区分：

- Triton dynamic batching 与 backend 内部 continuous batching 的职责；
- model instance 增加会复制权重/workspace，可能更快 OOM；
- ensemble 方便组合，但跨 backend 张量复制可能成为瓶颈；
- model repository 是部署接口，不应直接指向未经审核的用户可写目录；
- Python backend 可运行任意代码，应按代码执行面保护。

## 5. KServe 与 BentoML

KServe 更适合已标准化 Kubernetes、需要声明式 InferenceService、canary、路由和多 runtime 的
平台。它可以把 Triton 或自定义 Ray/HTTP 服务纳入 K8s 控制面，但不会替代引擎调优。

BentoML 更适合希望快速把 Python 模型、依赖和 API 打包为可部署服务的团队。若目标是复杂
多节点 LLM 或严格 GPU 拓扑，仍需明确底层引擎和集群调度方案。

本仓库已经使用 Ray，除非组织层面要求统一 K8s/KServe，否则不建议为单个模型额外引入第二
套集群控制面。可以在外层 Kubernetes 管理 RayCluster/RayService，但需要明确每层伸缩职责，
避免 K8s、Ray autoscaler 和 Serve autoscaler 同时无约束决策。

## 6. 托管与商业交付

市面上的托管服务通常把 runtime、容器、伸缩和运维打包，不代表底层出现了另一种通用加速
原理。典型选择包括：

| 产品类型/示例 | 价值 | 选型时仍需确认 |
| --- | --- | --- |
| NVIDIA NIM / NVIDIA AI Enterprise | 预构建生成式/AI 模型微服务、受支持容器和企业支持 | GPU/模型许可、可配置引擎、指标、升级和私有部署边界 |
| AWS SageMaker Inference + Neuron/Triton 等 | 托管 endpoint、实例/伸缩和 AWS 芯片路径 | 实例代际、容器/runtime、冷启动、数据传输和退出成本 |
| Google Vertex AI Prediction + TPU/GPU | 托管预测 endpoint 和 Google 加速器 | 自定义容器能力、TPU/XLA 路径、区域与配额 |
| Azure ML Managed Online Endpoint | 托管部署、流量和身份集成 | 底层 VM/runtime、镜像可控性、诊断和成本 |
| Hugging Face Inference Endpoints | 托管开源模型和引擎部署 | 具体 engine、硬件、模型 revision、API 语义和区域 |

采购或 PoC 报告应把“托管控制面费用”和“底层实例/每 token 成本”分开。仍要执行同一质量、
性能和安全协议，并确认能导出源权重、配置、日志与指标，避免只能在某个 endpoint 内复现。

## 7. 在线与离线分开

| 类型 | 推荐执行面 | 优化目标 |
| --- | --- | --- |
| 在线同步请求 | Ray Serve/Triton/其他服务器 | P99、goodput、可用性、背压 |
| 流式 LLM | 支持异步流式和取消的 LLM server + 服务层 | TTFT、ITL、断开清理 |
| 大规模离线批推理 | Ray Data `map_batches` 或参数化 Ray Job | 总吞吐、成本、幂等和恢复 |
| 定时小批 | Ray Job/工作流调度器 | 启动成本、可追踪、失败重试 |

不要通过向在线端点发送数百万请求来替代离线批推理。离线任务应直接复用模型进程、批量读取
对象存储、分区写结果，并用确定的任务 ID/输出路径实现幂等。

## 8. 推理制品 manifest

建议每个可部署制品附带机器可读 manifest；字段可以是 YAML/JSON，但不能含 secret：

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
  precision: bf16
engine:
  name: <inductor|tensorrt|onnxruntime|openvino|vllm|...>
  version: <exact-version>
  build_options_digest: <sha256>
  target_hardware: <hardware-and-architecture>
environment:
  image_digest: <container-image-digest>
  framework_versions: <artifact-path-or-digest>
validation:
  quality_report_uri: <mlflow-artifact-uri>
  benchmark_report_uri: <mlflow-artifact-uri>
  tested_shape_profile: <artifact-path-or-digest>
```

manifest 内的 MLflow URI 是逻辑引用。客户端通过 Tracking/Artifact API 下载，不打开
`mlflow.db`，也不读取服务端 MinIO 文件系统或持有长期对象存储密钥。

## 9. 构建与发布流程

1. 用参数化 Ray Job 下载已审核源模型，验证 digest 和 signature。
2. 在声明了目标 CPU/GPU/内存的 worker 上构建；TensorRT 等硬件相关引擎在兼容节点构建。
3. 运行算子覆盖、输出一致性、小流量压力、损坏制品和重启恢复测试。
4. 将 engine、manifest、构建日志摘要、质量和基准报告上传到新的 MLflow Run。
5. 只由权威 worker 完成 Run 和共享制品；重试使用唯一 Run/构建 ID，不覆盖前一次结果。
6. Ray Serve/Triton 从不可变版本加载，ready 之前完成预热和自检。
7. 影子或 canary 比较生产流量指标；失败回滚到上一完整版本。
8. 通过显式审核更新候选/生产 alias，不由构建或压测脚本自动更新。

## 10. 可观测性

至少按模型版本、engine 版本、deployment、replica、节点和加速器记录：

- 请求率、成功率、限流/排队/取消/超时；
- P50/P95/P99 端到端、排队、预处理、模型、后处理延迟；
- batch 大小分布、并发和 shape/长度 bucket；
- CPU、RSS、GPU 利用率、显存、功耗、PCIe/网络；
- 编译次数、graph break、fallback、cache 命中；
- LLM 的 input/output tokens、TTFT、ITL、KV Cache 和 prefix cache；
- 模型加载、健康检查、重启、OOM 和伸缩事件。

高基数字段如原始 prompt、请求 ID 和完整 shape 不应直接成为 Prometheus label。敏感输入和
预测不得进入普通日志；需要分析时使用受控、脱敏、有限保留的采样。

## 11. 服务安全

- 默认绑定 loopback 或受控私网；对外通过认证、授权、TLS 和限流代理；
- 不在命令、仓库、Notebook、日志或 manifest 中保存 token；
- 模型管理/加载 API 与普通 inference API 分权，生产默认关闭动态任意模型加载；
- 模型和插件视为可执行供应链制品，检查来源、digest、签名和依赖漏洞；
- 限制输入尺寸、batch、最大 token、超时和并发，避免内存/计算耗尽；
- Python/pickle、自定义 op、Triton Python backend 都具有代码执行风险；
- 多租户 prefix/KV/LoRA 缓存要有隔离和清理策略；
- Ray Dashboard、Triton 管理端点、MLflow 和 MinIO 不直接暴露到不可信网络。
