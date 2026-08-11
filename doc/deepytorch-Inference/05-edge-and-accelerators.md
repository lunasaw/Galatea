# 边缘部署与专用加速器

边缘推理的首要约束通常不是峰值算力，而是包体、内存、功耗、热限制、启动时间、离线能力、
操作系统版本和芯片碎片化。云端编译出的制品也不应假设可直接复制到移动 SoC。

## 1. 边缘运行时候选

| 方案 | 主要目标 | 优势 | 约束 |
| --- | --- | --- | --- |
| ExecuTorch | PyTorch 移动、嵌入式、边缘 | `torch.export` 主线、轻量 runtime、delegate 体系 | 算子和 delegate 逐设备验证 |
| Core ML/coremltools | Apple iOS/macOS/watchOS 等 | 深度集成 Apple 硬件与系统框架 | Apple 平台绑定、转换语义需验证 |
| ONNX Runtime Mobile | Android/iOS/嵌入式 | 多语言、多 EP、可裁剪 runtime | ONNX 导出和 EP 覆盖差异 |
| LiteRT/AI Edge Torch | Android/Google 边缘生态 | 转换 PyTorch 并利用移动加速后端 | 模型覆盖和 Google 工具链绑定 |
| MLC/IREE/TVM | 跨端 AOT 编译 | WebGPU、Vulkan、原生和多设备潜力 | 编译和运行时集成成本 |
| llama.cpp | 本地 LLM | GGUF、CPU/GPU offload、量化生态 | 不是通用 PyTorch runtime |
| ncnn/MNN/TNN | 移动端通用推理 | 轻量、ARM/移动生态成熟 | PyTorch 转换链和模型覆盖各异 |
| 芯片厂商 SDK | NPU/DSP/ASIC | 最接近硬件能力 | 强版本绑定、闭源工具、迁移成本 |

## 2. ExecuTorch

ExecuTorch 是 PyTorch 官方的端侧推理路径。典型流程是：

```text
PyTorch nn.Module
  -> torch.export.ExportedProgram
  -> edge dialect / transforms
  -> backend delegate partition and lowering
  -> ExecuTorch program
  -> 设备侧轻量 runtime
```

delegate 把支持的子图交给 XNNPACK、Core ML、MPS、Vulkan、Qualcomm、MediaTek 或其他已支持
后端，具体列表和成熟度以当前 ExecuTorch backend 文档为准。未下沉部分由 portable kernel
执行。

需要验证：

- delegate 覆盖率和分区边界，避免 CPU/NPU 间频繁拷贝；
- 静态/动态 shape 约束、内存规划和峰值工作区；
- FP16/INT8/INT4 格式是否由目标 delegate 真正执行；
- 模型包、runtime、delegate 库的总包体；
- 设备温升、持续性能和电池消耗；
- 真实系统版本和低端设备，不只测试开发板。

## 3. Apple 平台

### 3.1 PyTorch MPS

MPS 适合 macOS 开发、验证或桌面端直接运行 PyTorch，保持模型兼容性。它不等同于面向 Apple
应用分发的 Core ML 路径。应检查算子 fallback、统一内存压力和 macOS/PyTorch 版本。

### 3.2 Core ML

`coremltools` 提供 PyTorch 转 Core ML 工作流，并可利用 CPU、GPU 和 Apple Neural Engine。
适合 Apple-only 产品和深度系统集成。需要固定 deployment target、compute units、输入 shape、
精度和转换器版本，并在每类真实设备上验证。

## 4. Android 与移动 SoC

优先从产品约束反推路径：

- 需要统一 PyTorch 开发和多 delegate：ExecuTorch；
- 已有 ONNX 资产或需要多语言：ORT Mobile；
- 深度使用 Android/Google AI Edge：LiteRT/AI Edge Torch；
- Qualcomm 等特定 SoC：比较 ExecuTorch Qualcomm backend、ORT QNN EP 与厂商 QNN SDK；
- 极小包体或已有 C++ 移动栈：比较 ncnn/MNN/TNN/IREE。

同名 NPU 在不同手机、驱动和系统版本上的算子覆盖可能不同。生产应用应有 capability probe、
CPU fallback、制品兼容列表和远程禁用问题 backend 的能力。

## 5. Web 推理

浏览器端常见候选为 ONNX Runtime Web（WebGPU/WebNN/WASM）、Transformers.js 或 MLC LLM。
重点不是服务器 QPS，而是：

- 首次下载和缓存大小；
- 浏览器、GPU 驱动和 WebGPU feature 差异；
- 页面内存上限、tab 后台策略和 shader 编译时间；
- 模型权重来源、缓存完整性和内容安全策略；
- 用户设备数据不上传时带来的隐私收益与本地攻击面。

## 6. 数据中心和云端专用硬件

| 平台 | PyTorch 接入路径 | 关键约束 |
| --- | --- | --- |
| Google TPU | PyTorch/XLA、OpenXLA | shape/编译、XLA 语义、TPU 拓扑和软件版本 |
| AWS Inferentia/Trainium | PyTorch Neuron/`torch-neuronx`、Neuron compiler/runtime | 实例代际、Neuron SDK、编译缓存和算子支持 |
| Intel GPU/NPU | PyTorch XPU、OpenVINO | 驱动、oneAPI/OpenVINO/PyTorch 组合 |
| AMD Instinct | PyTorch ROCm、Inductor、vLLM/SGLang ROCm 路径 | GPU 型号、ROCm/PyTorch/内核支持矩阵 |
| NVIDIA GPU | PyTorch CUDA、TensorRT、TensorRT-LLM | compute capability、驱动/CUDA/TensorRT 组合 |

云实例上的编译缓存和 engine 也要按实例代际隔离。弹性扩容时，如果每个新节点都重新编译，
冷启动可能抵消容量收益；应在匹配的构建环境提前生成、签名和验证制品。

## 7. 国产与其他专用加速器

| 生态 | 常见 PyTorch 路径 | 评估要点 |
| --- | --- | --- |
| 华为昇腾 | `torch_npu`/Ascend Extension for PyTorch、CANN | PyTorch 适配版本、算子支持、ATC/图模式、HCCL、模型迁移工具 |
| 寒武纪 | `torch_mlu`/Neuware 生态 | 驱动固件、算子和低精度、编译/运行时版本、集群通信 |
| 百度昆仑等 | 厂商 PyTorch 适配或模型转换工具 | 官方支持模型、长期维护、转换与回退边界 |
| Rockchip/Qualcomm/MediaTek | RKNN/QNN/NeuroPilot 等 SDK 或上层 delegate | SoC/系统碎片化、闭源转换器、授权和端侧回滚 |

厂商声称“兼容 PyTorch”可能分别表示 Python API 适配、FX/ONNX 转换或只支持部分模型库。
PoC 必须使用目标模型，不用厂商自带 ResNet 样例代替。至少要求提供：

- 支持的 PyTorch/ONNX opset 与动态 shape 范围；
- 自定义算子接口和 fallback 行为；
- 编译器、驱动、固件的兼容与生命周期；
- profiler、错误定位和性能分析工具；
- 多卡通信、容器、Kubernetes/Ray 集成；
- 安全公告、升级窗口、商业支持和退出/权重迁移方案。

## 8. 边缘制品清单

每个端侧制品至少记录：

```text
source_model_uri / source_run_id / source_model_digest
exporter_name / exporter_version / export_options
runtime_name / runtime_version / delegate_version
target_os / min_os_version / target_soc / accelerator
input_schema / shape_constraints / preprocessing_digest
precision / quantization_config / calibration_digest
model_binary_digest / runtime_library_digest
quality_report / device_benchmark_report / compatibility_matrix
```

模型、runtime 和 delegate 应作为一组发布并签名校验。保留上一版本和 CPU/portable fallback，
避免一个驱动或 OS 升级使应用完全不可用。

## 9. 端侧基准注意事项

- 在断电冷启动、缓存热启动和连续运行三种状态分别测；
- 记录设备型号、OS、剩余电量、温度、功耗模式和后台负载；
- 报告初始性能和热稳定后的持续性能；
- 峰值内存包含 runtime、delegate、输入输出和临时 buffer；
- 图像/音频采集、预处理和 UI 线程纳入端到端延迟；
- 使用多档设备，不只使用最新旗舰机；
- 离线模型更新应支持完整性校验、断点、失败回滚和版本吊销。

