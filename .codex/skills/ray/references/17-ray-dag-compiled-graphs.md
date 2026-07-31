# Ray DAG & Compiled Graphs

## Overview

Ray DAG (Directed Acyclic Graph) API provides a declarative way to compose Ray tasks and actors into execution graphs. Compiled Graphs (experimental) optimize these DAGs for efficient execution.

## DAG API

### Core Components

| Component | Description |
|-----------|-------------|
| `InputNode` | Entry point for DAG input |
| `ClassNode` | Actor method node in DAG |
| `FunctionNode` | Task node in DAG |
| `DAGNode` | Base class for all nodes |

### Basic DAG Construction
```python
import ray
from ray.dag import InputNode

@ray.remote
def process(data):
    return transform(data)

@ray.remote
def aggregate(results):
    return combine(results)

# Build DAG
with InputNode() as inp:
    processed = process.bind(inp)
    result = aggregate.bind([processed])

dag = result
```

### Executing DAGs
```python
# Execute with input
result = dag.execute(input_data)

# Execute with different inputs
result1 = dag.execute(data1)
result2 = dag.execute(data2)
```

## Function DAGs

### Simple Pipeline
```python
@ray.remote
def extract(raw):
    return parse(raw)

@ray.remote
def transform(parsed):
    return clean(parsed)

@ray.remote
def load(cleaned):
    return save(cleaned)

# ETL Pipeline DAG
with InputNode() as inp:
    e = extract.bind(inp)
    t = transform.bind(e)
    l = load.bind(t)

dag = l
result = dag.execute(raw_data)
```

### Parallel Fan-Out/Fan-In
```python
@ray.remote
def split(data):
    return data[:len(data)//2], data[len(data)//2:]

@ray.remote
def process_shard(shard):
    return analyze(shard)

@ray.remote
def merge(results):
    return combine(results)

with InputNode() as inp:
    shards = split.bind(inp)
    r1 = process_shard.bind(shards[0])
    r2 = process_shard.bind(shards[1])
    result = merge.bind([r1, r2])

dag = result
output = dag.execute(full_data)
```

### Multiple Inputs
```python
from ray.dag import InputNode, InputAttributeNode

@ray.remote
def join_tables(left, right):
    return pd.merge(left, right, on="key")

with InputNode() as inp:
    # Access positional inputs
    left = inp[0]
    right = inp[1]
    result = join_tables.bind(left, right)

dag = result
output = dag.execute(table_a, table_b)
```

## Actor DAGs

### Actor Method Binding
```python
@ray.remote
class Model:
    def __init__(self, model_path):
        self.model = load_model(model_path)

    def predict(self, input_data):
        return self.model(input_data)

    def batch_predict(self, inputs):
        return [self.model(x) for x in inputs]

# Create actor instance
model = Model.bind("model.pt")

with InputNode() as inp:
    output = model.predict.bind(inp)

dag = output
result = dag.execute(input_tensor)
```

### Multi-Actor DAG
```python
@ray.remote
class Preprocessor:
    def preprocess(self, data):
        return normalize(data)

@ray.remote
class Predictor:
    def predict(self, features):
        return inference(features)

@ray.remote
class Postprocessor:
    def postprocess(self, prediction):
        return format_output(prediction)

# Wire actors together
preprocessor = Preprocessor.bind()
predictor = Predictor.bind()
postprocessor = Postprocessor.bind()

with InputNode() as inp:
    features = preprocessor.preprocess.bind(inp)
    prediction = predictor.predict.bind(features)
    output = postprocessor.postprocess.bind(prediction)

dag = output
result = dag.execute(raw_input)
```

### Shared State via Actors
```python
@ray.remote
class Cache:
    def __init__(self):
        self.cache = {}

    def get(self, key):
        return self.cache.get(key)

    def set(self, key, value):
        self.cache[key] = value

@ray.remote
def compute(cache_result, key, use_cached):
    if use_cached and cache_result is not None:
        return cache_result
    return expensive_compute(key)

cache = Cache.bind()

with InputNode() as inp:
    cached = cache.get.bind(inp["key"])
    result = compute.bind(cached, inp["key"], inp["use_cache"])
    update = cache.set.bind(inp["key"], result)

dag = update
```

## Compiled Graphs (Experimental)

### Overview
Compiled Graphs optimize DAG execution by:
1. Fusing adjacent nodes
2. Eliminating unnecessary serialization
3. Pre-computing scheduling decisions
4. Enabling zero-copy data transfer

### Compilation
```python
from ray.dag import InputNode

# Build DAG as before
with InputNode() as inp:
    processed = process.bind(inp)
    result = aggregate.bind(processed)

# Compile the DAG
compiled_dag = result.experimental_compile()

# Execute (lower overhead than regular DAG)
ref = compiled_dag.execute(input_data)
result = ray.get(ref)

# Cleanup
compiled_dag.teardown()
```

### Compiled DAG Execution
```python
# Build and compile
with InputNode() as inp:
    a = step_a.bind(inp)
    b = step_b.bind(a)
    c = step_c.bind(b)

compiled = c.experimental_compile()

# Multiple executions reuse compiled graph
for data in dataset:
    ref = compiled.execute(data)
    result = ray.get(ref)

# Teardown when done
compiled.teardown()
```

### Channel Types
Compiled graphs support optimized data channels:
- **Intra-process**: Zero-copy for same-process nodes
- **Shared memory**: For same-node communication
- **NCCL**: GPU-to-GPU tensor transfer

### GPU Compiled Graphs
```python
@ray.remote(num_gpus=1)
class GPUWorker:
    def forward(self, tensor):
        return model(tensor)

workers = [GPUWorker.bind() for _ in range(4)]

with InputNode() as inp:
    outputs = [w.forward.bind(inp) for w in workers]
    result = aggregate.bind(outputs)

compiled = result.experimental_compile(
    # Enable NCCL channels for GPU communication
    _transport="nccl",
)

result_ref = compiled.execute(torch_tensor)
```

## DAG Patterns

### Scatter-Gather
```python
@ray.remote
def scatter(data, num_shards):
    return np.array_split(data, num_shards)

@ray.remote
def process_shard(shard):
    return compute(shard)

@ray.remote
def gather(results):
    return np.concatenate(results)

with InputNode() as inp:
    shards = scatter.bind(inp, 4)
    results = [process_shard.bind(shards[i]) for i in range(4)]
    output = gather.bind(results)

dag = output
```

### Pipeline with Stages
```python
@ray.remote(num_cpus=2)
def stage_1(data):
    return preprocess(data)

@ray.remote(num_cpus=4, num_gpus=1)
def stage_2(data):
    return inference(data)

@ray.remote(num_cpus=1)
def stage_3(data):
    return postprocess(data)

with InputNode() as inp:
    s1 = stage_1.bind(inp)
    s2 = stage_2.bind(s1)
    s3 = stage_3.bind(s2)

pipeline = s3
```

### Conditional Execution
```python
@ray.remote
def route(data):
    if data["type"] == "A":
        return process_a(data)
    else:
        return process_b(data)

with InputNode() as inp:
    result = route.bind(inp)

dag = result
```

### Recursive/Tree Pattern
```python
@ray.remote
def tree_reduce(data, depth=0):
    if len(data) <= 1 or depth > 3:
        return final_reduce(data)
    mid = len(data) // 2
    left = tree_reduce.bind(data[:mid], depth + 1)
    right = tree_reduce.bind(data[mid:], depth + 1)
    return combine.bind(left, right)

with InputNode() as inp:
    result = tree_reduce.bind(inp)

dag = result
```

## DAG Visualization

### Getting DAG Structure
```python
# Get all nodes in the DAG
nodes = dag._get_all_nodes()

# Get root nodes
root_nodes = dag._get_root_nodes()

# Inspect node types
for node in nodes:
    if isinstance(node, ray.dag.FunctionNode):
        print(f"Function: {node._func.__name__}")
    elif isinstance(node, ray.dag.ClassNode):
        print(f"Actor: {node._cls.__name__}")
```

## Best Practices

1. **Use `bind()` for construction**, `execute()` for execution
2. **Prefer Compiled Graphs** for repeated DAG execution (lower overhead)
3. **Keep actor state immutable** in DAGs for reproducibility
4. **Use `InputNode`** for external data injection
5. **Decompose complex logic** into smaller, reusable DAG nodes
6. **Test DAGs locally** before deploying to cluster
7. **Use `teardown()`** to clean up compiled graph resources
8. **Pin GPU resources** to actors for GPU DAGs
9. **Use tree-reduce patterns** for aggregation workloads
10. **Monitor execution** via dashboard DAG visualization
