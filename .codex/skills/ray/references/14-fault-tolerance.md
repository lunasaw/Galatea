# Fault Tolerance

## Overview

Ray provides multiple fault tolerance mechanisms for tasks, actors, objects, and cluster nodes to ensure reliable distributed execution.

## Failure Domains

```
Node Failure → Worker process death → Task/Actor failure → Object loss
     ↑                                        ↑
     └── GCS detects via heartbeat ──────────┘
```

## Task Fault Tolerance

### Automatic Retry
```python
@ray.remote(
    max_retries=3,                    # Max retry attempts
    retry_exceptions=[ValueError],    # Retry on these exceptions
    num_cpus=1,
)
def my_task(data):
    return process(data)

# Task will be retried up to 3 times on worker failure
result = ray.get(my_task.remote(input_data))
```

### Retry Parameters
| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_retries` | 3 | Maximum retry attempts |
| `retry_exceptions` | None | Specific exception types to retry on |
| `retry_exception_allowlist` | None | Deprecated, use retry_exceptions |

### Task Failure Scenarios
1. **Worker crash**: Task retried on another worker
2. **Node failure**: Task retried on another node
3. **Exception raised**: Controlled by `retry_exceptions`
4. **Object loss**: Object reconstructed via lineage

### Disabling Retries
```python
@ray.remote(max_retries=0)
def no_retry_task():
    pass
```

## Actor Fault Tolerance

### Actor Restart
```python
@ray.remote(
    max_restarts=3,          # Max restart attempts
    max_task_retries=3,      # Max retries for pending tasks
    num_cpus=2,
)
class StatefulActor:
    def __init__(self):
        self.state = "initial"

    def set_state(self, value):
        self.state = value

    def get_state(self):
        return self.state
```

### Actor Restart Parameters
| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_restarts` | 0 | Max actor restarts (0 = no restart) |
| `max_task_retries` | 0 | Retries for tasks on restart |
| `max_restarts_upon_failure` | None | Max restarts due to failure |

### Actor Lifecycle States
```
DEPENDENCIES_UNREADY → PENDING_CREATION → ALIVE → RESTARTING → ALIVE
                                              → DEAD
```

### Actor Death Handling
```python
# Check if actor is alive
try:
    result = ray.get(actor.some_method.remote(), timeout=5)
except ray.ActorDiedError:
    print("Actor died")
except ray.ActorUnavailableError:
    print("Actor temporarily unavailable")
```

### Detached Actors (Survive Job Exit)
```python
@ray.remote(lifetime="detached")
class PersistentActor:
    pass

# Or dynamically
actor = PersistentActor.options(
    name="my_persistent_actor",
    namespace="my_namespace",
    lifetime="detached",
).remote()
```

### Actor Lifetime Options
| Lifetime | Description |
|----------|-------------|
| `None` (default) | Actor dies with creator job |
| `"detached"` | Actor survives creator job exit |
| `"non_detached"` | Explicitly non-detached |

## Object Fault Tolerance

### Object Reconstruction
Ray reconstructs lost objects via lineage-based recomputation:

```
Task A → Object X → Task B → Object Y → Task C → Result
                         ↑ Node failure
                    Object Y lost
                         ↓
              Recompute: Task A → X → Task B → Y
```

### Reconstruction Conditions
- Object stored in distributed memory (via `ray.put` or task return)
- Lineage is still available
- No reference count exhaustion

### When Reconstruction Fails
1. **Lineage eviction**: Too many tasks, lineage garbage collected
2. **Max reconstruction attempts exceeded**
3. **Source task has side effects** (non-deterministic)
4. **Reference lost**: No live references to the object

### Controlling Reconstruction
```python
# Disable reconstruction for a task
@ray.remote(num_returns=1)
def deterministic_task():
    return result

# Enable reconstruction explicitly
@ray.remote(max_retries=5)
def important_task():
    return critical_result
```

### Object Spilling
When object store memory is full, Ray spills objects to disk:

```python
# Configure spilling
ray.init(
    _system_config={
        "automatic_object_spilling_enabled": True,
        "object_store_full_delay_ms": 1000,
        "object_spilling_config": json.dumps({
            "type": "filesystem",
            "params": {
                "directory_path": "/tmp/ray_spill"
            }
        }),
    }
)
```

### Spilling Configuration
```bash
# Command line
ray start --head \
    --object-store-memory=1000000000 \
    --object-spilling-dir=/tmp/ray_spill
```

## Node Fault Tolerance

### GCS Fault Tolerance
```bash
# Enable GCS FT with Redis backend
ray start --head \
    --redis-password=your_password \
    --dashboard-host=0.0.0.0

# GCS uses external storage for fault tolerance
```

### GCS Configuration
```python
ray.init(
    _system_config={
        "gcs_rpc_server_reconnect_timeout_s": 60,
        "gcs_service_redis_address": "redis://localhost:6379",
    }
)
```

### Worker Node Failure
- Raylet heartbeat to GCS detects node failure
- Tasks on failed node are resubmitted
- Actors on failed node are restarted (if `max_restarts > 0`)
- Objects are reconstructed from lineage

### Head Node Failure
Without GCS FT:
- Entire cluster is lost
- All jobs fail

With GCS FT:
- GCS state persisted to external storage
- Head node can be restarted
- Workers reconnect

## Placement Group Fault Tolerance

### Strategies
```python
# STRICT_SPREAD: Each bundle on different node
pg = ray.util.placement_group(
    bundles=[{"GPU": 1}, {"GPU": 1}, {"GPU": 1}],
    strategy="STRICT_SPREAD",
)

# PACK: Pack bundles on fewest nodes
pg = ray.util.placement_group(
    bundles=[{"CPU": 4}, {"CPU": 4}],
    strategy="PACK",
)

# STRICT_PACK: All bundles on same node
pg = ray.util.placement_group(
    bundles=[{"CPU": 2}, {"CPU": 2}],
    strategy="STRICT_PACK",
)
```

### Placement Group Recovery
```python
pg = ray.util.placement_group(
    bundles=[{"GPU": 1}, {"GPU": 1}],
    strategy="PACK",
    lifetime="detached",       # Survive job exit
    max_replicas_per_group=2,  # Max replicas
)

# Check PG status
status = ray.util.placement_group_table(pg)
# PENDING, CREATED, REMOVED
```

## Application-Level Fault Tolerance

### Checkpointing Pattern
```python
import ray

@ray.remote(max_restarts=3, max_task_retries=3)
class CheckpointedActor:
    def __init__(self, checkpoint_path=None):
        if checkpoint_path:
            self.state = self.load_checkpoint(checkpoint_path)
        else:
            self.state = {}

    def process(self, key, value):
        self.state[key] = value
        self.save_checkpoint()
        return True

    def save_checkpoint(self):
        import json
        with open("/tmp/checkpoint.json", "w") as f:
            json.dump(self.state, f)

    def load_checkpoint(self, path):
        import json
        with open(path) as f:
            return json.load(f)
```

### Task-Level Error Handling
```python
@ray.remote
def may_fail():
    import random
    if random.random() < 0.3:
        raise ValueError("Random failure")
    return "success"

# Handle with ray.get
try:
    result = ray.get(may_fail.remote())
except ValueError as e:
    print(f"Task failed: {e}")

# Handle with ray.wait
refs = [may_fail.remote() for _ in range(10)]
ready, remaining = ray.wait(refs, timeout=10)
for ref in ready:
    try:
        result = ray.get(ref)
    except Exception as e:
        print(f"Error: {e}")
```

### Actor Method Timeout
```python
@ray.remote
class SlowActor:
    def slow_method(self):
        import time
        time.sleep(100)
        return "done"

actor = SlowActor.remote()
ref = actor.slow_method.remote()

try:
    result = ray.get(ref, timeout=5)
except ray.get_timeout_exception():
    print("Method timed out, cancelling")
    ray.cancel(ref, force=True)
```

## Exception Types

### Task/Actor Exceptions
```python
ray.TaskUncommittedError      # Task couldn't be committed
ray.WorkerCrashedError        # Worker process crashed
ray.ObjectLostError           # Object lost and can't reconstruct
ray.ObjectFetchTimedOutError  # Object fetch timed out
ray.ActorDiedError            # Actor died unexpectedly
ray.ActorUnavailableError     # Actor temporarily unavailable
ray.ActorCreationError        # Actor creation failed
ray.RayActorError             # Generic actor error
ray.RayTaskError              # Task execution error
ray.ObjectReconstructionError # Object reconstruction failed
ray.RaySystemError            # Internal Ray error
```

### Handling ObjectLostError
```python
try:
    result = ray.get(some_ref)
except ray.ObjectLostError:
    print("Object was lost, recomputing...")
    new_ref = my_task.remote()
    result = ray.get(new_ref)
```

### Handling RayTaskError
```python
try:
    result = ray.get(task_ref)
except ray.exceptions.RayTaskError as e:
    print(f"Task failed: {e}")
    # e.cause is the original exception
    original = e.cause
```

## Best Practices

1. **Set `max_restarts`** for stateful actors (1-3 restarts)
2. **Use `max_retries`** for idempotent tasks
3. **Checkpoint state** externally for critical actors
4. **Handle exceptions** explicitly rather than relying on retries
5. **Use detached actors** for long-lived services
6. **Monitor object store** to detect memory pressure early
7. **Enable GCS fault tolerance** for production clusters
8. **Keep tasks idempotent** for safe retries
9. **Use `ray.wait()`** with timeouts for long operations
10. **Log state transitions** for debugging actor restarts
