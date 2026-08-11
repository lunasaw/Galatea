# 实施手册

本章给出从 PyTorch 基线到生产候选的最短落地路径。示例只展示稳定概念，包版本、backend
options、动态 shape 和模型参数必须在项目环境内固定。正式代码属于对应
`train-model/<project-name>/`，不要把工作负载依赖加入仓库根 `requirements.txt`。

## 1. 阶段 0：建立推理契约

在改模型前创建项目配置，至少包含：

```yaml
model:
  source_run_id: <mlflow-run-id>
  source_model_uri: runs:/<mlflow-run-id>/model
  task: <task-name>
  primary_quality_metric: <metric>
  metric_direction: <max-or-min>
input:
  schema: <schema-reference>
  shape_buckets: []
  max_payload_bytes: <limit>
inference:
  precision: fp32
  batch_sizes: [1]
  engine: eager
  seed: 2026
validation:
  dataset_manifest_digest: <sha256>
  preprocessing_digest: <sha256>
  quality_gate: <value>
resources:
  cpus: 1
  gpus: 0
  memory_bytes: <bytes>
```

配置中不放 token、MinIO 密钥或私有 endpoint。Tracking URI 和 experiment 使用环境变量或受控
部署配置注入。

## 2. 阶段 1：PyTorch 正确性基线

```python
from __future__ import annotations

import torch


def prepare_model(model: torch.nn.Module, device: torch.device) -> torch.nn.Module:
    return model.eval().to(device)


def predict(
    model: torch.nn.Module,
    inputs: torch.Tensor,
    device_type: str,
    amp_dtype: torch.dtype | None = None,
) -> torch.Tensor:
    with torch.inference_mode():
        if amp_dtype is None:
            return model(inputs)
        with torch.autocast(device_type=device_type, dtype=amp_dtype):
            return model(inputs)
```

先在 FP32 或项目认可的高精度下生成 golden 输出和任务指标。输出应包含模型输出 schema、预处理
版本和输入 digest，而不是把敏感原始样本写入日志。

## 3. 阶段 2：可靠的模型级计时

```python
from collections.abc import Callable
from time import perf_counter

import torch


def synchronize(device: torch.device) -> None:
    if device.type == "cuda":
        torch.cuda.synchronize(device)


def benchmark_call(
    fn: Callable[[], object],
    device: torch.device,
    warmup: int = 20,
    iterations: int = 100,
) -> list[float]:
    for _ in range(warmup):
        fn()
    synchronize(device)

    latencies_ms = []
    for _ in range(iterations):
        start = perf_counter()
        fn()
        synchronize(device)
        latencies_ms.append((perf_counter() - start) * 1_000)
    return latencies_ms
```

该函数只适合进程内模型基准，不测在线排队和网络。正式报告应使用 percentile 和多轮重复，
并把原始聚合数据保存为 artifact。

## 4. 阶段 3：`torch.compile`

```python
compiled_model = torch.compile(model)

# 首次调用可能触发编译，不能混入稳态样本。
with torch.inference_mode():
    compiled_model(example_inputs)
```

实施步骤：

1. 固定一个 shape 和默认模式跑通正确性；
2. 查看 graph break、guard 和 compile 日志；
3. 依次测试生产 shape bucket，确认没有无界重编译；
4. 比较默认、`reduce-overhead` 或 `max-autotune` 中与场景相关的模式；
5. 保存编译时间、首请求、稳态、cache 路径策略和失败回退；
6. 在服务副本启动阶段预热所有 bucket，ready 后才接流量。

不要直接依赖 `torch._` 私有 API 做长期平台接口。诊断时可使用私有工具，但生产适配层应隔离，
升级时有回归测试。

## 5. 阶段 4A：ONNX Runtime

### 5.1 导出

当前 PyTorch 推荐基于 `torch.export` 的 ONNX 导出器：

```python
from pathlib import Path

import torch


output_path = Path("model.onnx")
onnx_program = torch.onnx.export(
    model,
    (example_inputs,),
    dynamo=True,
    verify=True,
    report=True,
)
onnx_program.save(output_path)
```

固定 shape 跑通后，再按模型 forward 参数名设置 `dynamic_shapes` 和有界 `torch.export.Dim`。
动态配置属于接口契约，不能只根据一个示例输入猜测。大模型权重注意 ONNX external data。

### 5.2 加载和检查节点分配

```python
from pathlib import Path

import onnxruntime as ort


model_path = Path("model.onnx")
providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
session = ort.InferenceSession(model_path.as_posix(), providers=providers)
print(session.get_providers())
```

实际生产不要只看 `get_providers()`；启用适当日志/profiling，确认节点分配和 CPU fallback。
CUDA 场景比较 I/O Binding 或 device tensor，避免每次请求在 NumPy/CPU 与 GPU 间复制。

## 6. 阶段 4B：Torch-TensorRT/TensorRT

Torch-TensorRT 的最小 compile backend 形式：

```python
import torch
import torch_tensorrt


trt_model = torch.compile(model, backend="tensorrt")

with torch.inference_mode():
    trt_model(example_inputs)
```

正式实现还要显式处理 enabled precision、输入 min/opt/max shape、workspace、engine cache、
unsupported op 和序列化。构建报告至少保存：

- Torch-TensorRT/TensorRT/CUDA/driver 和 GPU compute capability；
- 每个 input profile；
- precision 与校准/量化 digest；
- 分区、fallback、plugin 和 layer precision；
- engine/timing cache digest；
- 目标硬件的正确性和 `trtexec` + 端到端报告。

若需要 C++ 或离线构建，使用官方 `torch.export`/Torch-TensorRT AOT 路径或 ONNX->TensorRT，
不要 pickle 一个进程内 compiled object 作为跨版本部署格式。

## 7. 阶段 4C：OpenVINO

OpenVINO 提供 `torch.compile` backend 和显式转换/runtime 两条路径。前者适合低迁移 PoC：

```python
import openvino.torch
import torch


compiled_model = torch.compile(model, backend="openvino")
```

需要 C++、模型缓存、异步请求或更细设备配置时，使用 OpenVINO 转换与 `Core.compile_model`
路径。比较 `LATENCY`/`THROUGHPUT` 等 performance hint 时保持服务 SLO 一致，并用
`benchmark_app` 与真实应用各测一次。

## 8. 阶段 4D：LLM 服务 PoC

### 8.1 vLLM

```bash
vllm serve '<model-id-or-local-path>' \
  --host 127.0.0.1 \
  --port 8000 \
  --dtype auto
```

### 8.2 SGLang

```bash
python -m sglang.launch_server \
  --model-path '<model-id-or-local-path>' \
  --host 127.0.0.1 \
  --port 8000
```

这些命令只用于受控节点的起点。生产部署必须固定 model revision、本地 artifact digest、引擎和
kernel 版本，设置最大上下文/并发/token，关闭不必要管理接口，并通过认证 TLS 代理暴露。
模型仓库 token 不放在命令行。

先直接测引擎自带 server，确认底层结果，再接 Ray Serve/Triton/Dynamo；否则难以区分引擎和
编排开销。TensorRT-LLM 使用其当前版本官方 build/serve 流程，不在平台文档复制易过期的完整
命令链。

## 9. 阶段 5：参数化 Ray Job 构建

正式导出/构建使用脚本，不在 Notebook 内生成生产制品。提交时显式声明资源：

```bash
ray job submit \
  --address="${RAY_DASHBOARD_URL}" \
  --working-dir . \
  -- \
  python train-model/<project>/scripts/build_inference_artifact.py \
    --config train-model/<project>/configs/inference/<candidate>.yaml
```

项目脚本应：

1. 从显式 `MLFLOW_TRACKING_URI` 通过 Artifact API 下载源模型；
2. 验证 Run ID、digest、signature、任务和数据兼容；
3. 用唯一 build ID 创建新 MLflow Run；
4. 构建到 job 临时目录，不覆盖源模型或他人 Run；
5. 运行小规模正确性和加载恢复测试；
6. 上传制品、manifest、日志摘要和报告；
7. 失败 Run 保留诊断，不发布部分制品；
8. 不自动改变 production alias。

多 worker 构建时只由 authoritative worker 创建/结束父 Run 和发布共享制品。

## 10. 阶段 6：服务适配器

定义稳定的项目内协议，而不是让业务代码依赖引擎对象：

```python
from typing import Any, Protocol


class InferenceEngine(Protocol):
    def warmup(self) -> None: ...

    def predict(self, batch: Any) -> Any: ...

    def metadata(self) -> dict[str, str]: ...

    def close(self) -> None: ...
```

适配器负责 dtype/device、I/O 名称、shape bucket、输出规范和错误转换。预处理/后处理与训练版本
共享可测试代码，不在 Ray Serve deployment 里复制 Notebook 逻辑。

服务启动顺序：

```text
下载不可变 artifact -> digest/signature 验证 -> 加载 engine -> 预热全部 bucket
-> golden smoke -> ready -> 接入流量
```

## 11. 阶段 7：发布

1. 离线回归通过质量和性能门槛；
2. 在目标节点完成重启/恢复测试；
3. 部署独立版本，不覆盖现有服务；
4. 影子流量比较输出、资源和尾延迟；
5. 小比例 canary，监测错误、P99、质量代理指标和 OOM；
6. 扩容前进行容量和过载测试；
7. 审核后更新流量或 Registry alias；
8. 保留上一版本制品、配置和一键回滚；
9. 发布后记录实际版本、部署 ID、Ray Job ID 和 MLflow Run ID。

## 12. 快速排错

```text
结果不一致？
  -> eval/inference_mode -> 预处理/tokenizer -> dtype -> 导出图 -> fallback -> 量化/随机性

没有加速？
  -> 基准同步 -> 输入管线 -> graph break/重编译 -> batch/shape -> kernel 命中 -> H2D/D2H

P99 很高？
  -> 排队 -> batch wait -> 长请求 -> GC/allocator -> cache eviction -> autoscale -> 慢客户端

OOM？
  -> 权重 + workspace + activation/KV + graph capture + 并发 + 多副本 + 碎片/缓存

只在一台机器成功？
  -> 驱动/固件 -> GPU arch/ISA -> engine 可移植性 -> plugin ABI -> 环境未固定
```

