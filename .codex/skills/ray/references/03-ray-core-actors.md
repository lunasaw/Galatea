# Ray Core - Actors

## Overview

Actors are stateful remote objects in Ray. Unlike tasks (stateless functions), actors maintain mutable state across method calls.

## Creating Actors

### @ray.remote on Classes

```python
@ray.remote
class Counter:
    def __init__(self, initial=0):
        self.count = initial

    def increment(self, n=1):
        self.count += n
        return self.count

    def get_count(self):
        return self.count

    def reset(self):
        self.count = 0
```

### Actor Options (All Parameters)

```python
@ray.remote(
    num_cpus=1,                        # CPUs for actor lifetime
    num_gpus=0,                        # GPUs for actor lifetime
    memory=None,                       # Memory in bytes
    object_store_memory=None,          # Object store memory
    resources=None,                    # Custom resources {"name": amount}
    accelerator_type=None,             # Accelerator type
    label_selector=None,               # Node label requirements
    fallback_strategy=None,            # Soft constraint fallback
    max_restarts=0,                    # Max restarts (-1 = infinite)
    max_task_retries=0,                # Max retries per method call
    max_concurrency=1,                 # Max concurrent method calls
    max_pending_calls=-1,              # Max queued calls (-1 = unlimited)
    allow_out_of_order_execution=None, # Allow out-of-order execution
    name=None,                         # Globally unique name
    namespace=None,                    # Ray namespace
    lifetime=None,                     # "detached" or "non_detached"
    runtime_env=None,                  # Runtime environment
    scheduling_strategy=None,          # Scheduling strategy
    concurrency_groups=None,           # Dict {name: max_concurrency}
    enable_task_events=True,           # Enable task events
    enable_tensor_transport=False,     # Enable tensor transport
)
class MyActor:
    pass
```

## ActorClass API

### .remote(*args, **kwargs)
Create an actor instance.
```python
counter = Counter.remote(0)  # Returns ActorHandle
```

### .options(**kwargs)
Override options for creation.
```python
counter = Counter.options(
    num_cpus=4, num_gpus=2,
    max_restarts=3,
    name="my_counter",
    namespace="production",
    lifetime="detached",
    scheduling_strategy="SPREAD",
).remote(0)
```

### .bind(*args, **kwargs)
For DAG building.
```python
from ray.dag import InputNode
actor = Counter.bind(0)
dag = actor.increment.bind(InputNode())
```

## ActorHandle API

### Method Calls
```python
# Remote method call - returns ObjectRef
count_ref = counter.increment.remote(5)
count = ray.get(count_ref)

# Options override per call
result = counter.increment.options(
    num_returns=2,
    max_task_retries=3,
    name="important_call",
).remote(10)
```

### Named Actors
```python
# Create named detached actor
@ray.remote(name="shared_cache", lifetime="detached")
class Cache:
    def __init__(self):
        self.data = {}

# Get by name
cache = ray.get_actor("shared_cache")
cache = ray.get_actor("shared_cache", namespace="my_app")

# Check if actor exists
try:
    actor = ray.get_actor("name")
except ValueError:
    actor = None
```

### Actor Lifecycle
```python
# Kill actor
ray.kill(actor)                        # Kill, no restart
ray.kill(actor, no_restart=False)      # Kill, allow restart if max_restarts > 0

# Exit from inside actor
ray.actor.exit_actor()                 # Gracefully exit current actor

# Check if actor was restarted
ctx = ray.get_runtime_context()
if ctx.was_current_actor_reconstructed:
    # Restore state from checkpoint
    pass
```

## Concurrency Models

### Single-Threaded (Default)
```python
@ray.remote
class SingleThreaded:
    def method(self):
        pass  # Only one method runs at a time
```

### Multi-Threaded
```python
@ray.remote(max_concurrency=10)
class ThreadedActor:
    def method(self):
        pass  # Up to 10 methods concurrently
```

### Async Actor
```python
@ray.remote
class AsyncActor:
    async def slow_method(self):
        await asyncio.sleep(1)
        return "done"

    async def stream_data(self):
        for i in range(10):
            yield i
```

### Concurrency Groups
```python
@ray.remote(concurrency_groups={
    "io": 2,       # 2 concurrent I/O operations
    "compute": 4,  # 4 concurrent compute operations
})
class MultiGroupActor:
    @ray.method(concurrency_group="io")
    async def read_data(self, path):
        return await read_file(path)

    @ray.method(concurrency_group="compute")
    def process(self, data):
        return heavy_computation(data)

    def default_method(self):
        # Uses default thread pool
        pass
```

## ray.method() Decorator

```python
@ray.remote
class MyActor:
    @ray.method(num_returns=3)
    def multi_return(self):
        return 1, 2, 3

    @ray.method(num_returns="streaming")
    def generator(self):
        for i in range(10):
            yield i

    @ray.method(max_task_retries=5)
    def reliable_method(self):
        pass

    @ray.method(retry_exceptions=True)
    def retry_on_error(self):
        pass

    @ray.method(retry_exceptions=[ValueError, ConnectionError])
    def retry_specific(self):
        pass

    @ray.method(concurrency_group="io")
    def io_method(self):
        pass

    @ray.method(enable_task_events=False)
    def untracked_method(self):
        pass

    @ray.method(tensor_transport="nccl")
    def gpu_transfer(self, tensor):
        return tensor
```

## ray.method() Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `num_returns` | int/str | 1 | Return values. "streaming" for generators |
| `concurrency_group` | str | None | Concurrency group for this method |
| `max_task_retries` | int | 0 | Max retries on failure (-1 = until success) |
| `retry_exceptions` | bool/list | False | Retry on application exceptions |
| `enable_task_events` | bool | True | Track this method's events |
| `tensor_transport` | str | None | "NCCL", "GLOO", or "NIXL" |

## Actor Scheduling

```python
from ray.util.scheduling_strategies import (
    PlacementGroupSchedulingStrategy,
    NodeAffinitySchedulingStrategy,
    NodeLabelSchedulingStrategy,
)

# Placement group
pg = ray.util.placement_group([{"CPU": 2, "GPU": 1}])
actor = MyActor.options(
    scheduling_strategy=PlacementGroupSchedulingStrategy(
        placement_group=pg,
        placement_group_bundle_index=0,
    )
).remote()

# Node affinity
actor = MyActor.options(
    scheduling_strategy=NodeAffinitySchedulingStrategy(
        node_id=node_id,
        soft=True,
    )
).remote()

# Label-based
actor = MyActor.options(
    scheduling_strategy=NodeLabelSchedulingStrategy(
        hard={"gpu_type": "A100"},
        soft={"zone": "us-west-1a"},
    )
).remote()
```

## Tensor Transport (RDT)

```python
from ray.experimental.collective import create_collective_group

@ray.remote(enable_tensor_transport=True, num_gpus=1)
class GPUActor:
    @ray.method(tensor_transport="nccl")
    def send_tensor(self, tensor):
        return tensor

# Create collective group for NCCL/GLOO transport
actor1 = GPUActor.remote()
actor2 = GPUActor.remote()
create_collective_group([actor1, actor2], backend="nccl")

# Transfer tensors via NCCL
tensor_ref = actor1.send_tensor.remote(torch.randn(1000, 1000))
result = actor2.receive.options(tensor_transport="nccl").remote(tensor_ref)
```

## Actor State Inspection

```python
# Get local actor state
state = actor._get_local_state()

# Runtime context inside actor
ctx = ray.get_runtime_context()
actor_id = ctx.get_actor_id()
actor_name = ctx.get_actor_name()
task_id = ctx.get_task_id()
resources = ctx.get_assigned_resources()
node_id = ctx.get_node_id()
```

## Cross-Language Actors

```python
# Java actor
java_actor = ray.java_actor_class("com.example.MyActor")
handle = java_actor.remote()

# C++ actor
cpp_actor = ray.cpp_function("create_actor", "MyActor")
handle = cpp_actor.remote()
```

## Default Resource Behavior

| Scenario | Actor Creation CPUs | Method CPUs |
|----------|--------------------|----|
| No resources specified | 0 | 1 |
| Any resource specified | 1 | 0 |

```python
# No resources: actor gets 0 CPUs, methods get 1 CPU each
@ray.remote
class A:
    pass

# With resources: actor gets 1 CPU, methods get 0 CPUs
@ray.remote(num_gpus=1)
class B:
    pass
```

## Actor Patterns

### Stateful Service
```python
@ray.remote
class ModelServer:
    def __init__(self, model_path):
        self.model = load_model(model_path)

    def predict(self, input_data):
        return self.model.predict(input_data)

    def update_model(self, new_path):
        self.model = load_model(new_path)
```

### Actor Pool
```python
from ray.util.actor_pool import ActorPool

@ray.remote
class Worker:
    def process(self, data):
        return result

pool = ActorPool([Worker.remote() for _ in range(4)])
pool.submit(lambda w, d: w.process.remote(d), data_list)
results = [pool.get_next() for _ in range(len(data_list))]
```

### Detached Actor (Survives Creator)
```python
@ray.remote(name="global_cache", lifetime="detached")
class GlobalCache:
    pass

cache = GlobalCache.remote()
# Cache lives even after this process exits

# Later, from another process:
cache = ray.get_actor("global_cache")
```

## Best Practices

1. **Use actors for stateful computation** - counters, caches, model serving
2. **Set max_restarts for critical actors** - enables automatic recovery
3. **Use concurrency groups** - separate I/O from compute threads
4. **Name important actors** - enables cross-process access
5. **Use detached lifetime for long-lived services** - survives creator
6. **Set max_pending_calls** - prevents memory leaks from queued calls
7. **Avoid passing large objects** - use ObjectRefs for large data
8. **Use async actors for I/O-bound work** - better resource utilization
