# Ray Core - Objects and ObjectRef

## Object Store Architecture

Ray uses a distributed shared-memory object store called Plasma. Each node has its own Plasma store instance.

```
Worker A  ----put()--->
                       Plasma Object Store (Shared Memory)
Worker B  ----get()--->  [obj1] [obj2] [obj3] [obj4] ...
Worker C  ----get()--->

Disk Spill Area: [spilled_obj5] [spilled_obj6] ...
```

## Core APIs

### ray.put()
```python
ray.put(value: Any, _owner: Optional[ActorHandle] = None) -> ObjectRef

# Store object in shared memory
ref = ray.put(42)
ref = ray.put([1, 2, 3])
ref = ray.put(large_numpy_array)

# Specify owner (object lives as long as owner)
ref = ray.put(data, _owner=actor_handle)
```

### ray.get()
```python
ray.get(
    object_refs: Union[ObjectRef, List[ObjectRef]],
    timeout: Optional[float] = None,
) -> Any

# Single object
value = ray.get(ref)

# Multiple objects
values = ray.get([ref1, ref2, ref3])

# With timeout (raises GetTimeoutError)
try:
    value = ray.get(ref, timeout=5.0)
except ray.exceptions.GetTimeoutError:
    print("Timed out")
```

### ray.wait()
```python
ray.wait(
    object_refs: List[ObjectRef],
    num_returns: int = 1,
    timeout: Optional[float] = None,
    fetch_local: bool = True,
) -> Tuple[List[ObjectRef], List[ObjectRef]]

# Wait for any one
ready, remaining = ray.wait([ref1, ref2, ref3])

# Wait for all
ready, remaining = ray.wait([ref1, ref2, ref3], num_returns=3)

# With timeout
ready, remaining = ray.wait(refs, timeout=10.0)

# Don't fetch data (just check availability)
ready, remaining = ray.wait(refs, fetch_local=False)
```

### ray.cancel()
```python
ray.cancel(
    object_ref: ObjectRef,
    force: bool = False,
    recursive: bool = True,
)

# Graceful cancel (allows task to handle)
ray.cancel(ref, force=False)

# Force cancel (immediate termination)
ray.cancel(ref, force=True)

# Cancel only the task, not dependencies
ray.cancel(ref, recursive=False)
```

## ObjectRef Class

```python
class ObjectRef:
    # Check if nil (empty)
    ref.is_nil()

    # Get binary representation
    binary = ref.binary()

    # Get hex representation
    hex_str = ref.hex()

    # Get associated task ID
    task_id = ref.task_id()

    # Serialization
    serialized = ref.__reduce__()
```

## Serialization

### Default: CloudPickle
Ray uses cloudpickle for serialization, supporting:
- Functions, lambdas, closures
- Classes and instances
- NumPy arrays (zero-copy via Plasma)
- PyTorch tensors (zero-copy when possible)

### Custom Serializers
```python
# Register custom serializer
from ray._private.serialization import register_custom_serializer

register_custom_serializer(
    MyClass,
    serializer=my_serialize_fn,
    deserializer=my_deserialize_fn,
)
```

### Serialization for Exceptions
```python
# Exceptions are serialized and deserialized
# If deserialization fails, UnserializableException is raised
# Configure custom exception serializers for non-picklable exceptions
```

## Object Spilling

When the object store is full, Ray spills objects to disk:

### Configuration
```python
ray.init(_system_config={
    "automatic_object_spilling_enabled": True,
    "max_bytes_reclaimable_per_object_spilling": 10**9,
    "object_spilling_config": json.dumps({
        "type": "filesystem",
        "params": {
            "directory_path": "/tmp/ray/spill",
            "buffer_size": 1_000_000,
        },
    }),
})
```

### Spilling Behavior
1. Unreferenced objects evicted first
2. Referenced objects spilled to disk if needed
3. Objects automatically restored when accessed
4. Spilling is transparent to the user

## Object Reconstruction

### Lineage-Based Reconstruction
```python
ray.init(enable_object_reconstruction=True)

@ray.remote(max_retries=3)
def create_data():
    return expensive_computation()

# If the node holding the result dies, Ray can recompute it
ref = create_data.remote()
value = ray.get(ref)  # May trigger reconstruction if object was lost
```

### Reconstruction Failure Reasons
- `OBJECT_UNRECONSTRUCTABLE_MAX_ATTEMPTS_EXCEEDED` - Too many retries
- `OBJECT_UNRECONSTRUCTABLE_LINEAGE_EVICTED` - Lineage evicted
- `OBJECT_UNRECONSTRUCTABLE_PUT` - Created by ray.put (no lineage)
- `OBJECT_UNRECONSTRUCTABLE_RETRIES_DISABLED` - max_retries=0
- `OBJECT_UNRECONSTRUCTABLE_BORROWED` - Borrowed object
- `OBJECT_UNRECONSTRUCTABLE_TASK_CANCELLED` - Task was cancelled

## Object Ownership Model

### Ownership Rules
1. The worker that creates an object (via `.remote()` or `ray.put()`) owns it
2. The owner tracks the object's reference count
3. When all references are gone, the object can be evicted
4. Borrowed references (passed to other tasks) don't affect ownership

### Reference Counting
```python
# Creates reference - worker owns it
ref = ray.put(data)

# Passing ref to task creates a borrowed reference
result_ref = process.remote(ref)

# When ref goes out of scope locally, borrowed ref keeps object alive
# When result_ref completes and ref is out of scope, object can be evicted
```

## Memory Management

### Object Store Configuration
```python
ray.init(
    object_store_memory=10 * 1024 * 1024 * 1024,  # 10 GB
    _system_config={
        "object_store_full_delay_ms": 100,
    },
)
```

### Memory Monitoring
```python
# Available cluster memory
ray.available_resources()

# Total cluster memory
ray.cluster_resources()

# CLI
# ray memory - detailed memory breakdown
```

### Out of Memory Handling
```python
# ObjectStoreFullError when store is full
try:
    ref = ray.put(large_object)
except ray.exceptions.ObjectStoreFullError:
    print("Object store is full")
```

## ObjectRef Generators

### Streaming Generator
```python
@ray.remote(num_returns="streaming")
def stream_data(n):
    for i in range(n):
        yield i

gen = stream_data.remote(10)
for ref in gen:
    value = ray.get(ref)
```

### Dynamic Generator
```python
@ray.remote(num_returns="dynamic")
def dynamic_results():
    results = []
    for item in compute():
        results.append(item)
    return results

gen = dynamic_results.remote()
for ref in gen:
    value = ray.get(ref)
```

## Best Practices

### 1. Avoid Unnecessary Data Transfer
```python
# Bad - transfers data twice
result = ray.get(task1.remote())
ref = task2.remote(result)

# Good - passes ObjectRef directly
ref1 = task1.remote()
ref2 = task2.remote(ref1)
```

### 2. Use ray.put for Shared Data
```python
# Bad - same data sent to each task
refs = [task.remote(large_data) for _ in range(100)]

# Good - data stored once
data_ref = ray.put(large_data)
refs = [task.remote(data_ref) for _ in range(100)]
```

### 3. Handle Timeouts
```python
# Always set timeouts for potentially slow operations
try:
    result = ray.get(ref, timeout=30.0)
except ray.exceptions.GetTimeoutError:
    # Handle timeout
    ray.cancel(ref)
```

### 4. Use ray.wait for Progress Tracking
```python
refs = [task.remote(i) for i in range(1000)]
completed = 0
while refs:
    ready, refs = ray.wait(refs, num_returns=min(10, len(refs)), timeout=60)
    completed += len(ready)
    print(f"Progress: {completed}/1000")
    for ref in ready:
        process_result(ray.get(ref))
```

### 5. Monitor Memory Usage
```bash
# Check object store memory
ray memory

# Check from Python
ray.available_resources()
```

## Error Types for Objects

| Error | Description |
|-------|-------------|
| `ObjectStoreFullError` | Object store capacity exceeded |
| `ObjectLostError` | Object lost due to node failure |
| `ObjectFetchTimedOutError` | Fetch timed out |
| `OwnerDiedError` | Object owner process died |
| `ObjectReconstructionFailedError` | Cannot reconstruct object |
| `OutOfDiskError` | Both memory and disk full |
| `GetTimeoutError` | ray.get() timed out |
| `ReferenceCountingAssertionError` | Object deleted while referenced |
