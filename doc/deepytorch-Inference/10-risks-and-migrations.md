# 风险、维护状态与迁移

## 1. 已确认的维护状态

| 项目/路径 | 2026-08-11 官方状态证据 | 新项目处理 | 存量迁移方向 |
| --- | --- | --- | --- |
| TorchServe | 官方文档/README 标注 Limited Maintenance，无计划更新、修复、新功能或安全补丁；仓库已归档 | 禁止作为默认新选型 | Ray Serve、Triton、KServe/BentoML 或引擎自带 server |
| Hugging Face TGI | README 标注 maintenance mode，仓库元数据已归档，并推荐 vLLM/SGLang/local engines | 不作为默认候选 | vLLM、SGLang；本地场景 llama.cpp/MLX 等 |
| Intel Extension for PyTorch | 官方仓库归档，声明不保证维护/修复/更新并提示已知安全问题 | 不新增依赖 | 原生 PyTorch/Inductor、OpenVINO |
| FasterTransformer | 官方 README 声明开发已转向 TensorRT-LLM且不再继续开发 | 不新建 | TensorRT-LLM，或 vLLM/SGLang |
| AutoGPTQ | 官方 README 声明 unmaintained | 不新建 | GPTQModel 或目标引擎官方量化工具 |
| AutoAWQ | 官方 README 声明 deprecated、不再维护 | 不新建 | vLLM/llm-compressor 或目标引擎官方工具 |
| TorchScript 新部署 | PyTorch 主线已转向 compile/export；JIT 入口在新文档中不再是推荐部署主线 | 不作为新架构基础 | `torch.export`、AOTInductor、ONNX 新导出器 |
| 旧 ONNX exporter | PyTorch 推荐/default 已转向 `dynamo=True` 的 export-based exporter | 不新增旧 symbolic 依赖 | 新导出器、`dynamic_shapes`、custom translation |

存量系统不是发现弃用后立即停机。应先盘点安全暴露、模型和接口依赖，建立并行候选与契约测试，
再灰度迁移。

## 2. 风险分类

### 2.1 数值和质量

- 混合精度、TF32、FP8、INT8/INT4 改变输出；
- 图优化可能重排浮点运算或融合近似实现；
- tokenizer/chat template/后处理变化常被误判为引擎差异；
- NMS、top-k、采样等不连续操作会放大小数值差异；
- 量化校准或选择反复使用测试集会造成泄漏。

控制：golden + 任务指标 + 关键切片三层验收，固定完整前后处理和随机配置。

### 2.2 覆盖与 fallback

- 不支持节点回到 CPU/eager，产生设备复制；
- 一个示例 shape 成功，生产其他 shape 重编译或越过 profile；
- 自定义 op/plugin 在升级后 ABI 不兼容；
- 不同硬件 backend 对同一量化 checkpoint 布局不兼容。

控制：保存覆盖/分区日志，生产 shape 矩阵测试，对 fallback 设阈值而不是静默接受。

### 2.3 制品可移植性

- TensorRT、AOT、XLA、Neuron、NPU engine 与目标硬件和软件栈绑定；
- timing/compile cache 可能含版本或架构假设；
- pickle/Python 对象既不稳定又可能执行代码；
- 模型权重与 runtime/plugin 分开升级导致不可加载。

控制：不可变 manifest、digest、目标矩阵和可重建流水线；不把 engine 当源模型。

### 2.4 冷启动与重编译

- 首请求编译导致超时；
- autoscale 新副本逐个下载大模型和编译；
- 动态 shape 导致持续 cache miss；
- 编译 cache 放临时磁盘，重启丢失。

控制：有界 bucket、离线构建、预热到 ready、持久但按环境隔离的缓存、最小热容量。

### 2.5 服务稳定性

- 无背压队列导致内存耗尽；
- 双层 batch/queue 增加 P99；
- 慢流式客户端占用请求和 KV；
- 取消请求没有释放工作或 KV；
- 多副本复制权重超出显存；
- autoscaler 与集群 autoscaler/Kubernetes 相互振荡。

控制：并发/队列/token/输入上限、取消测试、内存水位、单一明确的各层伸缩职责。

### 2.6 安全与供应链

- 未维护 server/runtime 不再修复漏洞；
- pickle、自定义 op、Python backend 和 plugin 可执行任意代码；
- 模型管理 API 可被滥用加载恶意制品；
- prompt/预测、测试样本或 secret 泄漏到日志；
- 直接暴露 Ray Dashboard、MLflow、MinIO 或 Triton 管理端点。

控制：来源 allowlist、签名/digest、依赖扫描、最小权限、管理面隔离、认证 TLS 代理和日志脱敏。

## 3. TorchServe 迁移步骤

1. 盘点 `.mar`、handler、workflow、management API、batch 和指标依赖；
2. 把预处理、模型调用、后处理抽为普通可测试适配器；
3. 选择 Ray Serve（本仓库优先）或 Triton/KServe/BentoML；
4. 为现有 HTTP/gRPC 契约写请求、响应、错误和流式测试；
5. 在新服务复用同一源模型和 golden，比较数值与 SLO；
6. 双写/影子、canary、回滚后切流量；
7. 关闭 TorchServe 管理面和旧凭据，保留审计记录。

迁移期间 TorchServe 已无计划安全补丁，应限制在受控网络并减少暴露窗口。

## 4. TGI/FasterTransformer 迁移

### 4.1 TGI

- 导出当前启动参数、模型 revision、量化格式、sharding、路由和 API 行为；
- 在 vLLM/SGLang 对应版本核对模型、量化、speculative、LoRA 和 structured output；
- 对 OpenAI/HF 风格接口做契约测试，不假设完全兼容；
- 用同一 token 分布比较 TTFT/ITL/goodput；
- 逐流量切换并保留旧服务只读回滚窗口。

### 4.2 FasterTransformer

- 盘点自定义 kernel、checkpoint 转换、NCCL 并行和服务包装；
- 优先映射到 TensorRT-LLM 的模型与并行配置；
- 若定制成本过高，同时比较 vLLM/SGLang；
- engine/权重重新构建和质量验证，不复用未经证明兼容的旧二进制。

## 5. IPEX 迁移

1. 搜索 `intel_extension_for_pytorch` import、`ipex.optimize`、量化和 launcher 使用；
2. 建立相同线程、NUMA、batch 和 BF16/INT8 的 PyTorch CPU 基线；
3. 比较当前原生 `torch.compile`/Inductor 与 OpenVINO；
4. 量化迁移到 torchao PT2E 或 OpenVINO/NNCF 支持路径；
5. 在目标 Intel 代际做正确性和性能回归；
6. 删除 IPEX wheel/容器层前保留可回滚镜像和报告。

## 6. 量化工具迁移

从 AutoGPTQ/AutoAWQ 迁移时先识别 checkpoint 的真实 schema，而不是只改 Python import：

- quant method、bits、group size、zero point、desc_act、scale/packing；
- tokenizer、config 和权重 shard；
- 当前引擎使用的 kernel 和 loader；
- 新工具是否能直接读旧格式，还是需要从高精度源模型重做量化；
- 原量化模型的任务质量基线。

从高精度源权重重新量化通常更可审计。旧量化权重可用于一致性参考，不应成为唯一恢复源。

## 7. 升级策略

快速演进的编译器/LLM 栈按“兼容集合”升级，不单独随意升级某个 wheel：

```text
OS/driver/firmware
  + CUDA/ROCm/vendor runtime
  + PyTorch
  + compiler/engine
  + kernel libraries/collectives
  + model code/tokenizer
  + serving layer
```

每次升级：

1. 阅读 release/security/compatibility notes；
2. 新建环境和 MLflow Run，不覆盖生产镜像/engine；
3. 重建硬件相关制品；
4. 跑 golden、质量、shape、压力、故障和长稳测试；
5. 比较 graph/fallback/kernel 变化，不只比较最终吞吐；
6. canary 并保留上一完整兼容集合；
7. 升级成功后才废弃旧制品，按保留策略回收。

## 8. 生产故障处置

| 现象 | 首查 | 临时缓解 | 根因方向 |
| --- | --- | --- | --- |
| 延迟突然升高 | 排队、batch、输入长度、GPU/CPU、重编译 | 限流、隔离长请求、扩热副本 | 流量、cache、shape、资源争用 |
| OOM | 权重/KV/workspace、并发、副本、碎片 | 降并发/长度/batch，回滚 | 容量模型、泄漏、配置变化 |
| 质量回退 | 模型/digest、精度、预处理、tokenizer | 切回上一版本 | 制品错配、量化、fallback/算子变化 |
| 节点特有错误 | 硬件、驱动、固件、engine arch | 摘除节点 | 节点漂移、不可移植制品 |
| 首请求超时 | 下载、编译、预热、cache | 保持热副本、延长启动而非请求超时 | 构建未前移、缓存丢失 |
| 流式请求不结束 | 客户端断开、取消、生成上限 | 强制 token/时间上限 | 取消传播、后端泄漏 |

故障处理不直接修改 Registry production alias 或覆盖 artifact。回滚使用已审核的完整版本，事后
把日志/trace/环境摘要上传到独立诊断 Run，并保护敏感输入。

## 9. 定期复审

每季度或每次重大 PyTorch/驱动升级复审：

- 候选项目是否归档、变更许可证或停止安全支持；
- 模型/硬件支持矩阵和已知 CVE；
- 生产 shape、token、流量和成本是否偏离原基准；
- fallback、重编译、cache 和错误趋势；
- 是否仍能从源 Run 在干净节点重建；
- 回滚制品能否加载，恢复演练是否通过；
- 新候选是否值得用同一协议做小规模重新比较。

