# 基准测试与验收规范

本规范的目标是让两个引擎的结果可比较、可复现、可解释。性能结果必须与准确性、输入分布、
硬件、软件环境和制品 digest 绑定。

## 1. 先定义测试类型

| 测试 | 输入方式 | 目的 | 不能替代 |
| --- | --- | --- | --- |
| microbenchmark | 单个算子/模块 | 定位 kernel 和融合 | 完整模型、服务性能 |
| 模型离线基准 | 内存中批次、直接调用引擎 | 比较纯模型执行 | 预处理、网络和排队 |
| 端到端离线 | 从真实数据源读到结果落盘 | 评估批任务成本 | 在线 P99 |
| 单实例在线 | 通过 HTTP/gRPC 发送负载 | 评估服务开销和调度 | 集群伸缩、节点故障 |
| 集群容量 | 多实例/多节点、真实流量模型 | 容量、伸缩和稳定性 | 数值质量测试 |
| 长稳/故障 | 数小时或更长、注入失败 | 泄漏、恢复和尾延迟 | 峰值吞吐搜索 |

## 2. 公平性控制

所有候选必须固定：

- 同一源模型 revision、权重 digest、tokenizer、预处理和后处理；
- 同一质量门槛；不同精度可比较，但必须同时报告精度差异；
- 同一输入集合、顺序或可重放到达过程；
- 同一输入 shape/长度和输出 token 分布；
- 同一硬件型号、数量、拓扑、功耗模式和隔离策略；
- 同一服务协议、客户端位置、连接复用和流式消费速度；
- 可比的 batch/并发/SLO；若引擎自己调度 batch，报告实际 batch 分布；
- 明确冷缓存、预热后、prefix cache 冷/热状态。

关闭调试 profiler 后再测正式性能，因为 profiler 会改变时序。不要让另一个训练或 Notebook
任务共享被测 GPU。

## 3. 环境记录

建议在报告中保存：

```text
timestamp / timezone / benchmark_code_revision
source_run_id / model_digest / dataset_or_replay_digest
os / kernel / container_image_digest
python / pytorch / engine / compiler versions
cpu_model / sockets / physical_cores / numa / memory
accelerator_model / count / topology / driver / firmware
cuda_or_rocm_or_vendor_stack / collective_library
precision / quantization / shape_profiles / engine_build_digest
thread_env / cpu_affinity / power_mode / clock_policy
service_config / replica_resources / batching / concurrency / cache
```

容器内包版本不够，还要记录宿主驱动和设备固件。环境信息作为 artifact，不把超长文本都塞进
MLflow tag。

## 4. 正确的计时

### 4.1 预热与编译

分别报告：

- 导出时间；
- engine build/编译时间；
- 进程启动到 ready；
- 第一次真实请求；
- 预热后的稳态。

预热输入应覆盖所有 shape bucket/profile。只用一个 shape 预热会把其他 shape 的编译抖动留给
生产流量。

### 4.2 GPU 同步

CUDA/ROCm 调用通常异步。直接用 CPU wall clock 包住 `model(x)` 会低估时间。可以在样本边界
使用设备 event 或显式 synchronize；正式服务基准则以客户端和服务 trace 的端到端时间为准。

### 4.3 统计方法

- 延迟报告 P50/P90/P95/P99/max，不只平均值；
- 报告样本数、测试时长、warmup 数和置信区间/多轮波动；
- 吞吐搜索至少覆盖从低并发到饱和，再到出现排队/错误的区域；
- 稳态测试足够长以覆盖 GC、allocator、cache eviction、autoscale 和热降频；
- 同一配置重复多轮，随机化候选顺序以减少温度/后台负载偏差。

## 5. 通用模型指标

| 指标 | 定义/注意 |
| --- | --- |
| latency | 单请求/单批端到端或模型执行时间，必须注明边界 |
| throughput | samples/s、requests/s 或 batches/s，注明 batch 与并发 |
| goodput | 在质量、延迟 SLO 和错误率约束内完成的有效请求率 |
| queue time | 到达服务到进入执行的时间 |
| cold start | 进程/副本创建到 ready 及首请求时间 |
| memory | RSS、峰值 host memory、allocated/reserved/峰值显存 |
| utilization | CPU、GPU、内存带宽、SM/Tensor Core、I/O/网络 |
| efficiency | 每瓦、每设备、每成本单位的有效吞吐 |
| reliability | error、timeout、OOM、重启、取消泄漏、恢复时间 |

批处理服务应报告实际 batch size 直方图和 batch wait time。高吞吐但绝大多数请求超过 SLO 的
配置没有生产价值。

## 6. LLM 指标

| 指标 | 推荐含义 |
| --- | --- |
| TTFT | 请求到首个输出 token 可被客户端消费的时间 |
| ITL | 相邻输出 token 的时间间隔分布 |
| TPOT | 常用的每输出 token 时间汇总；必须说明公式和是否排除首 token |
| E2E latency | 请求到完整输出结束 |
| input throughput | prefill input tokens/s |
| output throughput | generated tokens/s，可按请求或系统总量报告 |
| total token throughput | input + output tokens/s；不能替代 output throughput |
| request throughput | requests/s，必须同时给 token 分布 |
| goodput | TTFT/ITL/E2E 和错误率均满足 SLO 的请求或 token 率 |
| KV utilization | KV block 使用、碎片、抢占、换出和 prefix 命中 |

必须保存输入长度、目标/实际输出长度的分布。一个输出 16 token 的实验不能与输出 512 token
的实验直接比较 requests/s。

推荐至少使用四类流量：

1. 短输入短输出的交互请求；
2. 中等输入和真实输出分布；
3. 长 prefill 混合短请求；
4. 生产回放或经过脱敏的等价合成分布。

另测 prefix cache 0%/代表性命中率、请求取消、慢客户端和结构化输出。完全相同 prompt 的 100%
命中只用于测上限，不用于容量结论。

## 7. 准确性与质量

### 7.1 三层验证

1. **数值层**：同输入比较 shape、dtype、NaN/Inf、绝对/相对误差、cosine similarity；
2. **任务层**：分类、检测、WER、NDCG、ROUGE、perplexity、人工/模型评审等项目主指标；
3. **业务层**：关键切片、安全规则、工具调用 schema、拒答、长尾和回归样例。

数值接近不保证 argmax、NMS、beam search 或生成结果一致。生成模型也不应要求随机采样逐 token
完全相同；使用固定 seed 的确定性路径做数值定位，再用任务质量分布验收生产配置。

### 7.2 数据边界

- 调参、校准和量化选择使用训练/验证证据；
- 最终测试集只用于选定配置的正式评估，不反复搜索；
- 公开 benchmark 数据不能替代项目数据；
- 报告不可包含敏感输入、测试样例或秘密标签；
- 记录数据/manifest digest、split 和预处理版本。

## 8. 工具箱

| 层 | 工具 |
| --- | --- |
| PyTorch/CPU | `torch.utils.benchmark`、PyTorch Profiler、Linux `perf`、Intel VTune |
| NVIDIA GPU | PyTorch Profiler、Nsight Systems、Nsight Compute、DCGM、`nvidia-smi dmon` |
| AMD GPU | PyTorch Profiler、rocprofiler/rocprof、ROCm 系统管理工具 |
| TensorRT | `trtexec`、TensorRT profiling/Engine Inspector |
| ONNX Runtime | profiling、`onnxruntime_perf_test`、provider 日志、Olive |
| OpenVINO | `benchmark_app`、性能 hints、profiling info |
| Triton Server | Performance Analyzer、Model Analyzer、GenAI-Perf |
| vLLM/SGLang | 项目自带 benchmark、服务指标，配合统一外部负载器 |
| 服务/集群 | Prometheus/Grafana、OpenTelemetry、Ray Dashboard/State API |

不同工具对 latency 的边界不同。正式报告必须写清命令、版本、参数和测量位置。

## 9. 基准矩阵

不要穷举所有组合。先根据生产分布选择小而有代表性的矩阵：

| 维度 | 建议点位 |
| --- | --- |
| batch | 1、典型在线 batch、离线吞吐 batch |
| shape/长度 | P50、P95、最大允许值、主要 bucket |
| 并发 | 1、SLO 容量点、饱和点、过载点 |
| 精度 | 认可基线 + 1 至 2 个目标低精度 |
| cache | 冷、代表性稳态、热上限 |
| 副本 | 单副本、单节点最优、多节点目标规模 |
| 故障 | 请求取消、错误输入、OOM 边界、进程/节点重启 |

## 10. MLflow 记录

每个候选配置使用独立 MLflow Run，至少记录：

- params/tags：engine、exact versions、precision、batch、并发、shape profile、硬件标识、代码提交；
- metrics：质量、P50/P95/P99、吞吐、goodput、内存、冷启动和错误率；
- artifacts：完整环境、引擎 manifest、原始聚合结果、曲线、覆盖/fallback、质量和故障报告；
- parent/child：一个评估批次可用 parent Run，候选为 child Run，但只由权威 driver 完成父 Run；
- source model：通过 Run ID/model URI 关联，不复制或直接查询 `mlflow.db`。

不要把每个请求作为 MLflow metric 点；保存聚合结果和受控的脱敏样本。大体积 profiler trace 通过
Artifact API 上传，MinIO 路径不作为客户端集成接口。

## 11. 验收门槛模板

```text
质量：主指标 >= ______；关键切片全部通过；NaN/Inf = 0
在线：P99 <= ______ ms；goodput >= ______ req/s；error <= ______
LLM：TTFT P99 <= ______；ITL P99 <= ______；output goodput >= ______ tok/s
资源：峰值显存 <= ______；RSS <= ______；单节点副本 >= ______
启动：ready <= ______ s；首次请求 <= ______ s
稳定：连续 ______ h 无泄漏/OOM；故障恢复 <= ______ s
成本：每 ______ 请求/token <= ______
可恢复：可从 source Run 重建；上一版本回滚演练通过
```

所有空白项在测试前填写。看到结果后再修改门槛会让选型失去约束。

