# Performance Tuning

## Overview

This guide covers performance optimization techniques for Ray applications, from task-level tuning to cluster-wide configuration.

## General Principles

1. **Minimize data movement** - compute where data lives
2. **Maximize parallelism** - use all available resources
3. **Avoid bottlenecks** - balance workload across workers
4. **Reduce serialization overhead** - use efficient data formats
5. **Tune resource allocation** - match resources to workload

## Task Performance

### Optimal Task Granularity
```python
# Too fine-grained (overhead dominated)
@ray.remote
def add_one(x):
    return x + 1

results = ray.get([add_one.remote(x) for x in range(100000)])
# Overhead: ~1ms per task → 100 seconds of overhead

# Better: batch processing
@ray.remote
def add_one_batch(batch):
    return [x + 1 for x in batch]

chunks = [range(i, i+1000) for i in range(0, 100000, 1000)]
results = ray.get([add_one_batch.remote(chunk) for chunk in chunks])
# Overhead: ~1ms per task → 0.1 seconds of overhead
```

### Task Duration Guidelines
| Duration | Recommendation |
|----------|---------------|
| < 1ms | Batch into larger tasks |
| 1ms - 100ms | Acceptable for fine-grained work |
| 100ms - 10s | Sweet spot for most tasks |
| > 10s | Consider breaking into subtasks |

### Resource Specification
```python
# Accurate resource specification reduces scheduling delays
@ray.remote(
    num_cpus=2,          # Match actual CPU usage
    num_gpus=1,          # Only if GPU is actually used
    memory=2 * 1024**3,  # 2 GB - prevent OOM
)
def properly_sized_task():
    pass

# Over-provisioning wastes resources
@ray.remote(num_gpus=4)  # Bad if only using 1 GPU
def wasteful_task():
    pass
```

## Object Store Optimization

### Memory Configuration
```python
import ray

# Set object store memory appropriately
ray.init(
    object_store_memory=8 * 1024 * 1024 * 1024,  # 8 GB
    _system_config={
        "automatic_object_spilling_enabled": True,
        "object_spilling_config": json.dumps({
            "type": "filesystem",
            "params": {"directory_path": "/fast-ssd/ray_spill"}
        }),
    }
)
```

### Reducing Object Store Pressure
```python
# BAD: Accumulate all results in memory
results = ray.get([long_task.remote(i) for i in range(10000)])

# GOOD: Process incrementally
refs = [long_task.remote(i) for i in range(10000)]
while refs:
    ready, refs = ray.wait(refs, num_returns=10)
    for ref in ready:
        result = ray.get(ref)
        process_result(result)

# GOOD: Use streaming generator
@ray.remote(num_returns="streaming")
def stream_results():
    for i in range(10000):
        yield compute(i)

gen = stream_results.remote()
for _ in range(10000):
    ref = next(gen)
    result = ray.get(ref)
    process_result(result)
```

### Object Reuse
```python
# Reuse objects across tasks
shared_data = ray.put(large_dataset)

@ray.remote
def process(data_ref):
    data = ray.get(data_ref)
    return transform(data)

# All tasks share the same data in object store
results = ray.get([process.remote(shared_data) for _ in range(100)])
```

## Actor Performance

### Actor Pool Pattern
```python
from ray.util.actor_pool import ActorPool

@ray.remote
class Worker:
    def process(self, data):
        return compute(data)

# Create pool of actors
actors = [Worker.remote() for _ in range(8)]
pool = ActorPool(actors)

# Submit work
results = list(pool.map(lambda a, v: a.process.remote(v), data_list))
```

### Concurrency Tuning
```python
# Threaded actor for IO-bound work
@ray.remote(concurrency_groups={"io": 8})
class IOActor:
    @ray.method(concurrency_group="io")
    async def fetch(self, url):
        return await aiohttp_get(url)

    def compute(self, data):
        return process(data)

# Async actor for concurrent requests
@ray.remote
class AsyncActor:
    async def process(self, data):
        result = await async_compute(data)
        return result

    async def batch(self, items):
        tasks = [self.process(item) for item in items]
        return await asyncio.gather(*tasks)
```

### Actor Warm-up
```python
@ray.remote(num_gpus=1)
class GPUActor:
    def __init__(self):
        # Expensive initialization
        self.model = load_large_model()

    def predict(self, x):
        return self.model(x)

# Pre-create actors before submitting work
actors = [GPUActor.remote() for _ in range(4)]
# Wait for initialization
ray.get([actor.predict.remote(dummy_input) for actor in actors])
```

## Serialization Optimization

### Efficient Data Formats
```python
import numpy as np

# GOOD: Use numpy arrays (Arrow-serialized, zero-copy)
@ray.remote
def process_array(arr: np.ndarray):
    return arr.mean()

data = np.random.rand(1000000)
ref = ray.remote(lambda x: x * 2).remote(data)  # Fast Arrow serialization

# BAD: Use Python lists (slow pickle serialization)
data_list = list(range(1000000))
ref = ray.remote(lambda x: sum(x)).remote(data_list)  # Slow serialization
```

### Custom Serializers
```python
from ray.util import register_custom_serializer

class MyObject:
    def __init__(self, data):
        self.data = data

def serialize(obj):
    return obj.data

def deserialize(data):
    return MyObject(data)

register_custom_serializer(MyObject, serializer=serialize, deserializer=deserialize)
```

## Data Pipeline Optimization

### Batch Size Tuning
```python
import ray

ds = ray.data.read_parquet("s3://bucket/data/")

# Tune batch size for your workload
# Small batch: more parallelism, more overhead
# Large batch: less overhead, less parallelism
results = ds.map_batches(
    process_fn,
    batch_size=4096,     # Tune this
    num_cpus=2,
    concurrency=8,
)
```

### Streaming Execution
```python
# Use streaming for large datasets
ds = ray.data.read_parquet("s3://huge-bucket/data/")
results = ds.map_batches(process_fn, batch_size=1024)

# Write results in streaming fashion
results.write_parquet("s3://output/", num_rows_per_file=100000)
```

### Dataset Optimization
```python
# Repartition for optimal parallelism
ds = ds.repartition(100)  # Match number of workers

# Select only needed columns early
ds = ds.select_columns(["feature_1", "feature_2", "label"])

# Use random blocks for better load balancing
ds = ds.random_shuffle()
```

## Network Optimization

### Data Locality
```python
# Schedule tasks near data
@ray.remote
def process(data_ref):
    data = ray.get(data_ref)
    return compute(data)

# Ray automatically schedules tasks where data lives
large_data = ray.put(huge_array)
result = process.remote(large_data)  # Scheduled on node with data
```

### Object Spilling to Fast Storage
```python
ray.init(
    _system_config={
        "object_spilling_config": json.dumps({
            "type": "filesystem",
            "params": {
                "directory_path": "/nvme/ray_spill"  # Fast SSD
            }
        }),
    }
)
```

## Cluster Configuration

### Head Node
```bash
ray start --head \
    --num-cpus=0 \          # Reserve head for coordination
    --dashboard-host=0.0.0.0 \
    --object-store-memory=4g \
    --disable-usage-stats
```

### Worker Nodes
```bash
ray start --address=head:6379 \
    --num-cpus=32 \
    --num-gpus=8 \
    --object-store-memory=32g \
    --resources='{"TPU": 4}'
```

### Autoscaler Tuning
```yaml
upscaling_speed: 5.0           # Aggressive scale-up
idle_timeout_minutes: 2        # Fast scale-down
max_workers: 50

available_node_types:
    gpu-worker:
        min_workers: 2
        max_workers: 20
        resources: {"CPU": 8, "GPU": 4}
```

## GPU Optimization

### Fractional GPU
```python
# Use fractional GPU for inference
@ray.remote(num_gpus=0.25)
def inference(model, input):
    return model(input)

# 4 inferences per GPU concurrently
```

### GPU Memory Management
```python
import torch

@ray.remote(num_gpus=1)
def gpu_task():
    # Limit GPU memory
    torch.cuda.set_per_process_memory_fraction(0.8, 0)

    # Clear cache between operations
    torch.cuda.empty_cache()

    return result
```

### NCCL Configuration
```python
# For multi-GPU training
@ray.remote(num_gpus=4)
class DistributedTrainer:
    def __init__(self):
        import torch.distributed as dist
        dist.init_process_group(backend="nccl")
```

## Memory Profiling

### Detecting Leaks
```python
import ray

# Monitor object store
while True:
    stats = ray.memory_monitor.get_memory_info()
    print(f"Object store: {stats.object_store_used / stats.object_store_total:.1%}")
    print(f"Pinned: {stats.object_store_pinned}")
    time.sleep(10)
```

### Reference Count Tracking
```python
# Check for leaked references
import ray._private.state as state

# Get reference counts
refs = state.object_ref_info()
for ref in refs:
    if ref.local_ref_count > 0:
        print(f"Ref: {ref.object_id}, Local refs: {ref.local_ref_count}")
```

## Benchmarking

### Measuring Task Throughput
```python
import ray
import time

ray.init()

@ray.remote
def noop():
    pass

# Warmup
ray.get([noop.remote() for _ in range(100)])

# Benchmark
start = time.time()
n = 10000
refs = [noop.remote() for _ in range(n)]
ray.get(refs)
elapsed = time.time() - start
print(f"Throughput: {n / elapsed:.0f} tasks/sec")
```

### Measuring Data Transfer
```python
import numpy as np
import time

# Benchmark ray.put/ray.get
data = np.random.rand(100, 1000, 1000)  # ~750 MB

start = time.time()
ref = ray.put(data)
put_time = time.time() - start

start = time.time()
_ = ray.get(ref)
get_time = time.time() - start

print(f"Put: {put_time:.3f}s, Get: {get_time:.3f}s")
print(f"Data size: {data.nbytes / 1e9:.2f} GB")
```

## Common Pitfalls

| Pitfall | Solution |
|---------|----------|
| Tasks too small | Batch into larger tasks |
| Over-fetching with ray.get() | Use ray.wait() for incremental processing |
| Object store full | Increase memory or use streaming |
| Serialization bottleneck | Use Arrow-compatible types (numpy, pandas) |
| Driver bottleneck | Submit tasks in batches, don't ray.get() individually |
| GPU underutilization | Use fractional GPUs or batching |
| Uneven load | Use dynamic batching or work stealing |
| Actor cold start | Pre-warm actors before production traffic |
| Network bottleneck | Use data locality, compress large transfers |

## System Configuration Tuning

### Key System Config Parameters
```python
ray.init(_system_config={
    # Task scheduling
    "max_task_args_bytes": 10 * 1024 * 1024,  # 10 MB inline args
    "task_retry_delay_ms": 100,
    "max_num_rejected_task_resubmit": 100,

    # Object store
    "object_store_full_delay_ms": 1000,
    "automatic_object_spilling_enabled": True,
    "max_object_size_in_memory": 100 * 1024 * 1024,

    # Worker pool
    "num_workers_soft_limit": 5,
    "worker_startup_timeout_seconds": 600,

    # Network
    "object_manager_max_bytes_in_flight": 2 * 1024 * 1024 * 1024,
    "object_manager_push_timeout_ms": 10000,

    # Memory
    "memory_monitor_interval_ms": 1000,
    "memory_usage_threshold_fraction": 0.9,

    # Debugging
    "event_stats": True,
    "event_stats_print_interval_ms": 60000,
})
```

## Best Practices Summary

1. **Right-size tasks** - aim for 10ms-10s per task
2. **Use batching** - batch small operations into larger units
3. **Minimize ray.get()** - use ray.wait() for incremental processing
4. **Reuse actors** - avoid recreating actors per request
5. **Use streaming generators** - for large result sets
6. **Tune batch_size** in map_batches - match GPU/CPU capacity
7. **Profile before optimizing** - identify actual bottlenecks
8. **Use Arrow types** - numpy, pandas for efficient serialization
9. **Configure object store memory** - 30-50% of total RAM
10. **Monitor with dashboard** - detect issues early
