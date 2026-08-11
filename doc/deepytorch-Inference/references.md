# 官方资料与状态来源

本页优先列官方文档、官方仓库和官方维护公告。链接核验基准日期为 **2026-08-11**。项目更新
很快，实施时仍应重新检查目标版本的 release notes、compatibility matrix 和 security policy。

## 1. PyTorch 原生优化

- [PyTorch Performance Tuning Guide](https://docs.pytorch.org/tutorials/recipes/recipes/tuning_guide.html)
- [`torch.compile` introduction](https://docs.pytorch.org/tutorials/intermediate/torch_compile_tutorial.html)
- [`torch.compiler` API](https://docs.pytorch.org/docs/stable/torch.compiler_api.html)
- [`torch.export` documentation](https://docs.pytorch.org/docs/stable/export.html)
- [AOTInductor documentation](https://docs.pytorch.org/docs/stable/torch.compiler_aot_inductor.html)
- [`torch.inference_mode`](https://docs.pytorch.org/docs/stable/generated/torch.autograd.grad_mode.inference_mode.html)
- [Automatic Mixed Precision](https://docs.pytorch.org/docs/stable/amp.html)
- [`scaled_dot_product_attention`](https://docs.pytorch.org/docs/stable/generated/torch.nn.functional.scaled_dot_product_attention.html)
- [PyTorch ONNX exporter](https://docs.pytorch.org/docs/stable/onnx.html)
- [PyTorch quantization migration notice](https://docs.pytorch.org/docs/stable/quantization.html)
- [torchao documentation](https://docs.pytorch.org/ao/stable/)
- [Torch-TensorRT documentation](https://docs.pytorch.org/TensorRT/)

## 2. 通用运行时与编译器

- [ONNX Runtime performance documentation](https://onnxruntime.ai/docs/performance/)
- [ONNX Runtime Execution Providers](https://onnxruntime.ai/docs/execution-providers/)
- [ONNX Runtime quantization](https://onnxruntime.ai/docs/performance/model-optimizations/quantization.html)
- [OpenVINO documentation](https://docs.openvino.ai/)
- [OpenVINO through `torch.compile`](https://docs.openvino.ai/2026/openvino-workflow/torch-compile.html)
- [Apache TVM documentation](https://tvm.apache.org/docs/)
- [IREE PyTorch guide](https://iree.dev/guides/ml-frameworks/pytorch/)
- [AITemplate official repository](https://github.com/facebookincubator/AITemplate)
- [PyTorch/XLA documentation](https://docs.pytorch.org/xla/master/)
- [BladeDISC official repository](https://github.com/alibaba/BladeDISC)

## 3. LLM 与生成式推理

- [vLLM documentation](https://docs.vllm.ai/en/latest/)
- [SGLang documentation](https://docs.sglang.ai/)
- [TensorRT-LLM documentation](https://nvidia.github.io/TensorRT-LLM/)
- [NVIDIA Dynamo documentation](https://docs.nvidia.com/dynamo/latest/)
- [DeepSpeed Inference API](https://deepspeed.readthedocs.io/en/stable/inference-init.html)
- [LMDeploy documentation](https://lmdeploy.readthedocs.io/en/latest/)
- [ONNX Runtime GenAI](https://onnxruntime.ai/docs/genai/)
- [llama.cpp official repository](https://github.com/ggml-org/llama.cpp)
- [MLC LLM documentation](https://llm.mlc.ai/docs/)
- [llm-compressor official repository](https://github.com/vllm-project/llm-compressor)
- [GPTQModel official repository](https://github.com/ModelCloud/GPTQModel)
- [NVIDIA Model Optimizer documentation](https://nvidia.github.io/Model-Optimizer/)

## 4. 边缘与专用硬件

- [ExecuTorch documentation](https://docs.pytorch.org/executorch/stable/)
- [ExecuTorch backend documentation](https://docs.pytorch.org/executorch/stable/backends-overview.html)
- [ExecuTorch Qualcomm backend](https://docs.pytorch.org/executorch/stable/backends-qualcomm.html)
- [Core ML Tools: convert PyTorch workflow](https://apple.github.io/coremltools/docs-guides/source/convert-pytorch-workflow.html)
- [ONNX Runtime Mobile](https://onnxruntime.ai/docs/tutorials/mobile/)
- [LiteRT Torch（原 AI Edge Torch）](https://github.com/google-ai-edge/litert-torch)
- [AWS Neuron PyTorch documentation](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/frameworks/torch/index.html)
- [AMD ROCm AI ecosystem: vLLM](https://rocm.docs.amd.com/projects/ai-ecosystem/en/latest/inference/vllm.html)
- [AMD ROCm AI ecosystem: SGLang](https://rocm.docs.amd.com/projects/ai-ecosystem/en/latest/inference/sglang.html)
- [Ascend Extension for PyTorch documentation](https://www.hiascend.com/document/detail/zh/Pytorch/700/ptmoddevg/trainingmigrguide/PT_LMTMOG_0002.html)

## 5. 服务、编排与性能工具

- [Ray Serve documentation](https://docs.ray.io/en/latest/serve/)
- [Ray Serve LLM guides](https://docs.ray.io/en/latest/serve/llm/index.html)
- [NVIDIA Triton Inference Server documentation](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/index.html)
- [Triton Performance Analyzer](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/perf_analyzer/docs/README.html)
- [Triton Model Analyzer](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/model_analyzer/docs/README.html)
- [NVIDIA GenAI-Perf](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/perf_analyzer/genai-perf/README.html)
- [KServe documentation](https://kserve.github.io/website/)
- [BentoML documentation](https://docs.bentoml.com/)
- [NVIDIA NIM documentation](https://docs.nvidia.com/nim/)
- [AWS SageMaker model deployment](https://docs.aws.amazon.com/sagemaker/latest/dg/deploy-model.html)
- [Google Vertex AI online predictions](https://cloud.google.com/vertex-ai/docs/predictions/overview)
- [Azure ML managed online endpoints](https://learn.microsoft.com/en-us/azure/machine-learning/concept-endpoints-online)
- [Hugging Face Inference Endpoints](https://huggingface.co/docs/inference-endpoints/)

## 6. 官方维护与迁移公告

以下链接是本资料判断“只做存量迁移、不用于新建”的直接依据：

- [TorchServe README: Limited Maintenance](https://github.com/pytorch/serve#readme)
- [TorchServe official documentation notice](https://pytorch.org/serve/)
- [TGI README: Maintenance Mode](https://github.com/huggingface/text-generation-inference#readme)
- [Intel Extension for PyTorch README: project archived](https://github.com/intel/intel-extension-for-pytorch#readme)
- [FasterTransformer README: development transitioned to TensorRT-LLM](https://github.com/NVIDIA/FasterTransformer#readme)
- [AutoGPTQ README: unmaintained](https://github.com/AutoGPTQ/AutoGPTQ#readme)
- [AutoAWQ README: deprecated](https://github.com/casper-hansen/AutoAWQ#readme)

本次还通过 GitHub 官方仓库元数据核验了 TorchServe、TGI、IPEX、AutoGPTQ 和 AutoAWQ 的
archive 状态。GitHub 页面上的“最近更新”可能来自 issue、metadata 或自动化，不应代替 README
中的维护声明和正式 release。

## 7. 阅读性能材料时的判断规则

引用任何 benchmark 前核对：

1. 模型、revision、输入/输出长度和精度是否相同；
2. 硬件型号、数量、功耗、驱动和软件版本是否相同；
3. 测的是 kernel、模型 forward、server 还是端到端；
4. batch、并发、缓存、speculative 和量化是否一致；
5. 是否同时报告质量、P99、显存和错误率；
6. 是否有可运行命令、数据分布和多轮统计；
7. 结果来自项目维护方、硬件厂商、第三方还是本项目实测。

最终容量和选型结论只使用本项目按 [推理加速选型与验证规范](README.md#5-验证规范) 产生的
可复现结果。外部数字用于确定候选和理解优化方向，不用于直接采购或生产承诺。
