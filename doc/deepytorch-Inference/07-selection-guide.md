# 推理加速选型方法

选型目标不是找到抽象意义上“最快”的框架，而是找到在质量、SLO、硬件、交付时间、稳定性、
安全和退出成本约束下可持续运行的组合。

## 1. 两阶段决策

```text
候选全集
  -> 兼容性硬门槛（不满足即淘汰）
  -> 2~3 个 PoC 候选
  -> 同协议基准和故障测试
  -> 加权评分 + 风险评审
  -> 主方案 + 可回滚方案
```

不要一开始对十几个引擎做完整压测。先用硬门槛淘汰不可能覆盖模型、硬件或部署约束的方案。

## 2. 先写清楚输入

### 2.1 模型与任务

- 项目、任务类型、模型架构、参数量、权重格式和源 MLflow Run ID；
- PyTorch 与第三方库版本、自定义算子、Python 控制流；
- 输入/输出 schema、dtype、shape/长度分布、动态范围；
- 预处理、tokenizer/chat template、后处理和业务规则；
- 基准质量指标、切片指标和允许误差；
- 模型更新频率和一次发布可接受的构建时间。

### 2.2 工作负载

- 在线、流式、离线批处理、端侧还是混合；
- 平均/峰值 QPS、并发、突发形态和到达过程；
- P50/P95/P99 SLO、超时和可接受排队；
- batch=1 与生产 batch；
- LLM 输入/输出 token 联合分布、缓存命中和长上下文占比；
- 冷启动、扩容、故障恢复和可用性目标。

### 2.3 硬件与平台

- CPU 型号/ISA/NUMA、GPU/ASIC 型号与数量、内存/显存；
- PCIe/NVLink/IB/RoCE 拓扑和跨节点带宽；
- OS、容器、驱动、固件、CUDA/ROCm/oneAPI/CANN 等版本；
- Python/C++/Java/移动/Web 的运行环境；
- Ray/Kubernetes/裸机/云服务和伸缩边界；
- 单请求、每小时、每百万样本或每百万 token 的成本目标。

### 2.4 组织约束

- 是否允许模型转换、低精度、闭源 runtime 或厂商绑定；
- 团队能否维护 C++ plugin、自定义 kernel 和编译器；
- 许可证、出口/地域、漏洞响应和商业支持要求；
- 交付期限、升级窗口、离线环境和制品供应链要求。

## 3. 兼容性硬门槛

任一项为“否”且没有可接受的修复路径时，候选淘汰：

| 门槛 | 验证问题 |
| --- | --- |
| 模型覆盖 | 所有关键算子、控制流、自定义 op 和输出结构是否支持？ |
| 输入覆盖 | 生产 shape/dtype/长度上界能否表达且不会持续重编译？ |
| 硬件覆盖 | 目标型号、驱动、架构和多卡拓扑是否在支持矩阵？ |
| 质量 | 目标精度/量化能否通过任务指标和切片门槛？ |
| 语言/系统 | 目标 Python/C++/Java/移动/Web 环境能否加载？ |
| 服务语义 | 流式、取消、超时、batch、工具调用等契约是否满足？ |
| 安全合规 | 是否仍接收安全修复，许可证和制品来源是否可接受？ |
| 可恢复性 | 能否从源模型重建，有健康检查、回滚和故障恢复路径？ |

## 4. 决策树

```text
是否是自回归 LLM/VLM 生成？
  |-- 是
  |    |-- 本地/CPU/边缘且接受模型格式转换？ -> llama.cpp / MLC LLM
  |    |-- NVIDIA 且追求性能上限、能维护 engine？ -> 加入 TensorRT-LLM
  |    `-- 通用在线/离线 GPU -> vLLM 与 SGLang，保留 Transformers 基线
  |
  `-- 否
       |-- 必须保留 Python/PyTorch 动态性？ -> eager + torch.compile
       |-- Intel 为主？ -> OpenVINO + torch.compile/ORT 基线
       |-- NVIDIA 为主且模型较稳定？ -> Torch-TensorRT/TensorRT
       |-- 需要跨语言/跨硬件？ -> ONNX Runtime
       |-- 移动/嵌入式？ -> ExecuTorch / ORT Mobile / Core ML
       `-- 特殊 ASIC？ -> 厂商 PyTorch 前端/编译器 + 可迁移基线
```

服务层另选：已有 Ray 时优先评估 Ray Serve；已有 Kubernetes 标准控制面时评估 KServe；
多框架 NVIDIA 模型服务器评估 Triton。服务层选择不应改变底层候选的公平基准。

## 5. 加权评分

通过硬门槛后，用 1 到 5 分评分。示例权重需要按项目调整：

| 维度 | 示例权重 | 评分证据 |
| --- | ---: | --- |
| 任务质量/数值一致性 | 20% | 正式验证集、切片和回归报告 |
| SLO 与 goodput | 20% | 生产分布负载测试，含 P99 |
| 资源/成本 | 15% | 内存、显存、功耗、节点数、单位请求/token 成本 |
| 模型和硬件覆盖 | 10% | 覆盖报告、fallback、支持矩阵 |
| 稳定性与恢复 | 10% | 长稳、OOM、取消、节点失败、重启测试 |
| 工程复杂度 | 10% | 代码改动、构建/调试/发布工时 |
| 可观测与运维 | 5% | 指标、trace、profiler、告警和容量工具 |
| 维护/安全/支持 | 5% | release、CVE 响应、支持渠道、许可证 |
| 可移植与退出成本 | 5% | 标准格式、适配层、重建与迁移验证 |

总分：

```text
weighted_score = sum(score_i / 5 * weight_i)
```

分数不是自动决策。必须在评审中单列：未知风险、未测特性、厂商绑定、需要长期维护的 plugin
和只有单人掌握的组件。

## 6. 场景化推荐组合

### 6.1 中小型 CUDA 视觉在线服务

- 基线：eager FP32 与 eager FP16/BF16；
- 候选：Inductor、Torch-TensorRT；需要 C++/标准服务时加入 TensorRT/ORT；
- 服务：已有 Ray 用 Ray Serve，或统一模型服务器用 Triton；
- 重点：图像 decode、H2D、batch 等待、P99 和 TensorRT profile。

### 6.2 CPU Transformer Encoder/Embedding

- 基线：PyTorch CPU，固定线程/NUMA；
- 候选：Inductor、ONNX Runtime；Intel 平台加入 OpenVINO；
- 精度：BF16、INT8 dynamic/static；
- 重点：tokenizer、序列长度 bucket、线程过度订阅和 batch。

### 6.3 高并发 NVIDIA LLM

- 基线：Transformers BF16/FP16；
- 候选：vLLM、SGLang、TensorRT-LLM；
- 服务：引擎自带 API 先基准，大规模再比较 Ray Serve/Dynamo/Triton 集成；
- 重点：真实 token 分布、TTFT/ITL/goodput、KV、量化质量和多卡通信。

### 6.4 AMD LLM

- 基线：Transformers + ROCm；
- 候选：目标版本明确支持的 vLLM、SGLang；
- 重点：不要按 CUDA 功能表推断 ROCm；逐模型检查 attention/quantization kernel、collective
  和 GPU 型号。

### 6.5 移动端视觉/语音

- 候选：ExecuTorch、ORT Mobile；Apple-only 加 Core ML；
- 精度：FP16、INT8，按 delegate 支持选择；
- 重点：包体、真实低端机、热稳定性能、电量和系统版本 fallback。

## 7. PoC 退出标准

一个 PoC 只有同时具备以下产物才算完成：

- 可重复构建脚本与固定环境，而不是只在 Notebook 成功；
- 源模型、导出物和 engine digest；
- 生产输入范围的算子覆盖/fallback 报告；
- 质量、性能、内存和冷启动报告；
- 负载、故障、取消和长稳结果；
- 已知限制、失败样例和未测试范围；
- 服务加载、健康检查、回滚和重建步骤；
- MLflow Run ID 和制品 URI。

## 8. 反模式

- 只比较厂商白皮书或开源项目首页的倍数；
- 用不同 batch、精度、最大长度或输出长度比较引擎；
- 只测模型 forward，不测 tokenizer、复制、排队和流式客户端；
- 因为 ONNX/engine 能生成就跳过准确性和 fallback 检查；
- 在测试集上反复选择量化方案；
- 用“支持 CUDA”推断支持所有 NVIDIA 代际或用“支持 ROCm”推断所有 AMD GPU；
- 选中已归档项目却没有安全补丁与迁移计划；
- 把服务 API 与引擎直接耦合，导致未来无法切换；
- PoC 使用 latest，生产无法还原版本。

