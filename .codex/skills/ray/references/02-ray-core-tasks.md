# Ray Core - Tasks (Remote Functions)

## Overview

Tasks are stateless remote functions that execute asynchronously on Ray worker processes. They are the fundamental building block of parallel computation in Ray.

## Creating Tasks

### @ray.remote Decorator

```python
@ray.remote(
    num_returns: Optional[Union[int, str]] = None,
    num_cpus: Optional[float] = None,
    num_gpus: Optional[float] = None,
    memory: Optional[int] = None,
    resources: Optional[Dict[str, float]] = None,
    accelerator_type: Optional[str] = None,
    label_selector: Optional[Dict[str, str]] = None,
    max_calls: Optional[int] = None,
    max_retries: Optional[int] = None,
    retry_exceptions: Optional[Union[bool, List[Type[Exception]]]] = None,
    runtime_env: Optional[Dict] = None,
    scheduling_strategy: Optional[Union[str, SchedulingStrategy]] = None,
    name: Optional[str] = None,
    namespace: Optional[str] = None,
    enable_task_events: Optional[bool] = None,
    _labels: Optional[Dict[str, str]] = None,
)
def my_function(args):
    pass
```

### Parameter Details

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `num_returns` | int/str | 1 | Number of return values. "streaming" for generators, "dynamic" for dynamic |
| `num_cpus` | float | None | CPU cores required |
| `num_gpus` | float | None | GPUs required |
| `memory` | int | None | Memory in bytes |
| `resources` | dict | None | Custom resources {"resource_name": amount} |
| `accelerator_type` | str | None | Accelerator type requirement |
| `label_selector` | dict | None | Node label requirements |
| `max_calls` | int | None | Max calls before worker restarts (prevents memory leaks) |
| `max_retries` | int | 3 | Max retries on system failure |
| `retry_exceptions` | bool/list | False | Retry on application exceptions |
| `runtime_env` | dict | None | Runtime environment override |
| `scheduling_strategy` | str/obj | None | Scheduling strategy |
| `name` | str | None | Task name for debugging |
| `enable_task_events` | bool | True | Enable task event tracking |

## RemoteFunction Class

### Key Methods

#### .remote(*args, **kwargs)
Execute the function remotely. Returns ObjectRef(s).
```python
ref = my_function.remote(arg1, arg2)
refs = multi_return_function.remote()  # Returns tuple of ObjectRefs
```

#### .options(**kwargs)
Override default options for a specific call.
```python
result = my_function.options(
    num_cpus=4,
    num_gpus=1,
    memory=2**31,
    max_retries=5,
    retry_exceptions=[ValueError, TypeError],
    scheduling_strategy="SPREAD",
    name="important_task",
    runtime_env={"pip": ["numpy==1.24"]},
).remote(42)
```

#### .bind(*args, **kwargs)
For DAG building. Returns a DAG node instead of executing.
```python
from ray.dag import InputNode
with InputNode() as inp:
    dag_node = my_function.bind(inp)
```

## Task Execution Model

### Scheduling Flow
1. `.remote()` called -> task submitted to local raylet
2. Raylet checks resource requirements
3. If resources available locally -> execute on local worker
4. If not -> forward to remote raylet with available resources
5. Task executes, result stored in object store
6. ObjectRef becomes ready

### Resource Acquisition
- Resources are acquired before task execution
- Tasks wait in queue if resources unavailable
- Resources released when task completes
- GPU resources are visible via `CUDA_VISIBLE_DEVICES`

### Task Dependencies
```python
@ray.remote
def fetch_data(url):
    return requests.get(url).json()

@ray.remote
def process(data):
    return transform(data)

# Automatic dependency tracking
data_ref = fetch_data.remote("http://example.com/api")
result_ref = process.remote(data_ref)  # Waits for data_ref
```

## Streaming Generators

```python
@ray.remote(num_returns="streaming")
def generate_stream(n):
    for i in range(n):
        yield {"value": i, "timestamp": time.time()}

gen_ref = generate_stream.remote(100)
# Iterate over streaming results
for ref in gen_ref:
    result = ray.get(ref)
    print(result)
```

### Generator Options
```python
@ray.remote(
    num_returns="streaming",
    _generator_backpressure_num_objects=10,  # Max buffered objects
)
def buffered_generator(n):
    for i in range(n):
        yield expensive_compute(i)
```

## Dynamic Generators

```python
@ray.remote(num_returns="dynamic")
def dynamic_generate():
    results = []
    for i in range(unknown_count):
        results.append(compute(i))
    return results

refs = dynamic_generate.remote()  # Returns ObjectRefGenerator
for ref in refs:
    print(ray.get(ref))
```

## Task Cancellation

```python
ref = long_running_task.remote()

# Graceful cancellation (allows cleanup)
ray.cancel(ref, force=False)

# Force cancellation (immediate termination)
ray.cancel(ref, force=True)

# Check if cancelled (inside task)
if ray.get_runtime_context().is_canceled():
    # Perform cleanup
    cleanup()
```

## Task Naming and Debugging

```python
# Named tasks appear in dashboard and logs
@ray.remote(name="data_preprocessing")
def preprocess(data):
    return clean(data)

# Get task name inside task
task_name = ray.get_runtime_context().get_task_name()
task_function_name = ray.get_runtime_context().get_task_function_name()
task_id = ray.get_runtime_context().get_task_id()
```

## Retry and Fault Tolerance

### System Failure Retry
```python
@ray.remote(max_retries=5)  # Retry up to 5 times on system failure
def reliable_task():
    pass
```

### Application Exception Retry
```python
# Retry on any exception
@ray.remote(retry_exceptions=True, max_retries=3)
def flaky_task():
    pass

# Retry only on specific exceptions
@ray.remote(retry_exceptions=[ConnectionError, TimeoutError], max_retries=5)
def network_task():
    pass
```

## Scheduling Strategies

### Default Scheduling
```python
@ray.remote
def task():
    pass
# Ray chooses best placement based on data locality and resources
```

### Spread Scheduling
```python
@ray.remote(scheduling_strategy="SPREAD")
def task():
    pass
# Distribute tasks across different nodes
```

### Placement Group Scheduling
```python
from ray.util.scheduling_strategies import PlacementGroupSchedulingStrategy

pg = ray.util.placement_group([{"CPU": 2}])

@ray.remote(scheduling_strategy=PlacementGroupSchedulingStrategy(
    placement_group=pg,
    placement_group_bundle_index=0,
    placement_group_capture_child_tasks=True,
))
def task():
    pass
```

### Node Affinity Scheduling
```python
from ray.util.scheduling_strategies import NodeAffinitySchedulingStrategy

@ray.remote(scheduling_strategy=NodeAffinitySchedulingStrategy(
    node_id=node_id,
    soft=False,  # Hard constraint
))
def task():
    pass
```

### Node Label Scheduling
```python
from ray.util.scheduling_strategies import NodeLabelSchedulingStrategy

@ray.remote(scheduling_strategy=NodeLabelSchedulingStrategy(
    hard={"region": "us-west"},
    soft={"zone": "1a"},
))
def task():
    pass
```

## Best Practices

### 1. Use Appropriate Granularity
```python
# Too fine-grained - overhead dominates
@ray.remote
def add(x, y):
    return x + y

# Better - batch processing
@ray.remote
def process_batch(batch):
    return [transform(x) for x in batch]
```

### 2. Avoid Large Object Transfers
```python
# Bad - transfers large data
@ray.remote
def process(data):
    return result

# Good - use ObjectRef
data_ref = ray.put(large_data)
result_ref = process.remote(data_ref)
```

### 3. Use max_calls for GPU Tasks
```python
# Prevent GPU memory leaks
@ray.remote(num_gpus=1, max_calls=100)
def gpu_task():
    # GPU memory released after 100 calls
    pass
```

### 4. Handle Errors Explicitly
```python
@ray.remote(max_retries=3, retry_exceptions=True)
def robust_task():
    try:
        return do_work()
    except Exception as e:
        log_error(e)
        raise
```

### 5. Use ray.wait for Progress
```python
refs = [task.remote(i) for i in range(100)]
completed = []
while refs:
    ready, refs = ray.wait(refs, num_returns=min(10, len(refs)))
    for ref in ready:
        completed.append(ray.get(ref))
    print(f"Progress: {len(completed)}/100")
```

### 6. Use map_batches for Data Processing
```python
import ray.data as rd

ds = rd.from_items(range(10000))
ds = ds.map_batches(process_fn, batch_size=256)
```
