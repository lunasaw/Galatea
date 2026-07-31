# Ray LLM

## Overview

Ray LLM provides integration with large language model (LLM) serving frameworks, enabling distributed inference, batch processing, and production deployment of LLMs on Ray clusters.

## Architecture

```
┌──────────────────────────────────────────┐
│              Ray LLM Layer               │
├──────────────┬───────────────────────────┤
│  vLLM Serve  │   Batch Processing       │
│  Integration │   Pipeline                │
├──────────────┴───────────────────────────┤
│           Ray Serve + Ray Data           │
├──────────────────────────────────────────┤
│            Ray Cluster (GPU)             │
└──────────────────────────────────────────┘
```

## vLLM Integration

### Single GPU Deployment
```python
from ray import serve

@serve.deployment(
    ray_actor_options={"num_gpus": 1},
    autoscaling_config=serve.config.AutoscalingConfig(
        min_replicas=1,
        max_replicas=4,
        target_num_ongoing_requests_per_replica=5,
    ),
)
class vLLMDeployment:
    def __init__(self, model_name: str):
        from vllm import LLM, SamplingParams
        self.llm = LLM(model=model_name)
        self.sampling_params = SamplingParams(
            max_tokens=256,
            temperature=0.7,
        )

    async def __call__(self, request):
        data = await request.json()
        prompts = data.get("prompts", [data["prompt"]])
        outputs = self.llm.generate(prompts, self.sampling_params)
        return [output.outputs[0].text for output in outputs]

app = vLLMDeployment.bind("meta-llama/Meta-Llama-3-8B-Instruct")
serve.run(app)
```

### Multi-GPU (Tensor Parallel)
```python
@serve.deployment(
    ray_actor_options={"num_gpus": 4},
    max_replicas=2,
)
class vLLMMultiGPU:
    def __init__(self, model_name: str):
        from vllm import LLM, SamplingParams
        self.llm = LLM(
            model=model_name,
            tensor_parallel_size=4,
            gpu_memory_utilization=0.9,
        )
        self.sampling_params = SamplingParams(
            max_tokens=512,
            temperature=0.7,
        )

    async def __call__(self, request):
        data = await request.json()
        outputs = self.llm.generate(
            [data["prompt"]],
            self.sampling_params,
        )
        return outputs[0].outputs[0].text

app = vLLMMultiGPU.bind("meta-llama/Meta-Llama-3-70B-Instruct")
serve.run(app, httproxy_options={"http_options": {"host": "0.0.0.0", "port": 8000}})
```

### Streaming Response
```python
from ray import serve
from vllm import AsyncLLMEngine, SamplingParams, AsyncEngineArgs

@serve.deployment(ray_actor_options={"num_gpus": 1})
class StreamingLLM:
    def __init__(self, model_name: str):
        engine_args = AsyncEngineArgs(
            model=model_name,
            gpu_memory_utilization=0.9,
            max_model_len=4096,
        )
        self.engine = AsyncLLMEngine.from_engine_args(engine_args)

    async def __call__(self, request):
        from starlette.responses import StreamingResponse
        data = await request.json()
        prompt = data["prompt"]

        sampling_params = SamplingParams(
            max_tokens=data.get("max_tokens", 256),
            temperature=data.get("temperature", 0.7),
            top_p=data.get("top_p", 1.0),
        )

        async def generate():
            async for output in self.engine.generate(prompt, sampling_params, request_id=""):
                yield output.outputs[0].text

        return StreamingResponse(generate(), media_type="text/plain")

app = StreamingLLM.bind("meta-llama/Meta-Llama-3-8B-Instruct")
serve.run(app)
```

## Batch Processing Pipeline

### Batch Inference with Ray Data
```python
import ray
from ray.data import from_pandas
import pandas as pd

# Create dataset of prompts
prompts_df = pd.DataFrame({
    "prompt": [
        "Translate to French: Hello",
        "Summarize: Long text...",
        "Generate: A poem about...",
    ] * 1000,
})
ds = ray.data.from_pandas(prompts_df)

# Define batch inference function
def infer_batch(batch):
    from vllm import LLM, SamplingParams
    # Use singleton pattern for model
    if not hasattr(infer_batch, "llm"):
        infer_batch.llm = LLM(model="meta-llama/Meta-Llama-3-8B-Instruct")
        infer_batch.params = SamplingParams(max_tokens=256)

    outputs = infer_batch.llm.generate(batch["prompt"].tolist(), infer_batch.params)
    return {"response": [o.outputs[0].text for o in outputs]}

# Run batch inference
results = ds.map_batches(
    infer_batch,
    batch_size=32,
    num_gpus=1,
    concurrency=4,
)

# Save results
results.write_parquet("s3://bucket/llm-outputs/")
```

### Large-Scale Batch Pipeline
```python
import ray

# Read input data
ds = ray.data.read_json("s3://bucket/prompts/")

# Preprocess
def preprocess(batch):
    return {"prompt": [format_prompt(p) for p in batch["text"]]}

ds = ds.map_batches(preprocess)

# Batch inference
@ray.remote(num_gpus=1)
class BatchInferencer:
    def __init__(self, model_name):
        from vllm import LLM, SamplingParams
        self.llm = LLM(model=model_name, tensor_parallel_size=1)
        self.params = SamplingParams(max_tokens=512)

    def infer(self, prompts):
        outputs = self.llm.generate(prompts, self.params)
        return [o.outputs[0].text for o in outputs]

# Map with actors for model reuse
results = ds.map_batches(
    lambda: BatchInferencer.remote("meta-llama/Meta-Llama-3-8B-Instruct"),
    batch_size=64,
    num_gpus=1,
    concurrency=8,
)

# Post-process and save
def postprocess(batch):
    return {"output": [clean_response(r) for r in batch["response"]]}

results = results.map_batches(postprocess)
results.write_parquet("s3://bucket/results/")
```

## LLM Serving with Ray Serve

### Production Deployment Pattern
```python
from ray import serve
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class LLMRequest(BaseModel):
    prompt: str
    max_tokens: int = 256
    temperature: float = 0.7
    top_p: float = 1.0
    model: str = "default"

@serve.deployment(
    ray_actor_options={"num_gpus": 1},
    autoscaling_config=serve.config.AutoscalingConfig(
        min_replicas=1,
        max_replicas=8,
        target_num_ongoing_requests_per_replica=4,
    ),
)
@serve.ingress(app)
class LLMServe:
    def __init__(self):
        from vllm import LLM, SamplingParams
        self.llm = LLM(
            model="meta-llama/Meta-Llama-3-8B-Instruct",
            gpu_memory_utilization=0.9,
            max_model_len=8192,
        )

    @app.post("/generate")
    async def generate(self, request: LLMRequest):
        from vllm import SamplingParams
        params = SamplingParams(
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            top_p=request.top_p,
        )
        outputs = self.llm.generate([request.prompt], params)
        return {"response": outputs[0].outputs[0].text}

    @app.post("/batch_generate")
    async def batch_generate(self, prompts: list[str]):
        from vllm import SamplingParams
        params = SamplingParams(max_tokens=256)
        outputs = self.llm.generate(prompts, params)
        return {"responses": [o.outputs[0].text for o in outputs]}

    @app.get("/health")
    async def health(self):
        return {"status": "healthy"}

serve_app = LLMServe.bind()
serve.run(serve_app, host="0.0.0.0", port=8000)
```

### Model Multiplexing
```python
from ray import serve

@serve.deployment(
    ray_actor_options={"num_gpus": 1},
    max_replicas=4,
)
class MultiModelLLM:
    def __init__(self):
        self.models = {}
        self.current_model = None

    @serve.multiplexed(max_num_models_per_replica=2)
    async def get_model(self, model_name):
        if model_name not in self.models:
            from vllm import LLM
            self.models[model_name] = LLM(model=model_name)
        return self.models[model_name]

    async def __call__(self, request):
        data = await request.json()
        model_name = data.get("model", "default-model")
        model = self.get_model(model_name)
        outputs = model.generate([data["prompt"]], self.params)
        return outputs[0].outputs[0].text
```

## LoRA Serving

### Multi-LoRA Deployment
```python
@serve.deployment(ray_actor_options={"num_gpus": 1})
class LoRAServing:
    def __init__(self, base_model: str):
        from vllm import LLM
        self.llm = LLM(
            model=base_model,
            enable_lora=True,
            max_loras=4,
            max_cpu_loras=8,
        )
        self.lora_cache = {}

    async def __call__(self, request):
        data = await request.json()
        lora_path = data.get("lora_path")

        from vllm import SamplingParams
        params = SamplingParams(
            max_tokens=data.get("max_tokens", 256),
            temperature=data.get("temperature", 0.7),
        )

        outputs = self.llm.generate(
            [data["prompt"]],
            params,
            lora_request=LoRARequest("lora", 1, lora_path) if lora_path else None,
        )
        return outputs[0].outputs[0].text
```

## Embedding Models

### Embedding Service
```python
@serve.deployment(
    ray_actor_options={"num_gpus": 1},
    autoscaling_config=AutoscalingConfig(
        min_replicas=1,
        max_replicas=4,
        target_num_ongoing_requests_per_replica=10,
    ),
)
class EmbeddingService:
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(model_name)

    async def __call__(self, request):
        data = await request.json()
        texts = data.get("texts", [data["text"]])
        embeddings = self.model.encode(texts)
        return {"embeddings": embeddings.tolist()}

app = EmbeddingService.bind("BAAI/bge-large-en-v1.5")
serve.run(app)
```

### Batch Embeddings with Ray Data
```python
ds = ray.data.read_text("s3://bucket/documents/")

@ray.remote(num_gpus=1)
class EmbeddingWorker:
    def __init__(self):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer("BAAI/bge-large-en-v1.5")

    def embed(self, texts):
        return self.model.encode(texts).tolist()

results = ds.map_batches(
    lambda batch: {"embeddings": EmbeddingWorker.remote().embed(batch["text"])},
    batch_size=128,
    num_gpus=1,
)
```

## RAG (Retrieval-Augmented Generation)

### RAG Pipeline
```python
from ray import serve

@serve.deployment
class VectorStore:
    def __init__(self):
        import chromadb
        self.client = chromadb.PersistentClient(path="/data/chroma")
        self.collection = self.client.get_or_create_collection("documents")

    def query(self, query_embedding, top_k=5):
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
        )
        return results["documents"][0]

@serve.deployment(ray_actor_options={"num_gpus": 1})
class RAGService:
    def __init__(self, vector_store_handle):
        self.vector_store = vector_store_handle
        from vllm import LLM
        self.llm = LLM(model="meta-llama/Meta-Llama-3-8B-Instruct")

    async def __call__(self, request):
        data = await request.json()
        query = data["query"]

        # Get context
        query_embedding = self.embed(query)
        context = await self.vector_store.query.remote(query_embedding)

        # Generate response
        prompt = f"Context: {context}\n\nQuestion: {query}\n\nAnswer:"
        outputs = self.llm.generate([prompt], self.params)
        return outputs[0].outputs[0].text

vector_store = VectorStore.bind()
rag = RAGService.bind(vector_store)
serve.run(rag)
```

## Best Practices

1. **Use vLLM** for high-throughput LLM inference on GPU
2. **Enable tensor parallelism** for large models (>13B params)
3. **Use `gpu_memory_utilization=0.9`** to maximize GPU memory usage
4. **Set `max_model_len`** to limit KV cache and enable more concurrent requests
5. **Use Ray Data map_batches** for batch inference pipelines
6. **Enable autoscaling** for variable traffic patterns
7. **Use model multiplexing** for serving multiple models on shared GPUs
8. **Implement streaming** for interactive applications
9. **Use LoRA** for efficient multi-model serving
10. **Monitor GPU utilization** and adjust replica count accordingly
